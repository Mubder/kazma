"""Regression: L3 FTS5LexicalStore.get_texts must not recurse.

A dual definition of get_texts (SQL batch + loop via get_text) caused
RecursionError on every UnifiedMemoryAdapter L3 hydrate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_get_texts_batch_sql_no_recursion(tmp_path: Path) -> None:
    """Index one row and hydrate via get_texts without RecursionError."""
    from kazma_core.swarm.memory.fts5 import FTS5LexicalStore

    db = tmp_path / "memory.db"
    store = FTS5LexicalStore(db_path=str(db))
    if not store.available:
        pytest.skip("kazma-memory not installed")

    mid = await store.index(
        {
            "id": "mem-l3-1",
            "content": "bulletproof workspace binding regression content",
            "metadata": {},
        }
    )
    assert mid is not None

    texts = await store.get_texts([mid, "missing-id"])
    assert mid in texts
    assert "bulletproof" in texts[mid]
    assert "missing-id" not in texts or texts.get("missing-id") == ""

    single = await store.get_text(mid)
    assert "bulletproof" in single

    await store.close()


@pytest.mark.asyncio
async def test_get_texts_many_ids_no_recursion(tmp_path: Path) -> None:
    """Batch path must stay iterative for many IDs (no per-id recursion)."""
    from kazma_core.swarm.memory.fts5 import FTS5LexicalStore

    db = tmp_path / "memory.db"
    store = FTS5LexicalStore(db_path=str(db))
    if not store.available:
        pytest.skip("kazma-memory not installed")

    ids: list[str] = []
    for i in range(12):
        mid = await store.index(
            {
                "id": f"mem-batch-{i}",
                "content": f"batch content number {i} workspace",
                "metadata": {},
            }
        )
        assert mid is not None
        ids.append(mid)

    texts = await store.get_texts(ids)
    assert len(texts) >= 12
    for mid in ids:
        assert mid in texts
        assert "batch content" in texts[mid]

    await store.close()
