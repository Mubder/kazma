"""Moving a chat between one's own space and a shared one (Spaces S2-12):
the move itself, its settling, and its routes.

**A move is a fork into the other space and the ordinary delete here**, under
a new session id and a new thread id. Moving a chat moves the conversation:
what Kazma remembered from it stays where it was said, and from now on it
remembers what is said in the new space. Taking a chat out of a shared space
makes that space forget it.

* ``GET /api/chat/sessions/{session_id}/move`` -- what the dialog shows
  before the move: the chat's size in turns and whether others wrote in it.
* ``POST /api/chat/sessions/{session_id}/move`` -- ``{"to_space"}``: move
  the bound space's chat ``session_id`` to ``to_space``; answers the copy's
  ``session_id``, which the page opens. Refusals are codes the page words
  (``chat.js``): ``spaces_off``, ``not_yours``,
  ``not_between_own_and_shared`` (``spaces.moves``), ``not_found``,
  ``not_your_chat``, ``others_wrote`` (the page offers "New private chat"),
  ``chat_app_chat`` (a chat app's chat stays in its space), ``turn_running``,
  ``turn_unfinished`` (a restart cut a turn off half way: Continue or Close
  it first), ``approval_pending``, ``approval_unknown`` (whether a card waits
  could not be read: refused, never guessed), ``swarm_running`` (a swarm task
  it started is not over), ``plan_waiting`` (it is in plan mode),
  ``move_in_progress``, ``space_limit`` (the files shared in the chat do not
  fit), ``changed`` (a message landed while it was copied: nothing moved, try
  again), ``kept_both`` (both chats changed: both are kept), ``move_failed``,
  ``identity_unavailable``.
* ``GET /api/chat/taken-out`` -- the lines the bound space keeps for the
  chats taken out of it: who (their name), when, the title each had.

The move (:func:`_move`) holds the thread's mutation lock
(``thread_locks``) and checks everything again under it -- no turn running
or cut off, no card waiting (the graph's checkpoint and the gate registry's
open rows; one that cannot be read refuses), no swarm task of it open, not in
plan mode, nobody else's words in a chat taken out -- then:

1. **journals** the attempt with its new ids and what it copies: the chat's
   count of messages, its last one's time and its checkpoint (stage
   ``copying``);
2. **copies**: the new thread's record in the target, which says when it was
   copied (``copied_at``: the target's memory starts there); the source's
   "Don't remember this chat", when it has one, under the new keys; the files
   Kazma shared in the chat, under new ids; the state, built from the row
   (``thread_rewind.carry_conversation``); and, last, the row -- the journal
   says ``copied`` at once, and from then nothing writes the chat here or
   runs on its thread (``SessionManager.hand_over``);
3. **deletes** it here (:data:`_STEPS`), each step on its own and recorded as
   it is done: first what memory must hold (a chat shared has its missed
   turns written; one kept out is kept out under both keys; a shared space
   the chat is taken out of forgets it), then the chat itself
   (``chat_delete.delete_chat``, as any chat's delete) -- only if it still
   holds what was copied -- then what was kept by its old ids.

The whole move runs in a task of its own: a request that goes away does not
cut it off. One that did not end is settled at the next start, on the
maintenance cadence, and by the next move of the same chat, by what the two
chats hold then (:func:`_decide`): the chat unchanged, its copy stands and the
delete goes on; the chat changed and its copy not, the copy is taken away;
both changed, both are kept.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.background import spawn_background
from kazma_core.db.pg_helpers import store_errors
from kazma_core.spaces import chat_moves, threads
from kazma_core.spaces.chat_moves import ChatMoveRefused
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope
from kazma_core.spaces.space_limits import SpaceOverLimit

from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.rate_limit import rate_limit

__all__ = ["create_chat_move_router", "settle_chat_moves", "settler"]

logger = logging.getLogger(__name__)

#: What a step of a move can fail with: any store's own errors -- Postgres's
#: among them -- and an identity store that cannot answer. Each step is
#: idempotent and tried again.
_STEP_FAILURES: tuple[type[Exception], ...] = (*store_errors(), IdentityUnavailable)

#: What reading a chat's checkpoint can raise: the store's errors and a state
#: that does not decode (either one means "not known").
_STATE_FAILURES: tuple[type[Exception], ...] = (*store_errors(), LookupError, AttributeError)

#: What settling one move can fail with: it keeps its entry, and the others
#: are settled -- whatever one chat's state is.
_SETTLE_FAILURES: tuple[type[Exception], ...] = (*_STEP_FAILURES, *_STATE_FAILURES)

#: How long the maintenance sweep waits for the settling it started.
_SETTLE_WAIT_S = 120.0

_TURN_RUNNING = ("turn_running", 409, "Kazma is answering in this chat. Move it when it is done.")
_CHANGED = ("changed", 409, "Something was written in this chat while it was moved, so it stayed here. Try again.")
_KEPT_BOTH = ("kept_both", 409, "Both chats were written in while this one was moved, so both are kept.")


def _snapshot_in(space: str, by: str, session_id: str) -> Any:
    """The chat as it is now, a copy of its own (blocking)."""
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        return get_session_manager().snapshot(session_id)


def _held_in(space: str, by: str, session_id: str) -> Any:
    """The chat's row as the store holds it now, or None. Raises when the
    store cannot be read. Blocking."""
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        return get_session_manager().held(session_id)


def _fingerprint(messages: list[Any]) -> tuple[int, str]:
    """How many messages, and the last one's time."""
    last = messages[-1] if messages else None
    return len(messages), str((last.get("ts") if isinstance(last, dict) else "") or "")


