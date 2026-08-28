"""Single durable sink for assistant replies, keyed by turn identity.

Every chat transport (SSE ``/api/chat/stream``, SSE ``/api/approve``, SSE
hard-steer, the WebSocket bus, the platform gateway mirror) writes the same
thing at the end of a turn: *the assistant reply for this user question*.
Before this module, each of them reimplemented that write against message
**position** — "look at the last row and guess whether it is mine":

======================  =================================================
writer                  rule
======================  =================================================
SSE incremental         append once, then overwrite ``messages[-1]``
SSE final               trailing assistant → replace, else APPEND
SSE detached callback   trailing user → APPEND, else replace
WS transport            trailing assistant → replace, else append
gateway mirror          dedupe on ``(role, content[:80])``
======================  =================================================

Five rules over one list, with no way to answer "is this row the reply to
the turn I am finishing?". The outcome then depends on interleaving, which
is why one defect surfaced three ways (live incidents 2026-08-27/28):

* **duplicate rows** — the detached callback appended, then the live final
  persist appended again, because the incremental persist that would have
  created a shared row is gated on ``len(content_acc) % 50 == 0`` and that
  chunk happened to be 125 chars (125 % 50 = 25). A coin flip decided
  whether the user saw one bubble or two.
* **lost finals** — ``/api/approve`` resumed the graph, streamed a
  1,781-char answer to the browser, and wrote nothing: the resume path
  takes ``ainvoke`` (no pump, therefore no done-callback) and the endpoint
  passed no ``session_id`` at all.
* **clobbered history** — an interim HITL narration landing while the
  trailing row belonged to the PREVIOUS turn replaced a good answer with a
  151-char fragment.

The fix is identity, not more positional special-casing. A reply row carries
``turn_id``; every writer upserts on it. Two writers for one turn converge
on one row instead of racing, a writer can never touch another turn's row,
and ordering stops mattering — a late interim write is simply overwritten by
the final one for the same turn.

Turn identity spans the whole user-visible turn, **including a HITL pause**:
prompt → interrupt → approval → resume → final answer is ONE turn with ONE
reply row, even though it is two HTTP requests. :func:`resolve_reply_turn`
recovers the open id on the resume request, falling back to the ``open``
marker stored on the row itself so the identity survives a process restart
during the pause.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from kazma_core.chaos import InjectionTarget, chaos_injection

__all__ = [
    "close_reply_turn",
    "current_reply_turn",
    "open_reply_turn",
    "record_instant_turn",
    "reset_reply_turns",
    "resolve_reply_text",
    "resolve_reply_turn",
    "upsert_reply",
]

logger = logging.getLogger(__name__)

# thread_id → turn_id of the reply currently being produced. Held in memory
# only; the durable copy is the ``open`` marker on the reply row, which is
# what :func:`resolve_reply_turn` falls back to after a restart.
#
# Entries are removed when a turn completes. A turn killed mid-flight (hard
# crash, OOM) leaves one behind, so the map is bounded: on a long-lived
# process those strays would otherwise accumulate one per thread forever.
# Eviction is safe — it only costs the durable ``open`` marker lookup.
_open_turns: OrderedDict[str, str] = OrderedDict()
_MAX_OPEN_TURNS = 5_000
_lock = threading.RLock()

# Marker key on the assistant row while its turn is still in flight.
_OPEN = "open"


def _store() -> Any:
    from kazma_ui.session_manager import get_session_manager

    return get_session_manager()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ══════════════════════════════════════════════════════════════════════
# Turn identity
# ══════════════════════════════════════════════════════════════════════


def open_reply_turn(thread_id: str, turn_id: str = "") -> str:
    """Begin a reply turn on *thread_id* and return its id.

    Prefers the ambient observability turn id so a stored row correlates
    with the log lines and the terminal ``done`` frame for the same turn.
    """
    if not turn_id:
        try:
            from kazma_core.observability.correlation import (
                current_turn_id,
                new_turn_id,
            )

            turn_id = current_turn_id() or new_turn_id()
        except Exception:
            turn_id = ""
    if not turn_id:
        import uuid

        turn_id = uuid.uuid4().hex
    turn_id = str(turn_id)
    if thread_id:
        with _lock:
            _open_turns[thread_id] = turn_id
            _open_turns.move_to_end(thread_id)
            while len(_open_turns) > _MAX_OPEN_TURNS:
                _open_turns.popitem(last=False)
    return turn_id


def current_reply_turn(thread_id: str) -> str:
    """Return the open reply turn id for *thread_id*, or ""."""
    if not thread_id:
        return ""
    with _lock:
        return _open_turns.get(thread_id, "")


def close_reply_turn(thread_id: str, session_id: str = "", turn_id: str = "") -> None:
    """Mark the turn finished: drop the in-memory id and the row's marker.

    Called once the reply is terminal. Leaving the ``open`` marker behind
    would let a later unrelated resume adopt a finished row and overwrite a
    good answer — the exact clobber this module exists to prevent.
    """
    tid = turn_id or current_reply_turn(thread_id)
    if thread_id:
        with _lock:
            if not turn_id or _open_turns.get(thread_id) == turn_id:
                _open_turns.pop(thread_id, None)
    if not session_id or not tid:
        return
    try:
        with _store().transact(session_id) as sess:
            for m in reversed(sess.messages or []):
                if isinstance(m, dict) and m.get("turn_id") == tid:
                    m.pop(_OPEN, None)
                    m.pop("pending", None)
                    break
    except Exception:
        logger.debug("[reply_sink] close_reply_turn failed", exc_info=True)


def resolve_reply_turn(thread_id: str, session_id: str = "") -> str:
    """Turn id to write under on a RESUME request (approve / steer).

    A resume is a separate HTTP request from the prompt that started the
    turn, so it must recover the in-flight id rather than mint a new one —
    otherwise the pause's narration and the post-approval answer land as two
    bubbles for one question. Resolution order:

    1. the in-memory open id for this thread (normal case);
    2. the ``turn_id`` of a trailing assistant row still marked ``open``
       (survives a process restart during a HITL pause);
    3. a fresh id (nothing to join — genuinely a new reply).
    """
    tid = current_reply_turn(thread_id)
    if tid:
        return tid
    if session_id:
        try:
            sess = _store().get(session_id)
            for m in reversed(getattr(sess, "messages", None) or []):
                if not isinstance(m, dict):
                    continue
                if (m.get("role") or "") != "assistant":
                    # Only a TRAILING assistant row can belong to an
                    # in-flight turn; a user row below it means the open
                    # marker is stale.
                    break
                if m.get(_OPEN) and m.get("turn_id"):
                    adopted = str(m["turn_id"])
                    if thread_id:
                        with _lock:
                            _open_turns[thread_id] = adopted
                            _open_turns.move_to_end(thread_id)
                    logger.info(
                        "[reply_sink] adopted open turn %s from stored row "
                        "(thread=%s)",
                        adopted[:12],
                        (thread_id or "")[:12],
                    )
                    return adopted
                break
        except Exception:
            logger.debug("[reply_sink] open-row adoption failed", exc_info=True)
    return open_reply_turn(thread_id)


def reset_reply_turns() -> None:
    """Drop all in-memory turn identities (tests / process reset)."""
    with _lock:
        _open_turns.clear()


# ══════════════════════════════════════════════════════════════════════
# Text selection
# ══════════════════════════════════════════════════════════════════════


def resolve_reply_text(checkpoint_text: str = "", streamed_text: str = "") -> str:
    """Best user-facing reply from the checkpoint and the streamed concat.

    Single implementation of a choice both transports used to make their own
    way. Two rules, each earned from an incident:

    * the plan fence is un-glued via ``pick_user_facing_text`` so a trailing
      ```` ```plan ```` closer cannot swallow the answer;
    * a LONGER streamed accumulation wins over the checkpoint, because a
      cancelled/stopped turn leaves the checkpoint holding only its last
      interim segment (2026-08-27: a 96-second sweep persisted as a 158-char
      fragment while 2,272 streamed chars were discarded).
    """
    ckpt = str(checkpoint_text or "").strip()
    streamed = str(streamed_text or "").strip()
    chosen = ""
    try:
        from kazma_core.agent.plan_fence import pick_user_facing_text

        chosen = pick_user_facing_text(ckpt, streamed) or ""
    except Exception:
        logger.debug("[reply_sink] plan_fence pick failed", exc_info=True)
        chosen = ckpt or streamed
    if streamed and len(streamed) > len(chosen.strip()):
        chosen = streamed
    return chosen.strip()


# ══════════════════════════════════════════════════════════════════════
# The write
# ══════════════════════════════════════════════════════════════════════


@chaos_injection(InjectionTarget.DATABASE)
def upsert_reply(
    session_id: str,
    turn_id: str,
    content: str,
    *,
    open_turn: bool = False,
    pending: bool = False,
    activity: list[dict[str, Any]] | None = None,
    model: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
    allow_shrink: bool = True,
) -> bool:
    """Idempotently write the reply for *turn_id* into *session_id*.

    Matches the assistant row carrying this ``turn_id`` and updates it in
    place; appends a new row only when this turn has none yet. Rows from
    other turns are never read or written, so a second writer for the same
    turn converges instead of duplicating, and a late writer for an old turn
    cannot clobber a newer answer.

    ``open_turn`` keeps the durable in-flight marker on the row (see
    :func:`resolve_reply_turn`). ``pending`` marks a bubble with no text yet
    so a reload shows a processing indicator rather than a blank gap.
    ``allow_shrink=False`` refuses to replace longer stored text with
    shorter text — used by best-effort writers (disconnect/error flushes)
    that must never trade a complete answer for a fragment.

    Returns True when the store was updated.
    """
    if not session_id or not turn_id:
        return False
    text = str(content or "")
    try:
        with _store().transact(session_id) as sess:
            rows = sess.messages if isinstance(sess.messages, list) else []
            row = None
            for m in reversed(rows):
                if isinstance(m, dict) and m.get("turn_id") == turn_id:
                    row = m
                    break

            if row is None:
                if not text.strip() and not pending:
                    # Nothing to say and no bubble requested — do not create
                    # an empty row.
                    return False
                row = {
                    "role": "assistant",
                    "content": text,
                    "ts": _now(),
                    "turn_id": turn_id,
                }
                rows.append(row)
                sess.messages = rows
            else:
                if text.strip():
                    if allow_shrink or len(text.strip()) >= len(
                        str(row.get("content") or "").strip()
                    ):
                        row["content"] = text

            if pending:
                row["pending"] = True
            elif text.strip():
                row.pop("pending", None)

            if open_turn:
                row[_OPEN] = True
            else:
                row.pop(_OPEN, None)

            if activity:
                row["activity"] = list(activity)
            if model:
                row["model"] = str(model)
            if tokens is not None:
                row["tokens"] = int(tokens or 0)
            if cost is not None:
                row["cost"] = round(float(cost or 0.0), 6)
        return True
    except Exception:
        logger.warning(
            "[reply_sink] upsert FAILED session=%s turn=%s (%d chars) — the "
            "reply is still in the checkpoint but the session was NOT updated",
            str(session_id)[:12],
            str(turn_id)[:12],
            len(text),
            exc_info=True,
        )
        # This is the failure that lost four answers on 2026-08-28 and was
        # found by the operator scrolling a transcript. It must interrupt.
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(
                "reply.persist_failed",
                "A reply was produced but NOT saved to the transcript.",
                f"session={str(session_id)[:12]} turn={str(turn_id)[:12]} "
                f"chars={len(text)}. The answer is still in the checkpoint; "
                f"a reload may show it missing.",
                severity="error",
            )
        except Exception:
            pass
        return False


def record_instant_turn(
    session_id: str,
    thread_id: str,
    user_text: str,
    reply_text: str,
    kind: str = "",
) -> str:
    """Store a slash command and its immediate reply as one durable turn.

    Commands answered without touching the graph (``/yolo``, ``/long``,
    ``/plan``, ``/compact``, usage text) still owe the transcript a question
    and an answer. Each transport used to hand-roll that append — and
    several stored nothing at all, so both halves vanished on reload.

    Returns the turn id of the stored reply.
    """
    turn_id = ""
    try:
        with _store().transact(session_id) as sess:
            sess.messages.append(
                {"role": "user", "content": user_text, "ts": _now()}
            )
        turn_id = open_reply_turn(thread_id)
        upsert_reply(session_id, turn_id, reply_text)
        if kind:
            with _store().transact(session_id) as sess:
                for m in reversed(sess.messages or []):
                    if isinstance(m, dict) and m.get("turn_id") == turn_id:
                        m["kind"] = kind
                        break
        close_reply_turn(thread_id)
    except Exception:
        logger.warning(
            "[reply_sink] instant turn NOT stored session=%s",
            str(session_id)[:12],
            exc_info=True,
        )
    return turn_id
