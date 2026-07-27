"""P1 sqlite authorizer functions, P2 legacy FTS cleanup, P3 swarm L4 index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def allow_any_db_path(monkeypatch: pytest.MonkeyPatch):
    """Bypass workspace/path allowlists so pytest tmp_path DBs are readable."""
    monkeypatch.setattr(
        "kazma_skills.native.database_client.tools._is_path_allowed",
        lambda _p: True,
    )
    monkeypatch.setattr(
        "kazma_skills.native.database_client.tools._workspace_scope_error",
        lambda *_a, **_k: None,
    )


@pytest.mark.asyncio
async def test_p1_sqlite_query_allows_count_like_substr(
    tmp_path: Path, allow_any_db_path
):
    """Safe read functions must not be denied by the SQLite authorizer."""
    from kazma_skills.native.database_client.tools import execute_db_query

    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO items (name) VALUES ('alpha'), ('beta'), ('alphabet')")
    conn.commit()
    conn.close()

    # COUNT
    out = await execute_db_query(str(db), "SELECT COUNT(*) AS n FROM items")
    assert "SQL Error" not in out, out
    assert "not authorized" not in out.lower(), out
    rows = json.loads(out)
    assert rows and int(rows[0].get("n", 0)) == 3

    # LIKE + length + substr
    out2 = await execute_db_query(
        str(db),
        "SELECT name, length(name) AS ln, substr(name, 1, 3) AS pre "
        "FROM items WHERE name LIKE '%pha%'",
    )
    assert "not authorized" not in out2.lower(), out2
    assert "SQL Error" not in out2, out2
    rows2 = json.loads(out2)
    assert any(r.get("name") == "alpha" for r in rows2)


@pytest.mark.asyncio
async def test_p1_sqlite_query_still_blocks_writes(tmp_path: Path, allow_any_db_path):
    from kazma_skills.native.database_client.tools import execute_db_query

    db = tmp_path / "t2.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    out = await execute_db_query(str(db), "DELETE FROM t")
    assert "not allowed" in out.lower() or "error" in out.lower()


def test_p2_empty_legacy_memory_fts_retired(tmp_path: Path):
    """Empty legacy memory_fts must be renamed/dropped, not left empty forever."""
    from kazma_core.memory.fts5 import FTS5Memory

    db = tmp_path / "mem.db"
    # Pre-create legacy empty table the way old installs did
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "text, metadata, doc_id UNINDEXED, timestamp UNINDEXED)"
    )
    conn.commit()
    conn.close()

    m = FTS5Memory(db_path=str(db))
    # Live path uses memories_fts
    tables = {
        r[0]
        for r in m._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }
    assert "memory_fts" not in tables, "legacy empty memory_fts should be retired"
    assert "memories_fts" in tables or any("memories" in t for t in tables)
    # Either renamed or dropped is fine
    assert "memory_fts_migrated" in tables or "memory_fts" not in tables
    m.close()


def test_p2_legacy_memory_fts_with_rows_migrated(tmp_path: Path):
    """Non-empty legacy table migrates rows then retires the name."""
    from kazma_core.memory.fts5 import FTS5Memory

    db = tmp_path / "mem2.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "text, metadata, doc_id UNINDEXED, timestamp UNINDEXED)"
    )
    conn.execute(
        "INSERT INTO memory_fts (text, metadata, doc_id, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("remember teal is favorite", "{}", "doc-teal-1", "2026-01-01"),
    )
    conn.commit()
    conn.close()

    m = FTS5Memory(db_path=str(db))
    tables = {
        r[0]
        for r in m._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }
    assert "memory_fts" not in tables
    row = m._conn.execute(
        "SELECT content FROM memories WHERE id = ?", ("doc-teal-1",)
    ).fetchone()
    assert row is not None
    assert "teal" in (row[0] or "")
    m.close()


@pytest.mark.asyncio
async def test_p3_index_worker_l4_sets_worker_meta(monkeypatch: pytest.MonkeyPatch):
    """Successful worker dispatch path indexes with worker name in metadata."""
    from kazma_core.swarm import worker_dispatch as wd

    captured: list[dict] = []

    class _FakeAdapter:
        async def store(self, text, metadata=None):
            captured.append({"text": text, "metadata": dict(metadata or {})})
            return "id1"

    monkeypatch.setattr(
        "kazma_core.swarm.memory.adapter.get_adapter",
        lambda: _FakeAdapter(),
    )
    await wd._index_worker_l4_memory(
        worker_name="Observer",
        prompt="Summarize the repo structure",
        output="Found 12 packages under monorepo.",
        task_id="task-abc",
    )
    assert captured
    assert captured[0]["metadata"].get("worker") == "Observer"
    assert captured[0]["metadata"].get("source") == "swarm_worker"
    assert "Result:" in captured[0]["text"] or "Found 12" in captured[0]["text"]


@pytest.mark.asyncio
async def test_p3_index_skips_empty_output(monkeypatch: pytest.MonkeyPatch):
    """No store when prompt and output are empty/trivial."""
    from kazma_core.swarm import worker_dispatch as wd

    called = []

    class _FakeAdapter:
        async def store(self, text, metadata=None):
            called.append(True)
            return "id1"

    monkeypatch.setattr(
        "kazma_core.swarm.memory.adapter.get_adapter",
        lambda: _FakeAdapter(),
    )
    await wd._index_worker_l4_memory(
        worker_name="Observer",
        prompt="",
        output="",
        task_id="x",
    )
    assert not called
