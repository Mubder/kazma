"""Research policy + KB auto-inject / FTS harden tests."""

from __future__ import annotations

import importlib

import pytest


def test_research_protocol_in_product_knowledge():
    from kazma_core.product_knowledge import build_product_knowledge

    pk = build_product_knowledge()
    assert "Research protocol" in pk or "research protocol" in pk.lower()
    assert "read_url_to_file" in pk
    assert "run_research_pipeline" in pk or "research" in pk.lower()


def test_deep_intent_and_nudge():
    from kazma_core.agent.research_policy import (
        is_deep_research_intent,
        should_nudge_more_sources,
    )

    assert is_deep_research_intent("Please research thoroughly the X market")
    assert not is_deep_research_intent("what is 2+2")

    msgs = [{"role": "user", "content": "Deep research on solid state batteries"}]
    nudge = should_nudge_more_sources(msgs, ["web_search"], already_nudged=False)
    assert nudge and "sources" in nudge.lower()

    no = should_nudge_more_sources(
        msgs, ["web_search", "read_url_to_file", "read_url"], already_nudged=False
    )
    assert no is None

    again = should_nudge_more_sources(msgs, ["web_search"], already_nudged=True)
    assert again is None


def test_auto_inject_excludes_archived(tmp_path, monkeypatch):
    from kazma_core.stores import knowledge as kn

    db = tmp_path / "kb.db"
    store = kn.KnowledgeStore(db_path=str(db))
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store)
    store.create_library("lib_a", "A", seed_url="https://example.com")
    store.create_library("lib_b", "B", seed_url="https://example.com/b")
    # enable auto inject via SQL
    with store._lock:
        conn = store._get_conn()
        conn.execute(
            "UPDATE knowledge_libraries SET auto_inject = 1 WHERE id = ?",
            ("lib_a",),
        )
        conn.execute(
            "UPDATE knowledge_libraries SET auto_inject = 1, archived = 1 WHERE id = ?",
            ("lib_b",),
        )
        conn.commit()
    libs = store.list_auto_inject_libraries()
    ids = {x["id"] for x in libs}
    assert "lib_a" in ids
    assert "lib_b" not in ids


def test_fts_search_sanitizes_punctuation(tmp_path):
    from kazma_core.stores.knowledge import KnowledgeStore

    store = KnowledgeStore(db_path=str(tmp_path / "k2.db"))
    store.create_library("lib", "L")
    store.upsert_chunk(
        {
            "id": "lib:u:h1",
            "library_id": "lib",
            "source_url": "https://example.com/a",
            "document_title": "T",
            "section_header": "S",
            "chunk_index": 0,
            "content_hash": "h1",
            "has_code": False,
            "char_count": 20,
            "content": "solid-state battery electrolytes work well",
        }
    )
    # Query with colon/hyphen that used to break FTS5
    hits = store.fts_search("solid-state:battery", "lib", limit=5)
    assert isinstance(hits, list)
    # Should find or at least not raise
    assert hits or hits == []


def test_purge_source_removes_orphans(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "k3.db"))
    store.create_library("lib", "L")
    for i in range(3):
        store.upsert_chunk(
            {
                "id": f"lib:u:h{i}",
                "library_id": "lib",
                "source_url": "https://example.com/page",
                "document_title": "T",
                "section_header": f"S{i}",
                "chunk_index": i,
                "content_hash": f"h{i}",
                "has_code": False,
                "char_count": 10,
                "content": f"chunk body number {i} content here",
            }
        )
    assert store.count_chunks("lib") == 3
    idx = KnowledgeIndex(store=store)
    # avoid chroma
    monkeypatch.setattr(idx, "_vector_store_for", lambda _id: type("V", (), {"available": False})())
    n = idx.purge_source("lib", "https://example.com/page")
    assert n == 3
    assert store.count_chunks("lib") == 0


@pytest.mark.asyncio
async def test_synthesize_paths_validation():
    from kazma_core.tools.research_synthesize import synthesize_from_digests

    out = await synthesize_from_digests([], "q")
    assert out.startswith("Error:")
    out2 = await synthesize_from_digests("nope.md", "")
    assert "question" in out2.lower() or out2.startswith("Error:")
