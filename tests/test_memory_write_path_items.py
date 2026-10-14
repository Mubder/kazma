"""Smaller write-path correctness items (Memory audit M26, MEM-R15).

* "remember" phrases match whole words, and "do you remember ...?" is a
  question, not a request ("Can you denote that" matched "note that");
* the unsafe belief writer ``dual_write.mirror_belief`` (no product caller,
  past the trust gate, the ledger and supersede) is gone;
* the current value of a single-valued fact is the newest, and extra current
  rows are closed (``LIMIT 1`` with no order read any of several);
* the deep pass is idempotent per episode (a durable marker);
* the embedder singleton is built once under a lock.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


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
    yield SimpleNamespace(db=db, mp=monkeypatch)
    db.close()


# ── the remember detector ─────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "asks"), [
    ("Can you denote that in the table?", False),
    ("Do you remember that film we talked about?", False),
    ("Did you remember my keys?", False),
    ("Remember that my sister is Noor.", True),
    ("Please note that the meeting moved to Tuesday.", True),
    ("Don’t forget my mother's birthday.", True),
    ("Can you remember that I am vegetarian?", True),
    ("تذكر أن اجتماعي يوم الثلاثاء", True),
    ("اشتريت تذكرة القطار", False),
])
def test_remember_phrases_are_whole_words(text, asks):
    from kazma_core.memory.remember_request import is_remember_request

    assert is_remember_request(text) is asks


def test_the_substring_rule_matched_denote():
    """Negative control: the old rule."""
    assert "note that" in "can you denote that in the table?"


# ── the dead writer ───────────────────────────────────────────────────────


def test_no_unsafe_belief_writer_is_left_in_the_episode_writer():
    from kazma_core.memory import dual_write

    assert not hasattr(dual_write, "mirror_belief")
    assert not hasattr(dual_write.DualWriteMirror, "mirror_belief")
    # Every public name it exports has a product caller.
    product = "\n".join(p.read_text(encoding="utf-8") for root in ("kazma-core/kazma_core", "kazma-ui/kazma_ui",
                                                                     "kazma-gateway/kazma_gateway")
                        for p in (REPO / root).rglob("*.py") if p.name != "dual_write.py")
    orphans = [name for name in dual_write.__all__ if name not in product]
    assert orphans == []


# ── the current value of a single-valued fact ─────────────────────────────


def test_the_newest_current_value_wins_and_extras_are_closed(mem):
    from kazma_core.memory.belief_mutation import mutate_belief

    mem.db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, valid_from, "
        "ingested_at, extraction_method) VALUES (?, 't', 'user', 'lives_in', 'functional', ?, 0.9, ?, ?, "
        "'user_explicit')", [("b_old", "Porto", 1000.0, 1000.0), ("b_new", "Lisbon", 2000.0, 2000.0)])
    mem.db.commit()
    out = mutate_belief(mem.db, "user", "lives_in", "Lisbon", predicate_type="functional", tenant_id="t",
                        extraction_method="user_explicit", now=3000.0)
    assert out["action"] == "noop" and out["belief_id"] == "b_new"
    current = [r[0] for r in mem.db.execute(
        "SELECT id FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL")]
    assert current == ["b_new"]
    assert mem.db.execute("SELECT valid_until FROM beliefs WHERE id = 'b_old'").fetchone()[0] == 2000.0


def test_the_unordered_lookup_read_the_older_row(mem):
    """Negative control: ``LIMIT 1`` with no order returns the first stored."""
    mem.db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, valid_from, "
        "ingested_at) VALUES (?, 't', 'user', 'lives_in', 'functional', ?, 0.9, ?, ?)",
        [("b_old", "Porto", 1000.0, 1000.0), ("b_new", "Lisbon", 2000.0, 2000.0)])
    row = mem.db.execute("SELECT id FROM beliefs WHERE subject='user' AND predicate='lives_in' AND tenant_id='t' "
                         "AND valid_until IS NULL AND invalidated_at IS NULL LIMIT 1").fetchone()
    assert row[0] == "b_old"


# ── the deep pass runs once per episode ───────────────────────────────────


def test_the_deep_pass_runs_once_per_episode(mem):
    from kazma_core.memory.worker_bootstrap import _handle_micro_consolidation

    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES ('e1', 't', 's', 1, 'My sister Noor is a curator', 'Nice', "
        "'episodic', ?, '{}')", (time.time(),))
    mem.db.commit()
    calls = []

    class _Client:
        async def chat(self, messages, **kw):
            calls.append(1)
            return json.dumps({"beliefs": []})

    mem.mp.setattr("kazma_core.model_registry.get_model_registry",
                   lambda: SimpleNamespace(get_client=lambda: _Client()))
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e1"})) is True
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e1"})) is True
    assert calls == [1]
    # Negative control: without the marker it runs again.
    mem.db.execute("UPDATE episodes SET metadata_json = '{}'")
    mem.db.commit()
    asyncio.run(_handle_micro_consolidation({"episode_id": "e1"}))
    assert calls == [1, 1]


# ── the embedder singleton ────────────────────────────────────────────────


def _race(target) -> None:
    threads = [threading.Thread(target=target) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)


def test_the_embedder_is_built_once_under_concurrent_first_calls(monkeypatch):
    from kazma_core.memory import embedder as E

    built = []

    def slow_build():
        time.sleep(0.05)
        built.append(1)
        # As the real builder does: it publishes the singleton itself.
        monkeypatch.setattr(E, "_embedder", object())
        return E._embedder

    monkeypatch.setattr(E, "_embedder", None)
    monkeypatch.setattr(E, "_create_embedder", slow_build)
    _race(E.get_embedder)
    assert built == [1]
    # Negative control: without the lock every first caller builds one.
    monkeypatch.setattr(E, "_embedder", None)
    built.clear()

    def unlocked():
        if E._embedder is None:
            slow_build()

    _race(unlocked)
    assert len(built) > 1
