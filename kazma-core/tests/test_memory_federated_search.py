"""Horizon A: federated search (memory + KB labeled, not merged)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    try:
        import kazma_core.paths as paths

        monkeypatch.setattr(paths, "primary_memory_db", lambda: str(db))
    except Exception:
        pass
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def test_format_source_footer():
    from kazma_core.memory.federated_search import format_source_footer

    assert "2 beliefs" in format_source_footer(beliefs=2, episodes=1)
    assert "1 episode" in format_source_footer(beliefs=0, episodes=1)
    assert format_source_footer() == ""


def test_federated_memory_hits(mem_db, monkeypatch):
    from kazma_core.memory.federated_search import federated_search

    now = time.time()
    mem_db.execute(
        """INSERT INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, tier, created_at)
           VALUES ('ep-teal','default','s',1,'My favorite color is teal','episodic',?)""",
        (now,),
    )
    mem_db.commit()

    # Skip KB store in this unit (empty / may not be initialized)
    monkeypatch.setattr(
        "kazma_core.memory.federated_search._search_knowledge",
        lambda *a, **k: [],
    )
    out = federated_search("teal color", tenant_id="default", include_knowledge=False)
    assert out["ok"] is True
    assert out["summary"]["memory"] >= 1
    assert all(h["store"] == "memory" for h in out["hits"])
    assert any("teal" in (h["content"] or "").lower() for h in out["hits"])


def test_federated_knowledge_label(monkeypatch):
    from kazma_core.memory import federated_search as fs

    def _fake_kb(query, limit=5, **kwargs):
        return [
            {
                "store": "knowledge",
                "kind": "chunk",
                "id": "c1",
                "content": "OAuth tokens expire after one hour",
                "score": 1.2,
                "source": "kb_fts",
                "sources": ["kb_fts"],
                "provenance": {
                    "library_id": "lib1",
                    "document_title": "Auth Guide",
                    "source_url": "https://example.com/auth",
                },
            }
        ]

    monkeypatch.setattr(fs, "_search_knowledge", _fake_kb)

    class _Empty:
        beliefs = []
        episodes = []
        empty = True

    monkeypatch.setattr(
        "kazma_core.memory.recall.recall",
        lambda *a, **k: _Empty(),
    )
    out = fs.federated_search(
        "OAuth tokens", include_memory=True, include_knowledge=True
    )
    assert out["ok"] is True
    assert out["summary"]["knowledge"] >= 1
    assert any(h["store"] == "knowledge" for h in out["hits"])
    kb = next(h for h in out["hits"] if h["store"] == "knowledge")
    assert kb["provenance"]["document_title"] == "Auth Guide"


def test_format_recall_block_has_footer(mem_db):
    from kazma_core.memory.recall import RecallHit, RecallResult, format_recall_block

    r = RecallResult(
        beliefs=[
            RecallHit(id="b1", content="user likes teal", score=1.0, kind="belief")
        ],
        episodes=[],
    )
    block = format_recall_block(r, explain=False)
    assert "Sources used" in block or "belief" in block.lower()
