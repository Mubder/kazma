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


def _on_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture()
def crawl(kb, monkeypatch: pytest.MonkeyPatch):
    """A real ``ingest_site`` over the tmp store/index, with the network faked out.

    Records every index/store write made ON the event loop: indexing embeds
    every chunk of a page, and it ran inline on the loop that serves every chat
    stream, once per crawled page (AGENTS.md §26E).
    """
    import asyncio
    import contextlib

    import kazma_core.security.ssrf as ssrf
    import kazma_core.stores.knowledge_ingest as ki

    store, index = kb
    on_loop: list[str] = []
    real_index, real_prune = index.index, index.prune_sources_not_in

    def index_chunks(*a, **k):
        if _on_loop():
            on_loop.append("index.index")
        return real_index(*a, **k)

    def prune(*a, **k):
        if _on_loop():
            on_loop.append("index.prune_sources_not_in")
        return real_prune(*a, **k)

    def validate_url(url, **_):
        if _on_loop():
            on_loop.append("validate_url")  # resolves DNS

    pages = [f"https://docs.example/tree/{name}" for name in ("a", "b", "c")]

    async def discover(seed, on_progress=None):
        return list(pages)

    async def extract(url):
        return f"# Page {url[-1]}\n\n" + f"content for page {url} " * 20, "ok", ""

    monkeypatch.setattr(index, "index", index_chunks)
    monkeypatch.setattr(index, "prune_sources_not_in", prune)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ssrf, "validate_url", validate_url)
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_extract_page", extract)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)

    def run_site():
        async def _go():
            return [u async for u in ki.ingest_site("lib", "https://docs.example/tree/")]

        return asyncio.run(_go())

    return type("Crawl", (), {"run_site": staticmethod(run_site), "on_loop": on_loop, "ki": ki})


def test_site_crawl_indexes_off_the_event_loop(crawl):
    done = crawl.run_site()[-1]
    assert done.phase == "done" and done.fetched == 3 and done.ingested > 0
    assert crawl.on_loop == []


def test_page_ingest_indexes_off_the_event_loop(crawl):
    import asyncio

    result = asyncio.run(crawl.ki.ingest_url("lib", "https://docs.example/tree/a"))
    assert result.pages_fetched == 1 and result.chunks_new > 0
    assert crawl.on_loop == []


def test_recrawl_counts_every_unchanged_page(crawl):
    """The per-page check used the crawl's running `skipped` total, so after the
    first unchanged page no later page could ever be counted: 3 → 1."""
    first = crawl.run_site()[-1]
    assert first.pages_unchanged == 0
    second = crawl.run_site()[-1]
    assert second.ingested == 0
    assert second.pages_unchanged == 3


def test_index_search_facades_run_off_the_event_loop(kb, monkeypatch: pytest.MonkeyPatch):
    """`search` / `search_document` / `search_all` are async façades over
    blocking work (FTS, vector query, query embedding) that ran inline."""
    import asyncio

    store, index = kb
    calls: list[bool] = []

    def raw_layers(*a, **k):
        calls.append(_on_loop())
        return [], []

    monkeypatch.setattr(index, "_raw_layers", raw_layers)

    async def _go():
        await index.search("q", "lib")
        await index.search_document("q", tenant_id="default", library_id="lib", document_id="d")
        await index.search_all("q", ["lib"])

    asyncio.run(_go())
    assert calls == [False, False, False]
