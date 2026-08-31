"""Turn persistence: writing replies, snapshots, and checkpoint backfill.

Extracted from the former 3,099-line ``kazma_ui/sse_chat.py``
(audit O5). Bodies are unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from kazma_ui.sse_chat import _helpers
from kazma_ui.sse_chat._helpers import (
    _last_assistant_text,
    _user_facing_reply,
)

__all__: list[str] = []

router = APIRouter(tags=["chat-sse"])


# ══════════════════════════════════════════════════════════════════════════
# Detached turn registry — keeps graph tasks alive across client disconnects
# ══════════════════════════════════════════════════════════════════════════
# Strong-reference map: thread_id → running graph pump task. Prevents CPython
# from garbage-collecting the task when the SSE generator is cancelled by a
# client disconnect (refresh / tab switch). The task runs to completion;
# the checkpointer + done_callback persist the result so the client finds
# it on reload.
#
# The registry is SHARED with the WebSocket transport (kazma_ui.active_turns)
# so a WS turn is visible to the SSE duplicate-turn guard and to the session
# status endpoint — otherwise a refresh could start a second concurrent
# graph run on the same thread/checkpointer.  ``_active_turns`` stays as a
# back-compat alias to the shared dict.
from kazma_ui.active_turns import (
    active_turns,
)

_active_turns = active_turns  # type: ignore[name-defined]

# T1: strong references to detached-pump watchdog tasks so CPython never
# GCs one while its pump is still running.


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════




def _persist_turn_reply(
    session_id: str,
    reply_turn_id: str,
    content: str,
    *,
    interrupted: bool = False,
    pending: bool = False,
    open_turn: bool | None = None,
    thread_id: str = "",
    model: str = "",
    tokens: int | None = None,
    cost: float | None = None,
    activity: list[dict[str, Any]] | None = None,
    parts: list[dict[str, Any]] | None = None,
    streamed_text: str = "",
    allow_shrink: bool | None = None,
) -> bool:
    """Write this turn's reply through the sink. Never raises.

    Delegates to ``turn_runtime.persist_reply`` so HTTP generators and
    headless finishers (HITL auto-deny) share one writer.
    """
    from kazma_ui.turn_runtime import persist_reply

    return persist_reply(
        session_id,
        reply_turn_id,
        content,
        interrupted=interrupted,
        pending=pending,
        open_turn=open_turn,
        thread_id=thread_id,
        model=model,
        tokens=tokens,
        cost=cost,
        activity=activity,
        parts=parts,
        streamed_text=streamed_text,
        allow_shrink=allow_shrink,
    )

def _snapshot_paused(snap: Any) -> bool:
    """True when the graph snapshot is parked on a pending interrupt.

    Derived from the checkpoint instead of a mutable flag. The flag version
    was unreadable at callback time: it is assigned in the post-stream block
    that runs *after* the pump task finishes, so every done-callback saw
    ``False`` and the HITL guard it protected never once fired in production
    (2026-08-28, 8 ms apart in the live log).
    """
    try:
        if snap is None:
            return False
        for task in getattr(snap, "tasks", None) or []:
            if getattr(task, "interrupts", None):
                return True
        return bool(getattr(snap, "next", None))
    except Exception:
        return False

async def _persist_detached_reply(
    graph: Any, config: dict, session_id: str, thread_id: str,
    streamed_text: str = "", interrupted: bool = False,
    reply_turn_id: str = "",
) -> None:
    """Persist a DETACHED turn's reply — the client is gone, the graph ran on.

    The pump survives client disconnects, so this done-callback is the only
    writer for a turn whose browser left mid-stream (live incident
    2026-08-21: the reply sat in the checkpoint for hours while the session
    store showed nothing and the failure was DEBUG-swallowed).

    It reads the checkpoint, reconciles it with whatever was streamed, and
    hands the result to the turn-keyed sink. Because the sink upserts on
    ``reply_turn_id``, this write and the streamer's own terminal write
    converge on ONE row no matter which of them lands first — the race that
    produced duplicate assistant bubbles (2026-08-28) is gone by
    construction rather than by ordering luck.
    """
    try:
        from kazma_ui.turn_runtime import close_turn

        await close_turn(
            graph,
            config,
            session_id=session_id,
            thread_id=thread_id,
            turn_id=reply_turn_id,
            streamed_text=streamed_text,
            interrupted=interrupted,
        )
        return
    except Exception:
        logger.warning(
            "[SSE] Detached turn persist FAILED for thread=%s session=%s "
            "(reply is still in the checkpoint; the session was NOT updated)",
            thread_id[:12],
            session_id[:12],
            exc_info=True,
        )
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(
                "reply.detached_persist_failed",
                "A background turn finished but its reply was NOT saved.",
                f"thread={thread_id[:12]} session={session_id[:12]}. The "
                f"answer exists in the checkpoint only.",
                severity="error",
            )
        except Exception:
            pass

def _persist_instant_turn(
    session: Any,
    thread_id: str,
    user_text: str,
    reply_text: str,
    kind: str = "",
) -> None:
    """Record a slash command and its immediate reply as one durable turn.

    These handlers answer without touching the graph (``/yolo``, ``/plan``,
    ``/long``, ``/compact``, usage text). Each used to hand-roll its own
    append — and three of them (``/research``, ``/swarm``, ``/compact``)
    stored nothing at all, so the question AND the answer vanished on
    reload. Routing them through the sink gives them the same turn-keyed
    row as every other reply, so a double-submit updates one row instead of
    stacking duplicates.
    """
    from kazma_ui.reply_sink import record_instant_turn

    record_instant_turn(
        getattr(session, "session_id", ""), thread_id, user_text, reply_text, kind
    )

async def _checkpoint_backfill_unanswered(session: Any) -> list[dict]:
    """Surface a checkpointed reply for a session whose last message is the
    user's (live incident 2026-08-21 recovery).

    When the detached-pump persist fails (or the process dies mid-turn),
    the completed reply still lives in the thread's checkpoint. On session
    load, if the transcript ends with an unanswered user message AND the
    checkpoint state ends with an assistant reply, append it — and heal
    the stored session so the fix outlives the reload.
    Returns the (possibly extended) message list.
    """
    messages = list(getattr(session, "messages", None) or [])
    last = next(
        (
            m
            for m in reversed(messages)
            if isinstance(m, dict) and str(m.get("content") or "").strip()
        ),
        None,
    )
    if not isinstance(last, dict):
        return messages
    _role = (last.get("role") or "").lower()
    # An unanswered turn is one ending with the user's message — OR one whose
    # trailing assistant row is still marked ``open``/``pending``, i.e. a turn
    # that never delivered its final answer. Requiring a trailing USER row
    # meant a stranded interim narration disabled the only recovery net there
    # is: on 2026-08-28 the 1,781-char answer sat in the checkpoint while this
    # returned early because a 125-char narration row was in the way.
    _stranded = _role == "assistant" and bool(last.get("open") or last.get("pending"))
    _last_text = str(last.get("content") or "")
    if _role != "user" and not _stranded:
        # Closed short assistant vs a much longer checkpoint reply: the
        # cancelled-SSE flush wrote an interim, persist later wrote the
        # real answer into the checkpoint only (2660-char "no answers" class).
        # We still have to load the checkpoint to compare — fall through
        # when last is assistant and we'll decide after `asst` is known.
        if _role != "assistant":
            return messages
    try:
        # Resolved through the module object, not a from-import binding, so
        # the seam stays patchable at its single definition site
        # (``kazma_ui.sse_chat._helpers``) after the audit-O5 package split.
        # A direct `from ._helpers import _module_graph` would freeze this
        # module's own global and silently ignore any later patch.
        live = _helpers._module_graph()
        tid = getattr(session, "thread_id", "") or ""
        if not live or not tid or not getattr(live, "checkpointer", None):
            return messages
        snap = await live.aget_state(
            {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
        )
        vals = (snap.values or {}) if snap else {}
        cp_msgs = [m for m in (vals.get("messages") or []) if isinstance(m, dict)]
        cp_last = next(
            (
                m
                for m in reversed(cp_msgs)
                if str(m.get("content") or "").strip()
            ),
            None,
        )
        asst = _user_facing_reply(_last_assistant_text(vals.get("messages") or []))
        cp_last_role = ""
        if isinstance(cp_last, dict):
            cp_last_role = (cp_last.get("role") or cp_last.get("type") or "").lower()
        if not asst or cp_last_role not in ("assistant", "ai"):
            return messages
        _prefix_stale = (
            _role == "assistant"
            and not _stranded
            and len(asst) > len(_last_text) + 400
            and (
                _last_text.strip() in asst
                or (
                    len(_last_text.strip()) >= 40
                    and asst.startswith(_last_text.strip()[:120])
                )
            )
        )
        if _role != "user" and not _stranded and not _prefix_stale:
            return messages
        # Already present? (idempotent heal)
        if any(
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and str(m.get("content") or "") == asst
            for m in messages
        ):
            return messages
        # Heal through the sink so the recovered answer lands in the STRANDED
        # row when there is one (replacing the interim narration the user was
        # left staring at) instead of stacking a second bubble under it.
        from kazma_ui.reply_sink import open_reply_turn
        from kazma_ui.turn_runtime import persist_reply

        _heal_turn = ""
        if (_stranded or _prefix_stale) and last.get("turn_id"):
            _heal_turn = str(last["turn_id"])
            messages = [
                dict(m, content=asst, pending=False) if m is last else m
                for m in messages
            ]
        elif _prefix_stale:
            _heal_turn = open_reply_turn(tid)
            messages = [
                dict(m, content=asst) if m is last else m for m in messages
            ]
        else:
            _heal_turn = open_reply_turn(tid)
            messages = messages + [
                {"role": "assistant", "content": asst, "turn_id": _heal_turn}
            ]
        if persist_reply(session.session_id, _heal_turn, asst, thread_id=tid):
            logger.info(
                "[SSE] Backfilled unanswered turn from checkpoint for "
                "session=%s (%d chars)",
                session.session_id[:12],
                len(asst),
            )
        return messages
    except Exception:
        logger.debug("[SSE] unanswered-turn checkpoint backfill skipped", exc_info=True)
        return messages
