"""Abort a chat turn: cancel it and mark it abandoned.

The web chat's ``POST /api/chat/abort`` and the run supervisor's Cancel on a
chat turn (``kazma_core.runs``) do the same thing through
:func:`abort_turn`: drop pending steers, cancel the running task, write the
abandonment marker into the checkpoint under the thread's mutation lock
(TT-04), rewind memory past the aborted request (M09), close the turn
(``close_aborted_turn``) and release its approval cards.

A chat that goes away and keeps its state (archived, ``chat_runs_end``)
has its cut turn ended the same way through :func:`mark_turn_abandoned`: the
marker and the memory rewind, so its next turn does not answer the request
that was cut and memory does not keep it as asked (M09).
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_ui.active_turns import cancel_turn

__all__ = ["abort_turn", "mark_turn_abandoned"]

from kazma_core.spaces.scope import space_of_state

logger = logging.getLogger(__name__)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


async def _write_abandoned(graph_inst: Any, thread_id: str) -> None:
    """The checkpoint's turn marked abandoned under the thread's mutation
    lock (TT-04, as every rewriter): its request answered "cancelled"
    (``abort_messages``), no auto-continue, and memory rewound past the
    aborted request (M09)."""
    from kazma_core.agent.steer import abort_messages
    from kazma_core.memory.forget import rewind_memory

    from kazma_ui.thread_locks import thread_mutation

    config = _config(thread_id)
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
        await rewind_memory(thread_id, before, msgs, tenant_id=space_of_state(values))


async def mark_turn_abandoned(thread_id: str, graph_inst: Any) -> bool:
    """End a cut turn in its checkpoint as :func:`abort_turn` does (the
    marker and the memory rewind), and nothing else: no cancel, no close.
    True when it was written; a failure is said at WARNING."""
    if not thread_id or graph_inst is None:
        return False
    from kazma_core.db.pg_helpers import store_errors
    from langgraph.errors import InvalidUpdateError

    try:
        await _write_abandoned(graph_inst, thread_id)
        return True
    except (*store_errors(), LookupError, InvalidUpdateError):  # the checkpointer's own; the caller goes on
        logger.warning("[turn] the abandonment marker of thread=%s was NOT written", thread_id[:12],
                       exc_info=True)
        return False


async def abort_turn(thread_id: str, graph_inst: Any, *, session_id: str = "") -> dict[str, Any]:
    """``{"ok", "cancelled"}`` (and ``warning`` when the marker could not be written)."""
    from kazma_core.agent.steer import clear_all_steers

    # Drop any pending steers first so a stray drain/resume can't fire.
    clear_all_steers(thread_id)
    # Cancel the running pump (no-op if no turn is running).
    cancelled = cancel_turn(thread_id) is not None

    try:
        # The marker and the memory rewind, under the thread's lock.
        await _write_abandoned(graph_inst, thread_id)
        from kazma_ui.turn_runtime import close_aborted_turn

        await close_aborted_turn(thread_id)  # never raises
        from kazma_core.agent.long_task import pause_long_task_for_abort

        await pause_long_task_for_abort(thread_id)  # never raises
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
        return {"ok": cancelled, "cancelled": cancelled,
                "warning": "The stop could not be written into the chat's history."}
