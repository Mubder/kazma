"""The fifth review of the chat move (Spaces S2-12): what a move put back, kept
or left waiting may never do. The store's lock and the event loop (M1, M3) are
``tests/test_chat_store_lock_and_loop.py``; a chat deleted on any install,
``tests/test_deleted_chat_stays_deleted.py``; an import taken back (M4),
``tests/test_space_move_ends_no_chat.py``.

* **M2** a move put back took the mark away from a chat its person deleted
  meanwhile, and a write still holding its copy stored it again;
* **M5** a chat written in whose copy was deleted was in neither space;
* **L1** a state that does not decode stopped every other move's settling;
* **L2** a chat that stayed in the shared space stayed out of its memory;
* **L4** the reverts no test noticed: a handover that compares how many
  messages there are, and an app that does not settle as it starts (or still
  does once stopped); the minute a share leaves to the live memory write is in
  ``tests/test_chat_move_memory.py``;
* **L6** a steer row had no time.

Each fails on 0457e8f23 but for the controls and the guards.
"""

from __future__ import annotations

import copy
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kazma_core.memory import forget, forget_journal
from kazma_core.spaces import chat_moves, scope, threads
from kazma_ui import chat_move, session_manager

from tests._chat_move_world import fails, handed_over, left, say, world  # noqa: F401  (world: a fixture)

REPO = Path(__file__).resolve().parents[1]


def _mgr() -> session_manager.SessionManager:
    return session_manager.get_session_manager()


def _texts(w: Any, space: str, session: str) -> list[str] | None:
    held = w.row(space, "reem", session)
    return None if held is None else [m["content"] for m in held.messages]


# ── M2: a chat its person deleted stays deleted, whatever its move does ───


@pytest.mark.parametrize("out", [False, True], ids=["shared", "taken-out"])
def test_a_chat_deleted_while_it_is_copied_stays_deleted(world, monkeypatch, out) -> None:
    w, i = world, world.ids
    frm, to = (i["family"], i["mine"]) if out else (i["mine"], i["family"])
    session, thread = w.chat("reem", frm, mark="del")
    reached, go_on = threading.Event(), threading.Event()
    real = chat_move._copy_files

    def held_up(*args: Any) -> Any:
        reached.set()
        assert go_on.wait(20)
        return real(*args)

    monkeypatch.setattr(chat_move, "_copy_files", held_up)
    moved: list[Any] = []
    deleted: list[Any] = []
    mover = threading.Thread(target=lambda: moved.append(w.move("reem", frm, session, to)))
    mover.start()
    assert reached.wait(10)
    deleter = threading.Thread(target=lambda: deleted.append(
        w.call("reem", "DELETE", f"/api/chat/sessions/{session}", space=frm)))
    deleter.start()
    for _ in range(200):  # its row goes before the delete waits on the thread's lock
        if _texts(w, frm, session) is None:
            break
        time.sleep(0.02)
    go_on.set()
    mover.join(30)
    deleter.join(30)
    assert moved[0].status_code == 409 and deleted[0].status_code == 200
    with scope.bind_scope(frm, i["reem"]):
        assert (frm, session) in _mgr()._deleted and threads.thread_ended(thread)
    bind = w.call("reem", "POST", "/api/chat/sessions", space=frm, json={"session_id": session})
    assert bind.status_code == 404 and session not in w.listed("reem", frm)


def _deleted_then_settled(w: Any) -> tuple[str, Any]:
    """A cut-off move's chat, deleted by its person before it is settled."""
    i = w.ids
    session, _thread, _entry = left(w, "cut", "late")
    held = copy.deepcopy(w.row(i["mine"], "reem", session))  # what a voice socket still holds
    assert w.call("reem", "DELETE", f"/api/chat/sessions/{session}", space=i["mine"]).status_code == 200
    assert [s["outcome"] for s in w.settle()] == ["put_back"]
    return session, held


def _late_write(w: Any, session: str, held: Any) -> Any:
    from kazma_ui.sse_chat._persistence import _mutate_session

    with scope.bind_scope(w.ids["mine"], w.ids["reem"]):
        return _mutate_session(session, lambda sess: sess.add_message("user", "late words"), held)


def test_a_chat_deleted_before_its_move_is_settled_stays_deleted(world) -> None:
    w, i = world, world.ids
    session, held = _deleted_then_settled(w)
    assert _late_write(w, session, held) is None
    assert session not in w.listed("reem", i["mine"])
    with scope.bind_scope(i["mine"], i["reem"]), pytest.raises(threads.ThreadEnded):
        threads.scope_for_turn(held.thread_id)


def test_control_an_unmark_that_does_not_ask_brings_it_back(world, monkeypatch) -> None:
    w, i = world, world.ids

    def unmark(entry: dict[str, Any]) -> None:
        with scope.bind_scope(entry["source"], entry["by"]):
            _mgr().unmark_deleted(entry["session"], entry["thread"])

    monkeypatch.setattr(chat_move, "_unmark", unmark)
    session, held = _deleted_then_settled(w)
    assert _late_write(w, session, held) is not None and session in w.listed("reem", i["mine"])


