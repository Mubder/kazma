"""A crawl's preview, and the scope the owner chose (2026-10-31).

Before a crawl starts, the Knowledge page can ask what it would fetch: the
crawl's own discovery runs (behind its admission and policy) and answers where
the list came from and how many pages "this section", "the whole site" and
"just this page" hold -- and nothing is stored. The scope the owner picks is
the crawl's, and the library's refreshes keep it; a resume keeps the scope the
crawl ran with.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult

SEED = "https://example.test/whatsapp/"
SECTION = [SEED + name for name in ("a", "b", "c", "d")]
ELSEWHERE = ["https://example.test/blog/x", "https://example.test/blog/y"]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


@pytest.fixture
def site(tmp_path, monkeypatch, journal):
    """A site whose sitemap lists its section and its blog; nothing real."""
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge, knowledge_index
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]
    state = {"sitemap": SECTION + ELSEWHERE, "admitted": [], "fetched": []}

    async def sitemaps(seed, **kwargs):
        docs = ki._SitemapDocuments()
        docs.pages.extend(state["sitemap"])
        return docs

    async def walk(seed, mode, **kwargs):
        return [url for url in SECTION if ki._in_scope(seed, url, mode)]

    async def admit(seed):
        state["admitted"].append(seed)

    async def fetch(url, **kwargs):
        state["fetched"].append(url)
        return FetchResult(ok=True, url=url, final_url=url, text="# Page\n\n" + "useful words " * 40, http_status=200)

    async def no_llms(seed):
        return []

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "_fetch_sitemaps", sitemaps)
    monkeypatch.setattr(ki, "_llms_txt_discover", no_llms)
    monkeypatch.setattr(ki, "_bfs_discover", walk)
    monkeypatch.setattr(ki, "_admit_public_seed", admit)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    for module in (ki, knowledge):
        monkeypatch.setattr(module, "get_knowledge_store", lambda: store)
    for module in (ki, knowledge_index):
        monkeypatch.setattr(module, "get_knowledge_index", lambda: index)
    state["store"] = store
    yield state
    store.close()


@pytest.mark.asyncio
async def test_a_preview_counts_each_scope_from_the_sitemap_and_stores_nothing(site):
    from kazma_core.stores.knowledge_ingest import preview_site

    preview = await preview_site(SEED)
    assert preview["source"] == "sitemap" and site["admitted"] == [SEED]
    assert preview["scopes"]["section"]["pages"] == 1 + len(SECTION)  # the seed and its section
    assert preview["scopes"]["site"]["pages"] == 1 + len(SECTION) + len(ELSEWHERE)
    assert preview["scopes"]["page"] == {"pages": 1, "sample": [SEED]}
    assert preview["scopes"]["section"]["sample"][0] == SEED
    assert crawl_jobs.inspect_jobs() == [] and site["store"].list_libraries() == []
    assert site["fetched"] == []  # no page fetched one by one


@pytest.mark.asyncio
async def test_a_link_walk_cannot_count_the_other_scopes(site):
    from kazma_core.stores.knowledge_ingest import preview_site

    site["sitemap"] = []
    preview = await preview_site(SEED)
    assert preview["source"] == "link_walk"
    assert preview["scopes"]["section"]["pages"] == 1 + len(SECTION)
    assert preview["scopes"]["site"] is None  # only a whole-site crawl can tell


@pytest.mark.asyncio
async def test_a_preview_is_admitted_like_a_crawl(site, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    async def refuse(seed):
        raise AcquisitionError(FetchErrorCode.SECURITY, "private network")

    monkeypatch.setattr(ki, "_admit_public_seed", refuse)
    with pytest.raises(AcquisitionError):
        await ki.preview_site(SEED)
    with pytest.raises(AcquisitionError):
        await ki.preview_site("file:///etc/passwd")


def _client():
    from kazma_ui.kb_api import create_kb_router

    app = FastAPI()
    app.include_router(create_kb_router())
    return TestClient(app)


async def _finished(job_id):
    import asyncio

    for _ in range(200):
        row = crawl_jobs.inspect_jobs(job_id)
        if row and row["status"] != "running":
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("crawl did not finish")


def test_the_preview_route(site):
    with _client() as client:
        body = client.post("/api/kb/preview", json={"url": SEED, "scope": "site"}).json()
        assert body["ok"] and body["preview"]["scope"] == "domain"
        assert client.post("/api/kb/preview", json={"url": SEED, "scope": "everything"}).status_code == 422


def test_the_chosen_scope_is_the_crawls_and_refreshes_keep_it(site):
    with _client() as client:
        started = client.post("/api/kb/ingest", json={
            "library_id": "docs", "url": SEED, "mode": "site", "scope": "site"}).json()
        assert started["ok"], started
        row = client.portal.call(_finished, started["job_id"])
        assert row["policy"]["scope"] == "domain"
        assert site["store"].crawl_scope("docs") == "domain"
        assert set(SECTION + ELSEWHERE) <= set(site["fetched"])  # the whole site
        again = client.post("/api/kb/libraries/docs/refresh").json()
        assert again["ok"], again
        assert client.portal.call(_finished, again["job_id"])["policy"]["scope"] == "domain"


def test_without_a_choice_a_crawl_takes_the_default(site):
    """Negative control: no scope asked and none stored, the section only."""
    with _client() as client:
        started = client.post("/api/kb/ingest", json={"library_id": "docs", "url": SEED, "mode": "site"}).json()
        row = client.portal.call(_finished, started["job_id"])
        assert row["policy"]["scope"] == "tree" and site["store"].crawl_scope("docs") == ""
        assert not set(ELSEWHERE) & set(site["fetched"])


def test_a_resume_keeps_the_scope_the_crawl_ran_with(site, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    async def cut_off():
        job, *_ = await ki._prepare_ingest_site("docs", SEED, job_id="docs:r1", scope="domain")
        await crawl_jobs.call(job.update, lambda row: row.update(kind="site"))
        with monkeypatch.context() as m:
            m.setattr(crawl_jobs, "_owner_gone", lambda row, now: True)
            crawl_jobs.recover()

    with _client() as client:
        site["store"].create_library("docs", "Docs", seed_url=SEED)
        client.portal.call(cut_off)
        site["store"].set_crawl_scope("docs", "exact")  # changed since
        resumed = client.post("/api/kb/jobs/docs:r1/resume").json()
        assert resumed["ok"], resumed
        assert client.portal.call(_finished, "docs:r1")["policy"]["scope"] == "domain"
