"""Spaces S2-12: what a chat move decides from the two chats as they are --
right before the delete in the request, and at settle -- and the chat's own
delete.

The journal holds what was copied: how many messages, the last one's time,
the checkpoint. The chat unchanged: its copy stands and the delete goes on.
The chat changed and its copy not: the copy is taken away ("try again"). Both
changed: both are kept, and said so. Nothing is removed that is nowhere else.

The old thread never runs again (``spaces.threads``), and the delete is one
function that takes its ids from the journal and may run twice
(``kazma_ui.chat_delete``).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any

import pytest
from kazma_core.spaces import chat_moves, scope, threads
from kazma_ui import session_manager

from tests import _tt_graph as tt
from tests._chat_move_world import (  # noqa: F401  (world: a fixture)
    World,
    entries_of,
    files_of,
    handed_over,
    left,
    say,
    shared_file,
    world,
)


def _texts(w: World, space: str, session: str) -> list[str] | None:
    held = w.row(space, "reem", session)
    return None if held is None else [m["content"] for m in held.messages]


# ── the fingerprint: a move a stop cut off ────────────────────────────────


def _stopped(w: World, stage: str, mark: str, *, out: bool) -> dict[str, Any]:
    """A move a stop cut off at *stage*, in a new process."""
    if stage == "copied":
        _s, _t, entry = handed_over(w, "stopped", mark, out=out)
    else:
        _s, _t, entry = left(w, "stopped", mark, out=out)
        if stage == "row":  # the copy's row is in, and the journal does not say so
            from kazma_ui import chat_move

            held = w.row(entry["source"], "reem", entry["session"])
            with scope.bind_scope(entry["target"], w.ids["reem"]):
                session_manager.get_session_manager().put(session_manager.ChatSession(
                    session_id=entry["new_session"], thread_id=entry["new_thread"], tenant_id=entry["target"],
                    messages=chat_move._rows_to_copy(held, w.ids["reem"])))
    w.restart()
    return entry


_OLD, _NEW = "written here since", "written in the copy since"


@pytest.mark.parametrize("out", [False, True], ids=["shared", "taken-out"])
@pytest.mark.parametrize(("stage", "old", "new", "outcome"), [
    ("copying", False, False, "put_back"),
    ("copying", True, False, "put_back"),
    ("row", False, False, "put_back"),   # nobody saw it
    ("row", False, True, "finished"),    # its words are nowhere else
    ("row", True, False, "put_back"),
    ("row", True, True, "kept_both"),
    ("copied", False, False, "finished"),
    ("copied", False, True, "finished"),
    ("copied", True, False, "put_back"),
    ("copied", True, True, "kept_both"),
])
def test_what_settles_a_move_that_stopped(world, caplog, stage, old, new, outcome, out) -> None:
    w, i = world, world.ids
    entry = _stopped(w, stage, "stopped", out=out)
    source, target = entry["source"], entry["target"]
    if old:
        say(w, source, "reem", entry["session"], _OLD)
    if new:
        say(w, target, "reem", entry["new_session"], _NEW)
    with caplog.at_level(logging.WARNING, logger="kazma_ui.chat_move"):
        assert [s["outcome"] for s in w.settle()] == [outcome]
    assert chat_moves.moves_under_way() == {}, "its entry is dropped"
    here, there = _texts(w, source, entry["session"]), _texts(w, target, entry["new_session"])
    copied = ["question stopped", "answer stopped"]
    assert here == (None if outcome == "finished" else copied + ([_OLD] if old else []))
    assert there == (None if outcome == "put_back" else copied + ([_NEW] if new else []))
    assert (threads.record_of(entry["thread"]) is None) is (outcome == "finished")
    assert (threads.record_of(entry["new_thread"]) is None) is (outcome == "put_back")
    said = [r for r in caplog.records if "both are kept" in r.getMessage()]
    assert bool(said) is (outcome == "kept_both")
    # What stands is written in as any chat is.
    for space, session, texts in ((source, entry["session"], here), (target, entry["new_session"], there)):
        if texts is not None:
            say(w, space, "reem", session, "and more")
            assert _texts(w, space, session) == [*texts, "and more"]


def test_words_that_land_as_a_leftover_is_removed_are_kept(world, monkeypatch) -> None:
    """Compared in the removal itself, and the copy is written in as before."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    entry = _stopped(w, "row", "stopped", out=False)
    real, landed = chat_move._remove_copy_row, []

    def remove(entry_: dict[str, Any]) -> bool:
        if not landed:
            landed.append(1)
            say(w, i["family"], "reem", entry["new_session"], _NEW)
        return real(entry_)

    monkeypatch.setattr(chat_move, "_remove_copy_row", remove)
    assert [s["outcome"] for s in w.settle()] == ["finished"]
    say(w, i["family"], "reem", entry["new_session"], "and more")
    assert _texts(w, i["family"], entry["new_session"])[-2:] == [_NEW, "and more"]
    assert _texts(w, i["mine"], entry["session"]) is None