# ── M5: never in neither space ────────────────────────────────────────────


@pytest.mark.parametrize("out", [False, True], ids=["shared", "taken-out"])
def test_a_chat_written_in_whose_copy_was_deleted_is_kept(world, out) -> None:
    """After a restart whose settle could not read a store nothing is marked:
    words land in the old chat, and the person deletes the copy. It was hidden
    from then on, its delete refused for good."""
    w, i = world, world.ids
    session, thread, entry = handed_over(w, "gone", "gone", out=out)
    chat_moves.record_move("gone", {"done": ["handed_over"]})
    w.restart()
    say(w, entry["source"], "reem", session, "new words here")
    with scope.bind_scope(entry["target"], i["reem"]):
        _mgr().delete(entry["new_session"])
    assert [s["outcome"] for s in w.settle()] == ["kept_both"]
    assert chat_moves.moves_under_way() == {}
    assert _texts(w, entry["source"], session)[-1] == "new words here"
    assert session in w.listed("reem", entry["source"])
    say(w, entry["source"], "reem", session, "and more")
    with scope.bind_scope(entry["source"], i["reem"]):
        assert threads.scope_for_turn(thread) is not None


# ── L1: one move's bad state is its own ───────────────────────────────────


@pytest.mark.parametrize("error", [KeyError("no such type"), AttributeError("gone"), ValueError("not msgpack")],
                         ids=lambda e: type(e).__name__)
def test_a_state_that_does_not_decode_holds_only_its_own_move(world, monkeypatch, error) -> None:
    w = world
    _s, bad, _e = handed_over(w, "a-bad", "bad")
    session, thread, entry = handed_over(w, "b-good", "good")
    manager = type(w.held["manager"])
    real = manager.aget_tuple

    async def does_not_decode(self: Any, config: Any) -> Any:
        if config["configurable"]["thread_id"] == bad:
            raise error
        return await real(self, config)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "aget_tuple", does_not_decode)
        assert {s["attempt"]: s["outcome"] for s in w.settle()} == {"a-bad": "waiting", "b-good": "finished"}
    assert list(chat_moves.moves_under_way()) == ["a-bad"], "it keeps its entry"
    w.nothing_left(entry["source"], "reem", session, thread)
    assert [s["outcome"] for s in w.settle()] == ["finished"], "once it reads"


# ── L2: a chat that stayed is remembered from then on ─────────────────────


def _cut_off_take_out(w: Any, monkeypatch: Any, *, forget_fails: bool) -> tuple[str, str]:
    """A take-out whose memory step ran (or stopped half way), then a restart
    that marked nothing: session, thread."""
    session, thread, _entry = handed_over(w, "out", "stayed", out=True)
    with monkeypatch.context() as patch:
        if forget_fails:
            patch.setattr(forget, "forget_chat", fails(sqlite3.OperationalError("database is locked")))
        patch.setattr(chat_move, "_old_is", _same)
        patch.setattr(session_manager.SessionManager, "delete_if", fails(sqlite3.OperationalError("locked")))
        assert [s["outcome"] for s in w.settle()] == ["copied"]
    assert not forget.chat_remembered(session, tenant_id=w.ids["family"]), "kept out before it is forgotten"
    w.restart()
    return session, thread


async def _same(_entry: dict[str, Any], _checkpoints: Any) -> str:
    return "same"


@pytest.mark.parametrize("forget_fails", [False, True], ids=["forgotten", "half done"])
def test_a_chat_that_stayed_in_the_shared_space_is_remembered_there(world, monkeypatch, forget_fails) -> None:
    w, i = world, world.ids
    session, _thread = _cut_off_take_out(w, monkeypatch, forget_fails=forget_fails)
    say(w, i["family"], "omar", session, "omar goes on here")
    assert [s["outcome"] for s in w.settle()] == ["kept_both"]
    assert session in w.listed("omar", i["family"]) and w.listed("reem", i["mine"])
    assert chat_moves.moves_under_way() == {}
    assert forget.chat_remembered(session, tenant_id=i["family"])


@pytest.mark.parametrize("when", ["before", "since"])
def test_a_persons_own_dont_remember_stays(world, monkeypatch, when) -> None:
    w, i = world, world.ids
    real = chat_move._entry
    if when == "before":
        monkeypatch.setattr(chat_move, "_entry", lambda *a: {**real(*a), "kept_out": True})
    session, thread = _cut_off_take_out(w, monkeypatch, forget_fails=False)
    if when == "since":
        forget.set_chat_remembered(thread, False, tenant_id=i["family"], keys=[session, thread])
    say(w, i["family"], "omar", session, "omar goes on here")
    assert [s["outcome"] for s in w.settle()] == ["kept_both"] and chat_moves.moves_under_way() == {}
    assert not forget.chat_remembered(session, tenant_id=i["family"])


