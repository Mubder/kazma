"""A fork of a chat keeps what the chat keeps out of memory (FA-246).

The 2026-11 functional audit: a chat kept out of memory ("Don't remember this
chat") was forked from the Time Travel page, and the fork was remembered --
its transcript (the source's turns, under the same turn numbers) reached
memory through turn reconcile, and its next turn through the live writer. A
turn the user had forgotten came back the same way, as the fork's.

The fork goes through the real route (``POST /api/replay/fork``) on the Time
Travel test graph; then every writer a fork's turns reach is asked to write:
the post-turn body (``consolidator._run_turn_memory``, what ``close_turn``
runs), the episode writer and turn reconcile. The negative control is the
fork without the ledger copy, the code before the fix.
"""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_core.memory import forget, turn_reconcile
from kazma_core.memory.schema_v2 import ensure_primary_schema
from tests import _tt_graph as tt

Q1 = "where do I work these days"
Q2 = "what is my favourite colour"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core import time_travel, time_travel_audit
    from kazma_core.memory import dual_write
    from kazma_core.safety import hitl_gates
    from kazma_core.time_travel import SnapshotRecorder
    from kazma_ui import thread_ownership
    from kazma_ui.session_manager import SessionManager, set_session_manager

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    time_travel_audit.set_db_path_for_tests(str(tmp_path / "tt_audit.db"))
    time_travel.set_process_recorder(None)
    store = SessionManager(db_path=str(tmp_path / "chat_sessions.db"))
    set_session_manager(store)  # the chat store memory reads (chat_history: data dir)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    graph = tt.build_graph(rec, [], InMemorySaver())
    yield SimpleNamespace(dir=tmp_path, db=db, store=store, rec=rec, graph=graph)
    dual_write._reset_mirror()
    db.close()
    rec.close()
    time_travel.set_process_recorder(None)
    hitl_gates.set_db_path_for_tests(None)
    time_travel_audit.set_db_path_for_tests(None)


def _source_chat(env) -> str:
    """A web chat (session ``s1``, thread ``src``) of two answered turns; the
    id of the step at the end of turn 2."""
    from kazma_ui.session_manager import ChatSession

    async def two_turns():
        await tt.run_turn(env.graph, "src", Q1)
        await tt.run_turn(env.graph, "src", Q2)

    tt.run(two_turns())
    env.store.put(ChatSession(
        session_id="s1", tenant_id="default", thread_id="src", title="Notes",
        messages=[{"role": "user", "content": Q1}, {"role": "assistant", "content": f"Answer to {Q1}"},
                  {"role": "user", "content": Q2}, {"role": "assistant", "content": f"Answer to {Q2}"}],
    ))
    return [s for s in env.rec.list_snapshots("src") if s.turn_index == 2][-1].id


def _fork(env, step: str) -> dict:
    """``POST /api/replay/fork``, the Time Travel page's Fork."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.replay_routes import create_replay_router

    app = FastAPI()
    app.include_router(create_replay_router(recorder=env.rec, graph=env.graph))
    resp = TestClient(app).post("/api/replay/fork", json={"thread_id": "src", "snapshot_id": step})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _episodes(env, key: str | None = None) -> list[str]:
    sql = "SELECT user_text FROM episodes WHERE tier != 'forgotten'"
    params: tuple = ()
    if key is not None:
        sql += " AND session_id = ?"
        params = (key,)
    return [r[0] for r in env.db.execute(sql, params)]


def _reconcile(env) -> dict:
    """One turn-reconcile pass with every stored turn settled."""
    return turn_reconcile._reconcile(env.db, ("", ""), time.monotonic() + 60, time.time() + 3600)


def _turn_in_fork(env, fork: str) -> bool:
    """The fork's next turn, as ``close_turn`` hands it to memory."""
    from kazma_core.memory import consolidator

    messages = [{"role": "user", "content": "my name is Dana"},
                {"role": "assistant", "content": "Nice to meet you, Dana."}]
    return consolidator._run_turn_memory(messages, session_id=fork, turn=3)


def _live_write(fork: str, turn: int, question: str):
    from kazma_core.memory.dual_write import mirror_episode

    return mirror_episode(session_id=fork, turn_number=turn, user_text=question,
                          assistant_text=f"Answer to {question}", created_at=time.time() - 3600)


