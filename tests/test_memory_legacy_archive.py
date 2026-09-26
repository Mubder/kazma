"""Memories stranded in a table recall does not read come back (item J).

On 2026-08-02/03 a one-off operation (never committed) moved 329 episodes
into ``episodes_archive`` -- July's carried-over memories, 39 notes the user
asked Kazma to keep, 10 chat turns -- and nothing ever read that table again.
``memory/legacy_tables.py`` restores them as cold memories. The table is
built here exactly as it exists on the live install, timestamps included
(most in Unix seconds, 25 ``archived_at`` values in julian days).
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import struct
from datetime import UTC, datetime

import pytest

from kazma_core.memory import legacy_tables
from kazma_core.memory.schema_v2 import ensure_primary_schema

DIM = 16

#: The legacy table's shape on the live install (``PRAGMA table_info``).
LEGACY_DDL = (
    "CREATE TABLE episodes_archive (id TEXT, tenant_id TEXT, session_id TEXT, "
    "turn_number INTEGER, user_text TEXT, assistant_text TEXT, summary_text TEXT, "
    "tier TEXT, structural_importance INTEGER, access_count INTEGER, last_accessed REAL, "
    "created_at REAL, expires_at REAL, embedding_model_version TEXT, metadata_json TEXT, "
    "embedding BLOB, archived_at REAL)"
)
CREATED = 1784379162.0  # 2026-07-18
ARCHIVED_EPOCH = 1785711428.0  # 2026-08-02 22:57
ARCHIVED_JULIAN = 2461255.546142083  # the same operation's second run, in julian days


def _unit(seed: int) -> list[float]:
    rnd = random.Random(seed)
    vec = [rnd.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    db.execute(LEGACY_DDL)
    db.commit()
    yield db
    db.close()


def _stranded(db, eid, text, *, tenant="default", vec=None, archived=ARCHIVED_EPOCH,
              source="memory_store_tool"):
    db.execute(
        "INSERT INTO episodes_archive VALUES (?,?,?,0,?,NULL,NULL,'episodic',4,0,NULL,?,NULL,"
        "'m',?,?,?)",
        (eid, tenant, "legacy-" + eid, text, CREATED, json.dumps({"source": source}),
         _blob(vec) if vec else None, archived),
    )
    db.commit()


def _live(db, eid, text, *, tenant="default"):
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, "
        "created_at, metadata_json) VALUES (?,?,'s',1,?,'episodic',?, '{}')",
        (eid, tenant, text, CREATED),
    )
    db.commit()


def _row(db, eid):
    return db.execute("SELECT * FROM episodes WHERE id = ?", (eid,)).fetchone()


def test_a_stranded_memory_comes_back_whole_and_cold(conn):
    vec = _unit(1)
    _stranded(conn, "e_passport", "My passport expires in March 2031", vec=vec,
              archived=ARCHIVED_JULIAN)
    assert _row(conn, "e_passport") is None  # stranded: recall's table lacks it

    report = legacy_tables.restore_legacy_episode_archive(conn)

    row = _row(conn, "e_passport")
    assert report["restored"] == 1
    assert row["tier"] == "archived"
    assert row["user_text"] == "My passport expires in March 2031"
    assert row["embedding"] == _blob(vec)
    assert row["created_at"] == CREATED
    meta = json.loads(row["metadata_json"])
    assert meta["source"] == "memory_store_tool"
    assert meta["restored_from"] == "episodes_archive"
    when = datetime.fromtimestamp(meta["legacy_archived_at"], UTC)
    assert (when.year, when.month, when.day) == (2026, 8, 3)  # julian day read as a date


def test_recall_reaches_a_restored_memory_and_could_not_before(conn, monkeypatch):
    """Both recall channels see it once it is back: by meaning (its own
    vector, a question sharing no word with it) and by words."""
    from kazma_core.memory.recall import recall

    question = "when does my travel document run out"
    meaning = _unit(2)

    class _Embedder:
        """Like a real model: the words query means the memory too."""

        dim = DIM

        def encode(self, text):
            if text == question or "passport" in text.lower():
                return meaning
            return _unit(abs(hash(text)) % 10_000)

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Embedder())
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m")
    _stranded(conn, "e_passport", "Passport renewal: valid until March 2031", vec=meaning)
    for i in range(20):
        _live(conn, f"e_other{i}", f"note about topic {i} and the weather")

    def by_meaning():
        # recall() itself: under rank fusion this memory lost to twenty notes
        # whose "about" held the question's "out" (Stage 2 R1/R2 fixed it).
        return [h.id for h in recall(question, conn=conn, limit=5).episodes]

    def by_words():
        return [h.id for h in recall("passport renewal", conn=conn, limit=5).episodes]

    assert "e_passport" not in by_meaning() + by_words()  # negative control: stranded

    legacy_tables.restore_legacy_episode_archive(conn)

    assert by_meaning()[0] == "e_passport"
    assert "e_passport" in by_words()


def test_nothing_is_overwritten_doubled_or_mixed_across_tenants(conn):
    _live(conn, "e_same_id", "the live text wins")
    _stranded(conn, "e_same_id", "a different legacy text under the same id")
    _live(conn, "e_live_copy", "Standup moved to 09:30")
    _stranded(conn, "e_legacy_copy", "  standup   moved to 09:30 ")  # same memory
    _stranded(conn, "e_twin_a", "Pick up the dry cleaning")
    _stranded(conn, "e_twin_b", "Pick up the dry cleaning")
    _live(conn, "e_acme_live", "Budget is 40k", tenant="acme")
    _stranded(conn, "e_default_budget", "Budget is 40k")  # another tenant's text

    report = legacy_tables.restore_legacy_episode_archive(conn)

    assert _row(conn, "e_same_id")["user_text"] == "the live text wins"
    assert _row(conn, "e_legacy_copy") is None
    assert (_row(conn, "e_twin_a") is None) != (_row(conn, "e_twin_b") is None)
    assert _row(conn, "e_default_budget")["tenant_id"] == "default"
    assert report == {"restored": 2, "present": 1, "duplicate": 2, "empty": 0}


def test_it_runs_once_and_leaves_the_legacy_table_alone(conn):
    for i in range(3):
        _stranded(conn, f"e_{i}", f"memory number {i}")
    first = legacy_tables.restore_legacy_episode_archive(conn)
    second = legacy_tables.restore_legacy_episode_archive(conn)
    assert first["restored"] == 3
    assert second == {"restored": 0, "present": 3, "duplicate": 0, "empty": 0}
    assert conn.execute("SELECT count(*) FROM episodes_archive").fetchone()[0] == 3


def test_a_database_without_the_table_is_untouched(tmp_path):
    db = sqlite3.connect(str(tmp_path / "m.db"))
    ensure_primary_schema(db)
    assert legacy_tables.restore_legacy_episode_archive(db) == {
        "restored": 0, "present": 0, "duplicate": 0, "empty": 0
    }
    assert legacy_tables.legacy_archive_counts(db)["total"] == 0


def test_health_warns_while_memories_are_stranded(conn):
    from kazma_core.memory.health import _findability_component

    _stranded(conn, "e_a", "first")
    _stranded(conn, "e_b", "second")
    _live(conn, "e_c_live", "third")
    _stranded(conn, "e_c", "third")
    before = legacy_tables.legacy_archive_counts(conn)
    assert before == {"total": 3, "restored": 0, "duplicate": 1, "pending": 2}
    row = _findability_component({"legacy_archive": before})
    assert row["status"] == "warn" and "legacy archive" in row["detail"]

    legacy_tables.restore_legacy_episode_archive(conn)
    after = legacy_tables.legacy_archive_counts(conn)
    assert after == {"total": 3, "restored": 2, "duplicate": 1, "pending": 0}
    assert _findability_component({"legacy_archive": after})["status"] == "ok"


def test_the_recovery_pass_restores_them(conn, monkeypatch):
    """The 15-minute memory recovery sweep is what runs it on an install."""
    from kazma_core.memory import rehydrate

    monkeypatch.setattr(rehydrate, "_backup_copies", lambda: [])
    _stranded(conn, "e_note", "Remember: the gate code is 4417")
    report = rehydrate.run_rehydrate_pass(time_budget_s=5)
    assert report["legacy_archive"]["restored"] == 1
    assert _row(conn, "e_note")["tier"] == "archived"