def test_a_forget_not_done_yet_is_done_before_memory_starts_again(world, monkeypatch) -> None:
    """Begun (journaled) and cut off: the maintenance pass would apply it to
    what is said from now on. Until it is done the chat stays kept out and its
    entry stays; the chat itself does not wait."""
    w, i = world, world.ids
    session, thread = _cut_off_take_out(w, monkeypatch, forget_fails=True)
    forget_journal.append("chat", tenant_id=i["family"], session_key=session,
                          detail=forget_journal.keys_detail([session, thread]))
    say(w, i["family"], "omar", session, "omar goes on here")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "forget_chat", fails(sqlite3.OperationalError("database is locked")))
        assert [s["outcome"] for s in w.settle()] == ["kept_both"]
        assert session in w.listed("omar", i["family"])
        assert not forget.chat_remembered(session, tenant_id=i["family"]) and chat_moves.moves_under_way()
    assert [s["outcome"] for s in w.settle()] == ["kept_both"] and chat_moves.moves_under_way() == {}
    assert forget.chat_remembered(session, tenant_id=i["family"])
    assert not forget_journal.chat_forget_pending(i["family"], [session, thread])


def test_a_take_out_that_is_done_stays_forgotten(world) -> None:
    """A guard: nothing is lifted where the chat left."""
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="left")
    w.moved("reem", i["family"], session, i["mine"])
    assert not forget.chat_remembered(session, tenant_id=i["family"])


# ── L4: the reverts no test noticed ───────────────────────────────────────


def test_a_message_rewritten_while_it_is_copied_holds_the_move(world, monkeypatch) -> None:
    """The handover compares what the messages say, not how many there are: a
    closing turn writes its answer into the row it opened."""
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="rewritten")
    real = chat_move._copy_files

    def rewritten(rows: Any, entry: Any) -> Any:
        with scope.bind_scope(i["family"], i["reem"]), _mgr().transact(session) as held:
            held.messages[-1]["content"] = "the whole answer"
        return real(rows, entry)

    monkeypatch.setattr(chat_move, "_copy_files", rewritten)
    assert w.move("reem", i["family"], session, i["mine"]).json()["error"] == "changed"
    assert _texts(w, i["family"], session) == ["question rewritten", "the whole answer"]
    assert w.listed("reem", i["mine"]) == []


@contextmanager
def _a_move_cut_off(attempt: str) -> Iterator[None]:
    """A move a stop cut off while it copied, recorded where the app reads."""
    chat_moves.record_move(attempt, {
        "thread": "t-app", "session": "s-app", "source": "s0000000000aa", "target": "s0000000000bb",
        "by": "p0000000000aa", "new_session": "s-app-2", "new_thread": "t-app-2", "taken_out": False,
        "kept_out": False, "title": "t", "stage": "copying", "count": 0, "last_ts": "", "new_count": 0,
        "new_last_ts": "", "at": time.time()})
    try:
        yield
    finally:
        chat_moves.settle_move(attempt)


def test_the_app_settles_before_anyone_is_served_and_until_it_stops(monkeypatch) -> None:
    """Through the real app: the moves a stop left are settled before its chat
    apps listen and before a request is answered; the sweep's settler is the
    running app's, and no stopped app's."""
    from kazma_ui.app import create_app

    app = create_app()
    gateway = app.state.kazma_runtime.gateway
    order: list[str] = []
    real = chat_move.settle_chat_moves

    async def settled(checkpoints: Any = None) -> Any:
        out = await real(checkpoints)
        order.append("settled")
        return out

    monkeypatch.setattr(chat_move, "settle_chat_moves", settled)
    if gateway is not None:
        started = gateway.start

        async def start(*args: Any, **kwargs: Any) -> Any:
            order.append("chat apps")
            return await started(*args, **kwargs)

        monkeypatch.setattr(gateway, "start", start)
    with _a_move_cut_off("at-start"), TestClient(app):
        assert order[:1] == ["settled"]
        assert chat_moves.moves_under_way() == {}
        assert len(chat_moves._SETTLER) == 1, "and the sweep has one to call"
    assert chat_moves._SETTLER == [], "a stopped app settles nothing"


# ── L6: a steer has its time ──────────────────────────────────────────────


def test_a_turn_with_a_steer_in_a_moved_chat_is_one_said_here() -> None:
    """A steer row opened with no time: in a moved chat its turn was never
    known to be said here, by reconcile or by the past-chats search."""
    from kazma_core.memory.chat_history import said_since

    source = (REPO / "kazma-ui/kazma_ui/sse_chat/__init__.py").read_text(encoding="utf-8")
    steer = source.split("_steer_row = ", 1)[1].split("})", 1)[0]
    assert '"steer": True' in steer and '"ts":' in steer
    at = "2026-11-25T10:00:00+00:00"
    rows = [{"role": "user", "content": "q", "ts": at}, {"role": "user", "content": "/steer more", "steer": True},
            {"role": "assistant", "content": "the answer"}]
    assert len(said_since(rows, 1.0)) == 1, "control: with no time the answer is not read"
    rows[1]["ts"] = at
    assert said_since(rows, 1.0) == rows
