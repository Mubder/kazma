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
    "open_turns_snapshot",
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

# Durable lifecycle field. `open`/`pending` are kept in sync beside it for
# readers that predate this.
_LIFECYCLE = "lifecycle"

# When the turn closed -- the reply's delivery time (see _write_lifecycle).
_CLOSED_AT = "closed_at"

#: Row fields the chat history returns to the client exactly as stored. Both
#: of the history route's serializers are whitelists, and a field the store
#: keeps is invisible to the client until it is named: the revision went out
#: as 0 (docs/plans/UNIFIED_TURN_BLOCK.md U05), and a turn's usage and its
#: close time vanished on every reload (2026-09-26). role, content, pending,
#: open, parts and activity have their own handling there.
#: tests/test_history_row_fields.py writes a row with every field this module
#: stores and requires each to come back, or to be in SERVER_ROW_FIELDS.
CLIENT_ROW_FIELDS: tuple[str, ...] = (
    "ts",
    _CLOSED_AT,
    "turn_id",
    "model",
    "rev",
    "schema",
    "tokens",
    "cost",
    "duration_ms",
)

#: Row fields kept for the server alone, and why.
SERVER_ROW_FIELDS: dict[str, str] = {
    _LIFECYCLE: "the client reads the legacy `open` flag written beside it",
    "kind": "an instant turn's origin (a capacity reply); no client reads it",
}

#: A turn's lifecycle is a JOIN-SEMILATTICE, not a field: it only ever moves
#: forward, and `closed` absorbs.
#:
#:     pending  ->  open  ->  closed
#:
#: This is the rule the module was missing. Identity fixed *which row* a
#: writer may touch, `allow_shrink` and `merge_parts` made content and parts
#: monotone — but lifecycle stayed a plain assignment any of the fifteen
#: write sites could flip in either direction. So a straggling in-flight
#: flush from one transport, landing after another transport had finalised,
#: reopened a finished turn and hung it forever (live, 2026-09-13: the final
#: close at 22:24:41 was undone by a 50-char flush at 22:24:42).
#:
#: With a join, arrival order stops mattering for lifecycle exactly as it
#: already did for content. Proven by exhaustive permutation in
#: `tests/test_turn_state_confluence.py`, which fails on the old assignment
#: for 18 of 24 orderings.
#: Two INDEPENDENT monotone fields, not one chain. An early version folded
#: `pending` into the chain and broke a real contract: a pending bubble may be
#: written with `open_turn=False` (a placeholder with no text yet), which the
#: chain read as "closed" and erased. Pending answers "is there text yet"; the
#: lifecycle answers "is this turn still running". Both only move one way.
_LIVE, _CLOSED = "open", "closed"
_LIFECYCLE_RANK = {_LIVE: 0, _CLOSED: 1}


def _lifecycle_of(row: dict[str, Any] | None) -> str | None:
    """The row's current lifecycle, tolerating rows written before this field."""
    if not isinstance(row, dict):
        return None
    stored = row.get(_LIFECYCLE)
    if stored in _LIFECYCLE_RANK:
        return str(stored)
    if _LIFECYCLE in row or _OPEN in row or "pending" in row:
        # A legacy row: absence of the open marker meant finished.
        return _LIVE if row.get(_OPEN) else _CLOSED
    return None


def _join_lifecycle(current: str | None, incoming: str) -> str:
    """Least upper bound. Never moves backwards, so `closed` is terminal."""
    if current is None:
        return incoming
    return max(current, incoming, key=lambda s: _LIFECYCLE_RANK.get(s, 0))


def _write_lifecycle(row: dict[str, Any], state: str) -> None:
    """Store the lifecycle plus the legacy `open` flag derived from it.

    The write that CLOSES a turn also records when (`closed_at`): the time a
    reply was delivered, which the chat shows under it. The row's `ts` is when
    the bubble was created -- the start of a turn that may then run for
    minutes -- so a reload used to show a different time from the one the
    live bubble showed at its end (2026-09-26). Set once, on the transition;
    a turn closed before this field existed keeps showing its `ts`.
    """
    closing = state == _CLOSED and _lifecycle_of(row) != _CLOSED
    row[_LIFECYCLE] = state
    if state == _CLOSED:
        row.pop(_OPEN, None)
        if closing:
            row.setdefault(_CLOSED_AT, _now())
    else:
        row[_OPEN] = True


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