def _thread_of(session: Any, session_id: str) -> str:
    return str(getattr(session, "thread_id", "") or session_id)


def _opener(thread: str) -> str:
    """Who opened the chat: its record's opener; a chat from before Spaces
    (no record: it is in the owner's space, ``threads.thread_home``) is the
    owner's (N19)."""
    record = threads.record_of(thread)
    return record.opener if record is not None else OWNER_ACCOUNT_ID


def _prepare(session_id: str, from_space: str, to_space: str, by: str) -> tuple[Any, bool]:
    """A snapshot of the chat and whether the move takes it out of a shared
    space, or a refusal. Blocking; asked again under the thread's lock: what
    is judged then is what is copied."""
    refused, taken_out = chat_moves.move_refusal(from_space=from_space, to_space=to_space, by=by)
    if refused is not None:
        raise refused
    session = _snapshot_in(from_space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    thread = _thread_of(session, session_id)
    if threads.thread_home(thread) != from_space:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    if chat_moves.is_chat_app_chat(session_id, thread):
        raise ChatMoveRefused("chat_app_chat", 409, "This chat is a chat app's, so it stays in its space.")
    if _opener(thread) != by:
        raise ChatMoveRefused("not_your_chat", 403, "Only the person who started a chat moves it.")
    if taken_out and chat_moves.others_wrote(session.messages, by):
        # Their words are the shared space's (the plan's S2-12 row, decided).
        raise ChatMoveRefused("others_wrote", 409,
                              "Others wrote in this chat, so it stays in this space. Start a new "
                              "private chat in your own space instead.")
    return session, taken_out


# ── What refuses a move ──────────────────────────────────────────────────

_APPROVAL_UNKNOWN = ("approval_unknown", 503,
                     "Kazma could not check whether this chat waits for an approval. Try again in a moment.")
_APPROVAL_PENDING = ("approval_pending", 409, "This chat waits for an approval. Answer it, then move the chat.")
_TURN_UNFINISHED = ("turn_unfinished", 409,
                    "An answer in this chat was cut off half way. Continue or close it, then move the chat.")
_SWARM_RUNNING = ("swarm_running", 409, "A swarm task this chat started is not over. Move the chat when it is.")
_PLAN_WAITING = ("plan_waiting", 409, "This chat is in plan mode. Approve or cancel the plan, then move the chat.")


def _pending_gate_rows(thread_id: str) -> int:
    """The gate registry's questions still open on the thread (none when the
    registry is switched off: the checkpoint is then the only truth)."""
    from kazma_core.safety import hitl_gates

    if not hitl_gates.gate_registry_enabled():
        return 0
    return len(hitl_gates.live_gates(thread_id))


def _nothing_open_in(thread_id: str, space: str, by: str) -> None:
    """A refusal while a swarm task the chat started is not over, or it is in
    plan mode: either one names the chat that would be gone. Blocking."""
    from kazma_core.agent.plan_mode import plan_waits
    from kazma_core.swarm.chat_tasks import open_task_of_chat

    with bind_scope(space, by):
        if plan_waits(thread_id):
            raise ChatMoveRefused(*_PLAN_WAITING)
        if open_task_of_chat(thread_id):
            raise ChatMoveRefused(*_SWARM_RUNNING)


def _checkpoint_of(snap: Any) -> str:
    config = getattr(snap, "config", None) or {}
    return str((config.get("configurable") or {}).get("checkpoint_id") or "")


async def _quiet(graph: Any, thread_id: str) -> str:
    """The id of the chat's checkpoint, or a refusal while something of it is
    unfinished:

    * a card waits on it -- an interrupt in its checkpoint, or an open row in
      the gate registry: it must never be left in the space the chat left;
    * a turn was cut off half way (a next step and no card): the copy starts
      at a turn's end, so what that turn had begun would be lost.

    Either that cannot be read refuses too."""
    if graph is None:
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN)
    try:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    except _STATE_FAILURES as exc:
        logger.warning("[spaces] whether a card waits on a chat could not be read; the move is refused",
                       exc_info=True)
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN) from exc
    unfinished = snap is not None and bool(getattr(snap, "next", None))
    if unfinished and any(getattr(task, "interrupts", None) for task in getattr(snap, "tasks", None) or []):
        raise ChatMoveRefused(*_APPROVAL_PENDING)
    try:
        waiting = await asyncio.to_thread(_pending_gate_rows, thread_id)
    except (sqlite3.Error, OSError) as exc:
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN) from exc
    if waiting:
        raise ChatMoveRefused(*_APPROVAL_PENDING)
    if unfinished:
        raise ChatMoveRefused(*_TURN_UNFINISHED)
    return _checkpoint_of(snap)


