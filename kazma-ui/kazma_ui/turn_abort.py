"""Abort a chat turn: cancel it and mark it abandoned.

The web chat's ``POST /api/chat/abort`` and the run supervisor's Cancel on a
chat turn (``kazma_core.runs``) do the same thing through
:func:`abort_turn`: drop pending steers, cancel the running task, write the
abandonment marker into the checkpoint under the thread's mutation lock
(TT-04), rewind memory past the aborted request (M09), close the turn
(``close_aborted_turn``) and release its approval cards.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_ui.active_turns import cancel_turn

__all__ = ["abort_turn"]

logger = logging.getLogger(__name__)


async def abort_turn(thread_id: str, graph_inst: Any, *, session_id: str = "") -> dict[str, Any]:
    """``{"ok", "cancelled"}`` (and ``warning`` when the marker could not be written)."""
    from kazma_core.agent.steer import abort_messages, clear_all_steers

    # Drop any pending steers first so a stray drain/resume can't fire.
    clear_all_steers(thread_id)
    # Cancel the running pump (no-op if no turn is running).
    cancelled = cancel_turn(thread_id) is not None

    config = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
    }
    try:
        # The thread's checkpoint-mutation lock (TT-04), as every rewriter.
        from kazma_ui.thread_locks import thread_mutation

        async with thread_mutation(thread_id):
            snap = await graph_inst.aget_state(config)
            values = (snap.values if snap and snap.values else {})
            msgs = list(values.get("messages") or [])
            before = list(msgs)
            msgs = abort_messages(msgs)
            await graph_inst.aupdate_state(config, {
                "messages": msgs,
                "task_status": "abandoned",
                "auto_continue": False,
            })
            # A turn aborted before its answer leaves memory too (M09).
            from kazma_core.memory.forget import rewind_memory

            await rewind_memory(thread_id, before, msgs, tenant_id=str(values.get("tenant_id") or "default"))
        from kazma_ui.turn_runtime import close_aborted_turn

        await close_aborted_turn(thread_id)  # never raises
        try:
            from kazma_ui.hitl_gate_bridge import abort_thread_hitl

            await abort_thread_hitl(thread_id, session_id=session_id)
        except Exception:
            logger.debug("[SSE] abort HITL release skipped", exc_info=True)
        logger.info(
            "[SSE] Abort thread=%s cancelled=%s marker_written=True",
            thread_id[:12], cancelled,
        )
        return {"ok": True, "cancelled": cancelled}
    except Exception as exc:  # noqa: BLE001 — never fail the HTTP call
        logger.warning("[SSE] Abort marker write failed thread=%s: %s", thread_id[:12], exc)
        return {"ok": cancelled, "cancelled": cancelled, "warning": str(exc)}
