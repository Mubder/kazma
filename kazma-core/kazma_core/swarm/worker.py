"""Worker abstractions for the SwarmManager.

Two concrete implementations:

* **InProcessWorker** — wraps ``kazma_core.agent.sub_agent.SubAgentManager.spawn``
  for fast, in-process delegation (same model, shared memory).
* **TelegramWorker** — launches a separate Hermes profile via subprocess
  (``hermes -p <profile>``), targeting a Telegram group chat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
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

    def mark_completed(self, status: str) -> None:
        """Record completion metadata for status and logs."""
        self.busy = False
        self.last_heartbeat = _utc_now_iso()
        self.logs.append(f"[{self.last_heartbeat}] Task completed with status={status}")


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
    ) -> None:
        super().__init__(
            name=name,
            role=role,
            model=model,
            provider=provider,
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
        logger.info("[InProcessWorker:%s] dispatching %s", self.name, task_id)
        try:
            manager = self._get_manager()
            result = await manager.spawn(
                goal=task,
                context=_compose_context_payload(context),
            )
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": result.status,
                "output": result.summary,
                "error": result.error,
            }
        except Exception as exc:
            logger.exception("[InProcessWorker:%s] dispatch failed", self.name)
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "error",
                "output": "",
                "error": str(exc)[:500],
            }


# ---------------------------------------------------------------------------
# Telegram bot (subprocess hermes -p)
# ---------------------------------------------------------------------------

class TelegramWorker(SwarmWorker):
    """Runs a dedicated Hermes profile as a subprocess.

    The worker shells out to ``hermes -p <profile>`` and pipes the task as
    a one-shot prompt.  The bot token is read from the environment variable
    named by ``bot_token_env``.

    .. note::

        **External dependency:** This worker requires the ``hermes`` CLI to be
        installed and available on ``PATH``.  ``hermes`` is a separate Telegram
        bot runner (not bundled with Kazma).  If it is not installed, dispatch
        will return an ``error`` result.  Install it separately or use
        :class:`InProcessWorker` instead.

    Args:
        name:          Worker identifier.
        profile:       Hermes profile name (e.g. ``core``).
        bot_token_env: Env var holding the Telegram bot token.
        group_chat_id: Telegram group chat ID to target (read from
                       ``SWARM_CHAT_ID`` env var by the config loader).
        role:          Semantic role.
    """

    def __init__(
        self,
        name: str,
        profile: str,
        bot_token_env: str,
        group_chat_id: int = 0,
        role: str = "",
        model: str = "",
        provider: str = "",
        capabilities: WorkerCapabilities | None = None,
    ) -> None:
        super().__init__(
            name=name,
            role=role,
            model=model,
            provider=provider,
            capabilities=capabilities or WorkerCapabilities(role=role),
        )
        self.profile = profile
        self.bot_token_env = bot_token_env
        self.group_chat_id = group_chat_id
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Verify the bot token is available (no subprocess yet)."""
        token = os.environ.get(self.bot_token_env, "")
        if not token:
            logger.warning(
                "[TelegramWorker:%s] env var %s not set — bot will fail on dispatch",
                self.name,
                self.bot_token_env,
            )
        self._running = True
        logger.info("[TelegramWorker:%s] registered (profile=%s)", self.name, self.profile)

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
            logger.info("[TelegramWorker:%s] process terminated", self.name)
        self._process = None
        self._running = False

    async def dispatch(
        self,
        task: str,
        context: str | SwarmDispatchContext = "",
    ) -> dict[str, Any]:
        """Send *task* to the Hermes profile via ``hermes -p <profile>`` CLI.

        This is a one-shot invocation — hermes processes the prompt and exits.
        """
        task_id = f"swarm-{self.name}-{uuid.uuid4().hex[:8]}"
        logger.info("[TelegramWorker:%s] dispatching %s", self.name, task_id)

        prompt = task
        context_value = _compose_context_payload(context)
        if context_value:
            prompt = f"{task}\n\nContext:\n{context_value}"

        cmd = f"hermes -p {shlex.quote(self.profile)} {shlex.quote(prompt)}"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode("utf-8", errors="replace").strip()
            err_text = stderr.decode("utf-8", errors="replace").strip()

            status = "success" if proc.returncode == 0 else "error"
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": status,
                "output": output,
                "error": err_text if err_text else None,
            }
        except TimeoutError:
            logger.warning("[TelegramWorker:%s] dispatch timed out", self.name)
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "timeout",
                "output": "",
                "error": "Dispatch timed out after 300s",
            }
        except Exception as exc:
            logger.exception("[TelegramWorker:%s] dispatch failed", self.name)
            return {
                "worker": self.name,
                "task_id": task_id,
                "status": "error",
                "output": "",
                "error": str(exc)[:500],
            }

    @property
    def worker_type(self) -> str:
        """Return the UI-facing worker type label."""
        return "telegram"
