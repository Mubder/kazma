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
  ``chat_app_chat`` (a chat app's chat stays in its space), ``turn_running``
  (a turn, or a message that landed while the chat was copied),
  ``turn_unfinished`` (a restart cut a turn off half way: Continue or Close
  it first), ``approval_pending``, ``approval_unknown`` (whether a card waits
  could not be read: refused, never guessed), ``move_in_progress``,
  ``space_limit`` (the files shared in the chat do not fit), ``move_failed``,
  ``identity_unavailable``.
* ``GET /api/chat/taken-out`` -- the lines the bound space keeps for the
  chats taken out of it: who (their name), when, the title each had.

The move (:func:`_move`) holds the thread's mutation lock
(``thread_locks``) and checks everything again under it -- no turn running
or cut off, no card waiting (the graph's checkpoint and the gate registry's
pending rows; one that cannot be read refuses), nobody else's words in a
chat taken out -- then:

1. **journals** the attempt with its new ids (stage ``copying``);
2. **copies**: the new thread's record in the target; the source's "Don't
   remember this chat", when it has one, under the new keys; the state, as a
   fork (``thread_rewind.carry_conversation``: questions and answers); the
   files Kazma shared in the chat;
3. **hands over** (``SessionManager.hand_over``), in one step: if the chat
   still holds exactly what was copied, its copy's row is stored, the
   journal says ``copied``, and its row here is deleted. A message that
   landed meanwhile makes it refuse, with nothing changed;
4. **deletes** the rest here (:data:`_STEPS`), as any chat's delete does
   (``chat_delete``), each step on its own and recorded as it is done. A
   shared space the chat was taken out of forgets it under its old keys and
   keeps its line.

The whole move runs in a task of its own: a request that goes away does not
cut it off. One that did not end is settled at the next start, on the
maintenance cadence, and by the next move of the same chat: ``copying`` takes
the copy away and the chat stays here, ``copied`` finishes the delete here.
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

#: How long the maintenance sweep waits for the settling it started.
_SETTLE_WAIT_S = 120.0

_TURN_RUNNING = ("turn_running", 409, "Kazma is answering in this chat. Move it when it is done.")


def _snapshot_in(space: str, by: str, session_id: str) -> Any:
    """The chat as it is now, a copy of its own (blocking)."""
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        return get_session_manager().snapshot(session_id)


def _row_in(space: str, by: str, session_id: str) -> bool:
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        return get_session_manager().get(session_id) is not None


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


def _pending_gate_rows(thread_id: str) -> int:
    """The gate registry's questions still open on the thread (none when the
    registry is switched off: the checkpoint is then the only truth)."""
    from kazma_core.safety import hitl_gates

    if not hitl_gates.gate_registry_enabled():
        return 0
    return len(hitl_gates.live_gates(thread_id))


async def _quiet(graph: Any, thread_id: str) -> dict[str, Any]:
    """The chat's state, or a refusal while something of it is unfinished:

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
    return dict((snap.values if snap is not None else None) or {})


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
    from kazma_ui.live_connections import chat_gone
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
        values = await _quiet(graph, thread)
        attempt = uuid.uuid4().hex
        entry = await asyncio.to_thread(_entry, session, session_id, from_space, to_space, by, taken_out)
        await asyncio.to_thread(chat_moves.record_move, attempt, entry)
        try:
            rows = _rows_to_copy(session, by)
            await _copy(rows, values, entry, graph)
            moved = await asyncio.to_thread(_hand_over, attempt, entry, session.messages, rows)
        except (ChatMoveRefused, *_STEP_FAILURES):
            # It may have stopped after the handover (the row's delete
            # failed): then the chat is in the target, and settling went on.
            if await _settle_one(attempt, checkpoints) not in _MOVED:
                raise
            moved = True
        else:
            # Not handed over: a message landed while the chat was copied,
            # and settling takes the copy away. Nothing moved.
            await _settle_one(attempt, checkpoints)
        if not moved:
            raise ChatMoveRefused(*_TURN_RUNNING)
        _drop_frames(thread)
        chat_gone(session_id=session_id, thread_id=thread, space=from_space)
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
    return {"thread": thread, "session": session_id, "source": from_space, "target": to_space, "by": by,
            "new_session": new_session, "new_thread": new_thread, "taken_out": taken_out,
            "kept_out": bool(kept_out), "title": str(session.title or ""), "stage": "copying",
            "at": time.time()}


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


async def _copy(rows: list[dict[str, Any]], values: dict[str, Any], entry: dict[str, Any], graph: Any) -> None:
    """Everything of the copy but its row (:func:`_hand_over` stores that):
    nobody sees any of it yet."""
    from kazma_ui.thread_rewind import carry_conversation

    await asyncio.to_thread(_record_copy, entry)
    await carry_conversation(graph, values, entry["new_thread"], space=entry["target"], by=entry["by"])
    await asyncio.to_thread(_copy_files, rows, entry)


def _record_copy(entry: dict[str, Any]) -> None:
    """The new thread's record, first: whatever is written under it is the
    target's. And the source's "Don't remember this chat", before the copy
    holds anything memory could read."""
    from kazma_core.memory import forget

    with bind_scope(entry["target"], entry["by"]):
        threads.record_new_thread(entry["new_thread"])
        if entry["kept_out"]:
            forget.set_chat_remembered(entry["new_session"], False, tenant_id=entry["target"],
                                       keys=[entry["new_session"], entry["new_thread"]])


def _copy_files(rows: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    from kazma_core.chat_files import copy_shared_files

    try:
        copy_shared_files(json.dumps(rows, ensure_ascii=False, default=str), thread_id=entry["thread"],
                          new_thread=entry["new_thread"], from_space=entry["source"],
                          to_space=entry["target"], by=entry["by"])
    except SpaceOverLimit as exc:
        raise ChatMoveRefused("space_limit", 507, "The new space has no room for this chat's files.") from exc


def _hand_over(attempt: str, entry: dict[str, Any], messages: list[dict[str, Any]],
               rows: list[dict[str, Any]]) -> bool:
    """The copy's row in, the journal's ``copied``, the chat's row out: one
    step (``SessionManager.hand_over``). False: the chat changed since it was
    copied, or the move was settled meanwhile; nothing moved."""
    from kazma_ui.session_manager import ChatSession, get_session_manager

    moved = ChatSession(session_id=entry["new_session"], thread_id=entry["new_thread"],
                        tenant_id=entry["target"], messages=copy.deepcopy(rows), copied=len(rows))
    with bind_scope(entry["source"], entry["by"]):
        return get_session_manager().hand_over(
            entry["session"], messages=messages, to=moved,
            commit=lambda: chat_moves.advance_move(attempt, at="copying", to="copied"))


def _drop_frames(thread: str) -> None:
    """The thread's journaled frames go with the chat: they hold what its
    turns were shown."""
    from kazma_ui.delivery import get_turn_broker

    get_turn_broker().clear(thread)


# ── The delete here: after the handover, one step at a time ──────────────


def _remove_row(space: str, by: str, session_id: str) -> None:
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        get_session_manager().delete(session_id)


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


def _forget_where_it_was(entry: dict[str, Any]) -> None:
    """A shared space a chat was taken out of forgets it: its memories, the
    agent's notes, its summaries and the lessons it taught
    (``memory.forget.forget_chat``: journaled first, and the forget
    convergence finishes one that fails). Its old keys are kept out first: a
    turn still waiting to be written is not. A personal space keeps what it
    remembers: a move never touches it."""
    from kazma_core.memory import forget

    keys = [entry["session"], entry["thread"]]
    for out in (forget.set_chat_remembered(entry["session"], False, tenant_id=entry["source"], keys=keys),
                forget.forget_chat(entry["session"], tenant_id=entry["source"], keys=keys)):
        if not out.get("ok"):
            raise RuntimeError(f"the shared space did not forget the chat: {out.get('error')}")


def _remove_files(entry: dict[str, Any], space: str, thread: str) -> None:
    from kazma_core.chat_files import remove_shared_files

    remove_shared_files(thread, space=space, by=entry["by"])


_Step = Callable[[dict[str, Any], Any], Awaitable[Any]]


def _in_thread(step: Callable[[dict[str, Any]], Any]) -> _Step:
    async def run(entry: dict[str, Any], _checkpoints: Any) -> Any:
        return await asyncio.to_thread(step, entry)

    return run


async def _remains(space: str, session: str, thread: str, checkpoints: Any) -> None:
    """Remove what a chat leaves beside its row in *space*. The thread's
    record goes last, so a thread recorded elsewhere (or nowhere) has nothing
    left there."""
    from kazma_ui.chat_delete import remove_chat_remains

    if await asyncio.to_thread(threads.thread_home, thread) != space:
        return
    if not await remove_chat_remains(session, thread, checkpoints):
        raise RuntimeError("something of a chat could not be deleted")


async def _remove_remains(entry: dict[str, Any], checkpoints: Any) -> None:
    await _remains(entry["source"], entry["session"], entry["thread"], checkpoints)


#: What deletes the chat here once its copy stands, in order, each on its own.
#: Its row went with the handover; asked again, it is gone.
_STEPS: tuple[tuple[str, _Step], ...] = (
    ("chat", _in_thread(lambda e: _remove_row(e["source"], e["by"], e["session"]))),
    ("pointers", _in_thread(lambda e: _drop_pointers(e["thread"]))),
    ("memory", _in_thread(lambda e: _forget_where_it_was(e))),
    ("line", _in_thread(lambda e: chat_moves.record_taken_out(
        space_id=e["source"], thread_id=e["thread"], title=str(e.get("title") or ""), by=e["by"],
        since=float(e.get("at") or 0.0)))),
    ("files", _in_thread(lambda e: _remove_files(e, e["source"], e["thread"]))),
    ("remains", _remove_remains),
)
#: The steps of a chat taken out of a shared space alone.
_TAKEN_OUT_STEPS = frozenset({"memory", "line"})


async def _finish(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """Every step not done yet (:data:`_STEPS`), each on its own: one that
    fails is said and tried again later, and the others run. True when the
    move is finished (its journal entry is gone)."""
    done = [str(s) for s in entry.get("done") or []]
    left: list[str] = []
    with bind_scope(entry["source"], entry["by"]):
        for name, step in _STEPS:
            if name in done or (name in _TAKEN_OUT_STEPS and not entry.get("taken_out")):
                continue
            try:
                await step(entry, checkpoints)
                await asyncio.to_thread(chat_moves.record_move, attempt, {"done": [*done, name]})
                done.append(name)
            except _STEP_FAILURES:
                logger.warning("[spaces] a chat move's step %s failed; it is tried again", name, exc_info=True)
                left.append(name)
    if left:
        return False
    try:
        await asyncio.to_thread(chat_moves.settle_move, attempt)
    except _STEP_FAILURES:
        logger.warning("[spaces] a finished chat move was not settled; it is tried again", exc_info=True)
        return False
    return True


# ── Settling: at start, by the maintenance sweep, by the next move ───────


def _copy_stands(entry: dict[str, Any]) -> bool:
    """Whether a move whose journal says ``copied`` stands. It does not when
    the copy is not there and the chat still is: the journal's write landed
    and its answer was lost, so the handover took the copy back."""
    if "chat" in (entry.get("done") or []) or _row_in(entry["target"], entry["by"], entry["new_session"]):
        return True
    return not _row_in(entry["source"], entry["by"], entry["session"])


async def _put_back(attempt: str, entry: dict[str, Any], checkpoints: Any) -> None:
    """Take the copy away: whatever the new ids have, in the target."""
    from kazma_core.memory import forget

    target, by = entry["target"], entry["by"]
    await asyncio.to_thread(_remove_row, target, by, entry["new_session"])
    await asyncio.to_thread(_remove_files, entry, target, entry["new_thread"])
    if entry.get("kept_out"):
        await asyncio.to_thread(forget.set_chat_remembered, entry["new_session"], True, tenant_id=target,
                                keys=[entry["new_session"], entry["new_thread"]])
    with bind_scope(target, by):
        await _remains(target, entry["new_session"], entry["new_thread"], checkpoints)
    await asyncio.to_thread(chat_moves.settle_move, attempt)


#: What settling answers for a move that stands.
_MOVED = ("copied", "finished")


async def _settle_one(attempt: str, checkpoints: Any) -> str:
    """Settle one move, its thread's lock held: ``copying`` is put back,
    ``copied`` goes on. What cannot be settled now keeps its entry, and is
    settled later. Returns ``put_back``, ``copied``, ``finished``,
    ``dropped`` or ``waiting``."""
    try:
        entry = (await asyncio.to_thread(chat_moves.moves_under_way)).get(attempt)
        if entry is None:
            return "dropped"
        if not all(entry.get(k) for k in ("thread", "target", "source", "session", "by", "new_session",
                                          "new_thread")):
            await asyncio.to_thread(chat_moves.settle_move, attempt)
            return "dropped"
        # The move's own handover and this never both decide.
        if entry.get("stage") == "copying" and not await asyncio.to_thread(
                chat_moves.advance_move, attempt, at="copying", to="putting_back"):
            entry = (await asyncio.to_thread(chat_moves.moves_under_way)).get(attempt) or entry
        if entry.get("stage") == "copied" and await asyncio.to_thread(_copy_stands, entry):
            return "finished" if await _finish(attempt, entry, checkpoints) else "copied"
        await _put_back(attempt, entry, checkpoints)
        return "put_back"
    except _STEP_FAILURES:
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
        except _STEP_FAILURES:
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
