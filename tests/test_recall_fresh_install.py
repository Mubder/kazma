"""A fresh install's first question recalls nothing -- quietly.

The memory schema is created by the first write (an episode after a turn),
so the first turn's recall read a database with no tables: it logged
``[recall] episode LIKE fallback failed: no such table: episodes`` at ERROR
with a traceback, and opening it with a plain ``sqlite3.connect`` created an
empty ``memory_state.db`` as a side effect of a read (2026-09-26, every run
of the unified-turn harness). Nothing was wrong: nothing had been remembered
yet. recall() now opens the database only if it exists and treats a missing
schema as "nothing yet".
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
    from kazma_core.memory import dual_write

    dual_write._reset_mirror()
    yield tmp_path
    dual_write._reset_mirror()


@pytest.fixture
def degraded(monkeypatch):
    marks: list[str] = []
    import kazma_core.memory.health as health

    monkeypatch.setattr(health, "mark_recall_degraded", lambda msg: marks.append(msg))
    return marks


def _loud(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name.startswith("kazma_core.memory") and r.levelno >= logging.WARNING
    ]


def test_no_database_yet_is_nothing_to_recall(data_dir, caplog, degraded) -> None:
    from kazma_core.memory.recall import recall
    from kazma_core.paths import primary_memory_db

    caplog.set_level(logging.DEBUG, logger="kazma_core.memory")
    result = recall("what did we decide about the deploy", limit=5)
    assert result.empty
    assert _loud(caplog) == [] and degraded == []
    assert not Path(primary_memory_db()).exists(), "a read created the database"


def test_a_database_without_the_schema_is_nothing_to_recall(data_dir, caplog, degraded) -> None:
    from kazma_core.memory.recall import recall
    from kazma_core.paths import primary_memory_db

    Path(primary_memory_db()).parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(primary_memory_db()).close()  # exists, no tables
    caplog.set_level(logging.DEBUG, logger="kazma_core.memory")
    result = recall("what did we decide about the deploy", limit=5)
    assert result.empty
    assert _loud(caplog) == [] and degraded == []


def test_negative_control_without_the_check_the_error_comes_back(
    data_dir, caplog, degraded, monkeypatch
) -> None:
    import kazma_core.memory.recall as rec
    from kazma_core.paths import primary_memory_db

    Path(primary_memory_db()).parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(primary_memory_db()).close()
    monkeypatch.setattr(rec, "_memory_schema_present", lambda conn: True)
    caplog.set_level(logging.DEBUG, logger="kazma_core.memory")
    rec.recall("what did we decide about the deploy", limit=5)
    assert any("no such table" in m for m in _loud(caplog) + degraded), (_loud(caplog), degraded)


def test_recall_still_reads_a_real_memory(data_dir) -> None:
    """The positive control: the open-existing path finds a stored episode."""
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(conn)
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e1", "default", "s1", 1, "The deploy window moved to Thursday night", "recall", time.time()),
    )
    conn.commit()
    conn.close()
    result = recall("deploy window Thursday", limit=5)
    assert any("Thursday" in h.content for h in result.episodes), result
