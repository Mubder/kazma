"""HITL thread status — one answer for every reader.

LangGraph ``interrupt()`` is the execution pause. It is not "the card is
still live". A resume that has been claimed (``register_turn`` /
``_resume_inflight``) or a persisted HITL part past ``pending`` means the
operator already acted. Pending-list, WS scan, and session status must
agree on that.

Statuses:
  pending  — checkpoint interrupt, nothing claimed, parts missing or pending
  inflight — resume registered or HITL part is approved/denied/inflight
  idle     — no live interrupt (settled or never paused)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

HitlStatus = Literal["pending", "inflight", "idle"]

__all__ = [
    "hitl_thread_status",
    "is_resume_claimed",
    "is_truly_pending",
    "persisted_hitl_for_thread",
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


async def hitl_thread_status(
    thread_id: str,
    *,
    graph: Any = None,
    snapshot: Any = None,
) -> HitlStatus:
    """Classify a thread as pending / inflight / idle.

    ``snapshot`` avoids a second ``aget_state`` when the caller already has
    one (pending-list scan). ``graph`` is used only when snapshot is omitted.
    Never raises.
    """
    if not thread_id:
        return "idle"

    paused = False
    try:
        from kazma_ui.sse_chat._streaming import is_thread_paused

        paused = is_thread_paused(thread_id)
    except Exception:
        paused = False

    part = persisted_hitl_for_thread(thread_id)
    part_state = str((part or {}).get("state") or "").strip().lower()

    # Resume claimed and the graph is not sitting on a (new) interrupt:
    # leftover checkpoint interrupt is stale. A later danger tool re-pauses
    # the same drive — that is a real pending card.
    if is_resume_claimed(thread_id) and not paused:
        return "inflight"
    if is_resume_claimed(thread_id) and paused and part_state in (
        "approved",
        "denied",
        "inflight",
    ):
        return "inflight"
    if part_state in ("approved", "denied", "inflight") and not paused:
        return "inflight"
    if part_state in ("settled", "done", "timeout", "error") and not paused:
        return "idle"

    snap = snapshot
    if snap is None and graph is not None:
        try:
            snap = await graph.aget_state(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            )
        except Exception:
            logger.debug("[hitl_status] aget_state failed thread=%s", thread_id, exc_info=True)
            snap = None

    payload = snapshot_interrupt_payload(snap)
    if payload is None:
        return "idle"
    return "pending"


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