def open_turns_snapshot() -> dict[str, str]:
    """A read-only copy of ``thread_id -> turn_id`` for turns still in flight.

    For the liveness watchdog. It is a snapshot on purpose: the watchdog must
    never hold this module's lock while it walks sessions or sends an alert.
    """
    with _lock:
        return dict(_open_turns)


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
                    _write_lifecycle(m, _CLOSED)
                    m.pop("pending", None)
                    break
    except Exception:
        logger.debug("[reply_sink] close_reply_turn failed", exc_info=True)


def _thread_has_live_gate(thread_id: str) -> bool:
    """True when the gate registry still has an unanswered question here.

    The registry is the decision authority (AGENTS.md §30); ``LIVE_STATES``
    is pending/claimed/resuming, all of which mean the turn has not
    finished. Used to rejoin an in-flight turn when the stored row's
    ``open`` flag has already been cleared.

    Fail-CLOSED on error, deliberately: if the registry cannot be read,
    this returns False and the caller falls back to its previous
    behaviour. A wrong True would glue a genuinely new question onto the
    previous turn's row, which is worse than the bug it fixes.
    """
    if not thread_id:
        return False
    try:
        from kazma_core.safety.hitl_gates import (
            gate_registry_enabled,
            live_gates,
        )

        if not gate_registry_enabled():
            return False
        return bool(live_gates(thread_id))
    except Exception:
        logger.debug("[reply_sink] live-gate probe failed", exc_info=True)
        return False


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
                # The row's `open` flag is the weakest authority here. A
                # LIVE gate on this thread means a question is still
                # outstanding, so the turn is not over however that flag
                # was left — and rejoining is the whole point of this
                # function (see the docstring: "two bubbles for one
                # question").
                #
                # Without this, a leg that finished without pausing closed
                # the turn, the next tool call minted a new id, and every
                # gate decided under the old one was orphaned on screen
                # (2026-09-20).
                if m.get("turn_id") and (
                    m.get(_OPEN) or _thread_has_live_gate(thread_id)
                ):
                    adopted = str(m["turn_id"])
                    why = "open flag" if m.get(_OPEN) else "live gate"
                    if thread_id:
                        with _lock:
                            _open_turns[thread_id] = adopted
                            _open_turns.move_to_end(thread_id)
                    logger.info(
                        "[reply_sink] adopted open turn %s from stored row "
                        "(thread=%s, via=%s)",
                        adopted[:12],
                        (thread_id or "")[:12],
                        why,
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


def resolve_reply_text(
    checkpoint_text: str = "",
    streamed_text: str = "",
    *,
    terminal: bool = False,
) -> str:
    """Best user-facing reply from the checkpoint and the streamed concat.

    Single implementation of a choice both transports used to make their own
    way. Two rules, each earned from an incident:

    * **Terminal authority** (``terminal=True``, the turn COMPLETED and the
      checkpoint holds the respond-node synthesis): the checkpoint text IS
      the answer — it wins regardless of length. The streamed accumulation
      (multi-hop narration, progress notes) is superseded: the caller routes
      it into ``reasoning`` parts via ``parts_from_stream`` so nothing is
      lost, but it can no longer out-length the final. Without this rule a
      6,455-char progress narration replaced a 2,813-char final synthesis
      and the user read the answer twice (live 2026-09-09).
    * **Cancelled-turn recovery** (``terminal=False``, the 2026-08-27 rule):
      no completed synthesis exists, so a LONGER streamed accumulation wins
      over the checkpoint, because a cancelled turn leaves the checkpoint
      holding only its last interim segment (the 96-second sweep persisted
      as a 158-char fragment while 2,272 streamed chars were discarded).

    The plan fence is un-glued via ``pick_user_facing_text`` in both paths
    so a trailing ```` ```plan ```` closer cannot swallow the answer.
    """
    ckpt = str(checkpoint_text or "").strip()
    streamed = str(streamed_text or "").strip()
    chosen = ""
    try:
        from kazma_core.agent.plan_fence import pick_user_facing_text

        if terminal and ckpt:
            # Final synthesis is authoritative; do not even offer the
            # streamed concat as a candidate (prose-length scoring would
            # let narration beat the answer again).
            chosen = pick_user_facing_text(ckpt) or ckpt
            return chosen.strip()
        chosen = pick_user_facing_text(ckpt, streamed) or ""
    except Exception:
        logger.debug("[reply_sink] plan_fence pick failed", exc_info=True)
        chosen = ckpt or streamed
    if not terminal and streamed and len(streamed) > len(chosen.strip()):
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
    parts: list[dict[str, Any]] | None = None,
    model: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
    duration_ms: float | None = None,
    allow_shrink: bool = False,
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
    ``allow_shrink=False`` (the default) refuses to replace longer stored
    text with shorter text unless *parts* carries a new ``text`` part —
    working notes belong in ``reasoning``, not in a clobbered ``content``.
    Best-effort disconnect flushes pass ``False`` explicitly.

    ``parts`` is the TurnDocument (reasoning / tool / status / hitl / text).
    Merge never deletes earlier reasoning or tools. ``content`` and
    ``activity`` are derived from parts for older clients.

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

            existing_state = _lifecycle_of(row)
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
                if text.strip() and parts is None:
                    if allow_shrink or len(text.strip()) >= len(
                        str(row.get("content") or "").strip()
                    ):
                        row["content"] = text

            if parts is not None:
                from kazma_ui.turn_document import activity_of, merge_parts, text_of

                existing_parts = (
                    row.get("parts") if isinstance(row.get("parts"), list) else []
                )
                if not existing_parts and str(row.get("content") or "").strip():
                    existing_parts = [
                        {"type": "text", "text": str(row.get("content") or "")}
                    ]
                merged = merge_parts(
                    existing_parts,
                    parts,
                )
                row["parts"] = merged
                derived = text_of(merged)
                if derived:
                    row["content"] = derived
                derived_act = activity_of(merged)
                if derived_act:
                    row["activity"] = derived_act

            # `pending` is monotone: it means "no text yet", so the first
            # text retires it permanently. Re-adding it after text exists
            # would show a processing spinner on an answered turn.
            if str(row.get("content") or "").strip():
                row.pop("pending", None)
            elif pending:
                row["pending"] = True

            # Lifecycle JOINS; it never assigns. A write that says "still in
            # flight", arriving after one that said "finished", is a straggler
            # — and a straggler must not resurrect the turn.
            _write_lifecycle(
                row, _join_lifecycle(existing_state, _LIVE if open_turn else _CLOSED)
            )

            if activity and not row.get("activity"):
                row["activity"] = list(activity)
            if model:
                row["model"] = str(model)
            if tokens is not None:
                row["tokens"] = int(tokens or 0)
            if cost is not None:
                row["cost"] = round(float(cost or 0.0), 6)
            if duration_ms is not None:
                row["duration_ms"] = int(round(float(duration_ms or 0.0)))

            # ── Authoritative document revision ────────────────────────
            # The delivery `seq` orders FRAMES on a thread; this orders
            # WRITES to one turn's durable state. They are independent
            # counters and `docs/plans/UNIFIED_TURN_BLOCK.md` §6 forbids
            # comparing them as if they were the same thing.
            #
            # A client that has applied rev N ignores a snapshot stamped
            # rev < N, which is what stops a slow /messages response from
            # repainting an older answer over a newer one (invariant U05).
            # Monotone per row, bumped on every successful write, and never
            # reset — a straggler write still advances it, because it did
            # change the row.
            from kazma_ui.turn_document import TURN_SCHEMA_VERSION

            try:
                row["rev"] = int(row.get("rev") or 0) + 1
            except (TypeError, ValueError):
                row["rev"] = 1
            row["schema"] = TURN_SCHEMA_VERSION
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
                # The old text ("still in the checkpoint") read as safe to
                # restart. It is not: the checkpoint copy is recovered only
                # while this is the chat's LAST turn, and a restart on that
                # advice lost a finished answer on 2026-09-24.
                f"session={str(session_id)[:12]} turn={str(turn_id)[:12]} "
                f"chars={len(text)}. It may exist only in memory: do not "
                f"restart Kazma until this is fixed. The checkpoint copy is "
                f"recovered only while this is the chat's last turn.",
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
        user_text = str(user_text or "")
        reply_text = str(reply_text or "")
        with _store().transact(session_id) as sess:
            rows = sess.messages if isinstance(sess.messages, list) else []
            last = rows[-1] if rows else None
            prev = rows[-2] if len(rows) >= 2 else None
            same_user = (
                isinstance(last, dict)
                and (last.get("role") or "") == "user"
                and str(last.get("content") or "").strip() == user_text.strip()
            )
            already = (
                isinstance(last, dict)
                and (last.get("role") or "") == "assistant"
                and str(last.get("content") or "").strip() == reply_text.strip()
                and isinstance(prev, dict)
                and (prev.get("role") or "") == "user"
                and str(prev.get("content") or "").strip() == user_text.strip()
            )
            if already:
                existing = str(last.get("turn_id") or "")
                if kind and not last.get("kind"):
                    last["kind"] = kind
                return existing
            if not same_user:
                rows.append({"role": "user", "content": user_text, "ts": _now()})
                sess.messages = rows
            elif isinstance(last, dict) and last.get("turn_id"):
                # User row already stored for this command (retried POST);
                # reuse its reply turn if one exists on the next row.
                pass
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
