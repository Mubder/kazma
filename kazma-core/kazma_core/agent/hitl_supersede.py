"""Cancel pending HITL interrupts cleanly before a new user turn.

When a user sends a new message while the graph is paused on
``interrupt()`` (HITL approval), LangGraph silently discards the
interrupt. Incomplete assistant ``tool_calls`` then poison history
until sanitize deletes them — the agent "forgets" mid-work.

This module **auto-denies** the pending interrupt so tool rows are
written, chains complete, then the new user message can proceed.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["cancel_pending_hitl", "has_pending_hitl"]

logger = logging.getLogger(__name__)


async def has_pending_hitl(graph: Any, config: dict[str, Any]) -> bool:
    """True if the graph is paused on a hitl_approval interrupt."""
    if graph is None:
        return False
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        return False
    if not getattr(snapshot, "next", None):
        return False
    for task in getattr(snapshot, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            payload = getattr(intr, "value", None)
            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                return True
    return False


async def cancel_pending_hitl(
    graph: Any,
    config: dict[str, Any],
    *,
    reason: str = "superseded by new user message",
    auto_deny: bool = False,
) -> bool:
    """Preserves pending HITL interrupts unless auto_deny is explicitly True.

    Prevents dropping interrupted tool tasks when follow-up text is sent.
    Returns ``True`` if a cancel resume was performed.
    """
    if graph is None:
        return False
    if not await has_pending_hitl(graph, config):
        return False

    if not auto_deny:
        logger.info(
            "[hitl_supersede] HITL is pending for thread=%s. Preserving state (auto-deny disabled).",
            (config.get("configurable") or {}).get("thread_id"),
        )
        return False

    try:
        from langgraph.types import Command
    except ImportError:
        logger.warning("[hitl_supersede] langgraph Command unavailable")
        return False

    try:
        logger.info(
            "[hitl_supersede] auto-denying pending HITL for thread=%s reason=%s",
            (config.get("configurable") or {}).get("thread_id"),
            reason,
        )
        # Phase 3: build the deny resume via the single chokepoint. Read the
        # pending payload each time (the chained loop may encounter different
        # interrupt types — semantic needs {tcid:"cancel"}, security needs
        # {approved:false}).
        from kazma_core.safety.commitment.resume import (
            build_resume_command,
            read_pending_interrupt,
        )

        async def _deny_resume_cmd():
            _payload = await read_pending_interrupt(graph, config)
            _cmd = build_resume_command(_payload, approved=False, reason=reason)
            if _cmd is None:
                # No semantic payload — security deny.
                _cmd = build_resume_command(
                    {"type": "hitl_approval", "kind": "security"},
                    approved=False, reason=reason,
                )
            return _cmd

        await graph.ainvoke(await _deny_resume_cmd(), config)
        # Chained danger tools may re-interrupt — deny those too (cap 5).
        for _ in range(5):
            if not await has_pending_hitl(graph, config):
                break
            await graph.ainvoke(await _deny_resume_cmd(), config)
        return True
    except Exception:
        logger.exception("[hitl_supersede] failed to cancel pending HITL")
        return False
