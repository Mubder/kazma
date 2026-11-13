"""A Knowledge crawl a restart cut off resumes on its own.

A reload in the middle of a crawl left it "interrupted -- explicit recovery
required" until the owner pressed Resume. Now recovery records why a crawl
stopped (``interrupted_by: process_ended``), and at the next start each such
crawl from the last day resumes through the resume route itself -- up to
three times a day, each time only if it settled more pages since its last
automatic resume (2026-11-13: once was not enough, a Windows update restarts
the machine twice). A crawl the owner stopped, an older one, a research
crawl, one a refusing site stopped, or one that made no progress since its
last automatic resume waits for the owner, and the monitor says nothing of a
crawl about to resume.
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
        return FetchResult(ok=True, url=url, final_url=url, text=f"# Page {url}\n\n" + "useful words " * 40, http_status=200)

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
    assert row["status"] == "completed" and len(row["auto_resumes"]) == 1
    assert sorted(library.list_source_urls("docs")) == [SEED + "a", SEED + "b"]
    assert await kb_api.resume_interrupted_crawls() == []  # finished: nothing to resume


@pytest.mark.asyncio
async def test_without_it_the_crawl_waits_for_the_owner(journal, library, monkeypatch):
    """Negative control: the route unset (the old start), nothing resumes."""
    from kazma_ui import kb_api

    job_id = await _cut_off_by_a_restart(monkeypatch)
    monkeypatch.setattr(kb_api, "_RESUME_ROUTE", {})
    assert await kb_api.resume_interrupted_crawls() == []
    assert crawl_jobs.inspect_jobs(job_id)["status"] == "interrupted"


_NOW = time.time()
_ONE_DONE = [{"url": SEED + "a", "status": "completed"}, {"url": SEED + "b", "status": "pending"}]
_TWO_DONE = [{"url": SEED + "a", "status": "completed"}, {"url": SEED + "b", "status": "failed"}]


@pytest.mark.parametrize("change,due", [
    ({}, True),
    ({"interrupted_by": None}, False),                       # the owner stopped it
    ({"interrupted_by": "site_refusing"}, False),            # a refusing site stopped it
    ({"kind": "research_crawl"}, False),                      # an agent's crawl
    ({"updated_at": _NOW - 2 * 86400}, False),               # older than a day
    ({"status": "completed"}, False),
    # Resumed before, and settled more pages since: again.
    ({"pages": _TWO_DONE, "auto_resumes": [{"at": _NOW - 60, "settled": 1}]}, True),
    # Resumed before, and no page settled since: it waits for the owner.
    ({"pages": _ONE_DONE, "auto_resumes": [{"at": _NOW - 60, "settled": 1}]}, False),
    # Three times in a day is the most.
    ({"pages": _TWO_DONE, "auto_resumes": [{"at": _NOW - 60 * n, "settled": 0} for n in (3, 2, 1)]}, False),
    # Resumes more than a day old do not count.
    ({"pages": _TWO_DONE, "auto_resumes": [{"at": _NOW - 2 * 86400, "settled": 2}]}, True),
    # A row from before the count resumed once, its progress then unknown.
    ({"auto_resumed": True}, True),
])
def test_which_crawls_resume_on_their_own(change, due):
    row = dict(status="interrupted", interrupted_by="process_ended", kind="site", updated_at=_NOW)
    row.update(change)
    assert crawl_jobs.auto_resume_due(row, _NOW) is due


def _old_once_only_rule(row, now=None):
    """Before 2026-11-13: resumed on its own once, ever."""
    now = time.time() if now is None else now
    return (row.get('status') == 'interrupted' and row.get('interrupted_by') == 'process_ended'
            and row.get('kind', 'site') in crawl_jobs._KB_KINDS and not row.get('auto_resumes')
            and now - float(row.get('updated_at') or 0) <= crawl_jobs.AUTO_RESUME_WITHIN_S)


async def _restart_again(monkeypatch, job_id, *, settle: str | None):
    """The crawl resumed (the product's resume of the journal), settled
    *settle* if given, and a restart cut it off again."""
    row = crawl_jobs.inspect_jobs(job_id)
    job = crawl_jobs.start(row["library_id"], row["seed"], {k: v for k, v in row["policy"].items()
                                                             if k != "browser_network"},
                           job_id=job_id, resume=True, kind=row.get("kind"))
    if settle is not None:
        assert job.begin_page(settle)
        job.end_page(settle, new=1)
    with monkeypatch.context() as m:
        m.setattr(crawl_jobs, "_owner_gone", lambda row, now: True)
        crawl_jobs.recover()
    assert crawl_jobs.inspect_jobs(job_id)["interrupted_by"] == "process_ended"


async def _restarted_three_times(journal, library, monkeypatch):
    """Two restarts each after the crawl made progress, then a third with
    none since: the decisions the next start makes, in order."""
    from kazma_core.stores import knowledge_ingest as ki

    job, *_ = await ki._prepare_ingest_site("docs", SEED, job_id="j-twice")
    await crawl_jobs.call(job.update, lambda row: row.update(kind="site"))
    await crawl_jobs.call(job.frontier, [SEED + "a", SEED + "b", SEED + "c"])
    with monkeypatch.context() as m:
        m.setattr(crawl_jobs, "_owner_gone", lambda row, now: True)
        crawl_jobs.recover()
    decisions = [crawl_jobs.claim_auto_resume("j-twice")]          # the first restart
    await _restart_again(monkeypatch, "j-twice", settle=SEED + "a")
    decisions.append(crawl_jobs.claim_auto_resume("j-twice"))      # the second, after progress
    await _restart_again(monkeypatch, "j-twice", settle=None)
    decisions.append(crawl_jobs.claim_auto_resume("j-twice"))      # the third, nothing settled since
    return decisions


@pytest.mark.asyncio
async def test_two_restarts_with_progress_both_resume_a_third_without_it_waits(journal, library, monkeypatch):
    assert await _restarted_three_times(journal, library, monkeypatch) == [True, True, False]
    row = crawl_jobs.inspect_jobs("j-twice")
    assert row["status"] == "interrupted" and [entry["settled"] for entry in row["auto_resumes"]] == [0, 1]


@pytest.mark.asyncio
async def test_the_once_only_rule_left_the_second_restart_waiting(journal, library, monkeypatch):
    """Negative control: the old rule, the same three restarts."""
    monkeypatch.setattr(crawl_jobs, "auto_resume_due", _old_once_only_rule)
    assert await _restarted_three_times(journal, library, monkeypatch) == [True, False, False]


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


async def _stopped_gracefully_mid_crawl(journal, library, monkeypatch, *, shutting_down: bool):
    """A crawl running in the server's background is cancelled by the drain.

    The functional audit's drills (FA-263): a graceful reload cancelled the
    crawl, its own clean-up finished the row ``interrupted`` with no
    ``interrupted_by``, and nothing resumed it -- every deploy during a crawl
    stranded it until the owner pressed Resume.
    """
    from kazma_core import background, shutdown
    from kazma_core.bounded_cancel import cancel_and_wait
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_ui import kb_api

    release = asyncio.Event()
    entered = asyncio.Event()

    async def fetch(url, **kwargs):
        entered.set()
        await release.wait()
        return FetchResult(ok=True, url=url, final_url=url, text=f"# Page {url}\n\n" + "useful words " * 40, http_status=200)

    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    kb_api.create_kb_router()
    job, *_ = await ki._prepare_ingest_site("docs", SEED, job_id="j-reload")
    await crawl_jobs.call(job.update, lambda row: row.update(kind="site"))
    await crawl_jobs.call(job.finish, "interrupted")  # stopped once by its owner
    answer = await kb_api._RESUME_ROUTE["resume"]("j-reload")  # and resumed by them
    assert answer.get("ok"), answer
    await asyncio.wait_for(entered.wait(), 10)
    task = next(t for t in background.background_tasks() if t.get_name() == "kb-crawl:j-reload")
    if shutting_down:
        shutdown.signal_shutdown()
    try:
        await cancel_and_wait(task, what="the crawl", timeout=10)
    finally:
        shutdown.reset_shutdown()
    release.set()
    return crawl_jobs.inspect_jobs("j-reload")


@pytest.mark.asyncio
async def test_a_crawl_a_graceful_reload_cut_off_resumes_at_the_next_start(journal, library, monkeypatch):
    from kazma_ui import kb_api

    row = await _stopped_gracefully_mid_crawl(journal, library, monkeypatch, shutting_down=True)
    assert row["status"] == "interrupted" and row["interrupted_by"] == "process_ended"
    assert await kb_api.resume_interrupted_crawls() == ["j-reload"]
    assert (await _settled("j-reload"))["status"] == "completed"


@pytest.mark.asyncio
async def test_a_crawl_cancelled_outside_a_shutdown_waits_for_the_owner(journal, library, monkeypatch):
    """Control: the same cancel with no shutdown is the owner's stop."""
    from kazma_ui import kb_api

    row = await _stopped_gracefully_mid_crawl(journal, library, monkeypatch, shutting_down=False)
    assert row["status"] == "interrupted" and row.get("interrupted_by") is None
    assert await kb_api.resume_interrupted_crawls() == []


@pytest.mark.asyncio
async def test_the_old_clean_up_stranded_it(journal, library, monkeypatch):
    """Negative control: the old finish (no shutdown check) left a reload's
    crawl looking stopped by its owner."""
    from kazma_ui import kb_api

    monkeypatch.setattr(crawl_jobs, "_process_is_ending", lambda: False)
    row = await _stopped_gracefully_mid_crawl(journal, library, monkeypatch, shutting_down=True)
    assert row.get("interrupted_by") is None
    assert await kb_api.resume_interrupted_crawls() == []
