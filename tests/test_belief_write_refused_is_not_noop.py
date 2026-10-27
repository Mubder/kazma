"""A fact the memory store refuses is a failure, never a "noop".

Audit 2026-10-26 AUD-027: ``mutate_belief`` caught every exception, logged
it at DEBUG and returned ``{"action": "noop"}``. With a locked database the
fact was lost in silence: turn reconcile moved its cursor past turns whose
facts were never written (against Memory audit M27, "a locked database is
never a verdict") and the post-turn pass counted the turn ok, so nothing was
retried (M12). Now a store error raises to the caller, which owns the retry.
"""

from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    path = tmp_path / "memory_state.db"
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    db.execute("PRAGMA journal_mode=WAL")
    db.commit()
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield SimpleNamespace(db=db, path=path, mp=monkeypatch)
    db.close()


def test_a_locked_database_raises_instead_of_noop(mem):
    """A real write lock held by another connection."""
    from kazma_core.memory.belief_mutation import mutate_belief

    writer = sqlite3.connect(str(mem.path), timeout=0.05, isolation_level=None)
    writer.row_factory = sqlite3.Row
    locker = sqlite3.connect(str(mem.path), isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            mutate_belief(writer, "user", "lives_in", "Paris", predicate_type="functional",
                          extraction_method="user_stated")
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    # The lock gone, the same write lands.
    result = mutate_belief(writer, "user", "lives_in", "Paris", predicate_type="functional",
                           extraction_method="user_stated")
    writer.close()
    assert result["action"] != "noop" and result["belief_id"]


def _reconciled_turn(mem) -> None:
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES ('r0', 'default', 'cX', 1, 'I live in Paris', 'Noted.', "
        "'episodic', ?, ?)",
        (time.time() - 3600, json.dumps({"source": "turn_reconcile"})))
    mem.db.commit()


def _store_locked(*_a, **_k):
    raise sqlite3.OperationalError("database is locked")


def _lock_the_store(mem) -> None:
    for name in ("_mutate_functional", "_mutate_set"):
        mem.mp.setattr(f"kazma_core.memory.belief_mutation.{name}", _store_locked)
    mem.mp.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")


def test_turn_reconcile_keeps_its_cursor_when_the_store_refuses(mem):
    """Through the real heuristic pass: the outage reaches the runner, the
    cursor stays before the turn and nothing is set aside."""
    from kazma_core.memory import turn_reconcile

    _reconciled_turn(mem)
    _lock_the_store(mem)
    with pytest.raises(sqlite3.OperationalError):
        turn_reconcile._extract_facts(mem.db, 0, time.monotonic() + 30, {})
    meta = mem.db.execute("SELECT metadata_json FROM episodes WHERE id = 'r0'").fetchone()[0]
    assert "facts_failed" not in meta


def test_a_bug_is_still_reported_without_raising(mem, monkeypatch):
    """Not a store refusal: the old shape stays, but named and not quiet."""
    from kazma_core.memory.belief_mutation import mutate_belief

    def broken(*_a, **_k):
        raise KeyError("predicate")

    monkeypatch.setattr("kazma_core.memory.belief_mutation._mutate_set", broken)
    result = mutate_belief(mem.db, "user", "likes", "tea", predicate_type="set")
    assert result["action"] == "noop" and result["error"] == "KeyError"


def test_negative_control_the_old_noop_moved_the_cursor_past_the_turn(mem):
    """The old handler turned the same refusal into a "noop": the reconcile
    pass then counted the turn done and moved past it with no fact written."""
    from kazma_core.memory import belief_mutation, turn_reconcile

    real = belief_mutation.mutate_belief

    def old_mutate(*a, **k):
        try:
            return real(*a, **k)
        except Exception:
            return {"action": "noop", "belief_id": "", "superseded_id": None}

    _reconciled_turn(mem)
    _lock_the_store(mem)
    mem.mp.setattr(belief_mutation, "mutate_belief", old_mutate)
    report = turn_reconcile._extract_facts(mem.db, 0, time.monotonic() + 30, {})
    assert report["facts_turns"] == 1 and report["facts_after"] > 0
    facts = mem.db.execute("SELECT COUNT(*) FROM beliefs WHERE predicate = 'lives_in'").fetchone()[0]
    assert facts == 0
