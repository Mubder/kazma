"""One text is stored once; one page's query-string copies do not fill a crawl
(2026-11-13).

A crawl's page identity is its address, so the same article under several
addresses (``/`` and ``/index.html``, ``http`` and ``https``, a ``?ref=``
tag) was stored once per address -- the recorded corpus held three such
pairs -- and a calendar's or a catalogue's query-string addresses filled the
page cap with one page's copies. Now a page whose text another page of the
crawl already stored is kept out (``duplicate``), remembered across a
resume through the journal, and a page's query-string addresses are kept to
``_QUERY_VARIANTS_PER_PATH`` in discovery and in the link walk.
"""
from __future__ import annotations

import contextlib
import itertools

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import FetchResult

from tests import _crawl_replay as cr

ORIGIN = "https://calendar.example"
SEED = ORIGIN + "/docs/"
MONTHS = [f"{SEED}events?month={n}" for n in range(1, 61)]
GUIDES = [f"{SEED}guide-{name}" for name in ("install", "configure", "deploy")]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


class _NotFoundElsewhere(dict):
    def get(self, key, default=None):
        return super().get(key, {"status": 404, "headers": {"content-type": "text/html"},
                                 "text": "<html><body>Not found</body></html>"})


def _html(title, words):
    return {"status": 200, "headers": {"content-type": "text/html"},
            "text": f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1>"
                    f"<p>{words}</p></main></body></html>"}


def _recording():
    """A site whose sitemap lists sixty months of one events page before its
    three guides, and one guide twice (a tracking tag)."""
    responses = _NotFoundElsewhere()
    responses[ORIGIN + "/robots.txt"] = {"status": 200, "headers": {"content-type": "text/plain"},
                                         "text": f"User-agent: *\nAllow: /\nSitemap: {ORIGIN}/sitemap.xml\n"}
    listed = MONTHS + GUIDES + [GUIDES[0] + "?ref=nav"]
    locs = "".join(f"<url><loc>{url.replace('&', '&amp;')}</loc></url>" for url in listed)
    responses[ORIGIN + "/sitemap.xml"] = {
        "status": 200, "headers": {"content-type": "application/xml"},
        "text": f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'}
    for n, url in enumerate(MONTHS, start=1):
        responses[url] = _html(f"Events of month {n}", " ".join(f"Month {n} holds event {k}." for k in range(30)))
    for url in GUIDES:
        name = url.rsplit("-", 1)[-1]
        responses[url] = _html(f"Guide {name}", " ".join(f"The {name} guide, step {k}, explained." for k in range(30)))
    responses[GUIDES[0] + "?ref=nav"] = responses[GUIDES[0]]
    return {"responses": responses}


async def _replay(tmp_path, monkeypatch):
    site = {"name": "calendar", "seed": SEED, "max_pages": 12, "scope": "section"}
    with cr.replaying(monkeypatch, _recording()):
        outcome = await cr.crawl(site, tmp_path / "kb", monkeypatch)
    return outcome, crawl_jobs.inspect_jobs("calendar:corpus")


@pytest.mark.asyncio
async def test_query_string_copies_do_not_fill_the_page_cap(journal, tmp_path, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    outcome, row = await _replay(tmp_path, monkeypatch)
    pages = outcome["pages"]
    assert all(pages[url] == ["ingested", ""] for url in GUIDES), pages
    assert sum(url in pages for url in MONTHS) == ki._QUERY_VARIANTS_PER_PATH
    assert row["query_variants_skipped"] == len(MONTHS) - ki._QUERY_VARIANTS_PER_PATH
    assert "query_variants_capped" in row["notes"]
    # The tagged copy of a guide is the same text: kept out, stored once.
    assert pages[GUIDES[0] + "?ref=nav"] == ["excluded", "duplicate"]
    assert "duplicates" in row["notes"]


@pytest.mark.asyncio
async def test_without_the_cap_one_page_s_months_took_the_whole_crawl(journal, tmp_path, monkeypatch):
    """Negative control: no cap on a page's query-string addresses."""
    from kazma_core.stores import knowledge_ingest as ki

    monkeypatch.setattr(ki, "_QUERY_VARIANTS_PER_PATH", 10 ** 6)
    outcome, _row = await _replay(tmp_path, monkeypatch)
    assert not any(url in outcome["pages"] for url in GUIDES)


# ── The same text under two addresses, and across a resume ───────────────────


def _scripted_store(tmp_path, monkeypatch, texts):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]

    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, final_url=url, http_status=200, text=texts[url])

    async def discover(*a, **k):
        return list(texts)

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    return store


