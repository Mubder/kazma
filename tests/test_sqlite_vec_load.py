"""L4 sqlite-vec must load via the PyPI package, not only system vec0."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def test_sqlite_vec_package_loads():
    """If sqlite-vec is installed, SQLiteVectorStore.available is True."""
    pytest.importorskip("sqlite_vec")
    from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore

    db = Path(tempfile.mkdtemp()) / "vec.db"
    store = SQLiteVectorStore(db_path=str(db))
    assert store.available is True, (
        "sqlite-vec is installed but SQLiteVectorStore could not load it — "
        "check sqlite_vec.load(conn) wiring"
    )


def test_sqlite_vec_index_and_query_roundtrip():
    pytest.importorskip("sqlite_vec")
    from kazma_core.swarm.memory.embedder import reset_embedder, get_embedder
    from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore

    reset_embedder()
    emb = get_embedder()
    if emb is None or not emb.encode("warmup"):
        pytest.skip("embedder unavailable")

    db = Path(tempfile.mkdtemp()) / "vec2.db"
    store = SQLiteVectorStore(db_path=str(db))
    if not store.available:
        pytest.skip("sqlite-vec not loadable")

    assert store.index("default", "d1", "User favorite color is teal") is True
    hits = store.query("default", "favorite color", limit=3)
    assert hits, "expected at least one L4 hit"
    texts = store.get_texts("default", [hits[0][0]])
    assert "teal" in texts.get(hits[0][0], "").lower()