# ── The move ─────────────────────────────────────────────────────────────


async def _move_chat(*, session_id: str, from_space: str, to_space: str, by: str,
                     graph: Any = None, checkpoints: Any = None) -> dict[str, Any]:
    """Move *by*'s chat *session_id* from *from_space* to *to_space*. Raises
    :class:`ChatMoveRefused`; anything else that stops it before the handover
    leaves the chat where it was. In a task of its own: a request that is
    cancelled waits no longer, and the move goes on."""
    task = spawn_background(
        _answered(_move(session_id=session_id, from_space=from_space, to_space=to_space, by=by,
                        graph=graph, checkpoints=checkpoints)), name="chat-move")
    answer = await asyncio.shield(task)
    if isinstance(answer, Exception):
        raise answer
    return answer


async def _answered(move: Awaitable[dict[str, Any]]) -> dict[str, Any] | Exception:
    """What the move answers, a refusal or a failure as a value: it is the
    request's to answer, never the task's to log."""
    try:
        return await move
    except (ChatMoveRefused, *_STEP_FAILURES) as exc:
        return exc


async def _move(*, session_id: str, from_space: str, to_space: str, by: str,
                graph: Any, checkpoints: Any) -> dict[str, Any]:
    session, _ = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
    thread = _thread_of(session, session_id)
    from kazma_ui.active_turns import is_turn_running
    from kazma_ui.thread_locks import thread_mutation

    async with thread_mutation(thread):
        # An earlier move of this chat that did not end: settled now, so it
        # never holds the chat.
        await _settle_thread(thread, checkpoints)
        # Everything again, under the lock: a turn that ended while this
        # waited, someone else's words, another space's rules.
        session, taken_out = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
        if is_turn_running(thread):
            raise ChatMoveRefused(*_TURN_RUNNING)
        checkpoint = await _quiet(graph, thread)
        await asyncio.to_thread(_nothing_open_in, thread, from_space, by)
        attempt = uuid.uuid4().hex
        rows = _rows_to_copy(session, by)
        entry = await asyncio.to_thread(_entry, session, session_id, from_space, to_space, by, taken_out)
        entry.update(checkpoint=checkpoint, **_copied_is(rows))
        await asyncio.to_thread(chat_moves.record_move, attempt, entry)
        handed = False
        try:
            rows = await _copy(rows, entry, graph)
            if not is_turn_running(thread) and await _quiet(graph, thread) == checkpoint:
                handed = await asyncio.to_thread(_hand_over, attempt, entry, session.messages, rows)
        except (ChatMoveRefused, *_STEP_FAILURES):
            # It may have stopped after the handover (its answer was lost):
            # then the chat is in the target, and settling goes on.
            if await _settle_one(attempt, checkpoints) not in _MOVED:
                raise
        else:
            # Handed over: the delete here. Not handed over -- something
            # landed while the chat was copied -- the copy is taken away.
            outcome = await _settle_one(attempt, checkpoints)
            if outcome not in _MOVED and not (handed and outcome == "waiting"):
                raise ChatMoveRefused(*(_KEPT_BOTH if outcome == "kept_both" else
                                        _TURN_RUNNING if is_turn_running(thread) else _CHANGED))
    logger.info("[spaces] %s moved a chat from %s to %s (taken out: %s)", by, from_space, to_space, taken_out)
    return {"session_id": entry["new_session"], "space": to_space, "taken_out": taken_out,
            "turns": _asked(session.messages)}


