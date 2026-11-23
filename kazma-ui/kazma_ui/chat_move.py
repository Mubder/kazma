"""Moving a chat between one's own space and a shared one (Spaces S2-12):
the move itself, its settling at start, and its routes.

* ``GET /api/chat/sessions/{session_id}/move`` -- what the dialog shows
  before the move: the chat's size in turns, whether others wrote in it,
  and whether the daily bounds let the person move it now.
* ``POST /api/chat/sessions/{session_id}/move`` -- ``{"to_space"}``: move
  the bound space's chat ``session_id`` to ``to_space``. Refusals are codes
  the page words (``chat.js``): ``spaces_off``, ``not_yours``,
  ``not_between_own_and_shared`` (``spaces.moves``), ``not_found``,
  ``not_your_chat``, ``others_wrote`` (the page offers "New private chat"),
  ``chat_app_chat`` (a chat app's chat stays in its space), ``already_there``,
  ``turn_running``, ``approval_pending``, ``approval_unknown`` (whether a card
  waits could not be read: refused, never guessed), ``move_in_progress``,
  ``moves_today``, ``moved_today``, ``move_failed``, ``identity_unavailable``.
* ``GET /api/chat/taken-out`` -- the lines the bound space keeps for the
  chats taken out of it: who (their name), when, the title each had.

The move (:func:`_move_chat`) takes the thread's mutation lock
(``thread_locks``) first and checks everything again under it -- where the
chat is, that it is not already there, no turn running and no card waiting
(the graph's checkpoint and the gate registry's pending rows; one that
cannot be read refuses) -- then:

1. **journal** the attempt (``chat_moves.record_move``, stage ``copying``)
   and **claim** its place in the daily bounds, checked and recorded in one
   write (``chat_moves.claim_move``);
2. **copy** the chat row into the target space (the person's own messages
   that name no author are stamped theirs when it goes into a shared space:
   it was their personal space) and the checkpoints into the target's file,
   without their turn notes (``CheckpointManager.acopy_thread``; nothing on
   one shared table);
3. **switch**: the thread's record names the target (``threads.move_thread``;
   stage ``switched``). From here the chat is in the target: the memory
   writers refuse the source, and every socket and stream of the chat open
   in another space is closed (``live_connections.thread_moved``);
4. **finish**: the source's chat row and checkpoints go, the rows that name
   the thread beside a space follow it (``chat_moves.move_thread_rows``),
   the chat apps' pointers to it go, its Time Travel steps are deleted (they
   do not move: the dialog says so), memory's part is queued as a durable
   task (``chat_move_memory``, ``memory.chat_move.move_chat_memory``), and
   the shared space keeps its line when the chat was taken out; the journal
   entry goes. The route answers then: memory follows in the background.

A failure before the switch takes the copies and the claim back (the chat
stays where it was). A failure once the record names the target -- in the
switch's own bookkeeping too -- never drops the journal: the finish runs at
once, or at the next start (:func:`settle_chat_moves`), as for a move a stop
cut off at any stage.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.spaces import chat_moves, threads
from kazma_core.spaces.chat_moves import ChatMoveRefused
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.rate_limit import rate_limit

__all__ = ["create_chat_move_router", "settle_chat_moves"]

logger = logging.getLogger(__name__)

#: What the move's own failures can be (each step is idempotent and the next
#: start finishes it).
_STEP_FAILURES = (sqlite3.Error, OSError, RuntimeError, ValueError, IdentityUnavailable)


def _session_in(space: str, by: str, session_id: str) -> Any:
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        return get_session_manager().get(session_id)


def _thread_of(session: Any, session_id: str) -> str:
    return str(getattr(session, "thread_id", "") or session_id)


def _opener(thread: str, from_space: str) -> str:
    """Who opened the chat: its record's opener; a chat from before Spaces
    (no record) is the owner's, in the owner's space only (N19)."""
    record = threads.record_of(thread)
    if record is not None:
        return record.opener
    return OWNER_ACCOUNT_ID if from_space == OWNER_SPACE_ID else ""


def _prepare(session_id: str, from_space: str, to_space: str, by: str) -> tuple[Any, bool]:
    """The chat and whether the move takes it out of a shared space, or a
    refusal. Blocking; asked again under the thread's lock."""
    refused, taken_out = chat_moves.move_refusal(from_space=from_space, to_space=to_space, by=by)
    if refused is not None:
        raise refused
    session = _session_in(from_space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    thread = _thread_of(session, session_id)
    if threads.thread_home(thread) != from_space:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    if chat_moves.is_chat_app_chat(session_id, thread):
        raise ChatMoveRefused("chat_app_chat", 409, "This chat is a chat app's, so it stays in its space.")
    if _opener(thread, from_space) != by:
        raise ChatMoveRefused("not_your_chat", 403, "Only the person who started a chat moves it.")
    if taken_out and chat_moves.others_wrote(session.messages, by):
        # Their words are the shared space's (the plan's S2-12 row, decided).
        raise ChatMoveRefused("others_wrote", 409,
                              "Others wrote in this chat, so it stays in this space. Start a new "
                              "private chat in your own space instead.")
    if _session_in(to_space, by, session_id) is not None:
        raise ChatMoveRefused("already_there", 409, "A chat of that id is already in that space.")
    return session, taken_out


def _copy_row(session: Any, to_space: str, by: str, *, taken_out: bool) -> None:
    """The chat row, in the target space. Going into a shared space, the
    person's own messages that name no author are stamped theirs: it was
    their personal space, and in a shared space every message names its
    author (``reply_sink.authored``)."""
    from kazma_ui.session_manager import get_session_manager

    messages = copy.deepcopy(list(session.messages))
    if not taken_out:
        for message in messages:
            if message.get("role") == "user" and not message.get("author"):
                message["author"] = by
    moved = dataclasses.replace(session, tenant_id=to_space, messages=messages)
    with bind_scope(to_space, by):
        get_session_manager().put(moved)


def _remove_row(space: str, by: str, session_id: str) -> None:
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space, by):
        get_session_manager().delete(session_id)