def test_a_copy_a_person_deleted_meanwhile_stays_deleted(world) -> None:
    """Once the copy stood, its delete is the person's: the delete here goes
    on, and the chat is not brought back where it was."""
    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)
    chat_moves.record_move("stopped", {"done": ["handed_over"]})
    answer = w.call("reem", "DELETE", f"/api/chat/sessions/{entry['new_session']}", space=i["family"])
    assert answer.status_code == 200, answer.text
    assert [s["outcome"] for s in w.settle()] == ["finished"]
    assert _texts(w, i["mine"], entry["session"]) is None
    assert _texts(w, i["family"], entry["new_session"]) is None


def test_a_chat_whose_move_is_still_settling_takes_nothing(world, monkeypatch) -> None:
    """After a restart, from the moment its copy is seen to stand: a message,
    a rename and a turn are refused, before the delete is reached."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)

    def fails(_entry: dict[str, Any]) -> None:
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_before_the_delete", fails)
        assert [s["outcome"] for s in w.settle()] == ["copied"]
    assert entry["session"] not in w.listed("reem", i["mine"])
    with scope.bind_scope(i["mine"], i["reem"]):
        with pytest.raises(session_manager.ChatDeleted):
            session_manager.get_session_manager().rename(entry["session"], "back")
        with pytest.raises(threads.ThreadEnded):
            threads.scope_for_turn(entry["thread"])
    assert [s["outcome"] for s in w.settle()] == ["finished"]


def test_control_a_settling_that_does_not_compare_loses_words(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)
    say(w, i["mine"], "reem", entry["session"], _OLD)
    monkeypatch.setattr(chat_move, "_unchanged", lambda _entry: lambda _row: True)
    assert [s["outcome"] for s in w.settle()] == ["finished"]
    assert _texts(w, i["mine"], entry["session"]) is None
    assert _OLD not in str(_texts(w, i["family"], entry["new_session"])), "and its words are nowhere"


def test_control_a_leftover_removed_whatever_it_holds_loses_words(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    entry = _stopped(w, "row", "stopped", out=False)
    say(w, i["family"], "reem", entry["new_session"], _NEW)
    monkeypatch.setattr(chat_move, "_still_the_copy", lambda _entry: lambda _row: True)
    assert [s["outcome"] for s in w.settle()] == ["put_back"]
    assert _texts(w, i["family"], entry["new_session"]) is None
    assert _NEW not in str(_texts(w, i["mine"], entry["session"])), "and its words are nowhere"


def test_a_last_message_of_another_time_is_another_chat(world) -> None:
    """The last message's time is compared beside the count: an edit, or an
    undo and a new question, leaves as many messages."""
    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)
    with scope.bind_scope(i["mine"], i["reem"]):
        store = session_manager.get_session_manager()
        held = store.get(entry["session"])
        held.messages[-1] |= {"content": "answered again", "ts": "2030-01-01T00:00:00+00:00"}
        store.put(held)
    assert [s["outcome"] for s in w.settle()] == ["put_back"]
    assert _texts(w, i["mine"], entry["session"]) == ["question stopped", "answered again"]


def _turn_on_the_checkpoint(w: World, space: str, thread: str) -> None:
    """A turn whose reply reached the checkpoint and not the row."""
    w.turn("reem", space, thread, "one more turn")


@pytest.mark.parametrize("compared", [True, False], ids=["compared", "control"])
def test_a_turn_that_reached_only_the_checkpoint_is_not_lost(world, monkeypatch, compared) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)
    _turn_on_the_checkpoint(w, i["mine"], entry["thread"])
    if not compared:
        async def unread(*_a: Any) -> None:
            return None

        monkeypatch.setattr(chat_move, "_old_checkpoint", unread)
    assert [s["outcome"] for s in w.settle()] == ["put_back" if compared else "finished"]
    assert bool(w.state("reem", i["mine"], entry["thread"])) is compared


def test_the_entry_names_the_checkpoint_it_copied(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="checkpoint")
    w.turn("reem", i["mine"], thread, "question checkpoint")

    async def now() -> str:
        snap = await w.held["graph"].aget_state(tt.config(thread))
        return str(snap.config["configurable"]["checkpoint_id"])

    checkpoint = w.run(i["mine"], "reem", now)
    entries = entries_of(monkeypatch)
    w.moved("reem", i["mine"], session, i["family"])
    (entry,) = entries
    assert entry["checkpoint"] == checkpoint and entry["count"] == 2 and entry["new_count"] == 2


# ── the same, in the request ──────────────────────────────────────────────


class _Running:
    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        return None


@contextmanager
def _a_turn_starts_in(monkeypatch, owner: Any, name: str, thread: str):
    """A turn of *thread* starts as *owner.name* runs (voice takes no lock)."""
    from kazma_ui.active_turns import register_turn, unregister_turn

    real, running = getattr(owner, name), _Running()
    if asyncio.iscoroutinefunction(real):
        async def starts(*args: Any, **kwargs: Any) -> Any:
            register_turn(thread, running)
            return await real(*args, **kwargs)
    else:
        def starts(*args: Any, **kwargs: Any) -> Any:
            register_turn(thread, running)
            return real(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, starts)
        try:
            yield
        finally:
            unregister_turn(thread, running)


def _nothing_moved(w: World, entry: dict[str, Any], session: str, thread: str) -> None:
    i = w.ids
    assert _texts(w, entry["source"], session) is not None, "the chat stayed"
    assert threads.record_of(thread).space_id == entry["source"]
    assert _texts(w, entry["target"], entry["new_session"]) is None
    assert threads.record_of(entry["new_thread"]) is None
    assert chat_moves.moves_under_way() == {} and files_of(entry["target"], i["reem"], entry["new_thread"]) == []
    say(w, entry["source"], "reem", session, "and more")


def _handovers(monkeypatch) -> list[str]:
    """The chats handed over: whether anyone could have seen a copy."""
    real, seen = session_manager.SessionManager.hand_over, []

    def hand_over(self: Any, session_id: str, **kwargs: Any) -> bool:
        seen.append(session_id)
        return real(self, session_id, **kwargs)

    monkeypatch.setattr(session_manager.SessionManager, "hand_over", hand_over)
    return seen


@pytest.mark.parametrize(("module", "name"), [("thread_rewind", "carry_conversation"),
                                              ("chat_move", "_before_the_delete")])
def test_a_turn_that_starts_meanwhile_holds_the_move(world, monkeypatch, module, name) -> None:
    """Asked again before the handover -- nobody sees a copy then -- and right
    before the delete."""
    import importlib

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="voice")
    shared_file(w, i["mine"], thread, "the report")
    entries, handed = entries_of(monkeypatch), _handovers(monkeypatch)
    with _a_turn_starts_in(monkeypatch, importlib.import_module(f"kazma_ui.{module}"), name, thread):
        refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "turn_running", refused.text
    assert handed == ([] if module == "thread_rewind" else [session])
    _nothing_moved(w, entries[0], session, thread)


def test_a_chat_whose_checkpoint_changed_meanwhile_is_not_moved(world, monkeypatch) -> None:
    from kazma_ui import thread_rewind

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="replied")
    w.turn("reem", i["mine"], thread, "question replied")
    real, entries, handed = thread_rewind.carry_conversation, entries_of(monkeypatch), _handovers(monkeypatch)

    async def carry(*args: Any, **kwargs: Any) -> Any:
        carried = await real(*args, **kwargs)
        with scope.bind_scope(i["mine"], i["reem"]):
            await tt.run_turn(w.held["graph"], thread, "one more turn")
        return carried

    with monkeypatch.context() as patch:
        patch.setattr(thread_rewind, "carry_conversation", carry)
        refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "changed", refused.text
    assert handed == []
    _nothing_moved(w, entries[0], session, thread)
    assert len(w.state("reem", i["mine"], thread)) == 4


@pytest.mark.parametrize(("out", "code"), [(False, "changed"), (True, "kept_both")], ids=["shared", "taken-out"])
def test_the_row_is_compared_in_the_delete_itself(world, monkeypatch, out, code) -> None:
    """A message landed behind the store's back (another process) right as
    the row was deleted: nothing of the chat goes. A shared space that forgot
    the chat already keeps it, and the person keeps the copy."""
    from kazma_ui import chat_delete, chat_move

    w, i = world, world.ids
    frm, to = (i["family"], i["mine"]) if out else (i["mine"], i["family"])
    session, thread = w.chat("reem", frm, mark="landed")
    entries = entries_of(monkeypatch)
    real, landed = chat_delete._remove_row, []

    def remove(*args: Any) -> bool:
        if not landed:
            landed.append(1)
            chat_move._unmark(entries[0])
            say(w, frm, "reem", session, _OLD)
            chat_move._mark(entries[0])
        return real(*args)

    monkeypatch.setattr(chat_delete, "_remove_row", remove)
    refused = w.move("reem", frm, session, to)
    assert refused.status_code == 409 and refused.json()["error"] == code, refused.text
    assert _texts(w, frm, session) == ["question landed", "answer landed", _OLD]
    assert (_texts(w, to, entries[0]["new_session"]) is not None) is out
    assert threads.record_of(thread).space_id == frm and chat_moves.moves_under_way() == {}
    say(w, frm, "reem", session, "and more")


def test_both_written_in_before_the_delete_are_both_kept_and_said(world, monkeypatch) -> None:
    """A message of the chat landed behind the store's back (another
    process), and one in the copy, before the chat was deleted."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="both")
    entries = entries_of(monkeypatch)
    real = chat_move._before_the_delete

    def meanwhile(entry: dict[str, Any]) -> None:
        real(entry)
        threads._ended.clear()
        session_manager.get_session_manager()._deleted.clear()
        say(w, i["mine"], "reem", session, _OLD)
        say(w, i["family"], "reem", entry["new_session"], _NEW)
        chat_move._mark(entry)

    monkeypatch.setattr(chat_move, "_before_the_delete", meanwhile)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "kept_both", refused.text
    assert _texts(w, i["mine"], session)[-1] == _OLD
    assert _texts(w, i["family"], entries[0]["new_session"])[-1] == _NEW
    assert chat_moves.moves_under_way() == {}


