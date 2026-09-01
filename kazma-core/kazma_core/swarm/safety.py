"""Swarm Safety Middleware — bus-gated HITL for tool execution.

Intercepts SwarmEngine / LocalToolRegistry danger-tier tool calls until the
operator approves through the SwarmMessageBus.

Danger list is **not** a separate SoT — it aliases
:data:`kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS` (same list as graph HITL
and ``kazma.yaml`` ``safety.hitl.require_approval_for``). Spawn tools are only
gated if they appear on that list.

Bus topology (app wiring): one adapter, or ``FanOutBusAdapter`` when multiple
platforms are configured (first approval wins). NullBus is fail-closed for
danger tools unless headless escape is enabled.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

__all__ = ["SafetyMiddleware", "SafetyViolationError", "get_safety", "set_safety"]

logger = logging.getLogger(__name__)


async def _notify_cron_denial(tool_name: str, reason: str) -> None:
    """Tell the scheduling conversation when a danger tool is denied inside
    a cron-fired turn (2026-08-27 fix, the 0/8 silent-failure class).

    Normal gateway turns see the denial in the live chat; cron turns have
    no conversation, so the user would never learn the action didn't run.
    Uses the cron-parent context's delivery target; fire-and-forget and
    never raises — a notification failure must not mask the denial itself.
    """
    try:
        from kazma_core.cron.scheduler import get_cron_parent

        parent = get_cron_parent()
        if not parent:
            return
        target = str(parent.get("delivery_target") or "").strip()
        if not target or ":" not in target:
            return
        from kazma_core.tools.send_message import send_message

        await send_message(
            target,
            f"⚠️ Scheduled job {parent.get('job_id', '')}: '{tool_name}' was "
            f"{reason} — the action was NOT executed.",
            backend=target.split(":", 1)[0],
        )
    except Exception:
        logger.debug("[Safety] cron denial notification failed", exc_info=True)

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

# Defensive fallback when safety.hitl cannot be imported (mirrors the ALWAYS
# set — YOLO must never widen beyond this on an import failure).
_ALWAYS_HITL_FALLBACK: frozenset[str] = frozenset({
    "x_post",
    "x_delete_post",
    "x_schedule_post",
    "x_cancel_scheduled_post",
})


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
        """Whether the swarm bus must gate this tool.

        Settings ``require_approval_for`` ADDS to the floor; it cannot un-gate
        a ``danger``/``unsafe`` TOOL_TIERS classification (audit H-9 / §26B).
        YOLO/grants are applied in :meth:`check`, not here — this is
        classification, not a skip.
        """
        try:
            from kazma_core.safety.hitl import (
                ALWAYS_HITL_TOOLS,
                get_hitl_config,
                get_tool_tier,
            )

            if tool_name in ALWAYS_HITL_TOOLS:
                return True
            hitl_cfg = get_hitl_config({})
            listed = set(hitl_cfg.get("require_approval_for") or ())
            listed |= set(self._danger_tools)
            if tool_name in listed:
                return True
            return get_tool_tier(tool_name) in ("danger", "unsafe")
        except Exception:
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

        # Classification BEFORE the YOLO shortcut (audit fix — HIGH): YOLO used
        # to short-circuit ahead of danger/ALWAYS evaluation, so
        # ALWAYS_HITL_TOOLS (x_post / x_delete_post) auto-ran through
        # LocalToolRegistry. Mirrors hitl.requires_approval(), which resolves
        # ALWAYS first: YOLO may bypass CANONICAL-danger tools only.
        try:
            from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS as _always_set
        except Exception:  # pragma: no cover - degraded import
            _always_set = _ALWAYS_HITL_FALLBACK

        if tool_name in _always_set:
            # Belt-and-braces: ALWAYS_HITL tools gate even if a narrowed
            # require_approval_for list omits them.
            force_danger = True

        # Blast radius (2026-08-27 incident): git WRITE commands (commit/
        # push/reset/…) always gate — YOLO must never auto-approve a repo
        # mutation born from a misread intent. Read-only git is exempt.
        _git_write = False
        try:
            if tool_name in ("exec", "shell_exec", "run_command") and tool_args:
                from kazma_core.agent.task_ledger import is_git_write_command

                _git_write = is_git_write_command(str(tool_args))
        except Exception:
            _git_write = False
        if _git_write:
            force_danger = True

        if not force_danger and self.is_sensitive_read(tool_name):
            logger.info("[Safety] Sensitive read allowed: %s (task=%s)", tool_name, task_id)
            return True

        if not force_danger and not self.is_danger_tool(tool_name):
            return True  # safe tool

        if tid and tool_name not in _always_set and not _git_write:
            try:
                from kazma_core.safety.yolo import is_yolo_active
                if is_yolo_active(tid):
                    logger.warning(
                        "[Safety] YOLO mode active for thread=%s — auto-approving: %s",
                        tid,
                        tool_name,
                    )
                    return True
            except Exception:
                pass

        if tool_name in _always_set:
            logger.warning(
                "[Safety] %s is an ALWAYS_HITL tool — YOLO cannot bypass the "
                "approval gate (task=%s)",
                tool_name,
                task_id,
            )

        # ── Danger tool — request approval ─────────────────
        logger.warning("[Safety] Danger tool blocked pending approval: %s", tool_name)
        self._blocked_count += 1

        from kazma_core.swarm.bus import NullBusAdapter, get_message_bus

        bus = get_message_bus()
        # Fail-closed when no real adapter is wired (mirror check_sync).
        # NullBusAdapter.request_approval() returns False (fail-closed); the
        # explicit isinstance check short-circuits before relying on it, so a
        # future change can't reintroduce silent auto-approve for headless
        # danger tools. (Comment corrected — it claimed True/auto-approve.)
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
            await _notify_cron_denial(tool_name, "blocked (no approval bus)")
            return False

        approved = False
        _gate_id = ""
        # Gate registry (P4): a swarm-bus approval is a gate row too — the
        # dashboard / status readers see it next to graph gates. Best-effort.
        try:
            import asyncio as _aio

            from kazma_core.safety.hitl_gates import (
                GateRow,
                gate_registry_enabled,
                make_gate_id,
                register_gate,
            )

            if gate_registry_enabled():
                _gate_id = make_gate_id(
                    tid or task_id or "swarm", tool_name, tool_args,
                    seq=int(time.time() * 1000) % 1_000_000,
                )
                await _aio.to_thread(
                    register_gate,
                    GateRow(
                        gate_id=_gate_id,
                        thread_id=tid or task_id or "swarm",
                        tool=tool_name,
                        mechanism="swarm_bus",
                        message=(tool_args or "")[:500],
                    ),
                    ttl_seconds=self._approval_timeout,
                )
        except Exception:
            logger.debug("[Safety] gate register skipped", exc_info=True)

        approved = await bus.request_approval(
            worker_name=worker_name,
            task_description=f"Tool: {tool_name}" + (f" — {tool_args[:100]}" if tool_args else ""),
            proposed_output=f"Danger-tier tool '{tool_name}' requires approval before execution.",
            task_id=task_id,
            timeout=self._approval_timeout,
        )

        # Gate registry (P4): settle the row with the bus outcome.
        if _gate_id:
            try:
                import asyncio as _aio

                from kazma_core.safety.hitl_gates import (
                    TransitionConflict,
                    claim_gate,
                    settle_gate,
                )

                def _settle() -> None:
                    try:
                        claim_gate(
                            _gate_id,
                            "approve" if approved else "deny",
                            "swarm_bus",
                        )
                    except TransitionConflict:
                        pass
                    try:
                        settle_gate(_gate_id)
                    except TransitionConflict:
                        pass

                await _aio.to_thread(_settle)
            except Exception:
                logger.debug("[Safety] gate settle skipped", exc_info=True)

        if approved:
            self._approved_count += 1
            logger.info("[Safety] Danger tool APPROVED: %s (task=%s)", tool_name, task_id)
        else:
            self._rejected_count += 1
            logger.warning("[Safety] Danger tool REJECTED: %s (task=%s)", tool_name, task_id)
            # Cron turns have no live conversation — the user would never
            # hear about the denial (the 0/8 silent-failure class). Tell the
            # scheduling conversation directly (2026-08-27 fix).
            await _notify_cron_denial(tool_name, "denied or timed out")

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
