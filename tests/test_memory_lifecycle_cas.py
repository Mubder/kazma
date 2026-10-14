"""Lifecycle passes that cannot be stopped by one bad row, cannot lose
vectors, and cannot overwrite a change they did not read (Memory audits M27,
M28 and M29: MEM-L12, MEM-L13, MEM-L14).

Found 2026-10-13: one reconciled turn whose fact extraction always failed
kept the cursor before it, so no later reconciled turn ever had its facts;
the embedding rebuild NULLed every old vector and committed before it knew
the model would load, and kept its first pre-rebuild copy forever; macro
sleep wrote tiers from a stale read, so a forget during the sweep got its
"forgotten" overwritten.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest

BLOB = b"\x00\x00\x80?" * 8


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield SimpleNamespace(db=db, mp=monkeypatch, dir=tmp_path)
    db.close()


# ── M27: a poison turn is set aside, the cursor moves on ──────────────────


def _reconciled(mem, n: int = 3) -> None:
    for i in range(n):
        mem.db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, metadata_json) VALUES (?, 'default', 'cX', ?, ?, 'ok', 'episodic', ?, ?)",
            (f"r{i}", i + 1, f"question {i}", time.time() - 3600, json.dumps({"source": "turn_reconcile"})))
    mem.db.commit()


def test_a_poison_turn_is_set_aside_and_later_turns_get_their_facts(mem):
    from kazma_core.memory import turn_reconcile

    _reconciled(mem)
    seen: list[int] = []

    def extract(messages, *, session_id, turn, tenant_id, now):
        seen.append(turn)
        if turn == 1:
            raise ValueError("this turn always fails")

    mem.mp.setattr("kazma_core.memory.consolidator._v2_extract_sync", extract)
    mem.mp.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")
    after, attempts = 0, {}
    for _ in range(turn_reconcile._FACT_ATTEMPTS):  # three 15-minute passes
        report = turn_reconcile._extract_facts(mem.db, after, time.monotonic() + 30, attempts)
        after, attempts = report["facts_after"], report["fact_attempts"]
    assert seen == [1, 1, 1, 2, 3]
    assert report["facts_set_aside"] == 1 and report["facts_turns"] == 2 and attempts == {}
    meta = json.loads(mem.db.execute("SELECT metadata_json FROM episodes WHERE id = 'r0'").fetchone()[0])
    assert "this turn always fails" in meta["facts_failed"]


def test_a_locked_database_is_never_a_verdict(mem):
    """Negative control: an outage raises to the runner and counts nothing."""
    from kazma_core.memory import turn_reconcile

    _reconciled(mem, 1)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    mem.mp.setattr("kazma_core.memory.consolidator._v2_extract_sync", locked)
    for _ in range(turn_reconcile._FACT_ATTEMPTS + 1):
        with pytest.raises(sqlite3.OperationalError):
            turn_reconcile._extract_facts(mem.db, 0, time.monotonic() + 30, {})
    assert "facts_failed" not in mem.db.execute("SELECT metadata_json FROM episodes").fetchone()[0]


def test_the_attempts_survive_between_passes(mem):
    """The reconcile pass keeps the count in its state (in the memory
    database since 2026-10-14, beside the cursors)."""
    from kazma_core.memory import turn_reconcile

    _reconciled(mem, 1)
    mem.mp.setattr(turn_reconcile, "_reconcile",
                   lambda conn, after, deadline, now: {"turns_written": 0, "after": list(after), "sessions": 0,
                                                       "done": True})

    def broken(*a, **k):
        raise ValueError("nope")

    mem.mp.setattr("kazma_core.memory.consolidator._v2_extract_sync", broken)
    turn_reconcile.run_turn_reconcile_pass()
    assert turn_reconcile.read_state(None)["fact_attempts"] == {"r0": 1}


# ── M28: a rebuild that cannot encode changes nothing ─────────────────────


def _vectored(mem) -> None:
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, "
        "embedding, embedding_model_version) VALUES ('e1', 'default', 'c', 1, 'q', 'episodic', 1.0, ?, "
        "'old-model')", (BLOB,))
    mem.db.commit()


def test_a_rebuild_without_a_model_keeps_every_vector(mem):
    from kazma_core.memory import reembed

    _vectored(mem)
    with pytest.raises(RuntimeError):
        reembed.rebuild_embeddings(model_name="new-model")
    assert mem.db.execute("SELECT embedding FROM episodes WHERE id = 'e1'").fetchone()[0] == BLOB
    assert not (mem.dir / "memory_state.db.pre_reembed").exists()


def test_a_rebuild_re_encodes_in_place_and_refreshes_its_copy(mem):
    from kazma_core.memory import reembed

    class Emb:
        dim = 8

        def encode(self, text):
            return [0.5] * 8

    _vectored(mem)
    stale = mem.dir / "memory_state.db.pre_reembed"
    stale.write_bytes(b"a copy from a rebuild long ago")
    old = time.time() - 86400
    os.utime(stale, (old, old))
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: Emb())
    out = reembed.rebuild_embeddings(model_name="new-model")
    assert out["episodes"] == 1 and out["skipped_rows"] == 0
    row = mem.db.execute("SELECT embedding, embedding_model_version FROM episodes WHERE id = 'e1'").fetchone()
    assert row[1] == "new-model" and row[0] != BLOB
    assert stale.stat().st_mtime > old + 3600  # taken again, not the old copy
    assert sqlite3.connect(str(stale)).execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


# ── M29: tier moves are compare-and-set ───────────────────────────────────


def test_a_forget_during_the_sweep_stays_forgotten(mem):
    from kazma_core.memory import macro_sleep

    now = time.time()
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, metadata_json) "
        "VALUES ('w1', 'default', 's', 1, 'remember the pump', 'working', ?, '{}')", (now - 3 * 86400,))
    mem.db.commit()
    real = mem.db.execute

    class _Conn:
        """The sweep's connection; a forget lands right after its read."""

        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *args):
            out = real(sql, *args)
            if "FROM episodes WHERE tenant_id=?" in sql:
                rows = out.fetchall()
                other = sqlite3.connect(str(mem.dir / "memory_state.db"))
                other.execute("UPDATE episodes SET tier = 'forgotten', user_text = '' WHERE id = 'w1'")
                other.commit()
                other.close()
                return SimpleNamespace(fetchall=lambda: rows)
            return out

        def __getattr__(self, name):
            return getattr(self._c, name)

    stats = macro_sleep.run_macro_sleep(_Conn(mem.db), cfg={}, tenant_id="default", now=now)
    assert stats["demoted_working"] == 1 and stats["moves_skipped"] == 1
    assert mem.db.execute("SELECT tier FROM episodes WHERE id = 'w1'").fetchone()[0] == "forgotten"


def test_without_the_expected_tier_the_forget_was_overwritten(mem):
    """Negative control: the old statement, no expected tier."""
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, metadata_json) "
        "VALUES ('w1', 'default', 's', 1, '', 'forgotten', 1.0, '{}')")
    mem.db.execute("UPDATE episodes SET tier='episodic' WHERE id=?", ("w1",))
    assert mem.db.execute("SELECT tier FROM episodes WHERE id = 'w1'").fetchone()[0] == "episodic"