def test_the_copys_row_is_written_last_and_copied_at_once(world, monkeypatch) -> None:
    """Nobody sees any of the copy before its row: by then its record, its
    files and its state are there; and nothing comes between the row and the
    journal's ``copied``."""
    from kazma_core import chat_files
    from kazma_ui import chat_move, thread_rewind

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="order")
    shared_file(w, i["mine"], thread, "the report")
    entries = entries_of(monkeypatch)
    order: list[str] = []
    for owner, name in ((chat_move, "_record_copy"), (chat_files, "copy_shared_files"),
                        (thread_rewind, "carry_conversation"), (chat_moves, "advance_move")):
        real = getattr(owner, name)
        if asyncio.iscoroutinefunction(real):
            async def called(*args: Any, real: Any = real, name: str = name, **kwargs: Any) -> Any:
                order.append(name)
                return await real(*args, **kwargs)
        else:
            def called(*args: Any, real: Any = real, name: str = name, **kwargs: Any) -> Any:
                order.append(name)
                return real(*args, **kwargs)
        monkeypatch.setattr(owner, name, called)
    real_put = session_manager.SessionManager.put

    def put(self: Any, chat: Any) -> None:
        if entries and chat.session_id == entries[0]["new_session"]:
            order.append("row")
        real_put(self, chat)

    monkeypatch.setattr(session_manager.SessionManager, "put", put)
    w.moved("reem", i["mine"], session, i["family"])
    assert order == ["_record_copy", "copy_shared_files", "carry_conversation", "row", "advance_move"]


