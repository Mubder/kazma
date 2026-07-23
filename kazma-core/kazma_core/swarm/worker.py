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

__all__ = ["InProcessWorker", "SwarmWorker"]

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
        
        # Initialize variables before try block so exception handler can access them safely
        total_tokens = 0
        total_cost = 0.0
        last_content = ""
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

            # Branding + product family (short) so Arabic replies never use كازما.
            try:
                from kazma_core.product_knowledge import identity_line

                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"{identity_line()} You are a swarm worker named "
                            f"{self.name} inside the Kazma framework. Prefer "
                            "workspace tools and accurate product terminology."
                        ),
                    }
                )
            except Exception:
                logger.debug(
                    "[InProcessWorker:%s] product identity injection skipped",
                    self.name, exc_info=True,
                )

            # ── Environment awareness (IDE/workspace/repo/tools) ──────
            # Inject a dedicated env-context system message so the worker
            # knows where the workspace is, what repo/branch it's in, and
            # which tools it has. Without this, workers get stuck on
            # discovery — they had the tools but no prompt ever told them.
            try:
                from kazma_core.ide.env_context import build_env_context

                # Honor per-task workspace_id (Phase 3) if present on the
                # dispatch context metadata.
                ws_id = None
                if isinstance(context, SwarmDispatchContext):
                    ws_id = context.metadata.get("workspace_id")
                env_block = build_env_context(workspace_id=ws_id)
                if env_block:
                    messages.append({"role": "system", "content": env_block})
            except Exception:
                logger.debug(
                    "[InProcessWorker:%s] env context injection skipped",
                    self.name, exc_info=True,
                )

            user_content = task
            if context_text:
                user_content = f"{task}\n\n--- Context ---\n{context_text}"
            messages.append({"role": "user", "content": user_content})

            # ── ReAct loop ────────────────────────────────────────────
            final_output = ""
            executed_tools: dict[str, str] = {}  # Track "tool_name:sorted_json_args" -> result_content
            _circuit_breaker_tripped = False
            _consecutive_tool_failures = 0

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
                    if _circuit_breaker_tripped:
                        logger.warning(
                            "[InProcessWorker:%s] Circuit breaker is active! Bypassing tool execution for '%s'.",
                            self.name, tc.name
                        )
                        result = {
                            "content": "SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now.",
                            "is_error": True,
                        }
                    else:
                        tc_args_str = _json.dumps(tc.arguments, sort_keys=True)
                        tc_key = f"{tc.name}:{tc_args_str}"

                        # 1. Loop-Prevention Intercept
                        if tc_key in executed_tools:
                            prev_res = executed_tools[tc_key]
                            logger.warning(
                                "[InProcessWorker:%s] Loop detected! Tool '%s' called with identical args.",
                                self.name, tc.name
                            )
                            result_content = (
                                f"System Note: You already executed '{tc.name}' with these identical arguments. "
                                f"The previous result was: '{prev_res}'. "
                                f"Repeating this call is forbidden and will not change the outcome. "
                                f"Please either try a different search query variation, use an alternative tool, "
                                f"or formulate your final response using available knowledge."
                            )
                            result = {"content": result_content, "is_error": True}
                        else:
                            # 2. Fresh Tool Execution
                            if tool_registry is None:
                                result = {
                                    "content": "Tool execution unavailable: registry not loaded.",
                                    "is_error": True,
                                }
                            else:
                                try:
                                    result = await tool_registry.execute(tc.name, tc.arguments)
                                    # Record result content for deduplication
                                    executed_tools[tc_key] = result.get("content", "")
                                except Exception:
                                    logger.exception(
                                        "[InProcessWorker:%s] tool %s execution failed",
                                        self.name, tc.name,
                                    )
                                    result = {
                                        "content": f"Tool execution error: {tc.name}",
                                        "is_error": True,
                                    }

                        # 3. Error-Only Circuit Breaker (matches graph_builder fix)
                        # Only actual errors and denials count as failures —
                        # empty search results ("no results" / "[]") are normal
                        # for research tasks, not tool malfunctions.
                        content_str = str(result.get("content", "")).strip()
                        is_failure = (
                            result.get("is_error", False)
                            or "denied by user" in content_str.lower()
                        )

                        if is_failure:
                            _consecutive_tool_failures += 1
                        else:
                            _consecutive_tool_failures = 0

                        if _consecutive_tool_failures >= 3:
                            logger.warning(
                                "[InProcessWorker:%s] Circuit breaker tripped! %d consecutive tool failures for %s.",
                                self.name, _consecutive_tool_failures, tc.name
                            )
                            result["content"] = "SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now."
                            result["is_error"] = True
                            _circuit_breaker_tripped = True
                            # We do NOT break here. The tool loop must continue to process
                            # remaining tool calls in the batch to avoid HTTP 400 errors.

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
