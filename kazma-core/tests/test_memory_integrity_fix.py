"""Solid write-path: L3 timestamps + embeddings, L2 graph population."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import struct
from pathlib import Path

import pytest


def _fake_blob(n: int = 8) -> bytes:
    return struct.pack(f"<{n}f", *([0.1] * n))


@pytest.mark.asyncio
async def test_adapter_l3_writes_timestamp_and_embedding(tmp_path: Path, monkeypatch):
    from kazma_core.swarm.memory.adapter import UnifiedMemoryAdapter
    from kazma_core.swarm.memory.fts5 import FTS5LexicalStore
    from kazma_core.swarm.memory.graph import KnowledgeGraph

    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.encode_text_to_blob",
        lambda text: _fake_blob() if text else None,
    )
    # Also used inside search_backend.index auto-embed path
    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.resolve_unix_timestamp",
        lambda meta=None: 1_700_000_000,
    )

    db = tmp_path / "memory.db"
    gdb = tmp_path / "kg.db"
    l3 = FTS5LexicalStore(db_path=str(db))
    l2 = KnowledgeGraph(path=str(gdb))
    adapter = UnifiedMemoryAdapter(graph=l2, fts5_store=l3)

    doc = await adapter.store(
        "I prefer dark mode for the editor",
        metadata={"source": "test_integrity", "type": "preference"},
    )
    assert doc, "store must return durable id"

    # Wait for aiosqlite commit path
    await asyncio.sleep(0.05)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT timestamp, embedding, source, metadata FROM memories WHERE id = ?",
        (doc,),
    ).fetchone()
    conn.close()
    assert row is not None
    ts, emb, source, meta = row
    assert int(ts) > 0, f"timestamp must be real, got {ts}"
    assert emb is not None and len(emb) > 0, "embedding BLOB must be populated"
    assert source

    # Graph must have chunk + user hub + heuristic prefers triple
    stats = l2.stats()
    assert stats["nodes"] >= 2, stats
    assert stats["edges"] >= 1, stats
    hits = l2.search("dark", limit=5)
    assert hits, "graph FTS should find the memory chunk"


@pytest.mark.asyncio
async def test_fts5memory_add_writes_emb_and_ts(tmp_path: Path, monkeypatch):
    from kazma_core.memory.fts5 import FTS5Memory

    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.encode_text_to_blob",
        lambda text: _fake_blob(4),
    )
    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.resolve_unix_timestamp",
        lambda meta=None: 1_700_000_123,
    )

    m = FTS5Memory(db_path=str(tmp_path / "m.db"))
    mid = m.add("Remember my favorite color is teal.")
    row = m._conn.execute(
        "SELECT timestamp, length(embedding) FROM memories WHERE id = ?", (mid,)
    ).fetchone()
    assert row[0] == 1_700_000_123
    assert row[1] and row[1] > 0
    m.close()


def test_broken_fts_delete_command_triggers_are_repaired(tmp_path: Path):
    """Legacy FTS5 'delete' command triggers must not block UPDATE."""
    from kazma_core.memory.schema import ensure_memories_schema_sync

    db = tmp_path / "broken.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, content_arabic TEXT,
            metadata TEXT, timestamp INTEGER DEFAULT 0, source TEXT,
            relevance REAL, embedding BLOB, tenant_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id, content, content_arabic)"
    )
    # Install the broken trigger form found in production DBs
    conn.executescript(
        """
        CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, memory_id, content, content_arabic)
            VALUES ('delete', old.id, old.content, old.content_arabic);
            INSERT INTO memories_fts(memory_id, content, content_arabic)
            VALUES (new.id, new.content, new.content_arabic);
        END;
        """
    )
    conn.execute(
        "INSERT INTO memories (id, content, timestamp) VALUES ('x1', 'hello world', 0)"
    )
    conn.commit()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE memories SET timestamp=1 WHERE id='x1'")
        conn.commit()
    conn.rollback()

    ensure_memories_schema_sync(conn)
    conn.execute("UPDATE memories SET timestamp=42, embedding=? WHERE id='x1'", (b"\x00" * 16,))
    conn.commit()
    row = conn.execute(
        "SELECT timestamp, length(embedding) FROM memories WHERE id='x1'"
    ).fetchone()
    assert row[0] == 42
    assert row[1] == 16
    conn.close()


def test_backfill_repairs_zero_ts_and_null_emb(tmp_path: Path, monkeypatch):
    from kazma_core.memory.backfill import (
        backfill_graph_from_memories,
        backfill_l3_timestamps_and_embeddings,
    )
    from kazma_core.swarm.memory.graph import KnowledgeGraph, reset_knowledge_graph

    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.encode_text_to_blob",
        lambda text: _fake_blob(4) if "teal" in text else _fake_blob(4),
    )
    monkeypatch.setattr(
        "kazma_core.swarm.memory.embedder.resolve_unix_timestamp",
        lambda meta=None: 1_700_000_999,
    )

    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_arabic TEXT,
            metadata TEXT DEFAULT '{}',
            timestamp INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            relevance REAL DEFAULT 1.0,
            embedding BLOB,
            tenant_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO memories (id, content, metadata, timestamp, source) VALUES (?,?,?,?,?)",
        (
            "legacy1",
            "User prefers teal accents",
            json.dumps({"timestamp": "2026-01-15T12:00:00+00:00"}),
            0,
            "memory",
        ),
    )
    conn.commit()
    conn.close()

    stats = backfill_l3_timestamps_and_embeddings(db_path=db, encode=True)
    assert stats["ts_fixed"] >= 1
    assert stats["emb_fixed"] >= 1

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT timestamp, length(embedding) FROM memories WHERE id='legacy1'"
    ).fetchone()
    conn.close()
    assert row[0] > 0
    assert row[1] > 0

    # Graph backfill uses process singleton — point path via env/path
    gdb = tmp_path / "kg.db"
    reset_knowledge_graph()
    kg = KnowledgeGraph(path=str(gdb))
    # monkeypatch get_knowledge_graph
    monkeypatch.setattr(
        "kazma_core.swarm.memory.graph.get_knowledge_graph",
        lambda: kg,
    )
    gstats = backfill_graph_from_memories(db_path=db)
    assert gstats["nodes"] >= 1
    assert kg.stats()["nodes"] >= 2  # user + chunk
