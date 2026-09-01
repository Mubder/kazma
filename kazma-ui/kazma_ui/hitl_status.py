"""HITL thread status — one answer for every reader.

The gate registry owns the DECISION (P6). LangGraph ``interrupt()`` is
execution pause, not "the card is still live". Pending-list, WS scan, and
session status must agree.

Statuses:
  pending  — a live unanswered gate (registry pending, or thin fallback)
  inflight — claimed/resuming rows, or a leftover interrupt of a claimed resume
  idle     — no live question
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

HitlStatus = Literal["pending", "inflight", "idle"]

__all__ = [
    "hitl_thread_status",
    "is_new_gate",
    "is_resume_claimed",
    "is_truly_pending",
    "persisted_hitl_for_thread",
    "snapshot_interrupt_id",
    "snapshot_interrupt_payload",
]


def is_resume_claimed(thread_id: str) -> bool:
    """True when Approve already started a drive for this thread."""
    if not thread_id:
        return False
    try:
        from kazma_ui.active_turns import is_turn_running

        if is_turn_running(thread_id):
            return True
    except Exception:
        logger.debug("[hitl_status] is_turn_running failed", exc_info=True)
    try:
        from kazma_ui.routes_direct import misc as misc_mod

        return thread_id in getattr(misc_mod, "_resume_inflight", set())
    except Exception:
        return False


def persisted_hitl_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Last HITL part on the thread's web session, if any."""
    if not thread_id:
        return None
    try:
        from kazma_ui.session_manager import get_session_manager

        sess = get_session_manager().get_by_thread_id(thread_id)
    except Exception:
        logger.debug("[hitl_status] session lookup failed", exc_info=True)
        return None
    if sess is None:
        return None
    messages = getattr(sess, "messages", None) or []
    for m in reversed(list(messages)):
        if not isinstance(m, dict):
            continue
        if str(m.get("role") or "").lower() != "assistant":
            continue
        parts = m.get("parts") if isinstance(m.get("parts"), list) else []
        for p in reversed(parts):
            if isinstance(p, dict) and p.get("type") == "hitl":
                return p
        return None
    return None


def snapshot_interrupt_payload(snapshot: Any) -> dict[str, Any] | None:
    """First HITL payload on a LangGraph state snapshot, or None."""
    if snapshot is None:
        return None
    if not getattr(snapshot, "next", None):
        return None
    try:
        from kazma_ui.sse_chat._helpers import _extract_hitl_payload
    except Exception:
        _extract_hitl_payload = None  # type: ignore[assignment]
    for task in getattr(snapshot, "tasks", None) or ():
        for intr in getattr(task, "interrupts", None) or ():
            payload: dict[str, Any] | None = None
            if _extract_hitl_payload is not None:
                try:
                    payload = _extract_hitl_payload(intr)
                except Exception:
                    payload = None
            if payload is None:
                value = getattr(intr, "value", None)
                if isinstance(value, dict) and (
                    value.get("type") == "hitl_approval"
                    or "tool" in value
                    or "args" in value
                ):
                    payload = value
            if payload:
                return dict(payload)
    return None


def snapshot_interrupt_id(snapshot: Any) -> str:
    """LangGraph's own id for the snapshot's live interrupt ('' if none)."""
    if snapshot is None or not getattr(snapshot, "next", None):
        return ""
    for task in getattr(snapshot, "tasks", None) or ():
        for intr in getattr(task, "interrupts", None) or ():
            for attr in ("id", "ns"):
                v = getattr(intr, attr, None)
                if v:
                    return str(v)
    return ""


def _part_gate_identity(part: dict[str, Any] | None) -> tuple[str, str]:
    """(interrupt_id, tool) recorded on a persisted HITL part."""
    if not isinstance(part, dict):
        return "", ""
    raw = part.get("payload")
    payload = raw if isinstance(raw, dict) else {}
    iid = str(part.get("interrupt_id") or payload.get("interrupt_id") or "").strip()
    tool = str(
        part.get("tool") or payload.get("tool") or payload.get("tool_name") or ""
    ).strip().lower()
    return iid, tool


