"""Moving a chat between one's own space and a shared one (Spaces S2-12):
the move itself, its settling, and its routes.

**Moving a chat moves the conversation.** What Kazma remembered from it stays
where it was said, and from now on it remembers what is said in the new
space. Taking a chat out of a shared space makes that space forget it.

* ``GET /api/chat/sessions/{session_id}/move`` -- what the dialog shows
  before the move: the chat's size in turns and whether others wrote in it.
* ``POST /api/chat/sessions/{session_id}/move`` -- ``{"to_space"}``: move
  the bound space's chat ``session_id`` to ``to_space``. Refusals are codes
  the page words (``chat.js``): ``spaces_off``, ``not_yours``,
  ``not_between_own_and_shared`` (``spaces.moves``), ``not_found``,
  ``not_your_chat``, ``others_wrote`` (the page offers "New private chat"),
  ``chat_app_chat`` (a chat app's chat stays in its space), ``already_there``,
  ``turn_running``, ``turn_unfinished`` (a restart cut a turn off half way:
  Continue or Close it first), ``approval_pending``, ``approval_unknown``
  (whether a card waits could not be read: refused, never guessed),
  ``move_in_progress``, ``space_limit`` (the files shared in the chat do not
  fit), ``move_failed``, ``identity_unavailable``.
* ``GET /api/chat/taken-out`` -- the lines the bound space keeps for the
  chats taken out of it: who (their name), when, the title each had.

The move (:func:`_move_chat`) takes the thread's mutation lock
(``thread_locks``) first and checks everything again under it -- where the
chat is, that it is not already there, no turn running or cut off, no card
waiting (the graph's checkpoint and the gate registry's pending rows; one
that cannot be read refuses) -- then:

1. **journal** the attempt (``chat_moves.record_move``, stage ``copying``);
2. **keep the old messages out of the target's memory**
   (``memory.forget.keep_moved_turns_out``, and the source's whole-chat
   keep-out), before anything of the chat is in the target;
3. **copy** the chat row into the target space without what its turns
   remembered (:func:`_moved_message`; the person's own messages that name no
   author are stamped theirs when it goes into a shared space: it was their
   personal space), the files shared in it (``chat_files.copy_shared_files``)
   and the newest state, cleaned (``CheckpointManager.amove_state``,
   ``chat_moves.moved_state``);
4. **switch**: the thread's record names the target (``threads.move_thread``;
   stage ``switched``) and its journal of frames is dropped. From here the
   chat is in the target: the memory writers refuse the source, and every
   socket and stream of the chat open in another space is closed
   (``live_connections.thread_moved``);
5. **finish** (:func:`_finish`, :data:`_STEPS`): the rows that name the
   thread beside a space follow it, the chat apps' pointers to it and its
   Time Travel steps go, a shared space it was taken out of forgets it
   (``memory.forget.forget_chat``, journaled, with its own retries) and keeps
   its line, and the source's files, checkpoints and chat row go -- each step
   on its own: one that fails does not hold the others.

A failure before the switch takes the copies back (the chat stays where it
was). A failure once the record names the target -- in the switch's own
bookkeeping too -- never drops the journal. What its request could not
settle is settled at the next start, by the maintenance sweep "chat moves"
and by the next move of the same chat (:func:`settle_chat_moves`).
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.db.pg_helpers import store_errors
from kazma_core.spaces import chat_moves, threads
from kazma_core.spaces.chat_moves import ChatMoveRefused
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope
from kazma_core.spaces.space_limits import SpaceOverLimit

from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.rate_limit import rate_limit

__all__ = ["create_chat_move_router", "settle_chat_moves", "settler"]

logger = logging.getLogger(__name__)

#: What a step of a move can fail with: any store's own errors -- Postgres's
#: among them (the move's re-review, N8) -- and an identity store that cannot
#: answer. Each step is idempotent and tried again.
_STEP_FAILURES: tuple[type[BaseException], ...] = (*store_errors(), IdentityUnavailable)

#: What reading a chat's checkpoint can raise: the store's errors and a state
#: that does not decode (either one means "not known").
_STATE_FAILURES: tuple[type[BaseException], ...] = (*store_errors(), LookupError, AttributeError)

#: How long the maintenance sweep waits for the settling it started.
_SETTLE_WAIT_S = 120.0


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
    refusal. Blocking; asked again under the thread's lock, with
    :func:`_not_there_yet`."""
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
    return session, taken_out


