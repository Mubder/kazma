"""Regression tests for the memory strengthen program (fail-closed, SoT, FTS)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kazma_core.swarm.memory.adapter import UnifiedMemoryAdapter


@pytest.mark.asyncio
async def test_store_fail_closed_empty_adapter():
    """Empty adapter must not invent a success id."""
    empty = UnifiedMemoryAdapter()
    doc = await empty.store("should_not_persist_anywhere", {"source": "test"})
    assert doc == ""
    hits = await empty.search("should_not_persist_anywhere", limit=3)
    assert hits == []


@pytest.mark.asyncio
async def test_store_fail_closed_l2_only():
    """L2 graph alone is not durable — store must return empty id."""

    class _Graph:
        available = True

        def add_entity(self, *a, **k):
            return None

        def add_relation(self, *a, **k):
            return None

        def query_by_type(self, *_a, **_k):
            return []

        def query_related(self, *_a, **_k):
            return []

    adapter = UnifiedMemoryAdapter(graph=_Graph())
    doc = await adapter.store("graph-only fact about teal", {"source": "test"})
    assert doc == ""


@pytest.mark.asyncio
async def test_store_succeeds_when_l3_writes(tmp_path: Path):
    """A durable L3 write alone is enough for a non-empty id."""
    from kazma_core.memory.fts5 import FTS5Memory

    class _L3:
        available = True

        def __init__(self, backend: FTS5Memory) -> None:
            self._b = backend

        async def index(self, memory: dict):
            return self._b.add(
                memory.get("content", ""),
                metadata=memory.get("metadata") or {},
                doc_id=memory.get("id"),
            )

        async def lexical_search(self, query: str, limit: int = 10):
            rows = self._b.search(query, limit=limit)
            return [(r["doc_id"], r["score"]) for r in rows]

        async def get_texts(self, ids: list[str]):
            # minimal: re-search
            out = {}
            for i in ids:
                # direct open via backend search is awkward; use sqlite
                with self._b._lock:
                    row = self._b._conn.execute(
                        "SELECT content FROM memories WHERE id = ?", (i,)
                    ).fetchone()
                if row:
                    out[i] = row[0]
            return out

    fts = FTS5Memory(db_path=str(tmp_path / "mem.db"))
    adapter = UnifiedMemoryAdapter(fts5_store=_L3(fts))
    probe = "UNIQUE_STRENGTHEN_FACT_teal_preference_99"
    doc = await adapter.store(probe, {"source": "test"})
    assert doc, "expected durable id from L3"
    hits = await adapter.search(probe, limit=3)
    assert hits, "expected search hit after store"
    assert any(probe in (h.get("content") or "") for h in hits)
    fts.close()


def test_memory_config_store_overlay(tmp_path: Path):
    """ConfigStore memory.enabled=false must disable per-turn and auto-store."""
    from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store
    from kazma_core.memory.config import (
        memory_auto_store_enabled,
        memory_enabled,
        memory_per_turn_enabled,
        read_memory_cfg,
    )

    db = tmp_path / "settings.db"
    store = ConfigStore(db_path=str(db))
    set_config_store(store)
    try:
        store.set("memory.enabled", False, category="memory")
        cfg = read_memory_cfg()
        assert cfg["enabled"] is False
        assert memory_enabled(cfg) is False
        assert memory_per_turn_enabled(cfg) is False
        assert memory_auto_store_enabled(cfg) is False

        store.set("memory.enabled", True, category="memory")
        store.set("memory.auto_store", False, category="memory")
        cfg2 = read_memory_cfg()
        assert memory_enabled(cfg2) is True
        assert memory_auto_store_enabled(cfg2) is False
        assert memory_per_turn_enabled(cfg2) is True
    finally:
        reset_config_store()


def test_fts5_uses_canonical_memories_table(tmp_path: Path):
    from kazma_core.memory.fts5 import FTS5Memory
    import sqlite3

    path = tmp_path / "m.db"
    # Seed legacy table
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE VIRTUAL TABLE memory_fts USING fts5(
            text, metadata, doc_id UNINDEXED, timestamp UNINDEXED
        )
        """
    )
    conn.execute(
        "INSERT INTO memory_fts (text, metadata, doc_id, timestamp) VALUES (?,?,?,?)",
        ("legacy teal fact", "{}", "legacy-1", "t"),
    )
    conn.commit()
    conn.close()

    fts = FTS5Memory(db_path=str(path))
    # Migrated row should be searchable
    hits = fts.search("legacy teal fact", limit=5)
    assert hits, "expected migrated legacy row"
    assert fts.count() >= 1
    # New writes go to memories
    fts.add("new durable fact", {"source": "test"}, doc_id="new-1")
    hits2 = fts.search("new durable fact", limit=3)
    assert hits2
    fts.close()


@pytest.mark.asyncio
async def test_empty_content_hits_filtered():
    """RRF results with empty content must not surface in search()."""

    class _L1:
        available = True

        def query(self, text, limit=10, tenant_id=None):
            return [("empty-id", 0.99)]

        def get_documents(self, ids):
            return {"empty-id": ""}  # empty body

    adapter = UnifiedMemoryAdapter(vector_store=_L1())
    hits = await adapter.search("anything", limit=5)
    assert hits == []