@pytest.mark.parametrize("step", ["_copy", "_delete_here"])
def test_a_request_that_goes_away_at_any_time_does_not_cut_it_off(world, monkeypatch, step) -> None:
    """The shield covers the whole move: the copy, and the delete here."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="cancel")
    w.turn("reem", i["mine"], thread, "question cancel")
    real = getattr(chat_move, step)
    entries = entries_of(monkeypatch)

    async def cancelled() -> None:
        reached, go = asyncio.Event(), asyncio.Event()

        async def slowly(*args: Any) -> Any:
            reached.set()
            await go.wait()
            return await real(*args)

        monkeypatch.setattr(chat_move, step, slowly)
        if step == "_delete_here":
            monkeypatch.setattr(chat_move, "_STEPS", tuple(
                (name, slowly if name == "delete" else run) for name, run in chat_move._STEPS))
        move = asyncio.ensure_future(chat_move._move_chat(
            session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"],
            graph=w.held["graph"], checkpoints=w.held["manager"]))
        await asyncio.wait_for(reached.wait(), 10)
        move.cancel()
        with pytest.raises(asyncio.CancelledError):
            await move
        go.set()
        for _ in range(1000):
            if not await asyncio.to_thread(chat_moves.moves_under_way):
                break
            await asyncio.sleep(0.01)

    w.run(i["mine"], "reem", cancelled)
    assert chat_moves.moves_under_way() == {}
    assert _texts(w, i["family"], entries[0]["new_session"]) == ["question cancel", "answer cancel"]
    w.nothing_left(i["mine"], "reem", session, thread)


# ── the old thread never runs again ───────────────────────────────────────


def test_a_forgotten_record_is_a_thread_that_ended(world) -> None:
    """The record store's own delete marks it: whatever removes a record."""
    i = world.ids
    threads.record_thread("t-forgotten", i["mine"], i["reem"])
    assert threads.forget_thread("t-forgotten") is True
    with scope.bind_scope(i["mine"], i["reem"]), pytest.raises(threads.ThreadEnded):
        threads.scope_for_turn("t-forgotten")


