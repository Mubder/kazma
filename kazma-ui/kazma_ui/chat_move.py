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
  ``already_there``, ``turn_running``, ``approval_pending``,
  ``move_in_progress``, ``moves_today``, ``moved_today``,
  ``identity_unavailable``.
* ``GET /api/chat/taken-out`` -- the lines the bound space keeps for the
  chats taken out of it: who (their name), when, the title each had.

The move (:func:`_move_chat`), under the thread's mutation lock
(``thread_locks``), with no turn running or waiting:

1. **journal** the attempt (``chat_moves.record_move``, stage ``copying``);
2. **copy** the chat row into the target space (the person's own messages
   that name no author are stamped theirs when it goes into a shared space:
   it was their personal space) and the checkpoints into the target's file
   (``CheckpointManager.acopy_thread``; nothing on one shared table);
3. **switch**: the thread's record names the target (``threads.move_thread``;
   stage ``switched``; the move counts toward the daily bounds). From here
   the chat is in the target, and the memory writers refuse the source;
4. **finish**: the source's chat row and checkpoints go, the rows that name
   the thread beside a space follow it (``chat_moves.move_thread_rows``),
   the chat apps' pointers to it go, memory follows it
   (``memory.chat_move.move_chat_memory``), and the shared space keeps its
   line when the chat was taken out; the journal entry goes.

A failure before the switch takes the copies back (the chat stays where it
was); a failure after it is finished at the next start
(:func:`settle_chat_moves`), as is a move a stop cut off at any stage.
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
    refusal. Blocking."""
    refused, taken_out = chat_moves.move_refusal(from_space=from_space, to_space=to_space, by=by)
    if refused is not None:
        raise refused
    session = _session_in(from_space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    thread = _thread_of(session, session_id)
    if threads.thread_home(thread) != from_space:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    if _opener(thread, from_space) != by:
        raise ChatMoveRefused("not_your_chat", 403, "Only the person who started a chat moves it.")
    if taken_out and chat_moves.others_wrote(session.messages, by):
        # Their words are the shared space's (the plan's S2-12 row, decided).
        raise ChatMoveRefused("others_wrote", 409,
                              "Others wrote in this chat, so it stays in this space. Start a new "
                              "private chat in your own space instead.")
    if _session_in(to_space, by, session_id) is not None:
        raise ChatMoveRefused("already_there", 409, "A chat of that id is already in that space.")
    bound = chat_moves.bound_refusal(thread, by)
    if bound is not None:
        raise bound
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
    """What memory holds of the chat follows it (``memory.chat_move``)."""
    from kazma_core.memory.chat_move import move_chat_memory

    out = move_chat_memory(
        str(entry["session"]), from_space=str(entry["source"]), to_space=str(entry["target"]),
        mover=str(entry["by"]), keys=[k for k in (entry.get("session"), entry.get("thread")) if k],
    )
    if not out.get("ok"):
        raise RuntimeError(f"the moved chat's memory did not follow it: {out.get('error')}")
    return out


async def _card_waits(graph: Any, thread_id: str) -> bool:
    if graph is None:
        return False
    from kazma_core.safety.commitment.resume import pending_interrupt

    card, _ = await pending_interrupt(graph, {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    return card is not None


async def _move_chat(*, session_id: str, from_space: str, to_space: str, by: str,
                    graph: Any = None, checkpoints: Any = None) -> dict[str, Any]:
    """Move *by*'s chat *session_id* from *from_space* to *to_space*.
    Raises :class:`ChatMoveRefused`; anything else that stops it before the
    switch leaves the chat where it was."""
    session, taken_out = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
    thread = _thread_of(session, session_id)
    from kazma_ui.active_turns import is_turn_running
    from kazma_ui.thread_locks import thread_mutation

    async with thread_mutation(thread):
        if is_turn_running(thread):
            raise ChatMoveRefused("turn_running", 409, "Kazma is answering in this chat. Move it when it is done.")
        if await _card_waits(graph, thread):
            raise ChatMoveRefused("approval_pending", 409,
                                  "This chat waits for an approval. Answer it, then move the chat.")
        attempt = uuid.uuid4().hex
        turns = sum(1 for m in session.messages if m.get("role") == "user")
        entry = {"thread": thread, "session": session_id, "source": from_space, "target": to_space,
                 "by": by, "taken_out": taken_out, "title": str(session.title or ""), "turns": turns,
                 "stage": "copying", "at": time.time()}
        await asyncio.to_thread(chat_moves.record_move, attempt, entry)
        switched = False
        try:
            await asyncio.to_thread(_copy_row, session, to_space, by, taken_out=taken_out)
            if checkpoints is not None:
                await checkpoints.acopy_thread(thread, from_space=from_space, to_space=to_space)
            await asyncio.to_thread(_switch, attempt, entry)
            switched = True
        finally:
            if not switched:
                await _put_back(entry, checkpoints)
                await asyncio.to_thread(chat_moves.settle_move, attempt)
        await _finish(attempt, {**entry, "stage": "switched"}, checkpoints)
    logger.info("[spaces] %s moved a chat from %s to %s (taken out: %s)", by, from_space, to_space, taken_out)
    return {"session_id": session_id, "space": to_space, "taken_out": taken_out, "turns": turns}


def _switch(attempt: str, entry: dict[str, Any]) -> None:
    threads.move_thread(entry["thread"], entry["target"])
    chat_moves.record_move(attempt, {"stage": "switched"})
    chat_moves.record_done(thread_id=entry["thread"], by=entry["by"], from_space=entry["source"],
                           to_space=entry["target"])


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
            await _put_back(entry, checkpoints)
            await asyncio.to_thread(chat_moves.settle_move, attempt)
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
        return JSONResponse({"ok": True, **moved})

    @router.get("/api/chat/taken-out")
    def taken_out_route() -> JSONResponse:
        from kazma_core.spaces.scope import require_space_id, spaces_enforced

        if not spaces_enforced():
            return JSONResponse({"ok": True, "lines": []})
        return JSONResponse({"ok": True, "lines": _lines(require_space_id())})

    return router
