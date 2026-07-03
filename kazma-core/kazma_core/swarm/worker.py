"""Worker abstractions for the SwarmManager.

One concrete implementation:

* **InProcessWorker** — calls the LLM directly via ModelRegistry for fast,
  in-process dispatch (shared model registry, token/cost tracking).
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kazma_core.swarm.blackboard import SwarmDispatchContext
from kazma_core.swarm.task import WorkerCapabilities

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass
class SwarmWorker(ABC):
    """Abstract base for all swarm workers."""

    name: str
    role: str = ""
    model: str = ""
    provider: str = ""
    system_prompt: str = ""
    capabilities: WorkerCapabilities = field(default_factory=WorkerCapabilities)
    added_at: str = field(default_factory=_utc_now_iso, init=False)
    busy: bool = field(default=False, init=False)
    last_task: str | None = field(default=None, init=False)
    last_heartbeat: str | None = field(default=None, init=False)
    logs: list[str] = field(default_factory=list, init=False)
    _running: bool = field(default=False, init=False, repr=False)

    @abstractmethod
    async def dispatch(
        self,
        task: str,
        context: str | SwarmDispatchContext = "",
    ) -> dict[str, Any]:
        """Send a task to this worker and return a result dict.

        Returns::

            {
                "worker": str,
                "task_id": str,
                "status": "success" | "error" | "timeout",
                "output": str,
                "error": str | None,
            }
        """
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start / warm-up the worker."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Shut down the worker gracefully."""
        ...

    async def status(self) -> dict[str, Any]:
        """Return health information."""
        return {
            "name": self.name,
            "role": self.role,
            "model": self.model,
            "provider": self.provider,
            "running": self._running,
        }

    @property
    def worker_type(self) -> str:
        """Return the UI-facing worker type label."""
        return "worker"

    def mark_dispatched(self, task: str) -> None:
        """Record dispatch metadata for status and logs."""
        self.busy = True
        self.last_task = task
        self.last_heartbeat = _utc_now_iso()
        self.logs.append(f"[{self.last_heartbeat}] Task dispatched: {task[:80]}")
        if len(self.logs) > 100:
            del self.logs[:len(self.logs) - 100]

    def mark_completed(self, status: str) -> None:
        """Record completion metadata for status and logs."""
        self.busy = False
        self.last_heartbeat = _utc_now_iso()
        self.logs.append(f"[{self.last_heartbeat}] Task completed with status={status}")
        if len(self.logs) > 100:
            del self.logs[:len(self.logs) - 100]


# ---------------------------------------------------------------------------
# In-process (sub_agent)
# ---------------------------------------------------------------------------