@pytest.mark.parametrize("by", ["move", "delete", "dashboard"])
def test_the_old_thread_never_runs_again(world, by) -> None:
    from kazma_ui.thread_locks import rewind_generation

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="ended")
    before = rewind_generation(thread)
    if by == "move":
        w.moved("reem", i["mine"], session, i["family"])
    else:
        path = f"/api/chat/sessions/{session}" if by == "delete" else f"/api/sessions/{thread}"
        assert w.call("reem", "DELETE", path, space=i["mine"]).status_code == 200
    assert rewind_generation(thread) > before, "a turn prepared from its state does not start"
    with scope.bind_scope(i["mine"], i["reem"]):
        with pytest.raises(threads.ThreadEnded):
            threads.scope_for_turn(thread)
        with pytest.raises(threads.ThreadEnded):
            threads.record_thread(thread, i["mine"], i["reem"])
        assert threads.record_of(thread) is None
        # Control: without the mark it runs, recorded again.
        threads._ended.clear()
        assert threads.scope_for_turn(thread) is not None
        assert threads.record_thread(thread, i["mine"], i["reem"]).space_id == i["mine"]
    threads.forget_thread(thread)


def test_a_real_turn_of_an_ended_thread_is_refused(world) -> None:
    """Whatever would run one -- a resume, a queued approval -- is refused
    before the graph runs."""
    from kazma_core.agent.turn_budget import turn_segment_budget

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="ended")
    w.turn("reem", i["mine"], thread, "question ended")
    w.moved("reem", i["mine"], session, i["family"])

    async def run() -> bool:
        async with turn_segment_budget(w.held["graph"], tt.config(thread), thread_id=thread) as segment:
            return bool(segment.refused)

    try:
        refused = w.run(i["mine"], "reem", run)
    except threads.ThreadEnded:
        refused = True
    assert refused
    assert w.state("reem", i["mine"], thread) == []


