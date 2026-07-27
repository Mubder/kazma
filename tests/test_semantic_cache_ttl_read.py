"""Semantic cache respects TTL on read path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kazma_core.swarm.semantic_cache import SemanticCache


def test_lookup_skips_expired_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SEMANTIC_CACHE_TTL_SECONDS", "1")
    db = tmp_path / "sc.db"
    cache = SemanticCache(db_path=str(db))
    cache.store("hello world prompt", {"content": "cached-answer"}, tools=None)

    # Force created_at into the past
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE semantic_cache SET created_at = datetime('now', '-2 days')"
        )
        conn.commit()

    hit = cache.lookup("hello world prompt", tools=None)
    assert hit is None