def is_new_gate(part: dict[str, Any] | None, snapshot: Any) -> bool:
    """True when the snapshot's live interrupt is a DIFFERENT gate than the
    persisted HITL part — a second danger tool asking during the same turn
    (write approved → the model tries a delete, 2026-09-01 incident).

    Evidence-based: without comparable ids or tool names it returns False
    (legacy behavior — a claimed pause is treated as a leftover of Approve).
    Never raises.
    """
    try:
        if not isinstance(part, dict) or snapshot is None:
            return False
        payload = snapshot_interrupt_payload(snapshot)
        if not payload:
            return False
        part_iid, part_tool = _part_gate_identity(part)
        live_iid = snapshot_interrupt_id(snapshot)
        if live_iid and part_iid:
            return live_iid != part_iid
        live_tool = str(
            payload.get("tool") or payload.get("tool_name") or ""
        ).strip().lower()
        if part_tool and live_tool and part_tool != live_tool:
            return True
        return False
    except Exception:
        logger.debug("[hitl_status] is_new_gate failed", exc_info=True)
        return False


async def _load_snapshot(
    thread_id: str,
    *,
    graph: Any = None,
    snapshot: Any = None,
) -> Any:
    if snapshot is not None:
        return snapshot
    if graph is None:
        return None
    try:
        return await graph.aget_state(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        )
    except Exception:
        logger.debug("[hitl_status] aget_state failed thread=%s", thread_id, exc_info=True)
        return None


async def _thin_execution_status(
    thread_id: str,
    *,
    graph: Any = None,
    snapshot: Any = None,
) -> HitlStatus:
    """Kill-switch / outage / empty-registry fallback.

    A live checkpoint interrupt is pending (live card). Never infer
    Approved from leftover parts. A claimed resume with the *same*
    leftover interrupt is inflight; a *different* interrupt is a new
    question (unregistered second gate).
    """
    snap = await _load_snapshot(thread_id, graph=graph, snapshot=snapshot)
    payload = snapshot_interrupt_payload(snap)
    if payload is None:
        return "idle"
    if not is_resume_claimed(thread_id):
        return "pending"
    part = persisted_hitl_for_thread(thread_id)
    if is_new_gate(part, snap):
        return "pending"
    return "inflight"


async def hitl_thread_status(
    thread_id: str,
    *,
    graph: Any = None,
    snapshot: Any = None,
) -> HitlStatus:
    """Classify a thread as pending / inflight / idle.

    Registry rows are decision truth when the registry is on and readable.
    ``snapshot`` avoids a second ``aget_state`` when the caller already has
    one. Never raises.
    """
    if not thread_id:
        return "idle"

    try:
        from kazma_ui.hitl_gate_bridge import registry_on

        if registry_on():
            import asyncio as _aio

            from kazma_core.safety.hitl_gates import live_gates

            rows = await _aio.to_thread(live_gates, thread_id)
            if any(r.state == "pending" for r in rows):
                return "pending"
            if rows:
                return "inflight"
            # Empty registry: unregistered pause or truly idle.
            return await _thin_execution_status(
                thread_id, graph=graph, snapshot=snapshot
            )
    except Exception:
        logger.debug("[hitl_status] registry read failed — thin fallback", exc_info=True)

    return await _thin_execution_status(thread_id, graph=graph, snapshot=snapshot)


async def is_truly_pending(
    thread_id: str,
    *,
    graph: Any = None,
    snapshot: Any = None,
) -> bool:
    """True only for a live unclaimed interrupt. Errors are not pending."""
    try:
        return await hitl_thread_status(
            thread_id, graph=graph, snapshot=snapshot
        ) == "pending"
    except Exception:
        logger.debug(
            "[hitl_status] is_truly_pending failed thread=%s",
            thread_id,
            exc_info=True,
        )
        return False