def _not_there_yet(session_id: str, to_space: str, by: str) -> None:
    """Refuse a move onto a chat of that id in the target: asked once what an
    earlier move left there was settled."""
    if _session_in(to_space, by, session_id) is not None:
        raise ChatMoveRefused("already_there", 409, "A chat of that id is already in that space.")


# ── What is copied, and what memory is kept out of ───────────────────────


def _moved_message(message: dict[str, Any]) -> dict[str, Any]:
    """A chat row's message as it moves: without the memory its turn was
    shown (the "Memory used" part and row), and without what its memory
    tools answered (the steps stay, empty)."""
    out = copy.deepcopy(message)
    if isinstance(out.get("parts"), list):
        out["parts"] = [_without_answer(p, p.get("name") or p.get("title")) if isinstance(p, dict) else p
                        for p in out["parts"] if not (isinstance(p, dict) and p.get("type") == "memory")]
    if isinstance(out.get("activity"), list):
        out["activity"] = [_without_answer(r, r.get("title")) if isinstance(r, dict) else r
                           for r in out["activity"] if not (isinstance(r, dict) and r.get("kind") == "memory")]
    return out


def _without_answer(row: dict[str, Any], tool: Any) -> dict[str, Any]:
    if "tool" not in (row.get("type"), row.get("kind")) or not chat_moves.is_memory_tool(tool):
        return row
    return {**row, **{field: "" for field in ("result", "detail") if field in row}}


def _copy_row(session: Any, to_space: str, by: str, *, taken_out: bool) -> None:
    """The chat row, in the target space (:func:`_moved_message`). Going into
    a shared space, the person's own messages that name no author are stamped
    theirs: it was their personal space, and in a shared space every message
    names its author (``reply_sink.authored``)."""
    from kazma_ui.session_manager import get_session_manager

    messages = [_moved_message(m) if isinstance(m, dict) else m for m in session.messages]
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


def _turns_of(messages: list[Any]) -> list[tuple[int, str]]:
    """``(number, question)`` of every message a person wrote, counted as the
    readers of the chat store count turns (``turn_reconcile._turns``,
    ``transcript_recall._without_turns``)."""
    from kazma_core.memory.chat_history import normalize_message
    from kazma_core.memory.consolidator import message_text

    asked = [m for m in (normalize_message(m) for m in messages) if m.get("role") == "user"]
    return [(number, message_text(m.get("content"))) for number, m in enumerate(asked, start=1)]


def _keep_out_of_memory(session: Any, entry: dict[str, Any]) -> None:
    """The target learns nothing from the messages the chat already holds:
    every turn is kept out of its memory, and so is the whole chat when the
    person keeps it out where it is now. Before the switch, and before the
    row is there for turn reconcile to read."""
    from kazma_core.memory import forget

    keys = [entry["session"], entry["thread"]]
    forget.keep_moved_turns_out(entry["session"], keys=keys, turns=_turns_of(session.messages),
                                tenant_id=entry["target"])
    whole, _turns = forget.forgotten_in_chat(entry["source"], keys)
    if whole:
        forget.set_chat_remembered(entry["session"], False, tenant_id=entry["target"], keys=keys)


def _forget_where_it_was(entry: dict[str, Any]) -> None:
    """A shared space a chat was taken out of forgets it: its memories, the
    agent's notes, its summaries and the lessons it taught
    (``memory.forget.forget_chat``: journaled first, and the forget
    convergence finishes one that fails). A personal space keeps what it
    remembers: a move never touches it."""
    from kazma_core.memory import forget

    out = forget.forget_chat(entry["session"], tenant_id=entry["source"], keys=[entry["session"], entry["thread"]])
    if not out.get("ok"):
        raise RuntimeError(f"the shared space did not forget the chat: {out.get('error')}")
    # A value's name on the memory map outlives its fact where the two are
    # spelled apart ("Lisbon", `lisbon`).
    forget.retire_names_left(entry["source"])


def _copy_files(session: Any, entry: dict[str, Any]) -> None:
    from kazma_core.chat_files import copy_shared_files

    try:
        copy_shared_files(json.dumps(session.messages, ensure_ascii=False, default=str), thread_id=entry["thread"],
                          from_space=entry["source"], to_space=entry["target"], by=entry["by"])
    except SpaceOverLimit as exc:
        raise ChatMoveRefused("space_limit", 507, "The new space has no room for this chat's files.") from exc


