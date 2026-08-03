"""KB smart re-index + hybrid inject/recall alignment."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from kazma_core.stores import knowledge as kn
    from kazma_core.stores.knowledge_index import KnowledgeIndex, reset_knowledge_index

    db = tmp_path / "kb.db"
    store = kn.KnowledgeStore(db_path=str(db))
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store)
    reset_knowledge_index()
    idx = KnowledgeIndex(store=store)
    # No Chroma in unit tests
    monkeypatch.setattr(
        idx,
        "_vector_store_for",
        lambda _id: type("V", (), {"available": False, "delete": lambda *a, **k: None})(),
    )
    store.create_library("lib", "Lib", seed_url="https://docs.example/tree/")
    return store, idx


def _chunk(url: str, idx: int, body: str, lib: str = "lib") -> dict:
    import hashlib

    h = hashlib.sha256(body.encode()).hexdigest()
    return {
        "id": f"{lib}:{idx}:{h[:16]}",
        "library_id": lib,
        "source_url": url,
        "document_title": "T",
        "section_header": f"S{idx}",
        "chunk_index": idx,
        "content_hash": h,
        "has_code": False,
        "char_count": len(body),
        "content": body,
    }


def test_smart_reindex_skips_unchanged_page(kb):
    store, index = kb
    url = "https://docs.example/tree/a"
    chunks = [
        _chunk(url, 0, "alpha content about widgets " * 5),
        _chunk(url, 1, "beta content about gadgets " * 5),
    ]
    n1, s1 = index.index("lib", chunks)
    assert n1 == 2
    assert store.count_chunks("lib") == 2

    # Identical re-index: no purge needed, full skip
    n2, s2 = index.index("lib", chunks)
    assert n2 == 0
    assert s2 == 2
    assert store.count_chunks("lib") == 2
    assert store.list_source_content_hashes("lib", url) == [
        chunks[0]["content_hash"],
        chunks[1]["content_hash"],
    ]


def test_smart_reindex_purges_on_shrink(kb):
    store, index = kb
    url = "https://docs.example/tree/a"
    big = [
        _chunk(url, 0, "section zero content " * 8),
        _chunk(url, 1, "section one content " * 8),
        _chunk(url, 2, "section two content " * 8),
    ]
    index.index("lib", big)
    assert store.count_chunks("lib") == 3

    small = [
        _chunk(url, 0, "section zero content " * 8),  # same as before
        _chunk(url, 1, "section one REVISED content " * 8),
    ]
    n, s = index.index("lib", small)
    assert store.count_chunks("lib") == 2
    assert n == 2  # purged then rewrote both
    # Old section two must be gone
    bodies = [c["content"] for c in store.list_chunks("lib", limit=50)]
    assert not any("section two" in b for b in bodies)


def test_prune_gone_urls_in_scope(kb):
    store, index = kb
    keep = "https://docs.example/tree/keep"
    gone = "https://docs.example/tree/gone"
    outside = "https://other.example/x"
    index.index("lib", [_chunk(keep, 0, "keep page " * 10)])
    index.index("lib", [_chunk(gone, 0, "gone page " * 10)])
    index.index("lib", [_chunk(outside, 0, "outside page " * 10)])
    assert store.count_chunks("lib") == 3

    n = index.prune_sources_not_in(
        "lib",
        {keep},
        candidate_urls={keep, gone},  # outside not a candidate
    )
    assert n == 1
    urls = set(store.list_source_urls("lib"))
    assert keep in urls
    assert gone not in urls
    assert outside in urls  # not pruned (out of candidate set)


def test_resolve_inject_library_ids(tmp_path, monkeypatch):
    from kazma_core.stores import knowledge as kn
    from kazma_core.stores.knowledge import KnowledgeStore

    store = KnowledgeStore(db_path=str(tmp_path / "ri.db"))
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store)
    store.create_library("lib", "Lib")
    with store._lock:
        conn = store._get_conn()
        conn.execute(
            "UPDATE knowledge_libraries SET auto_inject = 1, chunk_count = 3 WHERE id = ?",
            ("lib",),
        )
        conn.commit()

    monkeypatch.setattr(
        "kazma_core.stores.knowledge_index.kb_auto_inject_enabled", lambda: True
    )
    monkeypatch.setattr(
        "kazma_core.stores.knowledge_index.kb_smart_search_enabled", lambda: False
    )
    from kazma_core.memory.federated_search import resolve_kb_library_ids

    ids = resolve_kb_library_ids("how to configure oauth", mode="inject")
    assert "lib" in ids


def test_federated_kb_hit_shape_from_rrf(monkeypatch):
    """_search_knowledge maps KnowledgeHit → federated hit with kb_rrf source."""
    from kazma_core.stores.knowledge_index import KnowledgeHit
    import kazma_core.memory.federated_search as fs

    fake_hit = KnowledgeHit(
        chunk_id="lib:0:x",
        content="oauth jwt bearer token auth docs",
        score=0.05,
        library_id="lib",
        source_url="https://docs.example/a",
        document_title="Auth",
        section_header="OAuth",
        chunk_index=0,
        has_code=False,
    )

    class _Idx:
        def search_all_sync(self, *a, **k):
            return [fake_hit]

    monkeypatch.setattr(fs, "resolve_kb_library_ids", lambda *a, **k: ["lib"])

    import kazma_core.stores.knowledge_index as ki_mod

    monkeypatch.setattr(ki_mod, "get_knowledge_index", lambda: _Idx())
    out = fs._search_knowledge("oauth", limit=3, mode="inject")
    assert len(out) == 1
    assert out[0]["source"] == "kb_rrf"
    assert out[0]["store"] == "knowledge"
    assert "oauth" in out[0]["content"]
