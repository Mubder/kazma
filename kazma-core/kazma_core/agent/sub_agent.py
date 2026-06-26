"""Sub-agent spawning for autonomous task delegation.

The Brain calls spawn_agent(goal, context) as a tool.
Each child is an independent LangGraph invocation with its own
thread_id, checkpoint, and toolset. Results stream back and are
appended to the parent's message history.

Safety modes:
    "auto_deny" — Child HITL interrupts auto-reject after 1s (default)
    "inherit"   — Child inherits parent HITL config as-is
    "disabled"  — No HITL in child (all tools allowed)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singleton — set by app.py at startup
_sub_agent_manager: SubAgentManager | None = None


def set_sub_agent_manager(manager: SubAgentManager) -> None:
    """Set the global sub-agent manager (called by app.py at startup)."""
    global _sub_agent_manager
    _sub_agent_manager = manager


def get_sub_agent_manager() -> SubAgentManager | None:
    """Get the global sub-agent manager."""
    return _sub_agent_manager


@dataclass(slots=True)
class SubAgentResult:
    """Result from a sub-agent execution."""

    task_id: str
    goal: str
    status: str  # "success" | "error" | "timeout"
    summary: str
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "error": self.error,
        }


class SubAgentManager:
    """Manages spawning and lifecycle of child agent graphs.

    Args:
        graph_builder:  Callable that builds a compiled LangGraph.
                        Signature: (tools=None, hitl_config=None) -> compiled_graph
        store:          SessionStore for persisting child context.
        checkpointer:   AsyncSqliteSaver for child checkpointing.
        max_concurrent: Max parallel child agents (default 3).
    """

    def __init__(
        self,
        graph_builder: Any,
        store: Any = None,
        checkpointer: Any = None,
        max_concurrent: int = 3,
    ) -> None:
        self._graph_builder = graph_builder
        self._store = store
        self._checkpointer = checkpointer
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active: dict[str, asyncio.Task[SubAgentResult]] = {}

    async def spawn(
        self,
        goal: str,
        context: str = "",
        tools: list[str] | None = None,
        safety_mode: str = "auto_deny",
        timeout: float = 120.0,
    ) -> SubAgentResult:
        """Spawn a single sub-agent for a focused task.

        Args:
            goal:         Specific goal for the sub-agent.
            context:      Background info the sub-agent needs.
            tools:        Tool names to restrict to (None = all).
            safety_mode:  "auto_deny" | "inherit" | "disabled"
            timeout:      Max seconds before the child is killed.

        Returns:
            SubAgentResult with status, summary, and artifacts.
        """
        task_id = f"sub-{uuid.uuid4().hex[:8]}"

        async with self._semaphore:
            logger.info("[SubAgent] Spawning %s: %.80s", task_id, goal)

            try:
                # Build child graph with HITL config
                hitl_config = self._build_hitl_config(safety_mode)
                child_graph = self._graph_builder(
                    tools=tools,
                    hitl_config=hitl_config,
                )

                config = {"configurable": {"thread_id": task_id}}
                initial_state: dict[str, Any] = {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"You are a focused sub-agent. Your goal:\n{goal}\n\n"
                                f"Context:\n{context}\n\n"
                                "Complete the task and return a clear summary."
                            ),
                        },
                        {"role": "user", "content": goal},
                    ],
                }

                # Run with timeout
                result = await asyncio.wait_for(
                    child_graph.ainvoke(initial_state, config=config),
                    timeout=timeout,
                )

                # Extract summary from last assistant message
                messages = result.get("messages", [])
                summary = ""
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        summary = str(msg.get("content", ""))[:2000]
                        break

                # Extract artifacts (files created, etc.)
                artifacts = []
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "tool":
                        content = str(msg.get("content", ""))
                        if "Wrote" in content or "Created" in content:
                            artifacts.append(content[:200])

                logger.info(
                    "[SubAgent] %s completed (%d messages, %d artifacts)",
                    task_id,
                    len(messages),
                    len(artifacts),
                )

                return SubAgentResult(
                    task_id=task_id,
                    goal=goal,
                    status="success",
                    summary=summary or "(No summary produced)",
                    artifacts=artifacts,
                )

            except TimeoutError:
                logger.warning("[SubAgent] %s timed out after %.0fs", task_id, timeout)
                return SubAgentResult(
                    task_id=task_id,
                    goal=goal,
                    status="timeout",
                    summary="",
                    error=f"Timed out after {timeout}s",
                )

            except Exception as exc:
                logger.exception("[SubAgent] %s failed", task_id)
                return SubAgentResult(
                    task_id=task_id,
                    goal=goal,
                    status="error",
                    summary="",
                    error=str(exc)[:500],
                )

    async def spawn_parallel(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[SubAgentResult]:
        """Spawn multiple sub-agents in parallel.

        Args:
            tasks: List of task specs, each with "goal", optional "context",
                   optional "tools", optional "safety_mode", optional "timeout".
                   Max 5 tasks.

        Returns:
            List of SubAgentResult, one per task.
        """
        if len(tasks) > 5:
            logger.warning("[SubAgent] Clamping %d tasks to max 5", len(tasks))
            tasks = tasks[:5]

        coros = [
            self.spawn(
                goal=t["goal"],
                context=t.get("context", ""),
                tools=t.get("tools"),
                safety_mode="auto_deny",  # Ignore user-supplied safety_mode — always enforce HITL
                timeout=t.get("timeout", 120.0),
            )
            for t in tasks
        ]
        return await asyncio.gather(*coros)

    @staticmethod
    def _build_hitl_config(safety_mode: str) -> dict[str, Any] | None:
        """Build HITL config based on safety mode.

        Args:
            safety_mode: "auto_deny" | "inherit" | "disabled"

        Returns:
            HITL config dict or None.
        """
        if safety_mode == "disabled":
            return {"enabled": False}

        if safety_mode == "auto_deny":
            # Child agents auto-deny danger tools after 1s
            return {
                "enabled": True,
                "require_approval_for": ["file_write", "file_delete", "shell_exec"],
                "approval_timeout_seconds": 1,
                "auto_deny_on_timeout": True,
            }

        # "inherit" — caller passes their own config
        return None