def test_a_resume_of_an_ended_thread_says_the_chat_was_deleted(world, caplog) -> None:
    """An approval answered as its chat goes: nothing runs, nothing is
    journaled or stored, and it is no error."""
    from kazma_ui.delivery import get_turn_broker
    from kazma_ui.sse_chat import _streaming
    from langgraph.types import Command

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="ended")
    w.turn("reem", i["mine"], thread, "question ended")
    w.moved("reem", i["mine"], session, i["family"])

    async def resume() -> list[str]:
        return [frame async for frame in _streaming._stream_langgraph_events(
            w.held["graph"], Command(resume={"approved": True}), tt.config(thread),
            thread_id=thread, session_id=session, reply_turn_id="turn-1")]

    with caplog.at_level(logging.INFO, logger="kazma_ui.sse_chat._streaming"):
        frames = w.run(i["mine"], "reem", resume)
    assert any('"code": "chat_deleted"' in f for f in frames), frames
    said = [r for r in caplog.records if r.name == "kazma_ui.sse_chat._streaming"]
    assert any("was deleted; its turn did not run" in r.getMessage() for r in said)
    assert [r for r in said if r.levelno >= logging.WARNING] == []
    assert get_turn_broker().resume(thread, 0)[0] == []
    w.nothing_left(i["mine"], "reem", session, thread)


# ── the delete itself ─────────────────────────────────────────────────────


def _delete(w: World, session: str, thread: str, checkpoints: Any, space: str) -> bool | None:
    from kazma_ui.chat_delete import delete_chat

    async def run() -> bool | None:
        return await delete_chat(session, thread, checkpoints, scope=(space, w.ids["reem"]))

    # Asked from another space: the ids and the scope are the journal's.
    return w.run(w.ids["family"], "reem", run)


def test_the_delete_takes_its_ids_from_the_journal_and_may_run_twice(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="twice")
    w.turn("reem", i["mine"], thread, "question twice")
    assert _delete(w, session, thread, w.held["manager"], i["mine"]) is True
    w.nothing_left(i["mine"], "reem", session, thread)
    assert _delete(w, session, thread, w.held["manager"], i["mine"]) is True
    w.restart()
    assert _delete(w, session, thread, w.held["manager"], i["mine"]) is True
    w.nothing_left(i["mine"], "reem", session, thread)


def test_a_checkpoint_that_could_not_be_deleted_keeps_the_record(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="stuck")
    w.turn("reem", i["mine"], thread, "question stuck")

    class Refuses:
        async def adelete_thread(self, _thread: str) -> None:
            raise sqlite3.OperationalError("database is locked")

    assert _delete(w, session, thread, Refuses(), i["mine"]) is False
    assert session not in w.listed("reem", i["mine"]), "its row is gone"
    assert threads.record_of(thread).space_id == i["mine"], "what is left is still her space's"
    with scope.bind_scope(i["mine"], i["reem"]), pytest.raises(threads.ThreadEnded):
        threads.scope_for_turn(thread)
    assert _delete(w, session, thread, w.held["manager"], i["mine"]) is True
    w.nothing_left(i["mine"], "reem", session, thread)


