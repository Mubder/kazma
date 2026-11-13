"""A site that refuses page after page stops its crawl (2026-11-13).

When a site started refusing -- a bot wall, a run of 403s, a server down for
good -- every remaining page still ran the whole fetch ladder (up to 90 s each,
three HTTP retries and a browser render behind a bot wall) up to the page cap.
Now, after ``page_outcome.REFUSAL_STREAK`` failed pages of one class with no
page stored between, the crawl stops ``interrupted`` (``site_refusing``): the
pages not reached stay pending for Resume, each with its reason, and the
crawl is never resumed on its own.

The replayed site runs the product's whole crawl (``tests/_crawl_replay.py``):
admission, robots.txt, the sitemap, the fetch ladder and its retries, the
journal; only the network is a recording.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs, page_outcome
from kazma_core.web_acquire.result import FetchResult

from tests import _crawl_replay as cr

ORIGIN = "https://refuse.example"
SEED = ORIGIN + "/docs/"
PAGES = [f"{SEED}page-{n:02d}" for n in range(40)]
#: The site answers the first pages, then refuses everything (a bot wall).
ANSWERED = 5


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


class _NotFoundElsewhere(dict):
    """A recording whose every other address answers 404 (llms.txt and the
    well-known sitemap paths), so discovery settles without a connect error."""

    def get(self, key, default=None):
        return super().get(key, {"status": 404, "headers": {"content-type": "text/html"},
                                 "text": "<html><body>Not found</body></html>"})


def _recording():
    responses = _NotFoundElsewhere()
    responses[ORIGIN + "/robots.txt"] = {"status": 200, "headers": {"content-type": "text/plain"},
                                         "text": f"User-agent: *\nAllow: /\nSitemap: {ORIGIN}/sitemap.xml\n"}
    locs = "".join(f"<url><loc>{url}</loc></url>" for url in PAGES)
    responses[ORIGIN + "/sitemap.xml"] = {
        "status": 200, "headers": {"content-type": "application/xml"},
        "text": f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'}
    for n, url in enumerate(PAGES):
        if n < ANSWERED:
            words = " ".join(f"Page {n} of the guide explains topic {n} step {k}." for k in range(20))
            responses[url] = {"status": 200, "headers": {"content-type": "text/html"},
                              "text": f"<html><head><title>Page {n}</title></head><body><main><h1>Page {n}</h1>"
                                      f"<p>{words}</p></main></body></html>"}
        else:
            responses[url] = {"status": 403, "headers": {"content-type": "text/html"},
                              "text": "<html><body>Forbidden</body></html>"}
    return {"responses": responses}


@pytest.fixture
def quick_waits(monkeypatch):
    """The ladder waits 1.5-4.5 s before each HTTP retry of a 403; the replay
    does not need to wait them out (every wait of a second or more is cut)."""
    real = asyncio.sleep

    async def sleep(delay, result=None):
        return await real(0 if delay >= 1 else delay, result)

    monkeypatch.setattr(asyncio, "sleep", sleep)


async def _replay_refusing_site(tmp_path, monkeypatch):
    # The seed is a page of the frontier too (the site answers 404 for it).
    site = {"name": "refusing", "seed": SEED, "max_pages": len(PAGES) + 1, "scope": "section"}
    with cr.replaying(monkeypatch, _recording()) as transport:
        outcome = await cr.crawl(site, tmp_path / "kb", monkeypatch)
    fetched_pages = sorted({url for url in transport.served if url in PAGES})
    return outcome, fetched_pages, crawl_jobs.inspect_jobs("refusing:corpus")


@pytest.mark.asyncio
async def test_a_site_that_turns_to_refusing_stops_the_crawl_after_a_streak(
        journal, quick_waits, tmp_path, monkeypatch):
    outcome, fetched_pages, row = await _replay_refusing_site(tmp_path, monkeypatch)
    streak = page_outcome.REFUSAL_STREAK
    assert len(fetched_pages) == ANSWERED + streak, fetched_pages  # 15 of 40
    assert row["status"] == "interrupted" and row["interrupted_by"] == "site_refusing"
    assert row["refusal"] == {"reason": "http_error", "pages": streak}
    assert "site_refusing" in row["notes"]
    pending = [page for page in row["pages"] if page["status"] == "pending"]
    assert len(pending) == len(PAGES) - ANSWERED - streak
    assert {page["reason"] for page in pending} == {"site_refusing"}
    assert outcome["chunks"] > 0  # what the site answered is stored
    # The owner resumes it (never on its own); the Running work card and the
    # incident say why it stopped.
    projection = crawl_jobs.projection("refusing:corpus")
    assert projection["resume_available"] and not crawl_jobs.auto_resume_due(row)
    assert crawl_jobs.failure_reason(row).startswith("The site refused 10 pages in a row (an HTTP refusal)")


@pytest.mark.asyncio
async def test_without_the_breaker_every_page_ran_the_ladder(journal, quick_waits, tmp_path, monkeypatch):
    """Negative control: the same site with the streak out of reach."""
    monkeypatch.setattr(page_outcome, "REFUSAL_STREAK", 10 ** 6)
    _outcome, fetched_pages, row = await _replay_refusing_site(tmp_path, monkeypatch)
    assert len(fetched_pages) == len(PAGES)
    assert row["status"] == "partial"


# ── The streak's rule, on a scripted site ────────────────────────────────────


def _ok(url):
    return FetchResult(ok=True, url=url, final_url=url, text=f"# {url}\n\n" + "useful words " * 40, http_status=200)


def _refused(url, code="challenge_page", status=403):
    return FetchResult(ok=False, url=url, error=f"{code} for {url}", error_code=code, http_status=status)


async def _scripted(tmp_path, monkeypatch, answers):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]
    urls = [f"https://docs.example.test/p{n:02d}" for n in range(len(answers))]
    script = dict(zip(urls, answers))
    calls = []

    async def fetch(url, **kwargs):
        calls.append(url)
        return script[url](url)

    async def discover(*a, **k):
        return list(urls)

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    monkeypatch.setattr(ki, "_PAGE_RETRY_WAITS", (0, 0))
    try:
        async for _ in ki.ingest_site("docs", "https://docs.example.test/", job_id="scripted"):
            pass
    finally:
        store.close()
    return calls, crawl_jobs.inspect_jobs("scripted")


@pytest.mark.asyncio
async def test_a_stored_page_clears_the_streak(journal, tmp_path, monkeypatch):
    answers = [_refused] * 9 + [_ok] + [_refused] * 9 + [_ok]
    calls, row = await _scripted(tmp_path, monkeypatch, answers)
    assert len(calls) == len(answers) and row["status"] == "partial"


@pytest.mark.asyncio
async def test_failures_that_are_not_the_sites_refusal_do_not_count(journal, tmp_path, monkeypatch):
    def empty(url):
        return FetchResult(ok=True, url=url, final_url=url, text="", http_status=200)

    answers = [empty] * 12 + [_ok]
    calls, row = await _scripted(tmp_path, monkeypatch, answers)
    assert len(calls) == len(answers) and row.get("refusal") is None


async def _research_crawl(monkeypatch):
    """A research crawl (``crawl_site``) of a site whose seed links to forty
    pages and refuses every one of them."""
    import importlib

    wr = importlib.import_module("kazma_core.tools.web_research")
    calls = []

    async def public(*args, **kwargs):
        return ("93.184.216.34",)

    async def fetch(url, **kwargs):
        calls.append(url)
        if url == "https://docs.example/":
            return FetchResult(ok=True, url=url, text="seed page", links_complete=True,
                               links=tuple(f"https://docs.example/p{n}" for n in range(40)))
        return _refused(url)

    monkeypatch.setattr("kazma_core.web_acquire.dns_worker.validate_public_url", public)
    monkeypatch.setattr(wr, "_central_fetch", fetch)
    report = await wr.crawl_site("https://docs.example/", max_pages=50, max_depth=2, delay_ms=0,
                                 save=False, respect_robots=False)
    return calls, report


@pytest.mark.asyncio
async def test_a_research_crawl_of_a_refusing_site_stops_too(journal, monkeypatch):
    calls, report = await _research_crawl(monkeypatch)
    assert len(calls) == 1 + page_outcome.REFUSAL_STREAK
    assert "Stopped: The site refused 10 pages in a row" in report and "status=partial" in report


@pytest.mark.asyncio
async def test_without_the_breaker_the_research_crawl_asked_every_page(journal, monkeypatch):
    """Negative control: the streak out of reach."""
    monkeypatch.setattr(page_outcome, "REFUSAL_STREAK", 10 ** 6)
    calls, _report = await _research_crawl(monkeypatch)
    assert len(calls) == 41


@pytest.mark.asyncio
async def test_each_class_keeps_its_own_streak(journal, tmp_path, monkeypatch):
    def server_down(url):
        return _refused(url, code="http_error", status=503)

    # Nine refusals, then server errors: the refusals never reach ten, the
    # server errors do (each tried again inside the crawl first).
    answers = [_refused] * 9 + [server_down] * 12
    calls, row = await _scripted(tmp_path, monkeypatch, answers)
    assert len(set(calls)) == 9 + page_outcome.REFUSAL_STREAK
    assert row["refusal"] == {"reason": "server_error", "pages": page_outcome.REFUSAL_STREAK}
