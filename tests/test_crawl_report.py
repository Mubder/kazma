"""What a crawl reports, and what its journal keeps (2026-10-31).

Every page of every retained Knowledge crawl is counted by outcome and by
reason against the target (99% stored or kept out by a rule): the Dashboard,
/metrics and the weekly report read it. The journal keeps what a report or a
resume reads -- each page's outcome and reason, the latest receipt -- and not
the parsed sitemaps or per-page receipts nothing reads after discovery.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs

from tests import _crawl_replay as cr


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _finished(library, pages, status="completed", kind=None):
    """A finished crawl whose pages ended as *pages* says: [(outcome, reason)]."""
    job = crawl_jobs.start(library, f"https://{library}.example.test/",
                           dict(mode="local_only", robots=True, max_pages=10, scope="tree", delay_ms=0), kind=kind)
    urls = [f"https://{library}.example.test/p{n}" for n in range(len(pages))]
    job.frontier(urls)
    for url, (outcome, reason) in zip(urls, pages):
        job.begin_page(url)
        job.end_page(url, error="refused" if outcome == "failed" else "", excluded=outcome == "excluded",
                     unchanged=outcome == "unchanged", reason=reason)
    job.finish(status)
    return job


def test_every_page_is_counted_by_outcome_and_reason(journal):
    _finished("docs", [("ingested", ""), ("unchanged", ""), ("excluded", "robots"), ("excluded", "robots")])
    _finished("api", [("ingested", ""), ("failed", "network")], status="partial")
    _finished("check", [("failed", "network")], kind="canary")  # not a Knowledge crawl
    summary = crawl_jobs.outcome_summary()
    assert summary["crawls"] == 2 and summary["pages"] == 6
    assert summary["outcomes"] == {"ingested": 2, "unchanged": 1, "excluded": 2, "failed": 1}
    assert summary["reasons"][0] == {"outcome": "excluded", "reason": "robots", "pages": 2}
    assert summary["rate"] == round(5 / 6, 4) and summary["target"] == 0.99
    lines = crawl_jobs.metric_lines()
    assert 'kazma_kb_crawl_pages{outcome="excluded"} 2' in lines
    assert 'kazma_kb_crawl_page_reasons{outcome="failed",reason="network"} 1' in lines
    assert f"kazma_kb_crawl_page_handled_ratio {round(5 / 6, 4)}" in lines


def test_the_outcomes_route(journal):
    from kazma_ui.kb_api import create_kb_router

    _finished("docs", [("ingested", ""), ("excluded", "gone")])
    app = FastAPI()
    app.include_router(create_kb_router())
    body = TestClient(app).get("/api/kb/outcomes").json()
    assert body["ok"] and body["summary"]["rate"] == 1.0 and "canary" in body


def test_each_crawl_and_each_page_not_stored_leaves_a_line_for_the_weekly_report(journal, caplog):
    import logging
    import re

    from kazma_core.observability.firing_ledger import FIRING_SIGNATURES
    from kazma_core.stores.knowledge_ingest import _log_outcome

    job = _finished("docs", [("ingested", ""), ("failed", "network"), ("excluded", "robots")], status="partial")
    with caplog.at_level(logging.INFO, logger="kazma_core.stores.knowledge_ingest"):
        _log_outcome(crawl_jobs.inspect_jobs(job.job_id), "partial", [])
    messages = [record.getMessage() for record in caplog.records]
    by_name = {signature.mechanism: signature for signature in FIRING_SIGNATURES}
    crawl, page = by_name["Knowledge crawl"], by_name["Knowledge page not stored"]
    assert [re.search(crawl.group, m).group("k") for m in messages if re.search(crawl.pattern, m)] == ["partial"]
    assert sorted(re.search(page.group, m).group("k") for m in messages if re.search(page.pattern, m)) == [
        "excluded (robots)", "failed (network)"]


@pytest.mark.asyncio
async def test_a_finished_crawl_keeps_only_what_a_report_or_a_resume_reads(journal, tmp_path, monkeypatch):
    """A real sitemap crawl (the recorded corpus): no parsed sitemap list and
    one receipt are left in the journal; every page's outcome is."""
    site = next(site for site in cr.sites() if site["name"] == "fastapi-tutorial")
    with cr.replaying(monkeypatch, cr.load(site["name"])):
        outcome = await cr.crawl(site, tmp_path / "kb", monkeypatch)
    assert outcome == cr.expected()[site["name"]]
    row = crawl_jobs.inspect_jobs(f"{site['name']}:corpus")
    assert [record for record in row.get("metadata", {}).values() if record["status"] == "completed"] == []
    assert sum(page.get("receipt") is not None for page in row["pages"]) == 1
    assert crawl_jobs.projection(row["job_id"])["acquisition"]["last_receipt"] is not None
    assert len(crawl_jobs.page_outcomes(row)) == len(row["pages"]) == len(outcome["pages"])
    assert len(json.dumps(row)) < 64 * 1024


@pytest.mark.asyncio
async def test_without_the_trim_the_row_kept_every_sitemap_list(journal, tmp_path, monkeypatch):
    """Negative control: the frontier left as it was, the sitemaps stay."""
    real = crawl_jobs._CrawlJob.frontier

    def untrimmed(self, urls):
        kept = {}
        self.update(lambda row: kept.update(row.get("metadata", {})))
        real(self, urls)
        self.update(lambda row: row.__setitem__("metadata", kept))

    monkeypatch.setattr(crawl_jobs._CrawlJob, "frontier", untrimmed)
    site = next(site for site in cr.sites() if site["name"] == "fastapi-tutorial")
    with cr.replaying(monkeypatch, cr.load(site["name"])):
        await cr.crawl(site, tmp_path / "kb", monkeypatch)
    row = crawl_jobs.inspect_jobs(f"{site['name']}:corpus")
    assert [record for record in row["metadata"].values() if record["status"] == "completed"]
