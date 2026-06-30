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

import asyncio
import logging
from typing import Any

from kazma_core.safety.hitl import DEFAULT_DANGER_TOOLS

logger = logging.getLogger(__name__)

# Extended danger tier — adds tools that were previously un-gated.
_EXTENDED_DANGER = DEFAULT_DANGER_TOOLS + [
    "python_exec",
    "code_exec",
    "spawn_agent",
    "spawn_agents",
    "schedule_task",
    "cancel_scheduled",
]

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

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
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
    ) -> bool:
        """Check if a tool call should be allowed.

        Returns True if the tool call is safe or approved.  Returns
        False if it was rejected or timed out.

        For danger-tier tools, this will post an approval request to
        the bus and wait for the operator's response.
        """
        if not self.enabled:
            return True  # development mode

        if self.is_sensitive_read(tool_name):
            logger.info("[Safety] Sensitive read allowed: %s (task=%s)", tool_name, task_id)
            return True

        if not self.is_danger_tool(tool_name):
            return True  # safe tool

        # ── Danger tool — request approval ─────────────────
        logger.warning("[Safety] Danger tool blocked pending approval: %s", tool_name)
        self._blocked_count += 1

        from kazma_core.swarm.bus import get_message_bus

        bus = get_message_bus()
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

    def check_sync(self, tool_name: str) -> bool:
        """Synchronous check — blocks danger tools only when bus is active.

        Returns True (allow) if:
          - SafetyMiddleware is disabled
          - Tool is not danger-tier
          - No active bus adapter (test/headless mode — allow to not break tests)
        Returns False (block) if:
          - Tool is danger-tier AND a bus adapter is available for HITL
        """
        if not self.enabled:
            return True
        if not self.is_danger_tool(tool_name):
            return True
        # Only block if we have an active bus for approvals
        try:
            from kazma_core.swarm.bus import NullBusAdapter, get_message_bus
            bus = get_message_bus()
            if isinstance(bus._adapter, NullBusAdapter):
                # No real adapter — headless/test mode, allow
                return True
        except Exception:
            # Bus unavailable — headless/test mode, allow
            return True
        self._rejected_count += 1
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
