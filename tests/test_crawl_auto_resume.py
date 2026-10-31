"""A Knowledge crawl a restart cut off resumes on its own, once.

A reload in the middle of a crawl left it "interrupted -- explicit recovery
required" until the owner pressed Resume. Now recovery records why a crawl
stopped (``interrupted_by: process_ended``), and at the next start each such
crawl from the last day resumes through the resume route itself. A crawl the
owner stopped, an older one, a research crawl, or one already resumed once
waits for the owner, and the monitor says nothing of a crawl about to resume.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import FetchResult

SEED = "https://docs.example.test/"


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


@pytest.fixture
def library(tmp_path, monkeypatch):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge, knowledge_index
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]

    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, final_url=url, text="# Page\n\n" + "useful words " * 40, http_status=200)

    async def discover(*a, **k):
        return [SEED + "a", SEED + "b"]

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    for module in (ki, knowledge):
        monkeypatch.setattr(module, "get_knowledge_store", lambda: store)
    for module in (ki, knowledge_index):
        monkeypatch.setattr(module, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    yield store
    store.close()


async def _cut_off_by_a_restart(monkeypatch, job_id="j1"):
    """A crawl admitted the product's way, then its process ends."""
    from kazma_core.stores import knowledge_ingest as ki

    job, *_ = await ki._prepare_ingest_site("docs", SEED, job_id=job_id)
    await crawl_jobs.call(job.update, lambda row: row.update(kind="site"))
    with monkeypatch.context() as m:
        m.setattr(crawl_jobs, "_owner_gone", lambda row, now: True)
        crawl_jobs.recover()
    row = crawl_jobs.inspect_jobs(job_id)
    assert row["status"] == "interrupted" and row["interrupted_by"] == "process_ended"
    return job_id


async def _settled(job_id):
    for _ in range(200):
        row = crawl_jobs.inspect_jobs(job_id)
        if row["status"] != "running":
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("the resumed crawl did not finish")


@pytest.mark.asyncio
async def test_a_crawl_a_restart_cut_off_resumes_once_through_the_resume_route(journal, library, monkeypatch):
    from kazma_ui import kb_api

    job_id = await _cut_off_by_a_restart(monkeypatch)
    kb_api.create_kb_router()  # the app's router: its resume route
    assert await kb_api.resume_interrupted_crawls() == [job_id]
    row = await _settled(job_id)
    assert row["status"] == "completed" and row["auto_resumed"] is True
    assert sorted(library.list_source_urls("docs")) == [SEED + "a", SEED + "b"]
    assert await kb_api.resume_interrupted_crawls() == []  # once


@pytest.mark.asyncio
async def test_without_it_the_crawl_waits_for_the_owner(journal, library, monkeypatch):
    """Negative control: the route unset (the old start), nothing resumes."""
    from kazma_ui import kb_api

    job_id = await _cut_off_by_a_restart(monkeypatch)
    monkeypatch.setattr(kb_api, "_RESUME_ROUTE", {})
    assert await kb_api.resume_interrupted_crawls() == []
    assert crawl_jobs.inspect_jobs(job_id)["status"] == "interrupted"


@pytest.mark.parametrize("change,due", [
    ({}, True),
    ({"interrupted_by": None}, False),                       # the owner stopped it
    ({"kind": "research_crawl"}, False),                      # an agent's crawl
    ({"auto_resumed": True}, False),                          # once
    ({"updated_at": time.time() - 2 * 86400}, False),        # older than a day
    ({"status": "completed"}, False),
])
def test_which_crawls_resume_on_their_own(change, due):
    row = dict(status="interrupted", interrupted_by="process_ended", kind="site", updated_at=time.time())
    row.update(change)
    assert crawl_jobs.auto_resume_due(row) is due


@pytest.mark.asyncio
async def test_the_monitor_says_nothing_of_a_crawl_about_to_resume(journal, library, monkeypatch, tmp_path):
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import monitor

    monkeypatch.setattr(research_session, "_db_path", lambda: tmp_path / "research.db")
    job_id = await _cut_off_by_a_restart(monkeypatch)
    keys = {job["key"] for job in monitor._collect()[0]}
    assert "job:" + job_id not in keys
    assert crawl_jobs.claim_auto_resume(job_id) is True  # resumed, or refused: shown again
    assert crawl_jobs.claim_auto_resume(job_id) is False
    assert "job:" + job_id in {job["key"] for job in monitor._collect()[0]}
