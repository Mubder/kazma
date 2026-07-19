"""Swarm Safety Middleware — bus-gated HITL for tool execution.

Intercepts SwarmEngine dispatches and blocks "danger"-tier tool calls
until the operator approves through the SwarmMessageBus.  Extends the
existing HITL tier system with bus integration and broader tool coverage.

Danger-tier tools now include:
    file_write, file_delete, shell_exec, python_exec, code_exec,
    spawn_agent, spawn_agents, sqlite_query (writes),
    schedule_task, cancel_scheduled
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

__all__ = ["SafetyMiddleware", "SafetyViolationError", "get_safety", "set_safety"]

logger = logging.getLogger(__name__)

# Single source of truth — see kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS.
# Alias kept so existing imports of _EXTENDED_DANGER keep working.
_EXTENDED_DANGER: list[str] = list(CANONICAL_DANGER_TOOLS)

# Tools classified as "sensitive reads" — allowed but logged.
_SENSITIVE_READS = [
    "sqlite_query",
    "file_search",
]

# Maximum time to wait for approval before auto-rejecting.
_DEFAULT_APPROVAL_TIMEOUT = 60.0  # seconds


class SafetyViolationError(Exception):
    """Raised when a tool call is blocked by the safety middleware."""


class SafetyMiddleware:
    """Bus-gated safety layer for swarm tool execution.

    Wraps the message bus to gate dangerous operations behind operator
    approval.  Integrates with the existing HITL tier system from
    ``kazma_core.safety.hitl``.

    Args:
        enabled: Whether safety gating is active.  When False all tools
                 pass through (development mode).

    Usage::

        safety = SafetyMiddleware()
        engine.set_safety_middleware(safety)

        # The engine calls this before any dangerous tool:
        if await safety.check("shell_exec", "rm -rf /tmp/old-logs", task_id):
            # approved — proceed
        else:
            # rejected — abort
    """

    def __init__(self, enabled: bool = True, allow_headless_danger: bool = False) -> None:
        self.enabled = enabled
        # When True, danger tools are allowed even with no bus adapter
        # (NullBusAdapter). Defaults to False (fail-closed) so unattended
        # deployments cannot run danger tools without an approval path.
        # Tests/dev set this to True to avoid wiring a bus everywhere.
        self.allow_headless_danger = allow_headless_danger
        self._danger_tools: set[str] = set(_EXTENDED_DANGER)
        self._sensitive_reads: set[str] = set(_SENSITIVE_READS)
        self._approval_timeout: float = _DEFAULT_APPROVAL_TIMEOUT
        self._blocked_count: int = 0
        self._approved_count: int = 0
        self._rejected_count: int = 0

    # ── Configuration ───────────────────────────────────────────────────

    def add_danger_tool(self, tool_name: str) -> None:
        """Register an additional tool as danger-tier."""
        self._danger_tools.add(tool_name)
        logger.info("[Safety] Added danger-tier tool: %s", tool_name)

    def remove_danger_tool(self, tool_name: str) -> None:
        """Remove a tool from the danger tier."""
        self._danger_tools.discard(tool_name)
        logger.info("[Safety] Removed danger-tier tool: %s", tool_name)

    def is_danger_tool(self, tool_name: str) -> bool:
        """Check if a tool requires approval."""
        return tool_name in self._danger_tools

    def is_sensitive_read(self, tool_name: str) -> bool:
        """Check if a tool is a sensitive read (allowed but logged)."""
        return tool_name in self._sensitive_reads

    # ── Gating ──────────────────────────────────────────────────────────

    async def check(
        self,
        tool_name: str,
        tool_args: str | None = None,
        task_id: str = "",
        worker_name: str = "",
        *,
        force_danger: bool = False,
    ) -> bool:
        """Check if a tool call should be allowed.

        Returns True if the tool call is safe or approved.  Returns
        False if it was rejected or timed out.

        For danger-tier tools, this will post an approval request to
        the bus and wait for the operator's response.

        Args:
            force_danger: When True, treat *tool_name* as danger-tier even
                if it is not in the static ``_EXTENDED_DANGER`` list.
                Required for MCP tools whose names (e.g. ``write_file``,
                ``run_command``) differ from local builtins (``file_write``,
                ``shell_exec``) but were classified as danger/unknown.

        Fail-closed: when no real bus adapter is wired (``NullBusAdapter``),
        danger tools are rejected unless ``allow_headless_danger`` is set
        (test/dev escape hatch).  This mirrors :meth:`check_sync` so the
        async and sync paths enforce consistently — previously the async
        path silently auto-approved via ``NullBusAdapter.request_approval``.
        """
        if not self.enabled:
            return True  # development mode

        from kazma_core.safety.hitl import get_current_thread_id
        tid = get_current_thread_id()
        if tid:
            try:
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                if cs.get(f"yolo.{tid}"):
                    logger.info("[Safety] YOLO mode active for thread=%s. Auto-approving dangerous tool: %s", tid, tool_name)
                    return True
            except Exception:
                pass

        if not force_danger and self.is_sensitive_read(tool_name):
            logger.info("[Safety] Sensitive read allowed: %s (task=%s)", tool_name, task_id)
            return True

        if not force_danger and not self.is_danger_tool(tool_name):
            return True  # safe tool

        # ── Danger tool — request approval ─────────────────
        logger.warning("[Safety] Danger tool blocked pending approval: %s", tool_name)
        self._blocked_count += 1

        from kazma_core.swarm.bus import NullBusAdapter, get_message_bus

        bus = get_message_bus()
        # Fail-closed when no real adapter is wired (mirror check_sync).
        # NullBusAdapter.request_approval() returns True (auto-approve), which
        # would silently bypass HITL for danger tools in headless deployments.
        if isinstance(bus.adapter, NullBusAdapter):
            if self.allow_headless_danger:
                self._approved_count += 1
                logger.info(
                    "[Safety] Danger tool APPROVED (headless; allow_headless_danger=True): "
                    "%s (task=%s)", tool_name, task_id,
                )
                return True
            self._rejected_count += 1
            logger.warning(
                "[Safety] Danger tool '%s' BLOCKED (no approval bus; "
                "allow_headless_danger=False) (task=%s)", tool_name, task_id,
            )
            return False

        approved = await bus.request_approval(
            worker_name=worker_name,
            task_description=f"Tool: {tool_name}" + (f" — {tool_args[:100]}" if tool_args else ""),
            proposed_output=f"Danger-tier tool '{tool_name}' requires approval before execution.",
            task_id=task_id,
            timeout=self._approval_timeout,
        )

        if approved:
            self._approved_count += 1
            logger.info("[Safety] Danger tool APPROVED: %s (task=%s)", tool_name, task_id)
        else:
            self._rejected_count += 1
            logger.warning("[Safety] Danger tool REJECTED: %s (task=%s)", tool_name, task_id)

        return approved

    def check_sync(self, tool_name: str, *, force_danger: bool = False) -> bool:
        """Synchronous check — fail-closed gate for danger tools.

        Returns True (allow) if:
          - SafetyMiddleware is disabled
          - Tool is not danger-tier (unless force_danger=True)
        Returns False (block) if:
          - Tool is danger-tier AND either:
            - a real bus adapter is available (use async check() to approve), OR
            - no bus adapter exists and allow_headless_danger is False (default)
        Returns True only when allow_headless_danger is True and no real
        bus adapter is present (test/dev escape hatch).
        """
        if not self.enabled:
            return True
        if not force_danger and not self.is_danger_tool(tool_name):
            return True
        # Danger tool — check bus adapter state.
        try:
            from kazma_core.swarm.bus import NullBusAdapter, get_message_bus
            bus = get_message_bus()
            if isinstance(bus._adapter, NullBusAdapter):
                # No real adapter — fail-closed unless explicitly relaxed.
                if self.allow_headless_danger:
                    return True
                self._rejected_count += 1
                logger.warning(
                    "[Safety] Danger tool '%s' BLOCKED (no approval bus; "
                    "allow_headless_danger=False)", tool_name,
                )
                return False
            # A real adapter exists — the sync path cannot wait for
            # approval, so block; callers must use the async check().
            self._rejected_count += 1
            return False
        except Exception as exc:
            # Bus unavailable — fail-closed unless explicitly relaxed.
            if self.allow_headless_danger:
                return True
            self._rejected_count += 1
            logger.warning(
                "[Safety] Danger tool '%s' BLOCKED (bus error; "
                "allow_headless_danger=False): %s", tool_name, exc,
            )
            return False

    # ── Statistics ──────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return safety statistics for monitoring."""
        return {
            "enabled": self.enabled,
            "danger_tools": sorted(self._danger_tools),
            "blocked_count": self._blocked_count,
            "approved_count": self._approved_count,
            "rejected_count": self._rejected_count,
            "approval_timeout": self._approval_timeout,
        }


# Module-level singleton
_safety: SafetyMiddleware | None = None


def get_safety() -> SafetyMiddleware:
    """Return the shared SafetyMiddleware instance."""
    global _safety
    if _safety is None:
        _safety = SafetyMiddleware()
    return _safety


def set_safety(safety: SafetyMiddleware) -> None:
    """Replace the shared SafetyMiddleware instance."""
    global _safety
    _safety = safety