def test_a_delete_never_reaches_another_spaces_thread(world) -> None:
    """The record goes last: a thread recorded elsewhere has nothing left in
    the space the journal names, and is not touched."""
    w, i = world, world.ids
    _session, thread = w.chat("omar", i["family"], mark="omars")
    w.turn("omar", i["family"], thread, "question omars")
    assert _delete(w, "s-none", thread, w.held["manager"], i["mine"]) is True
    assert threads.record_of(thread).space_id == i["family"]
    threads._ended.clear()
    assert w.state("omar", i["family"], thread)


def test_the_route_is_the_same_delete(world, monkeypatch) -> None:
    from kazma_ui import chat_delete

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="route")
    real, asked = chat_delete.delete_chat, []

    async def delete(*args: Any, **kwargs: Any) -> Any:
        asked.append((args[:2], kwargs.get("reason")))
        return await real(*args, **kwargs)

    monkeypatch.setattr(chat_delete, "delete_chat", delete)
    assert w.call("reem", "DELETE", f"/api/chat/sessions/{session}", space=i["mine"]).status_code == 200
    other, other_thread = w.chat("reem", i["mine"], mark="moved")
    w.moved("reem", i["mine"], other, i["family"])
    assert asked == [((session, thread), None), ((other, other_thread), "moved")]


# ── what is asked of a deleted chat answers "deleted" ─────────────────────


@pytest.mark.parametrize(("lang", "word"), [("en", "was deleted or moved"), ("ar", "حُذفت هذه المحادثة")])
@pytest.mark.parametrize(("method", "what", "body"), [
    ("PATCH", "", {"title": "back"}), ("POST", "/archive", None), ("POST", "/unarchive", None),
    ("POST", "/pin", None), ("POST", "/unpin", None),
])
def test_a_whole_chat_request_for_a_deleted_chat_says_so(world, monkeypatch, method, what, body, lang, word) -> None:
    from kazma_ui import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: lang)
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="gone")
    real = session_manager.SessionManager.get

    def a_copy_read_before(self: Any, session_id: str) -> Any:
        """The request read the chat right before it was deleted."""
        return stale if session_id == session else real(self, session_id)

    stale = w.row(i["mine"], "reem", session)
    w.moved("reem", i["mine"], session, i["family"])
    monkeypatch.setattr(session_manager.SessionManager, "get", a_copy_read_before)
    answer = w.call("reem", method, f"/api/chat/sessions/{session}{what}", space=i["mine"],
                    **({"json": body} if body else {}))
    assert answer.status_code == 404, answer.text
    assert answer.json()["error"] == "chat_deleted" and word in answer.json()["message"]


def test_a_chat_store_that_cannot_be_read_settles_nothing(world, monkeypatch) -> None:
    """"No row" is an answer a move acts on: an outage is never one."""
    w, i = world, world.ids
    entry = _stopped(w, "copied", "stopped", out=False)
    store = session_manager.get_session_manager()

    class Down:
        def execute(self, *_a: Any) -> Any:
            raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch, scope.bind_scope(i["mine"], i["reem"]):
        patch.setattr(store, "_conn", Down())
        with pytest.raises(sqlite3.OperationalError):
            store.held(entry["session"])
        assert [s["outcome"] for s in w.settle()] == ["waiting"]
    assert chat_moves.moves_under_way()["stopped"]["stage"] == "copied"
    assert [s["outcome"] for s in w.settle()] == ["finished"]
    assert _texts(w, i["family"], entry["new_session"]) == ["question stopped", "answer stopped"]


def test_the_stores_last_write_is_refused_too(world) -> None:
    """``_write_durably``, the one way into the database: a copy read before
    the delete, saved by whatever holds it."""
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="late")
    stale = w.row(i["mine"], "reem", session)
    w.moved("reem", i["mine"], session, i["family"])
    store = session_manager.get_session_manager()
    with scope.bind_scope(i["mine"], i["reem"]):
        with pytest.raises(session_manager.ChatDeleted):
            store._write_durably(stale)
        assert store.held(session) is None
        # Control: a store that forgets its deletes stores it again.
        store._deleted.clear()
        threads._ended.clear()
        store._write_durably(stale)
        assert store.held(session) is not None