def _remove_files(entry: dict[str, Any], space: str) -> None:
    from kazma_core.chat_files import remove_shared_files

    remove_shared_files(entry["thread"], space=space, by=entry["by"])


def _working_root(space: str, by: str) -> str:
    """Where work in *space* runs: the moved state's root (``/compact`` reads
    it before the chat's next turn captures its own). "" when it cannot be
    worked out: the state then names none."""
    from kazma_core.workspace.binding import resolve_active_root

    try:
        with bind_scope(space, by):
            return str(resolve_active_root())
    except _STEP_FAILURES:
        logger.debug("[spaces] the working folder of %s could not be read", space, exc_info=True)
        return ""


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


def _forget_steps(keys: list[str]) -> int:
    from kazma_core.time_travel import forget_thread_snapshots

    return forget_thread_snapshots(keys)


def _drop_frames(thread: str) -> None:
    """The thread's journaled frames go: they hold what its turns were shown
    (``memory_explain``, the tools' answers), which a member of the target
    attaching would be replayed (the move's re-review, N3)."""
    from kazma_ui.delivery import get_turn_broker

    get_turn_broker().clear(thread)


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


async def _quiet(graph: Any, thread_id: str) -> None:
    """Refuse the move while something of the chat is unfinished:

    * a card waits on it -- an interrupt in its checkpoint, or an open row in
      the gate registry: it must never be left in the space the chat left;
    * a turn was cut off half way (a next step and no card): its tools'
      receipts are kept under the space (§30 G), so in the target Continue
      would find none and run a finished tool again (the move's re-review,
      N6: an email went out twice).

    Either that cannot be read refuses too (the chat move review's M5)."""
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


# ── The move ─────────────────────────────────────────────────────────────


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
        # An earlier move of this chat that did not end: settled now, so it
        # never holds the chat (the move's re-review, N8).
        await _settle_thread(thread, checkpoints)
        # Everything again, under the lock (the chat move review's M4): a
        # move or a turn that ended while this one waited for it.
        session, taken_out = await asyncio.to_thread(_prepare, session_id, from_space, to_space, by)
        await asyncio.to_thread(_not_there_yet, session_id, to_space, by)
        if is_turn_running(thread):
            raise ChatMoveRefused("turn_running", 409, "Kazma is answering in this chat. Move it when it is done.")
        await _quiet(graph, thread)
        attempt = uuid.uuid4().hex
        turns = len(_turns_of(session.messages))
        entry: dict[str, Any] = {"thread": thread, "session": session_id, "source": from_space,
                                 "target": to_space, "by": by, "taken_out": taken_out,
                                 "title": str(session.title or ""), "stage": "copying", "at": time.time()}
        await asyncio.to_thread(chat_moves.record_move, attempt, entry)
        # ``None`` while it runs: a stop of any kind (a refusal, a cancel)
        # settles it in ``finally``.
        outcome: str | None = None
        try:
            await _copy(session, entry, checkpoints)
            _drop_frames(thread)
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
        _drop_frames(thread)
        thread_moved({session_id, thread}, to_space)
        await _finish(attempt, {**entry, "stage": "switched"}, checkpoints)
    logger.info("[spaces] %s moved a chat from %s to %s (taken out: %s)", by, from_space, to_space, taken_out)
    return {"session_id": session_id, "space": to_space, "taken_out": taken_out, "turns": turns}


async def _copy(session: Any, entry: dict[str, Any], checkpoints: Any) -> None:
    """Everything the target needs, before the switch. The keep-out first:
    nothing of the chat is in the target without it."""
    await asyncio.to_thread(_keep_out_of_memory, session, entry)
    await asyncio.to_thread(_copy_files, session, entry)
    await asyncio.to_thread(_copy_row, session, entry["target"], entry["by"], taken_out=entry["taken_out"])
    if checkpoints is None:
        return
    root = await asyncio.to_thread(_working_root, entry["target"], entry["by"])
    await checkpoints.amove_state(
        entry["thread"], from_space=entry["source"], to_space=entry["target"],
        clean=lambda values: chat_moves.moved_state(values, to_space=entry["target"], workspace_root=root))


def _switch(attempt: str, entry: dict[str, Any]) -> None:
    threads.move_thread(entry["thread"], entry["target"])
    chat_moves.record_move(attempt, {"stage": "switched"})


