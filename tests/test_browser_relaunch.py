"""A crawl longer than its browser's limits keeps every page (2026-11-13).

The job-owned browser (``web_acquire.browser.BrowserJob``) runs inside a
process tree with a 30-minute hard lifetime, a 512 MiB protocol budget and a
2 GiB memory cap (``browser_driver``). A Knowledge crawl renders for hours:
once a limit ended the tree, the job kept its dead context, every later
render failed ``resource_limit`` and the crawl lost every page that needed
the browser. A Chromium that crashed (out of memory under the cap) did the
same. Now the job relaunches its browser between pages -- when the tree has
ended, Chromium disconnected, or a limit is near -- and a page under which
the browser died is rendered once more in the relaunched browser.

The unit cases run everywhere; the crawls run real Chromium (skipped where
it is not installed), with the browser's lifetime shrunk to seconds.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import psutil
import pytest
from kazma_core import config_store
from kazma_core.web_acquire import browser as browser_mod
from kazma_core.web_acquire import browser_driver as bd
from kazma_core.web_acquire import crawl_jobs, resources
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult

from tests.test_browser_acquisition_broker import broker_transport, chromium  # noqa: F401  (fixtures)

SEED = "https://docs.example/"


class _Driver:
    """The parts of ``BrowserDriver`` a relaunch decision reads."""

    def __init__(self, *, failure=None, ending=False, left=1000.0, traffic=bd._TRAFFIC):
        self.failure, self.ending, self.left, self.traffic = failure, ending, left, traffic
        self.stop = AsyncMock()
        self.cleanup_window = Mock()

    def seconds_left(self):
        return self.left

    def traffic_left(self):
        return self.traffic


def _running_job(driver=None, *, connected=True, renders=0):
    job = browser_mod.BrowserJob()
    job._driver = driver
    job.context, job.pw = AsyncMock(), AsyncMock()
    job.browser = SimpleNamespace(is_connected=lambda: connected, close=AsyncMock())
    job._renders = renders
    return job


@pytest.mark.parametrize("driver,connected,renders,why", [
    (_Driver(), True, 0, None),
    (_Driver(failure=AcquisitionError(FetchErrorCode.LIMIT, "lifetime")), True, 3, "browser_ended"),
    (_Driver(ending=True), True, 3, "browser_ended"),
    (_Driver(left=1.0), True, 3, "lifetime"),
    (_Driver(traffic=1024), True, 3, "protocol_budget"),
    (_Driver(), False, 3, "browser_closed"),
    (_Driver(), True, browser_mod._RELAUNCH_AFTER_RENDERS, "page_budget"),
])
def test_a_browser_near_or_past_a_limit_is_replaced_before_the_next_page(driver, connected, renders, why):
    assert _running_job(driver, connected=connected, renders=renders)._relaunch_reason() == why


@pytest.mark.asyncio
async def test_a_relaunch_ends_the_old_tree_first_and_keeps_the_jobs_capacity(monkeypatch):
    pool = resources._CapacityPool("fixture browsers", 1, 0)
    monkeypatch.setattr(resources, "_BROWSERS", pool)
    job = browser_mod.BrowserJob()
    launched = []

    async def launch():
        driver = _Driver()
        launched.append(driver)
        job._driver, job.context, job.pw = driver, AsyncMock(), AsyncMock()
        job.browser = SimpleNamespace(is_connected=lambda: True, close=AsyncMock())

    monkeypatch.setattr(job, "_launch_impl", launch)
    await job._start_impl()
    await job._start_impl()  # healthy: the same browser
    assert len(launched) == 1
    launched[0].failure = AcquisitionError(FetchErrorCode.LIMIT, "fixture: lifetime ended the tree")
    await job._start_impl()
    assert len(launched) == 2 and job.relaunches == 1
    launched[0].stop.assert_awaited()  # ended before the new one started
    assert job._driver is launched[1]
    assert pool.snapshot()["active"] == 1 and pool.snapshot()["quarantined"] == 0
    await job._close_impl()
    assert pool.snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_a_relaunch_whose_cleanup_fails_never_starts_a_second_tree(monkeypatch):
    pool = resources._CapacityPool("fixture browsers", 1, 0)
    monkeypatch.setattr(resources, "_BROWSERS", pool)
    job = browser_mod.BrowserJob()
    launched = []

    async def launch():
        driver = _Driver(ending=True)
        launched.append(driver)
        job._driver, job.context, job.pw = driver, AsyncMock(), AsyncMock()
        job.browser = SimpleNamespace(is_connected=lambda: True, close=AsyncMock())

    monkeypatch.setattr(job, "_launch_impl", launch)
    await job._start_impl()
    first = launched[0]
    first.stop.side_effect = AcquisitionError(FetchErrorCode.LIMIT, "fixture: OS proof unavailable")
    with pytest.raises(AcquisitionError, match="OS proof unavailable"):
        await job._start_impl()
    assert len(launched) == 1 and job._driver is first  # retained, not replaced
    assert pool.snapshot()["quarantined"] == 1
    first.stop.side_effect = None  # the cleanup is tried again at the next page
    await job._start_impl()
    assert len(launched) == 2 and pool.snapshot()["quarantined"] == 0
    await job._close_impl()


@pytest.mark.asyncio
async def test_a_page_the_browser_died_under_is_tried_once_more(monkeypatch):
    lost = browser_mod.RenderedDocument(FetchResult(ok=False, url="u", error_code=FetchErrorCode.LIMIT), lost=True)
    good = browser_mod.RenderedDocument(FetchResult(ok=True, url="u", text="text"))
    answers = [lost, good]
    calls = []

    async def once(owner, url, **kwargs):
        calls.append(url)
        return answers.pop(0)

    monkeypatch.setattr(browser_mod, "_render_once", once)
    owner = SimpleNamespace(_closed=False)
    assert (await browser_mod._render_in_job(owner, "u", challenge=None, profile="generic")) is good
    assert calls == ["u", "u"]
    # Lost twice: the second answer stands; a page is never tried a third time.
    answers[:] = [lost, lost, good]
    calls.clear()
    assert (await browser_mod._render_in_job(owner, "u", challenge=None, profile="generic")).lost
    assert calls == ["u", "u"]


# ── Real Chromium ──────────────────────────────────────────────────────────


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _served(request):
    path = request.url.path
    words = " ".join(f"{path} explains part {n} of the guide in its own words." for n in range(12))
    body = f"<html><head><title>{path}</title></head><body><h1>Guide {path}</h1><p>{words}</p></body></html>"
    return httpx.Response(200, headers={"content-type": "text/html"}, text=body)


async def _rendered_crawl(tmp_path, monkeypatch, pages, *, before_render=None):
    """A Knowledge crawl of *pages* pages, each rendered by the crawl's own
    job-owned browser (the ladder's Chromium tier, called directly)."""
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: SimpleNamespace(available=False)
    urls = [SEED + f"page-{n}" for n in range(pages)]
    owners = []

    async def fetch(url, **kwargs):
        owner = browser_mod.current_browser_job.get()
        owners.append(owner)
        if before_render is not None:
            await before_render(len(owners), owner)
        document = await run_in_browser_loop(browser_mod.render_document(
            url, challenge=lambda html, status: False, profile="generic"))
        return document.result

    async def discover(*a, **k):
        return list(urls)

    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    monkeypatch.setattr(ki, "_PAGE_RETRY_WAITS", (0, 0))
    try:
        async for _update in ki.ingest_site("docs", SEED, job_id="long"):
            pass
        return crawl_jobs.projection("long"), sorted(store.list_source_urls("docs")), urls, owners
    finally:
        store.close()


def _old_rule(monkeypatch):
    """Before 2026-11-13: a started browser was used until the job ended,
    and a page under which it died was not tried again."""
    monkeypatch.setattr(browser_mod.BrowserJob, "_relaunch_reason", lambda self: None)
    monkeypatch.setattr(browser_mod, "_render_in_job", browser_mod._render_once)


@pytest.mark.asyncio
async def test_a_crawl_longer_than_the_browsers_lifetime_stores_every_page(
        chromium, broker_transport, journal, tmp_path, monkeypatch):  # noqa: F811
    broker_transport(_served)
    monkeypatch.setattr(bd, "_LIFETIME", 6)
    projection, stored, urls, owners = await _rendered_crawl(tmp_path, monkeypatch, 10)
    assert stored == sorted(urls), projection["pages"]
    assert projection["acquisition"]["status"] == "completed"
    assert owners[0].relaunches >= 1  # the crawl outlived at least one browser


@pytest.mark.asyncio
async def test_without_the_relaunch_the_same_crawl_loses_its_pages(
        chromium, broker_transport, journal, tmp_path, monkeypatch):  # noqa: F811
    """Negative control: the old rule, the same crawl."""
    broker_transport(_served)
    monkeypatch.setattr(bd, "_LIFETIME", 6)
    _old_rule(monkeypatch)
    projection, stored, urls, _owners = await _rendered_crawl(tmp_path, monkeypatch, 10)
    assert len(stored) < len(urls)
    assert projection["acquisition"]["status"] == "partial"


async def _kill_chromium(owner):
    """End every Chromium process of the job's tree; its driver lives on."""
    leader = psutil.Process(owner._driver.transport.proc.pid)
    victims = leader.children(recursive=True)
    assert victims, "Chromium did not start under the driver"
    for process in victims:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    await asyncio.to_thread(psutil.wait_procs, victims, timeout=10)


@pytest.mark.asyncio
async def test_a_chromium_killed_mid_crawl_is_relaunched_and_no_page_is_lost(
        chromium, broker_transport, journal, tmp_path, monkeypatch):  # noqa: F811
    broker_transport(_served)

    async def kill_once(rendered, owner):
        if rendered == 4:
            await _kill_chromium(owner)

    projection, stored, urls, owners = await _rendered_crawl(tmp_path, monkeypatch, 7, before_render=kill_once)
    assert stored == sorted(urls), projection["pages"]
    assert owners[0].relaunches >= 1


@pytest.mark.asyncio
async def test_without_the_relaunch_a_killed_chromium_fails_the_rest_of_the_crawl(
        chromium, broker_transport, journal, tmp_path, monkeypatch):  # noqa: F811
    """Negative control: the old rule after the same kill."""
    broker_transport(_served)
    _old_rule(monkeypatch)

    async def kill_once(rendered, owner):
        if rendered == 4:
            await _kill_chromium(owner)

    projection, stored, urls, _owners = await _rendered_crawl(tmp_path, monkeypatch, 7, before_render=kill_once)
    assert len(stored) <= 3, stored
    assert projection["acquisition"]["status"] == "partial"