def _asked(messages: list[Any]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")


def _entry(session: Any, session_id: str, from_space: str, to_space: str, by: str,
           taken_out: bool) -> dict[str, Any]:
    """The journal's entry of a move about to start, with the copy's ids."""
    from kazma_core.memory import forget

    thread = _thread_of(session, session_id)
    new_session = str(uuid.uuid4())
    with bind_scope(to_space, by):
        new_thread = threads.new_web_thread_id(new_session)
    kept_out, _turns = forget.forgotten_in_chat(from_space, [session_id, thread])
    count, last_ts = _fingerprint(session.messages)
    return {"thread": thread, "session": session_id, "source": from_space, "target": to_space, "by": by,
            "new_session": new_session, "new_thread": new_thread, "taken_out": taken_out,
            "kept_out": bool(kept_out), "title": str(session.title or ""), "stage": "copying",
            "count": count, "last_ts": last_ts, "at": time.time()}


def _copied_is(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the copy's row holds, for its entry."""
    count, last_ts = _fingerprint(rows)
    return {"new_count": count, "new_last_ts": last_ts}


def _rows_to_copy(session: Any, by: str) -> list[dict[str, Any]]:
    """The copy's messages: the chat's questions and answers. The person's
    own that name no author -- from their personal space, before Spaces --
    are stamped theirs: in a shared space every message names its author
    (``reply_sink.authored``)."""
    from kazma_ui.thread_rewind import projected_rows

    rows = projected_rows(session.messages)
    for row in rows:
        if row["role"] == "user" and not row.get("author"):
            row["author"] = by
    return rows


async def _copy(rows: list[dict[str, Any]], entry: dict[str, Any], graph: Any) -> list[dict[str, Any]]:
    """Everything of the copy but its row (:func:`_hand_over` writes that,
    last): nobody sees any of it yet. Returns the row's messages, their links
    naming the files' copies."""
    from kazma_ui.thread_rewind import carry_conversation

    await asyncio.to_thread(_record_copy, entry)
    rows = await asyncio.to_thread(_copy_files, rows, entry)
    await carry_conversation(graph, rows, entry["new_thread"], space=entry["target"], by=entry["by"])
    return rows


def _record_copy(entry: dict[str, Any]) -> None:
    """The new thread's record, first: whatever is written under it is the
    target's, and its memory starts at this moment. And the source's "Don't
    remember this chat", before the copy holds anything memory could read."""
    from kazma_core.memory import forget

    with bind_scope(entry["target"], entry["by"]):
        threads.record_new_thread(entry["new_thread"], copied_at=float(entry["at"]))
        if entry["kept_out"]:
            forget.set_chat_remembered(entry["new_session"], False, tenant_id=entry["target"],
                                       keys=[entry["new_session"], entry["new_thread"]])


def _copy_files(rows: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    from kazma_core.chat_files import copy_shared_files, relink

    text = json.dumps(rows, ensure_ascii=False, default=str)
    try:
        ids = copy_shared_files(text, thread_id=entry["thread"], new_thread=entry["new_thread"],
                                from_space=entry["source"], to_space=entry["target"], by=entry["by"])
    except SpaceOverLimit as exc:
        raise ChatMoveRefused("space_limit", 507, "The new space has no room for this chat's files.") from exc
    return json.loads(relink(text, ids)) if ids else rows


def _hand_over(attempt: str, entry: dict[str, Any], messages: list[dict[str, Any]],
               rows: list[dict[str, Any]]) -> bool:
    """The copy's row in, the journal's ``copied``, the chat here marked
    deleted: one step (``SessionManager.hand_over``). False: the chat changed
    since it was copied, or the move was settled meanwhile; nothing moved."""
    from kazma_ui.session_manager import ChatSession, get_session_manager

    moved = ChatSession(session_id=entry["new_session"], thread_id=entry["new_thread"],
                        tenant_id=entry["target"], messages=copy.deepcopy(rows))
    with bind_scope(entry["source"], entry["by"]):
        return get_session_manager().hand_over(
            entry["session"], messages=messages, to=moved,
            commit=lambda: chat_moves.advance_move(attempt, at="copying", to="copied"))


# ── The delete here, one step at a time ──────────────────────────────────


def _unchanged(entry: dict[str, Any]) -> Callable[[Any], bool]:
    """Whether a row is still the one that was copied."""
    copied = (int(entry.get("count") or 0), str(entry.get("last_ts") or ""))
    return lambda row: _fingerprint(row.messages) == copied


def _still_the_copy(entry: dict[str, Any]) -> Callable[[Any], bool]:
    """Whether a row is still exactly the copy."""
    copy_is = (int(entry.get("new_count") or 0), str(entry.get("new_last_ts") or ""))
    return lambda row: _fingerprint(row.messages) == copy_is


def _mark(entry: dict[str, Any]) -> None:
    """Nothing writes the chat here, and its thread runs no turn."""
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(entry["source"], entry["by"]):
        get_session_manager().mark_deleted(entry["session"], entry["thread"])


def _unmark(entry: dict[str, Any]) -> None:
    """The chat stays here after all -- but one whose person deleted it
    meanwhile stays deleted. Raises when the store cannot say which."""
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(entry["source"], entry["by"]):
        get_session_manager().unmark_if_held(entry["session"], entry["thread"])


def _before_the_delete(entry: dict[str, Any]) -> None:
    """What memory must hold before the chat goes, while its words are here:

    * a chat shared: the turns memory missed are written where they were said
      (no later pass would find the chat);
    * a chat kept out of memory under either of its keys is kept out under
      both: a write still queued names one of them;
    * a chat taken out of a shared space: that space forgets it -- its
      memories, the agent's notes, its summaries and the lessons it taught
      (``memory.forget.forget_chat``: journaled first, and the forget
      convergence finishes one that fails) -- kept out first, under both keys.
      A personal space keeps what it remembers: a move never touches it."""
    from kazma_core.memory import forget
    from kazma_core.memory.turn_reconcile import reconcile_chat

    source, keys = entry["source"], [entry["session"], entry["thread"]]
    if not entry["taken_out"]:
        row = _held_in(source, entry["by"], entry["session"])
        if row is not None:
            reconcile_chat({"tenant_id": source, "session_id": entry["session"], "thread_id": entry["thread"],
                            "created_at": row.created_at, "updated_at": row.updated_at,
                            "messages": row.messages})
    kept_out, _turns = forget.forgotten_in_chat(source, keys)
    answers = []
    if entry["taken_out"]:
        # Said first: a chat that stays after all has its keep-out lifted
        # (:func:`_lift_keep_out`), and a forget begun is never put back.
        chat_moves.record_move(str(entry.get("attempt") or ""), {"keep_outs": int(entry.get("keep_outs") or 0) + 1})
    if entry["taken_out"] or kept_out:
        answers.append(forget.set_chat_remembered(entry["session"], False, tenant_id=source, keys=keys))
    if entry["taken_out"]:
        answers.append(forget.forget_chat(entry["session"], tenant_id=source, keys=keys))
    for out in answers:
        if not out.get("ok"):
            raise RuntimeError(f"a space did not take back a chat that left it: {out.get('error')}")


def _lift_keep_out(entry: dict[str, Any]) -> None:
    """The chat stays in the shared space it was being taken out of: the
    keep-out the move wrote there is lifted, so the space remembers what is
    said in it from now on. The forget it began is final and is done first
    (what was said is forgotten; nothing said after is). A person's own "Don't
    remember this chat" -- before the move, or since it began -- stays.
    Raises when memory cannot answer: asked again. Blocking."""
    if not entry.get("taken_out") or entry.get("kept_out") or not entry.get("keep_outs"):
        return
    from kazma_core.memory import forget, forget_journal

    source, keys = entry["source"], [entry["session"], entry["thread"]]
    asked = forget_journal.chat_entries("keep_out", tenant_id=source, keys=keys, since=float(entry["at"]))
    if len(asked) > int(entry["keep_outs"]):
        return  # someone asked for it too
    if forget_journal.chat_entries("chat", tenant_id=source, keys=keys, since=float(entry["at"])):
        forget.converge()
        if forget_journal.chat_forget_pending(source, keys):
            raise RuntimeError("a forget of a chat that stays is not done yet")
    out = forget.set_chat_remembered(entry["session"], True, tenant_id=source, keys=keys)
    if not out.get("ok"):
        raise RuntimeError(f"a keep-out was not lifted: {out.get('error')}")


def _drop_pointers(thread_id: str) -> int:
    """The chat apps' pointers to the thread (``active_thread.<sender>``)
    go: a chat app's next message starts a chat of its own. Blocking."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    dropped = 0
    # The pointers are the "session" category's (sessions.directory).
    for key, value in store.get_category("session").items():
        if str(key).startswith("active_thread.") and str(value or "") == thread_id:
            store.delete(key)
            dropped += 1
    return dropped


def _remove_what_it_left(entry: dict[str, Any]) -> None:
    """The shared space keeps nothing by the chat's old thread: its drafts,
    plan, confirmations, decided gates, the finished swarm tasks its maker
    started from it."""
    from kazma_core.swarm.chat_tasks import forget_finished_tasks

    chat_moves.remove_what_it_left(entry["thread"], space_id=entry["source"], by=entry["by"])
    forget_finished_tasks(entry["thread"], entry["by"])


def _remove_files(entry: dict[str, Any], space: str, thread: str) -> None:
    from kazma_core.chat_files import remove_shared_files

    remove_shared_files(thread, space=space, by=entry["by"])


async def _end_the_rest(entry: dict[str, Any], _checkpoints: Any) -> None:
    """What was kept for the running of the old thread ends with it: its
    grants, YOLO and path grants, ``/long``, its steers, its tool receipts,
    and the journaled frames of what its turns were shown."""
    from kazma_core.agent.effect_journal import forget_thread_receipts
    from kazma_core.agent.steer import clear_all_steers
    from kazma_core.spaces.space_data import forget_chat_state

    from kazma_ui.delivery import get_turn_broker

    thread = entry["thread"]
    clear_all_steers(thread)
    get_turn_broker().clear(thread)
    await asyncio.to_thread(forget_chat_state, thread)
    await asyncio.to_thread(forget_thread_receipts, thread)


class _ChatChangedError(RuntimeError):
    """The chat is not what was copied: nothing of it was deleted."""


async def _delete_here(entry: dict[str, Any], checkpoints: Any) -> None:
    """The chat's own delete, only if it still holds what was copied: its
    checkpoint and whether a turn runs on it are asked right before (voice
    takes no lock), its row in the delete itself."""
    from kazma_ui.chat_delete import delete_chat

    if await _old_is(entry, checkpoints) == "changed":
        raise _ChatChangedError("the chat changed")
    gone = await delete_chat(entry["session"], entry["thread"], checkpoints,
                             scope=(entry["source"], entry["by"]), reason="moved",
                             unchanged=_unchanged(entry))
    if gone is None:
        raise _ChatChangedError("the chat changed")
    if not gone:
        raise RuntimeError("something of a chat could not be deleted")


_Step = Callable[[dict[str, Any], Any], Awaitable[Any]]


def _in_thread(step: Callable[[dict[str, Any]], Any]) -> _Step:
    async def run(entry: dict[str, Any], _checkpoints: Any) -> Any:
        return await asyncio.to_thread(step, entry)

    return run


async def _handed_over(_entry: dict[str, Any], _checkpoints: Any) -> None:
    """The copy stands: recorded, so a copy that is gone later was deleted
    by a person."""


#: What deletes the chat here once its copy stands, in order, each on its own.
_STEPS: tuple[tuple[str, _Step], ...] = (
    ("handed_over", _handed_over),
    ("memory", _in_thread(lambda e: _before_the_delete(e))),
    ("delete", _delete_here),
    ("pointers", _in_thread(lambda e: _drop_pointers(e["thread"]))),
    ("line", _in_thread(lambda e: chat_moves.record_taken_out(
        space_id=e["source"], thread_id=e["thread"], title=str(e.get("title") or ""), by=e["by"],
        since=float(e.get("at") or 0.0)))),
    ("left", _in_thread(lambda e: _remove_what_it_left(e))),
    ("jobs", _in_thread(lambda e: chat_moves.follow_jobs(
        e["thread"], space_id=e["source"], by=e["by"], to_space=e["target"], to_thread=e["new_thread"]))),
    ("files", _in_thread(lambda e: _remove_files(e, e["source"], e["thread"]))),
    ("ended", _end_the_rest),
)
#: The steps of a chat taken out of a shared space alone.
_TAKEN_OUT_STEPS = frozenset({"line", "left", "jobs", "files"})
#: The steps that must be done before the next one is: the chat is still here.
_IN_ORDER = frozenset({"handed_over", "memory"})


async def _finish(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """Every step not done yet (:data:`_STEPS`), each on its own: one that
    fails is said and tried again later, and the others run -- but nothing
    past the steps of :data:`_IN_ORDER` before they are done, nor past the
    delete while the chat's row is still here. True when the move is
    finished (its journal entry is gone). Raises :class:`_ChatChangedError`."""
    done = [str(s) for s in entry.get("done") or []]
    left: list[str] = []
    entry = {**entry, "attempt": attempt}  # a step that notes something names it
    with bind_scope(entry["source"], entry["by"]):
        for name, step in _STEPS:
            if name in done or (name in _TAKEN_OUT_STEPS and not entry.get("taken_out")):
                continue
            try:
                await step(entry, checkpoints)
                await asyncio.to_thread(chat_moves.record_move, attempt, {"done": [*done, name]})
                done.append(name)
            except _ChatChangedError:
                raise
            except _STEP_FAILURES:
                logger.warning("[spaces] a chat move's step %s failed; it is tried again", name, exc_info=True)
                left.append(name)
                if name in _IN_ORDER or (name == "delete" and await _row_is_here(entry)):
                    break
    if left:
        return False
    try:
        await asyncio.to_thread(chat_moves.settle_move, attempt)
    except _STEP_FAILURES:
        logger.warning("[spaces] a finished chat move was not settled; it is tried again", exc_info=True)
        return False
    return True


async def _row_is_here(entry: dict[str, Any]) -> bool:
    """Whether the chat's row is still here, as far as it can be read."""
    try:
        return await asyncio.to_thread(_held_in, entry["source"], entry["by"], entry["session"]) is not None
    except _STEP_FAILURES:
        return True


# ── Settling: at start, by the maintenance sweep, by the next move ───────


async def _old_checkpoint(entry: dict[str, Any], checkpoints: Any) -> str | None:
    """The id of the chat's checkpoint now, or None when there is no store
    to ask. One that does not decode is not known: the move waits."""
    if checkpoints is None or not hasattr(checkpoints, "aget_tuple"):
        return None
    with bind_scope(entry["source"], entry["by"]):
        found = await checkpoints.aget_tuple(
            {"configurable": {"thread_id": entry["thread"], "checkpoint_ns": ""}})
    return _checkpoint_of(found)


async def _old_is(entry: dict[str, Any], checkpoints: Any) -> str:
    """What the chat here is now: ``absent``, ``same`` as copied, or
    ``changed`` -- its messages, its checkpoint, or a turn running on it."""
    from kazma_ui.active_turns import is_turn_running

    row = await asyncio.to_thread(_held_in, entry["source"], entry["by"], entry["session"])
    if row is None:
        return "absent"
    if is_turn_running(entry["thread"]) or not _unchanged(entry)(row):
        return "changed"
    now = await _old_checkpoint(entry, checkpoints)
    return "same" if now is None or now == str(entry.get("checkpoint") or "") else "changed"


async def _decide(entry: dict[str, Any], checkpoints: Any) -> str:
    """What becomes of a move, from the chat here and its copy as they are
    now: ``finish`` it, ``put_back`` its copy, or ``keep_both``.

    * The copy as it was made and the chat unchanged: the copy stands once
      the journal says ``copied``; one still being made is taken away.
    * The copy written in and the chat unchanged: the copy is the chat now.
    * The chat changed and the copy not: the copy is taken away -- unless the
      shared space forgot the chat already, or began to: then both are kept.
    * Both changed: both are kept.
    * The chat gone: the copy stands; one still being made, which nobody saw,
      is taken away (its chat was deleted).
    * The copy gone: once it stood, a person deleted it, and the delete here
      goes on -- unless the chat changed: then it is kept (the delete would
      refuse it, and the chat was in neither space for good); else what is
      left of it is taken away.
    """
    done = [str(s) for s in entry.get("done") or []]
    copied = entry.get("stage") == "copied"
    new = await asyncio.to_thread(_held_in, entry["target"], entry["by"], entry["new_session"])
    old = await _old_is(entry, checkpoints)
    if new is None:
        if not (copied and (done or old == "absent")):
            return "put_back"
        return "keep_both" if old == "changed" else "finish"
    written_in = not _still_the_copy(entry)(new)
    if old in ("absent", "same"):
        return "finish" if copied or written_in else "put_back"
    forgotten = bool(entry.get("taken_out")) and ("memory" in done or bool(entry.get("keep_outs")))
    return "keep_both" if written_in or forgotten else "put_back"


def _remove_copy_row(entry: dict[str, Any]) -> bool:
    """The copy's row out, only while it is exactly the copy. Blocking."""
    from kazma_ui.session_manager import get_session_manager

    store = get_session_manager()
    with bind_scope(entry["target"], entry["by"]):
        store.mark_deleted(entry["new_session"], entry["new_thread"])
        if store.delete_if(entry["new_session"], _still_the_copy(entry)):
            return True
        store.unmark_deleted(entry["new_session"], entry["new_thread"])
        return False


async def _put_back(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """Take the copy away, whatever the new ids have in the target, and the
    chat stays here. False: someone wrote in the copy meanwhile."""
    from kazma_core.memory import forget

    from kazma_ui.chat_delete import remove_chat_remains

    target, by = entry["target"], entry["by"]
    if not await asyncio.to_thread(_remove_copy_row, entry):
        return False
    await asyncio.to_thread(_remove_files, entry, target, entry["new_thread"])
    if entry.get("kept_out"):
        await asyncio.to_thread(forget.set_chat_remembered, entry["new_session"], True, tenant_id=target,
                                keys=[entry["new_session"], entry["new_thread"]])
    with bind_scope(target, by):
        # The record goes last: a thread recorded nowhere has nothing left.
        if await asyncio.to_thread(threads.thread_home, entry["new_thread"]) == target:
            if not await remove_chat_remains(entry["new_session"], entry["new_thread"], checkpoints):
                raise RuntimeError("something of a chat's copy could not be removed")
    await asyncio.to_thread(_unmark, entry)
    await asyncio.to_thread(chat_moves.settle_move, attempt)
    return True


async def _keep_both(attempt: str, entry: dict[str, Any]) -> None:
    """The chat holds words of its own, and so may its copy: neither is
    removed. The entry stays until the shared space's keep-out is lifted."""
    await asyncio.to_thread(_unmark, entry)
    logger.warning("[spaces] a chat was written in while it was moved from %s to %s; it and its copy, both are "
                   "kept (%s and %s)", entry["source"], entry["target"], entry["session"], entry["new_session"])
    try:
        await asyncio.to_thread(_lift_keep_out, entry)
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat that stayed is still kept out of memory; it is tried again", exc_info=True)
        return
    await asyncio.to_thread(chat_moves.settle_move, attempt)


#: What settling answers for a move that stands.
_MOVED = ("copied", "finished")

_ENTRY_FIELDS = ("thread", "target", "source", "session", "by", "new_session", "new_thread")


async def _settle_one(attempt: str, checkpoints: Any) -> str:
    """Settle one move, its thread's lock held (:func:`_decide`). What cannot
    be settled now keeps its entry, and is settled later. Returns
    ``put_back``, ``kept_both``, ``copied`` (it stands; a step is left),
    ``finished``, ``dropped`` or ``waiting``."""
    try:
        # What was decided may change while it is done: decided again.
        for _ in range(3):
            entry = (await asyncio.to_thread(chat_moves.moves_under_way)).get(attempt)
            if entry is None:
                return "dropped"
            if not all(entry.get(k) for k in _ENTRY_FIELDS):
                await asyncio.to_thread(chat_moves.settle_move, attempt)
                return "dropped"
            decided = await _decide(entry, checkpoints)
            if decided == "keep_both":
                await _keep_both(attempt, entry)
                return "kept_both"
            # The move's own handover and this never both decide.
            stage = "putting_back" if decided == "put_back" else "copied"
            at = str(entry.get("stage") or "")
            if at != stage and not await asyncio.to_thread(chat_moves.advance_move, attempt, at=at, to=stage):
                continue
            if decided == "put_back":
                if await _put_back(attempt, entry, checkpoints):
                    return "put_back"
                continue
            await asyncio.to_thread(_mark, entry)
            try:
                return "finished" if await _finish(attempt, entry, checkpoints) else "copied"
            except _ChatChangedError:
                continue
    except _SETTLE_FAILURES:
        logger.warning("[spaces] a chat move that stopped was not settled; it is tried again", exc_info=True)
    return "waiting"


async def _settle_thread(thread: str, checkpoints: Any) -> None:
    """Settle what an earlier move of *thread* left (its lock is held, so no
    move of it runs)."""
    for attempt, entry in (await asyncio.to_thread(chat_moves.moves_under_way)).items():
        if entry.get("thread") == thread:
            await _settle_one(attempt, checkpoints)


async def settle_chat_moves(checkpoints: Any = None) -> list[dict[str, str]]:
    """Settle every chat move that did not end: at every start, and on the
    maintenance cadence. Each under its thread's lock -- a move running now
    holds it, and is left to its own request -- and each on its own: one
    that cannot be settled does not stop the others."""
    from kazma_ui.thread_locks import thread_mutation

    settled: list[dict[str, str]] = []
    for attempt, entry in sorted((await asyncio.to_thread(chat_moves.moves_under_way)).items()):
        outcome = "waiting"
        try:
            async with thread_mutation(str(entry.get("thread") or attempt)):
                # Read again: the move that held the lock may have ended.
                if attempt not in await asyncio.to_thread(chat_moves.moves_under_way):
                    continue
                outcome = await _settle_one(attempt, checkpoints)
        except _SETTLE_FAILURES:
            logger.warning("[spaces] a chat move was not settled; it is tried again", exc_info=True)
        settled.append({"attempt": attempt, "outcome": outcome})
        logger.info("[spaces] a chat move that did not end: %s", outcome)
    return settled


def settler(loop: asyncio.AbstractEventLoop, checkpoints_getter: Callable[[], Any]) -> Callable[[], None]:
    """What the maintenance sweep "chat moves" calls, in its thread
    (``chat_moves.register_settler``): the settling runs on the server's
    loop, where the checkpoints and the thread locks live."""

    def settle() -> None:
        running = asyncio.run_coroutine_threadsafe(settle_chat_moves(checkpoints_getter()), loop)
        running.result(timeout=_SETTLE_WAIT_S)

    return settle


# ── The routes ───────────────────────────────────────────────────────────


def _mover() -> str | None:
    """The account a move is made for: the person bound to this request."""
    from kazma_core.spaces.scope import bound_asker

    return bound_asker() or None


def _refused(exc: ChatMoveRefused) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.code}, status_code=int(exc.status or 400))


def _unavailable() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "identity_unavailable"}, status_code=503,
                        headers={"Retry-After": "5"})