def test_a_fork_of_a_chat_kept_out_of_memory_stays_out(env):
    step = _source_chat(env)
    forget.set_chat_remembered("s1", False, tenant_id="default")
    out = _fork(env, step)
    fork = out["new_thread_id"]
    assert out["message_count"] == 4  # the fork holds both turns

    assert not forget.chat_remembered(fork, tenant_id="default")
    assert _turn_in_fork(env, fork) is True  # handled: nothing written
    assert _live_write(fork, 1, Q1) is None
    assert _reconcile(env)["turns_written"] == 0
    assert _episodes(env) == []
    assert env.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


def test_a_turn_kept_out_in_the_source_stays_out_in_the_fork(env):
    """The turn in which the user asks to forget something is kept out of
    memory (``keep_turn_out``) and keeps its saved steps: a fork of a later
    step holds it, and without the ledger turn reconcile wrote it."""
    step = _source_chat(env)
    assert forget.keep_turn_out("s1", 1, Q1, tenant_id="default")
    fork = _fork(env, step)["new_thread_id"]

    report = _reconcile(env)
    assert Q1 not in _episodes(env, fork)
    assert _episodes(env, fork) == [Q2]  # the turn not forgotten is remembered as before
    assert report["turns_written"] >= 1
    assert forget.chat_remembered(fork, tenant_id="default")  # one turn, not the chat


def test_the_fork_ledger_is_journaled_so_a_restore_keeps_it(env):
    """A memory database restored from before the fork gets the rows back
    from the forget journal (convergence)."""
    from kazma_core.memory import forget_journal

    step = _source_chat(env)
    forget.set_chat_remembered("s1", False, tenant_id="default")
    forget.keep_turn_out("s1", 2, Q2, tenant_id="default")
    fork = _fork(env, step)["new_thread_id"]

    # The restored copy: no ledger rows for the fork, no record of applying them.
    env.db.execute("DELETE FROM memory_forgotten WHERE session_key = ?", (fork,))
    env.db.execute("DELETE FROM memory_forget_applied")
    env.db.commit()
    assert forget.chat_remembered(fork, tenant_id="default")
    forget_journal.apply_pending(env.db)
    env.db.commit()
    assert not forget.chat_remembered(fork, tenant_id="default")
    whole, turns = forget.forgotten_turns(env.db, tenant_id="default", keys=[fork])
    assert whole and (2, forget.question_sha(Q2)) in turns


def test_a_ledger_that_cannot_be_copied_refuses_the_fork(env, monkeypatch):
    """Fail closed: a fork that could not carry the ledger is not made."""
    step = _source_chat(env)
    forget.set_chat_remembered("s1", False, tenant_id="default")

    def broken(source, new):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(forget, "inherit_ledger", broken)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.replay_routes import create_replay_router

    app = FastAPI()
    app.include_router(create_replay_router(recorder=env.rec, graph=env.graph))
    resp = TestClient(app).post("/api/replay/fork", json={"thread_id": "src", "snapshot_id": step})
    assert resp.status_code == 503 and resp.json()["error"] == "memory_ledger_unavailable"
    assert [s.session_id for s in env.store.list_all() if s.session_id.startswith("fork-")] == []


def test_the_old_fork_was_remembered(env, monkeypatch):
    """Negative control: the fork without the ledger copy (the code before
    the fix) -- the kept-out chat's turns and its fork's next turn reach
    memory."""
    step = _source_chat(env)
    forget.set_chat_remembered("s1", False, tenant_id="default")
    monkeypatch.setattr(forget, "inherit_ledger", lambda source, new: {})
    fork = _fork(env, step)["new_thread_id"]

    assert forget.chat_remembered(fork, tenant_id="default")
    assert _reconcile(env)["turns_written"] == 2
    assert sorted(_episodes(env, fork)) == sorted([Q1, Q2])
    assert _live_write(fork, 3, "my name is Dana") is not None


def test_the_old_fork_wrote_the_kept_out_turn(env, monkeypatch):
    """Negative control for the turn case: without the ledger copy, turn
    reconcile writes the turn the source kept out as the fork's memory."""
    step = _source_chat(env)
    forget.keep_turn_out("s1", 1, Q1, tenant_id="default")
    monkeypatch.setattr(forget, "inherit_ledger", lambda source, new: {})
    fork = _fork(env, step)["new_thread_id"]
    _reconcile(env)
    assert Q1 in _episodes(env, fork)