class InProcessWorker(SwarmWorker):
    """Delegates tasks via :class:`kazma_core.agent.sub_agent.SubAgentManager`.

    Args:
        name:     Worker identifier.
        role:     Semantic role (e.g. ``backend_core``).
        manager:  A ``SubAgentManager`` instance (or ``None`` to use the
                   global singleton via ``get_sub_agent_manager``).
    """

    def __init__(
        self,
        name: str,
        role: str = "",
        model: str = "",
        provider: str = "",
        capabilities: WorkerCapabilities | None = None,
        manager: Any = None,
        system_prompt: str = "",
    ) -> None:
        super().__init__(
            name=name,
            role=role,
            model=model,
            provider=provider,
            system_prompt=system_prompt,
            capabilities=capabilities or WorkerCapabilities(role=role),
        )
        self._manager = manager

    def _get_manager(self) -> Any:
        if self._manager is not None:
            return self._manager
        # Lazy import to avoid circular deps
        from kazma_core.agent.sub_agent import get_sub_agent_manager
        mgr = get_sub_agent_manager()
        if mgr is None:
            raise RuntimeError(
                "SubAgentManager not initialised. "
                "Call set_sub_agent_manager() at app startup or pass manager= explicitly."
            )
        return mgr

    async def start(self) -> None:
        self._running = True
        logger.info("[InProcessWorker:%s] started", self.name)

    async def stop(self) -> None:
        self._running = False
        logger.info("[InProcessWorker:%s] stopped", self.name)

    @property
    def worker_type(self) -> str:
        """Return the UI-facing worker type label."""
        return "in-process"

    async def dispatch(
        self,
        task: str,
        context: str | SwarmDispatchContext = "",
    ) -> dict[str, Any]:
        """Dispatch a task to the LLM with tool-calling support.

        The worker runs a lightweight ReAct loop: it calls the LLM with
        available tools, executes any tool calls returned, feeds the results
        back into the conversation, and repeats until the model produces a
        final text response (or the iteration limit is reached).
        """
        import json as _json

        MAX_ITERATIONS = 15
        task_id = f"swarm-{self.name}-{uuid.uuid4().hex[:8]}"
        logger.info("[InProcessWorker:%s] dispatching %s (model=%s)", self.name, task_id, self.model or "default")
        dispatch_started = time.monotonic()
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()

            # Resolve the correct provider for this worker's model.
            # Priority: (1) worker's self.provider, (2) model search
            # across all providers, (3) active/default provider.
            provider = None
            if self.provider:
                provider = registry.get_client_by_provider(
                    self.provider, model=self.model or None
                )
            if provider is None and self.model:
                try:
                    provider = registry.get_model(self.model)
                except Exception:
                    logger.debug(
                        "[InProcessWorker:%s] get_model(%s) failed, falling back to get_client",
                        self.name, self.model, exc_info=True,
                    )
                    provider = registry.get_client(model=self.model)
            if provider is None:
                provider = registry.get_client()

            if provider is None:
                return {"worker": self.name, "task_id": task_id, "status": "error", "output": "", "error": "No provider available"}

            # ── Tool definitions ──────────────────────────────────────
            tool_defs: list[dict[str, Any]] = []
            tool_registry = None
            try:
                from kazma_core.agent.tool_registry import get_tool_registry
                tool_registry = get_tool_registry()
                all_defs = tool_registry.get_tool_definitions()
                allowed = getattr(self.capabilities, "tools", None) or []
                if allowed:
                    allowed_set = set(allowed)
                    tool_defs = [
                        td for td in all_defs
                        if td["function"]["name"] in allowed_set
                    ]
                else:
                    tool_defs = all_defs
            except Exception:
                logger.debug(
                    "[InProcessWorker:%s] tool registry unavailable, proceeding without tools",
                    self.name, exc_info=True,
                )

            # ── Build initial messages ─────────────────────────────────
            messages: list[dict[str, Any]] = []
            system_prompt = None
            context_text = ""
            if isinstance(context, SwarmDispatchContext):
                system_prompt = context.system_prompt
                context_text = context.text
            elif context:
                context_text = str(context)

            if not system_prompt and self.system_prompt:
                system_prompt = self.system_prompt

            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            user_content = task
            if context_text:
                user_content = f"{task}\n\n--- Context ---\n{context_text}"
            messages.append({"role": "user", "content": user_content})

            # ── ReAct loop ────────────────────────────────────────────
            total_tokens = 0
            total_cost = 0.0
            final_output = ""
            # Track the last non-empty content so partial progress is
            # preserved if provider.chat() raises on a later iteration.
            last_content = ""

            for iteration in range(1, MAX_ITERATIONS + 1):
                response = await provider.chat(
                    messages,
                    tools=tool_defs if tool_defs else None,
                    model=self.model or None,
                )

                # Accumulate token/cost across all iterations.
                # Sum ONLY prompt_tokens + completion_tokens — NOT
                # total_tokens (which equals their sum, causing ~2x
                # over-counting if summed alongside them).
                usage = getattr(response, "usage", {}) or {}
                total_tokens += int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
                total_cost += getattr(response, "cost_usd", 0.0) or 0.0

                # No tool calls → final response
                if not response.tool_calls:
                    final_output = response.content or last_content
                    break

                # Track any content the model produced alongside tool calls
                # so partial progress isn't lost on a later-iteration failure.
                if response.content:
                    last_content = response.content

                logger.info(
                    "[InProcessWorker:%s] iteration %d — %d tool call(s): %s",
                    self.name, iteration, len(response.tool_calls),
                    [tc.name for tc in response.tool_calls],
                )

                # Append the assistant message (with tool_calls block).
                # tool_calls is guaranteed truthy here (we checked above).
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": _json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call and append the result.
                for tc in response.tool_calls:
                    if tool_registry is None:
                        result = {
                            "content": "Tool execution unavailable: registry not loaded.",
                            "is_error": True,
                        }
                    else:
                        try:
                            result = await tool_registry.execute(tc.name, tc.arguments)
                        except Exception:
                            logger.exception(
                                "[InProcessWorker:%s] tool %s execution failed",
                                self.name, tc.name,
                            )
                            result = {
                                "content": f"Tool execution error: {tc.name}",
                                "is_error": True,
                            }
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.get("content", ""),
                    })

            else:
                # Iteration limit exhausted — return the last response we got.
                final_output = (
                    response.content
                    if hasattr(response, "content") and response.content
                    else "Max tool-use iterations reached without a final answer."
                )

            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "success",
                "output": final_output,
                "error": None,
                "tokens_used": total_tokens,
                "cost": total_cost,
                "duration_seconds": time.monotonic() - dispatch_started,
            }
        except Exception as exc:
            logger.exception("[InProcessWorker:%s] dispatch failed", self.name)
            # Preserve partial progress: if the ReAct loop accumulated
            # any content before the exception, return it rather than
            # discarding everything.  The accumulated tokens/cost are
            # also retained for accurate accounting.
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "error",
                "output": last_content if last_content else "",
                "error": str(exc)[:500],
                "tokens_used": total_tokens,
                "cost": total_cost,
                "duration_seconds": time.monotonic() - dispatch_started,
            }