async def _settle_failure(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """A move that stopped: True when the chat's record names the target
    (switched after all: the journal stays, the finish runs). Else its copies
    go back and its journal entry goes -- once they went. What cannot be
    settled now keeps its entry, and is settled later."""
    try:
        home = await asyncio.to_thread(threads.thread_home, entry["thread"])
        if home == entry["target"]:
            return True
        await asyncio.to_thread(_remove_row, entry["target"], entry["by"], entry["session"])
        await asyncio.to_thread(_remove_files, entry, entry["target"])
        if checkpoints is not None:
            await checkpoints.adelete_thread_in(entry["thread"], space=entry["target"])
        await asyncio.to_thread(chat_moves.settle_move, attempt)
    except _STEP_FAILURES:
        logger.warning("[spaces] a chat move that stopped was not settled; it is tried again", exc_info=True)
    return False


# ── The finish: after the switch, one step at a time ─────────────────────

_Step = Callable[[dict[str, Any], Any], Awaitable[Any]]


def _in_thread(step: Callable[[dict[str, Any]], Any]) -> _Step:
    async def run(entry: dict[str, Any], _checkpoints: Any) -> Any:
        return await asyncio.to_thread(step, entry)

    return run


async def _remove_state(entry: dict[str, Any], checkpoints: Any) -> None:
    if checkpoints is not None:
        await checkpoints.adelete_thread_in(entry["thread"], space=entry["source"])


#: What finishes a move once the record names the target, in order, each on
#: its own. The source's chat row goes last (the move's re-review, N8: it
#: went first, and its failure stopped everything after it).
_STEPS: tuple[tuple[str, _Step], ...] = (
    ("rows", _in_thread(lambda e: chat_moves.move_thread_rows(e["thread"], from_space=e["source"],
                                                              to_space=e["target"]))),
    ("pointers", _in_thread(lambda e: _drop_pointers(e["thread"]))),
    # Its Time Travel steps do not move (the review's M1): kept by thread
    # alone, they would follow it with the source's state.
    ("steps", _in_thread(lambda e: _forget_steps([e["session"], e["thread"]]))),
    ("memory", _in_thread(lambda e: _forget_where_it_was(e))),
    ("line", _in_thread(lambda e: chat_moves.record_taken_out(
        space_id=e["source"], thread_id=e["thread"], title=str(e.get("title") or ""), by=e["by"],
        since=float(e.get("at") or 0.0)))),
    ("files", _in_thread(lambda e: _remove_files(e, e["source"]))),
    ("state", _remove_state),
    ("chat", _in_thread(lambda e: _remove_row(e["source"], e["by"], e["session"]))),
)
#: The steps of a chat taken out of a shared space alone.
_TAKEN_OUT_STEPS = frozenset({"memory", "line"})


async def _finish(attempt: str, entry: dict[str, Any], checkpoints: Any) -> bool:
    """Every step not done yet (:data:`_STEPS`), each on its own: one that
    fails is said and tried again later, and the others run. True when the
    move is finished (its journal entry is gone)."""
    done = [str(s) for s in entry.get("done") or []]
    left: list[str] = []
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


async def _settle_one(attempt: str, entry: dict[str, Any], checkpoints: Any) -> str:
    """Settle one move, its thread's lock held: before its switch its copies
    go back, after it the finish runs."""
    if not all(entry.get(k) for k in ("thread", "target", "source", "session", "by")):
        await asyncio.to_thread(chat_moves.settle_move, attempt)
        return "dropped"
    if await _settle_failure(attempt, entry, checkpoints):
        return "finished" if await _finish(attempt, entry, checkpoints) else "waiting"
    left = attempt in await asyncio.to_thread(chat_moves.moves_under_way)
    return "waiting" if left else "put_back"


async def _settle_thread(thread: str, checkpoints: Any) -> None:
    """Settle what an earlier move of *thread* left (its lock is held, so no
    move of it runs)."""
    for attempt, entry in (await asyncio.to_thread(chat_moves.moves_under_way)).items():
        if entry.get("thread") == thread:
            await _settle_one(attempt, entry, checkpoints)


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
                now = (await asyncio.to_thread(chat_moves.moves_under_way)).get(attempt)
                if now is None:
                    continue
                outcome = await _settle_one(attempt, now, checkpoints)
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
    session = _session_in(space, by, session_id)
    if session is None:
        raise ChatMoveRefused("not_found", 404, "No such chat in this space.")
    return {"ok": True, "turns": len(_turns_of(session.messages)),
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