ARTICLE = "# Article\n\n" + "The article explains the topic in full. " * 20
TEXTS = {"https://docs.example.test/": ARTICLE,
         "https://docs.example.test/other": "# Other\n\n" + "A different page entirely. " * 20,
         "https://docs.example.test/index.html": ARTICLE.replace("  ", " ") + "\n"}


@pytest.mark.asyncio
async def test_the_same_text_is_stored_once_even_across_a_resume(journal, tmp_path, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    store = _scripted_store(tmp_path, monkeypatch, TEXTS)
    try:
        iterator = ki.ingest_site("docs", "https://docs.example.test/", job_id="dup")
        async for update in iterator:
            if update.phase == "fetch" and update.current_url.endswith("/index.html"):
                await iterator.aclose()  # the process ends before the copy
                break
        updates = [u async for u in ki.ingest_site("docs", "https://docs.example.test/", job_id="dup",
                                                   resume=True)]
        view = crawl_jobs.projection("dup")
        outcomes = {page["url"]: (page["outcome"], page["reason"]) for page in view["pages"]}
        assert outcomes["https://docs.example.test/index.html"] == ("excluded", "duplicate")
        assert sorted(store.list_source_urls("docs")) == ["https://docs.example.test/",
                                                           "https://docs.example.test/other"]
        assert updates[-1].phase == "done" and view["acquisition"]["status"] == "completed"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_without_the_text_check_the_copy_was_stored_too(journal, tmp_path, monkeypatch):
    """Negative control: every text its own (no two digests equal)."""
    from kazma_core.stores import knowledge_ingest as ki

    counter = itertools.count()
    monkeypatch.setattr(ki, "_text_digest", lambda text: f"{next(counter):016d}")
    store = _scripted_store(tmp_path, monkeypatch, TEXTS)
    try:
        _ = [u async for u in ki.ingest_site("docs", "https://docs.example.test/", job_id="dup")]
        assert len(store.list_source_urls("docs")) == 3
    finally:
        store.close()


async def _walk(tmp_path, monkeypatch):
    """A link walk from a seed whose links are forty days of one events page,
    then a guide; the page cap is twenty."""
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]
    seed = "https://docs.example.test/"
    links = tuple(f"{seed}events?day={n}" for n in range(40)) + (seed + "guide",)
    walked = []

    async def fetch(url, **kwargs):
        walked.append(url)
        return FetchResult(ok=True, url=url, final_url=url, http_status=200, links=links, links_complete=True,
                           text=f"# {url}\n\n" + f"Words of {url} for this test page. " * 20)

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    for name in ("_llms_txt_discover", "_fetch_sitemaps"):
        monkeypatch.setattr(ki, name, nothing)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    try:
        _ = [u async for u in ki.ingest_site("docs", seed, max_pages=20, job_id="walk", scope="domain")]
    finally:
        store.close()
    return {page["url"] for page in crawl_jobs.projection("walk")["pages"]}, set(walked), seed


@pytest.mark.asyncio
async def test_the_link_walk_follows_a_few_of_a_pages_query_addresses(journal, tmp_path, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    stored, walked, seed = await _walk(tmp_path, monkeypatch)
    assert seed + "guide" in stored
    assert sum("events?day=" in url for url in walked) == ki._QUERY_VARIANTS_PER_PATH


@pytest.mark.asyncio
async def test_without_the_cap_the_walk_never_reached_the_guide(journal, tmp_path, monkeypatch):
    """Negative control: the walk spends its pages on the events page's days."""
    from kazma_core.stores import knowledge_ingest as ki

    monkeypatch.setattr(ki, "_QUERY_VARIANTS_PER_PATH", 10 ** 6)
    stored, _walked, seed = await _walk(tmp_path, monkeypatch)
    assert seed + "guide" not in stored
