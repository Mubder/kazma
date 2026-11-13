"""A page the site removed leaves its library (2026-11-13).

A refresh never took anything out: a 404 or 410 was "excluded: gone" and the
page's stored text stayed searchable for good, and a page that had left the
sitemap was never asked for again. Now a refresh also asks the site for the
pages the library holds (bounded); the site's own answer for a page decides:
410 takes it out at once, 404 once a later crawl finds it missing again at
least an hour after the first. Absence from discovery alone never takes a
page out. A page taken out leaves the table, the keyword index, its vectors
and its fetched copy, through one transaction (``KnowledgeStore.page_gone``).
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import FetchResult

SEED = "https://docs.example.test/"
A, B, C = SEED + "a", SEED + "b", SEED + "c"


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _page(url):
    word = {"a": "aardvarkian", "b": "zebracorn", "c": "capybarish"}[url.rsplit("/", 1)[-1]]
    return FetchResult(ok=True, url=url, final_url=url, http_status=200,
                       text=f"# Page {word}\n\n" + f"The {word} chapter explains its topic. " * 12)


def _status(code):
    def answer(url):
        return FetchResult(ok=False, url=url, error=f"HTTP {code} for {url}", error_code="http_error",
                           http_status=code)
    return answer


class _Site:
    """What the site answers, page by page, and what discovery lists."""

    def __init__(self):
        self.answers = {A: _page, B: _page, C: _page}
        self.listed = [A, B, C]
        self.asked: list[str] = []


@pytest.fixture
def site(tmp_path, monkeypatch):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs", seed_url=SEED)
    index = KnowledgeIndex(store)
    deleted_vectors: list[str] = []

    class _Vectors:
        available = True

        def delete_many(self, ids):
            deleted_vectors.extend(ids)

        def index_many(self, docs):
            return None

        identity = "fixture"

    index._vector_store_for = lambda _lib: _Vectors()  # type: ignore[method-assign]
    index._chunk_vector_metadata = lambda *a, **k: {}  # type: ignore[method-assign]
    the_site = _Site()
    the_site.store, the_site.index, the_site.deleted_vectors = store, index, deleted_vectors

    async def fetch(url, **kwargs):
        the_site.asked.append(url)
        return the_site.answers[url](url)

    async def discover(*a, **k):
        return list(the_site.listed)

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_provenance_dir", lambda library: tmp_path / "provenance" / library)
    yield the_site
    store.close()


async def _crawl(site, kind, job_id):
    """A crawl the way the Knowledge page starts one (admission, its kind)."""
    from kazma_core.stores import knowledge_ingest as ki

    site.asked.clear()
    prepared = await ki._prepare_ingest_site("docs", SEED, job_id=job_id)
    await crawl_jobs.call(prepared[0].update, lambda row: row.update(kind=kind))
    async for _ in ki.ingest_site("docs", SEED, job_id=job_id, _prepared=prepared):
        pass
    return crawl_jobs.projection(job_id)


def _finds(site, word):
    return bool(site.store.fts_search(word, "docs"))


def _outcomes(projection):
    return {page["url"]: (page["outcome"], page["reason"]) for page in projection["pages"]}


@pytest.mark.asyncio
async def test_a_page_the_site_says_is_gone_leaves_the_library_at_the_refresh(journal, site, tmp_path):
    await _crawl(site, "site", "first")
    assert sorted(site.store.list_source_urls("docs")) == [A, B, C] and _finds(site, "zebracorn")
    copy = tmp_path / "provenance" / "docs"
    assert len(list(copy.iterdir())) == 3
    # The site removes B: it answers 410 and its sitemap no longer lists it.
    site.answers[B] = _status(410)
    site.listed = [A, C]
    projection = await _crawl(site, "refresh", "refresh-1")
    assert B in site.asked  # the refresh asked the site for the page the library holds
    assert _outcomes(projection)[B] == ("removed", "removed")
    assert projection["removed"] == 1 and "removed" in projection["notes"]
    assert projection["acquisition"]["status"] == "completed"
    assert sorted(site.store.list_source_urls("docs")) == [A, C]
    assert not _finds(site, "zebracorn") and _finds(site, "capybarish")
    assert site.deleted_vectors  # its meaning vectors went too
    assert len(list(copy.iterdir())) == 2  # and its fetched copy
    # The Dashboard's card and /metrics count it as removed, and handled.
    summary = crawl_jobs.outcome_summary()
    assert summary["outcomes"].get("removed") == 1 and summary["rate"] == 1.0
    assert {"outcome": "removed", "reason": "removed", "pages": 1} in summary["reasons"]
    assert 'kazma_kb_crawl_pages{outcome="removed"} 1' in crawl_jobs.metric_lines()
    assert site.store.count_chunks("docs") == site.store.get_library("docs")["chunk_count"]


@pytest.mark.asyncio
async def test_a_404_needs_a_second_crawl_an_hour_later(journal, site):
    await _crawl(site, "site", "first")
    site.answers[B] = _status(404)
    projection = await _crawl(site, "refresh", "refresh-1")
    assert _outcomes(projection)[B] == ("excluded", "gone_unconfirmed")
    assert _finds(site, "zebracorn")  # one missing answer is not a removal
    projection = await _crawl(site, "refresh", "refresh-2")  # Refresh pressed again at once
    assert _outcomes(projection)[B] == ("excluded", "gone_unconfirmed") and _finds(site, "zebracorn")
    record = site.store.gone_record("docs", B)
    assert record["sightings"] == 2
    # An hour later, still missing: it leaves the library.
    earlier = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    site.store._get_conn().execute("UPDATE knowledge_pages_gone SET gone_since = ?", (earlier,))
    projection = await _crawl(site, "refresh", "refresh-3")
    assert _outcomes(projection)[B] == ("removed", "removed")
    assert not _finds(site, "zebracorn") and site.store.gone_record("docs", B) is None


@pytest.mark.asyncio
async def test_a_page_that_answers_again_is_no_longer_counted_missing(journal, site):
    await _crawl(site, "site", "first")
    site.answers[B] = _status(404)
    await _crawl(site, "refresh", "refresh-1")
    assert site.store.gone_record("docs", B) is not None
    site.answers[B] = _page  # back after the site's deploy
    await _crawl(site, "refresh", "refresh-2")
    assert site.store.gone_record("docs", B) is None and _finds(site, "zebracorn")


@pytest.mark.asyncio
async def test_absence_from_discovery_alone_never_takes_a_page_out(journal, site):
    await _crawl(site, "site", "first")
    site.listed = [A, C]  # B left the sitemap but the site still has it
    projection = await _crawl(site, "refresh", "refresh-1")
    assert _outcomes(projection)[B] == ("unchanged", "")
    assert sorted(site.store.list_source_urls("docs")) == [A, B, C]


@pytest.mark.asyncio
async def test_a_crawl_that_is_not_a_refresh_asks_for_listed_pages_only(journal, site):
    await _crawl(site, "site", "first")
    site.listed = [A, C]
    await _crawl(site, "site", "again")
    assert B not in site.asked


@pytest.mark.asyncio
async def test_the_old_refresh_left_a_removed_page_searchable(journal, site, monkeypatch):
    """Negative control: a refresh that never asks for the library's pages
    and never takes one out (before 2026-11-13)."""
    from kazma_core.stores import knowledge_ingest as ki

    monkeypatch.setattr(ki, "_pages_to_revisit", lambda *a: [])
    monkeypatch.setattr(ki, "_settle_gone_page", lambda *a: "not_held")
    await _crawl(site, "site", "first")
    site.answers[B] = _status(410)
    site.listed = [A, C]
    await _crawl(site, "refresh", "refresh-1")
    assert B not in site.asked and _finds(site, "zebracorn")
    site.listed = [A, B, C]  # even asked for, a 410 left it in the library
    projection = await _crawl(site, "refresh", "refresh-2")
    assert _outcomes(projection)[B] == ("excluded", "gone") and _finds(site, "zebracorn")


def test_a_documents_chunks_are_never_taken_out_by_a_crawl(tmp_path):
    from kazma_core.stores.knowledge import KnowledgeStore

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    try:
        store.create_library("docs", "Docs")
        store.upsert_chunk({"id": "doc-1", "library_id": "docs", "source_url": B, "chunk_index": 0,
                            "content": "zebracorn handbook", "content_hash": "h", "document_id": "d1",
                            "version_id": "v1"})
        assert store.page_gone("docs", B, 410)["outcome"] == "not_held"
        assert store.fts_search("zebracorn", "docs")
        assert store.web_page_urls("docs") == []
    finally:
        store.close()
