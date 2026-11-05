"""A failed crawl or research run says why it failed.

Live 2026-11-05: a deep research run failed because the search found no
addresses, and its run incident -- the Dashboard's Running work card and the
chat card -- gave the reason as "0 failed, 0 uncertain": the adapter showed
the page counts of every failed crawl, and the job never recorded why it
ended. The pipeline now records its own reason when it finishes
(``_CrawlJob.finish(failure=)``), and ``crawl_jobs.failure_reason`` is the one
answer the adapter shows.
"""
from __future__ import annotations

import pytest

from kazma_core import config_store
from kazma_core.runs.adapters.research import CRAWLS
from kazma_core.runs.contract import RunState
from kazma_core.web_acquire import crawl_jobs

NO_URLS = "deep research found no URLs. Check SearXNG / network (`KAZMA_SEARXNG_URL`)."


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _old_detail(row):
    """The adapter's detail before the fix: the counts, whatever the cause."""
    counts = crawl_jobs._summary_counts(row)
    return f"{counts['failed']} failed, {counts['ambiguous']} uncertain"


@pytest.mark.asyncio
async def test_a_pipeline_that_found_no_addresses_says_so(store, tmp_path, monkeypatch):
    from kazma_core.tools import read_url, research_pipeline as pipeline, research_planner
    from kazma_core.web_acquire import research_pipeline_job as stages
    from kazma_core.web_acquire.serp import SearchResult
    import kazma_core.web_acquire as acquisition

    root = tmp_path / "workspace"
    monkeypatch.setattr(pipeline, "_get_ws_root", lambda: root)
    monkeypatch.setattr(read_url, "_workspace_root", lambda: root)
    monkeypatch.setattr("kazma_core.tools.research_readiness.research_readiness", lambda **kw: {"ready": True})
    monkeypatch.setattr(stages, "_scope_signature", lambda: "fixed-fixture-scope")
    monkeypatch.setenv("KAZMA_RESEARCH_GAP_LOOP", "0")
    monkeypatch.setenv("KAZMA_BROWSER_NETWORK_MODE", "compatibility")

    async def plan(*args, **kwargs):
        return research_planner.ResearchPlan(search_queries=["initial"])

    async def search(query, **kwargs):
        return SearchResult(ok=True, query=query, urls=[])

    async def fetch(url, **kwargs):
        raise AssertionError("nothing to fetch")

    monkeypatch.setattr(research_planner, "plan_research_queries", plan)
    monkeypatch.setattr(acquisition, "search", search)
    monkeypatch.setattr(read_url, "fetch_result", fetch)

    result = await pipeline.run_research_pipeline("fixture topic", depth="standard", max_sources=3,
                                                  parallel_acquire=False)
    assert result.startswith("Error: deep research found no URLs")
    summary, = crawl_jobs.inspect_jobs()
    row = crawl_jobs.inspect_jobs(summary["job_id"])
    assert row["status"] == "failed"
    assert row["failure"] == NO_URLS

    run = CRAWLS._run(row, {}, None, False)
    assert run.state == RunState.FAILED
    assert run.detail == NO_URLS
    # Negative control: the old rule said the counts, which told nothing.
    assert _old_detail(row) == "0 failed, 0 uncertain"


def _crawl(**extra):
    row = dict(job_id="j1", library_id="lib", seed="https://example.com", policy={}, pages=[],
               discovered=True, claim="c" * 32, owner=None, status="failed", created_at=1.0,
               updated_at=2.0, fetched=0, failed=0, ingested=0, skipped=0, pages_unchanged=0,
               progress={})
    row.update(extra)
    return row


def test_the_reason_is_read_in_order():
    assert crawl_jobs.failure_reason(_crawl(failure="robots.txt refused the seed")) == "robots.txt refused the seed"
    assert crawl_jobs.failure_reason(_crawl(status="interrupted", interrupted_by="process_ended")) \
        == "Kazma restarted while it ran"
    assert crawl_jobs.failure_reason(_crawl(status="interrupted")) == "Stopped before it finished"
    assert crawl_jobs.failure_reason(_crawl(kind="research_pipeline", effects={
        "plan": {"status": "failed", "error": "model unavailable"}})) == "Stage plan failed (model unavailable)"
    assert crawl_jobs.failure_reason(_crawl(progress={"phase": "error", "message": "Error: seed gone\nmore"})) \
        == "seed gone"
    pages = [{"url": "https://example.com/a", "status": "failed"},
             {"url": "https://example.com/b", "status": "ambiguous"}]
    assert crawl_jobs.failure_reason(_crawl(pages=pages, failed=1)) == "1 failed, 1 uncertain"
    # Nothing known is said as nothing, never as a count of nothing.
    assert crawl_jobs.failure_reason(_crawl()) == ""
    assert _old_detail(_crawl()) == "0 failed, 0 uncertain"


def test_finish_records_one_redacted_line_and_a_resume_clears_it(store):
    job = crawl_jobs.start("lib-why", "https://example.com/", {"max_pages": 3})
    job.finish("interrupted", failure="Error: login refused, password=demo-value\nsecond line")
    row = crawl_jobs.inspect_jobs(job.job_id)
    assert row["failure"] == "login refused, <redacted>"
    assert len(crawl_jobs._one_line("x" * 1000)) == crawl_jobs._FAILURE_MAX

    resumed = crawl_jobs.start("lib-why", "https://example.com/", {"max_pages": 3}, job_id=job.job_id, resume=True)
    assert "failure" not in crawl_jobs.inspect_jobs(job.job_id)
    resumed.finish("completed")
    assert "failure" not in crawl_jobs.inspect_jobs(job.job_id)


def test_a_row_with_a_malformed_reason_is_refused():
    assert crawl_jobs._valid_row(_crawl(failure="ok"))
    assert not crawl_jobs._valid_row(_crawl(failure=123))
    assert not crawl_jobs._valid_row(_crawl(failure="x" * (crawl_jobs._FAILURE_MAX + 1)))