def _drop_pointers(thread_id: str) -> int:
    """The chat apps' pointers to the thread (``active_thread.<sender>``)
    go: a chat app's next message starts a chat of its own in its space,
    never the moved one. Blocking."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    dropped = 0
    # The pointers are the "session" category's (sessions.directory).
    for key, value in store.get_category("session").items():
        if str(key).startswith("active_thread.") and str(value or "") == thread_id:
            store.delete(key)
            dropped += 1
    return dropped


def _memory_follows(entry: dict[str, Any]) -> dict[str, Any]:
    """What memory holds of the chat follows it: queued as a durable task
    (``chat_move_memory``, which the memory queue retries), so the route
    answers once the chat is switched (the chat move review's M6). Raises
    when the queue refuses it: the next start queues it again."""
    from kazma_core.memory.task_queue import CHAT_MOVE_MEMORY, enqueue_task

    payload = {"session": str(entry["session"]), "thread": str(entry.get("thread") or ""),
               "source": str(entry["source"]), "target": str(entry["target"]), "by": str(entry["by"])}
    if not enqueue_task(CHAT_MOVE_MEMORY, payload):
        raise RuntimeError("the moved chat's memory could not be queued")
    return {"ok": True, "queued": True}


_APPROVAL_UNKNOWN = ("approval_unknown", 503,
                     "Kazma could not check whether this chat waits for an approval. Try again in a moment.")
_APPROVAL_PENDING = ("approval_pending", 409, "This chat waits for an approval. Answer it, then move the chat.")


def _pending_gate_rows(thread_id: str) -> int:
    """The gate registry's questions still open on the thread (none when the
    registry is switched off: the checkpoint is then the only truth)."""
    from kazma_core.safety import hitl_gates

    if not hitl_gates.gate_registry_enabled():
        return 0
    return len(hitl_gates.live_gates(thread_id))


def _state_read_failures() -> tuple[type[BaseException], ...]:
    """What reading a chat's checkpoint can raise: the store's errors and a
    state that does not decode (either one means "not known")."""
    failures: tuple[type[BaseException], ...] = (
        sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, LookupError, AttributeError)
    try:
        import psycopg
    except ImportError:
        return failures
    return (*failures, psycopg.Error)


async def _no_card_waits(graph: Any, thread_id: str) -> None:
    """Refuse the move while a card waits on the chat: an interrupt in its
    checkpoint, or an open row in the gate registry. Either that cannot be
    read refuses too (the chat move review's M5): a card must never be left
    in the space a chat left."""
    if graph is None:
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN)
    try:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    except _state_read_failures() as exc:  # what a saver raises: unknown, so refused
        logger.warning("[spaces] whether a card waits on a chat could not be read; the move is refused",
                       exc_info=True)
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN) from exc
    if snap is not None and getattr(snap, "next", None):
        for task in getattr(snap, "tasks", []) or []:
            if getattr(task, "interrupts", None):
                raise ChatMoveRefused(*_APPROVAL_PENDING)
    try:
        waiting = await asyncio.to_thread(_pending_gate_rows, thread_id)
    except (sqlite3.Error, OSError) as exc:
        raise ChatMoveRefused(*_APPROVAL_UNKNOWN) from exc
    if waiting:
        raise ChatMoveRefused(*_APPROVAL_PENDING)


async def _move_chat(*, session_id: str, from_space: str, to_space: str, by: str,
                    graph: Any = None, checkpoints: Any = None) -> dict[str, Any]:
    """Move *by*'s chat *session_id* from *from_space* to *to_space*.
    Raises :class:`ChatMoveRefused`; anything else that stops it before the
    switch leaves the chat where it was."""
    session, _ = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
    thread = _thread_of(session, session_id)
    from kazma_ui.active_turns import is_turn_running
    from kazma_ui.live_connections import thread_moved
    from kazma_ui.thread_locks import thread_mutation

    async with thread_mutation(thread):
        # Everything again, under the lock (the chat move review's M4): a
        # move or a turn that ended while this one waited for it.
        session, taken_out = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
        if is_turn_running(thread):
            raise ChatMoveRefused("turn_running", 409, "Kazma is answering in this chat. Move it when it is done.")
        await _no_card_waits(graph, thread)
        attempt = uuid.uuid4().hex
        turns = sum(1 for m in session.messages if m.get("role") == "user")
        entry = {"thread": thread, "session": session_id, "source": from_space, "target": to_space,
                 "by": by, "taken_out": taken_out, "title": str(session.title or ""), "turns": turns,
                 "stage": "copying", "at": time.time()}
        await asyncio.to_thread(chat_moves.record_move, attempt, entry)
        # ``None`` while it runs: a stop of any kind (a refusal, a cancel)
        # settles it in ``finally``.
        outcome: str | None = None
        try:
            entry["claimed"] = await asyncio.to_thread(
                chat_moves.claim_move, thread_id=thread, by=by, from_space=from_space, to_space=to_space)
            await asyncio.to_thread(chat_moves.record_move, attempt, {"claimed": entry["claimed"]})
            await asyncio.to_thread(_copy_row, session, to_space, by, taken_out=taken_out)
            if checkpoints is not None:
                await checkpoints.acopy_thread(thread, from_space=from_space, to_space=to_space,
                                               strip_turn_notes=True)
            await asyncio.to_thread(_switch, attempt, entry)
            outcome = "switched"
        except _STEP_FAILURES:
            # Switched after all (the switch's own bookkeeping failed): the
            # chat is in the target, and the finish runs (the review's H2).
            outcome = "switched" if await _settle_failure(attempt, entry, checkpoints) else "put_back"
            if outcome == "put_back":
                raise
        finally:
            if outcome is None:
                await _settle_failure(attempt, entry, checkpoints)
        thread_moved({session_id, thread}, to_space)
        await _finish(attempt, {**entry, "stage": "switched"}, checkpoints)
    logger.info("[spaces] %s moved a chat from %s to %s (taken out: %s)", by, from_space, to_space, taken_out)
    return {"session_id": session_id, "space": to_space, "taken_out": taken_out, "turns": turns}


def _switch(attempt: str, entry: dict[str, Any]) -> None:
    threads.move_thread(entry["thread"], entry["target"])
    chat_moves.record_move(attempt, {"stage": "switched"})


async def _settle_failure(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """A move that stopped: True when the chat's record names the target
    (switched after all: the journal stays, the finish runs). Else its copies
    and its claim go back and its journal entry goes. When where it is
    cannot be read, nothing is settled: the next start does it."""
    try:
        home = await asyncio.to_thread(threads.thread_home, entry["thread"])
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat move stopped and where the chat is could not be read; "
                       "the next start settles it", exc_info=True)
        return False
    if home == entry["target"]:
        return True
    await _put_back(entry, checkpoints)
    try:
        if entry.get("claimed"):
            await asyncio.to_thread(chat_moves.release_move, thread_id=entry["thread"], by=entry["by"],
                                    at=float(entry["claimed"]))
        await asyncio.to_thread(chat_moves.settle_move, attempt)
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat move put back was not settled; the next start does it", exc_info=True)
    return False


async def _put_back(entry: dict[str, Any], checkpoints: Any) -> None:
    """Before the switch: the copies in the target go, so the chat is where
    it was. One that cannot go is said, and the next start tries again."""
    thread, session_id, target, by = entry["thread"], entry["session"], entry["target"], entry["by"]
    if await asyncio.to_thread(threads.thread_home, thread) == target:
        return  # switched after all: the finish takes it from here
    try:
        await asyncio.to_thread(_remove_row, target, by, session_id)
        if checkpoints is not None:
            await checkpoints.adelete_thread_in(thread, space=target)
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat move's copy could not be taken back; the next start does it",
                       exc_info=True)


async def _finish(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """After the switch: the source's copies go, the rows that name the
    thread beside a space follow it, the chat apps' pointers go, memory
    follows, and the line is written. Every step is idempotent: a failure is
    said, and the next start finishes it. True when it finished."""
    thread, session_id, source, target, by = (entry["thread"], entry["session"], entry["source"],
                                              entry["target"], entry["by"])
    try:
        await asyncio.to_thread(_remove_row, source, by, session_id)
        if checkpoints is not None:
            await checkpoints.adelete_thread_in(thread, space=source)
        await asyncio.to_thread(chat_moves.move_thread_rows, thread, from_space=source, to_space=target)
        await asyncio.to_thread(_drop_pointers, thread)
        if not entry.get("steps"):
            # Its Time Travel steps do not move (the review's M1): kept by
            # thread alone, they would follow it with the source's state.
            await asyncio.to_thread(_forget_steps, [session_id, thread])
            await asyncio.to_thread(chat_moves.record_move, attempt, {"steps": True})
        if not entry.get("memory"):
            await asyncio.to_thread(_memory_follows, entry)
            await asyncio.to_thread(chat_moves.record_move, attempt, {"memory": True})
        if entry.get("taken_out") and not entry.get("line"):
            await asyncio.to_thread(chat_moves.record_taken_out, space_id=source, thread_id=thread,
                                    title=str(entry.get("title") or ""), by=by)
            await asyncio.to_thread(chat_moves.record_move, attempt, {"line": True})
        await asyncio.to_thread(chat_moves.settle_move, attempt)
        return True
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat move was not finished; the next start finishes it", exc_info=True)
        return False


def _forget_steps(keys: list[str]) -> int:
    from kazma_core.time_travel import forget_thread_snapshots

    return forget_thread_snapshots(keys)


async def settle_chat_moves(checkpoints: Any = None) -> list[dict[str, str]]:
    """Settle every chat move a stop or a crash cut off: before its switch
    its copies in the target go (the chat is where it was); after it, the
    finish runs. Run at every start, before any move."""
    settled: list[dict[str, str]] = []
    for attempt, entry in sorted((await asyncio.to_thread(chat_moves.moves_under_way)).items()):
        thread, target = str(entry.get("thread") or ""), str(entry.get("target") or "")
        if not thread or not target or not entry.get("session") or not entry.get("by"):
            await asyncio.to_thread(chat_moves.settle_move, attempt)
            continue
        if await asyncio.to_thread(threads.thread_home, thread) == target:
            outcome = "finished" if await _finish(attempt, entry, checkpoints) else "waiting"
        else:
            await _settle_failure(attempt, entry, checkpoints)
            outcome = "put_back"
        settled.append({"attempt": attempt, "outcome": outcome})
        logger.info("[spaces] a chat move a stop cut off was settled: %s", outcome)
    return settled


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
    session = _session_in(space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    thread = _thread_of(session, session_id)
    bound = chat_moves.bound_refusal(thread, by)
    return {"ok": True, "turns": sum(1 for m in session.messages if m.get("role") == "user"),
            "others_wrote": chat_moves.others_wrote(session.messages, by),
            "bounded": bound.code if bound is not None else ""}


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