def _preview(session_id: str, space: str, by: str) -> dict[str, Any]:
    session = _snapshot_in(space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    return {"ok": True, "turns": _asked(session.messages),
            "others_wrote": chat_moves.others_wrote(session.messages, by)}


def _names(accounts: set[str]) -> dict[str, str]:
    from kazma_core.spaces import identity

    held = identity.read_registry()["accounts"]
    return {a: str((held.get(a) or {}).get("name") or "") for a in accounts}


def _lines(space: str) -> list[dict[str, str]]:
    lines = chat_moves.taken_out_chats(space)
    try:
        names = _names({line["by"] for line in lines})
    except IdentityUnavailable:
        names = {}
    return [{"title": line["title"], "at": line["at"], "by_name": names.get(line["by"], "")} for line in lines]


def create_chat_move_router(*, graph_getter: Callable[[], Any] | None = None,
                            checkpoints_getter: Callable[[], Any] | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/chat/sessions/{session_id}/move")
    def move_preview(session_id: str) -> JSONResponse:
        from kazma_core.spaces.scope import require_space_id, spaces_enforced

        if not spaces_enforced():
            return JSONResponse({"ok": False, "error": "spaces_off"}, status_code=409)
        by = _mover()
        if not by:
            return JSONResponse({"ok": False, "error": "not_yours"}, status_code=403)
        try:
            return JSONResponse(_preview(session_id, require_space_id(), by))
        except ChatMoveRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()

    @router.post("/api/chat/sessions/{session_id}/move", dependencies=[Depends(rate_limit("chat_move", 6))])
    async def move_route(session_id: str, request: Request) -> JSONResponse:
        from kazma_core.spaces.scope import require_space_id

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        to_space = str(body.get("to_space") or "").strip()
        by = _mover()
        if not by:
            return JSONResponse({"ok": False, "error": "not_yours"}, status_code=403)
        try:
            moved = await _move_chat(
                session_id=session_id, from_space=require_space_id(), to_space=to_space, by=by,
                graph=graph_getter() if graph_getter else None,
                checkpoints=checkpoints_getter() if checkpoints_getter else None,
            )
        except ChatMoveRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        except _STEP_FAILURES:
            logger.warning("[spaces] a chat move failed; the chat stayed where it was", exc_info=True)
            return JSONResponse({"ok": False, "error": "move_failed"}, status_code=503,
                                headers={"Retry-After": "5"})
        return JSONResponse({"ok": True, **moved})

    @router.get("/api/chat/taken-out")
    def taken_out_route() -> JSONResponse:
        from kazma_core.spaces.scope import require_space_id, spaces_enforced

        if not spaces_enforced():
            return JSONResponse({"ok": True, "lines": []})
        return JSONResponse({"ok": True, "lines": _lines(require_space_id())})

    return router
