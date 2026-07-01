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

from kazma_core.swarm.blackboard import SwarmDispatchContext, context_text
from kazma_core.swarm.task import WorkerCapabilities

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def _compose_context_payload(context: str | SwarmDispatchContext) -> str:
    """Render dispatch context text, including consult system guidance."""
    plain_context = context_text(context).strip()
    if not isinstance(context, SwarmDispatchContext) or not context.system_prompt:
        return plain_context

    sections = [f"System prompt:\n{context.system_prompt.strip()}"]
    if plain_context:
        sections.append(f"Additional context:\n{plain_context}")
    return "\n\n".join(sections)


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
                # The worker is pinned to a specific provider — build a
                # client directly for that provider + model combination.
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

            # Build messages with system prompt and context from SwarmDispatchContext
            messages: list[dict[str, str]] = []
            system_prompt = None
            context_text = ""
            if isinstance(context, SwarmDispatchContext):
                system_prompt = context.system_prompt
                context_text = context.text
            elif context:
                context_text = str(context)

            # If no system_prompt from context, use the worker's own (from config/registry)
            if not system_prompt and self.system_prompt:
                system_prompt = self.system_prompt

            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            user_content = task
            if context_text:
                user_content = f"{task}\n\n--- Context ---\n{context_text}"
            messages.append({"role": "user", "content": user_content})

            response = await provider.chat(messages)

            # Extract token/cost data from response.
            # usage may contain nested dicts from modern APIs
            # (e.g. completion_tokens_details) — filter to scalars only.
            usage = getattr(response, "usage", {}) or {}
            cost_usd = getattr(response, "cost_usd", 0.0)
            tokens_used = sum(
                v for v in (usage.values() if usage else ())
                if isinstance(v, (int, float))
            )

            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "success",
                "output": response.content,
                "error": None,
                "tokens_used": tokens_used,
                "cost": cost_usd,
                "duration_seconds": time.monotonic() - dispatch_started,
            }
        except Exception as exc:
            logger.exception("[InProcessWorker:%s] dispatch failed", self.name)
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "error",
                "output": "",
                "error": str(exc)[:500],
                "tokens_used": 0,
                "cost": 0.0,
                "duration_seconds": time.monotonic() - dispatch_started,
            }
