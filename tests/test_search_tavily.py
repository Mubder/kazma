"""Tavily is the search service after SearXNG (resilience plan F1).

Search was SearXNG first, then free fallbacks; SearXNG's engines were
suspended 14 times, and with them down the fallbacks answered off-topic
results. Tavily is a paid remote provider, so it follows the acquisition
policy: never asked unless ``KAZMA_ACQUISITION_MODE`` and
``KAZMA_ACQUISITION_REMOTE_PROVIDERS`` name it, each query reserves one
request of the remote allowance before it leaves, its key travels in a
header (never a URL, never through the scraping proxy), and SearXNG
answering nothing for an hour is a page.
"""

from __future__ import annotations

import functools
import importlib
import json

import httpx
import pytest

from kazma_core import config_store
from kazma_core.web_acquire import accounting, search_http

ws = importlib.import_module("kazma_core.tools.web_search")
_REAL_TAVILY = ws._tavily_search  # the real one, before any test patches it

KEY = "tvly-demo-fixture-key"
QUERY = "Kuwait independence 1961 Iraqi claim"
ROWS = [
    {"title": "Kuwait independence 1961", "href": "https://example.org/kuwait-1961",
     "body": "Kuwait gained independence from Britain in 1961; Iraq claimed it."},
    {"title": "Iraqi claim on Kuwait", "href": "https://example.org/iraq-claim",
     "body": "Qasim's 1961 claim on Kuwait."},
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()


@pytest.fixture
def permitted(store, monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "tavily")
    monkeypatch.setenv("KAZMA_TAVILY_API_KEY", KEY)
    return store


def _worker(rows=ROWS, status="ok", detail=""):
    calls = []

    def run(backend, query, count, *, key=None):
        calls.append({"backend": backend, "query": query, "count": count, "key": key})
        return {"status": status, "results": rows if status == "ok" else [], "reached": True, "detail": detail}

    run.calls = calls
    return run


# ── the policy decides ────────────────────────────────────────────────


def test_tavily_is_a_name_the_policy_accepts(monkeypatch):
    from kazma_core.web_acquire.policy import acquisition_policy

    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "tavily")
    assert acquisition_policy().permits("tavily")


def test_without_the_policy_tavily_is_never_asked(store, monkeypatch):
    monkeypatch.delenv("KAZMA_ACQUISITION_MODE", raising=False)
    monkeypatch.setenv("KAZMA_TAVILY_API_KEY", KEY)
    run = _worker()
    assert ws._tavily_search(QUERY, 6, run=run) == (None, "tavily:not permitted")
    assert run.calls == [], "a key is not consent"
    assert accounting.snapshot()["day_reserved"] == 0


def test_without_a_key_tavily_is_not_asked(permitted, monkeypatch):
    monkeypatch.delenv("KAZMA_TAVILY_API_KEY")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    run = _worker()
    assert ws._tavily_search(QUERY, 6, run=run) == (None, "tavily:no key")
    assert run.calls == []


# ── reserved, then recorded ───────────────────────────────────────────


def test_a_tavily_query_is_reserved_before_it_leaves_and_recorded(permitted):
    reserved_at_call = []

    def run(backend, query, count, *, key=None):
        reserved_at_call.append(accounting.snapshot()["day_reserved"])
        return {"status": "ok", "results": ROWS, "reached": True, "detail": ""}

    results, note = ws._tavily_search(QUERY, 6, run=run)

    assert (results, note) == (ROWS, "tavily:ok")
    assert reserved_at_call == [1], "the request was reserved before the worker ran"
    row = accounting.snapshot()["recent"][-1]
    assert row["provider"] == "tavily" and row["units"] == 1
    assert row["state"] == "response_received" and row["http_status"] == 200


def test_a_refused_tavily_answer_is_recorded_and_passed_on(permitted):
    run = _worker(status="http", detail="432")
    assert ws._tavily_search(QUERY, 6, run=run) == (None, "tavily:http_432")
    assert accounting.snapshot()["recent"][-1]["http_status"] == 432


def test_a_spent_allowance_passes_the_query_on(permitted, monkeypatch):
    monkeypatch.setenv("KAZMA_REMOTE_DAY_REQUESTS", "1")
    run = _worker()
    assert ws._tavily_search(QUERY, 6, run=run)[1] == "tavily:ok"
    results, note = ws._tavily_search(QUERY, 6, run=run)
    assert results is None and note == "tavily:resource_limit"
    assert len(run.calls) == 1, "nothing left the machine past the allowance"


# ── the chain: SearXNG down, Tavily answers ───────────────────────────


def _chain(monkeypatch, tavily):
    calls = []

    def backend(name, rows):
        def go(query, count):
            calls.append(name)
            return (rows, f"{name}:ok") if rows else (None, f"{name}:empty")
        return go

    monkeypatch.setattr(ws, "_searxng_search", backend("searxng", None))
    monkeypatch.setattr(ws, "_tavily_search", tavily)
    monkeypatch.setattr(ws, "_bing_search", backend("bing", [{"title": "Telegram", "href": "https://telegram.org/", "body": ""}]))
    monkeypatch.setattr(ws, "_wikipedia_search", backend("wikipedia", None))
    return calls


def test_with_searxng_down_tavily_answers(permitted, monkeypatch):
    worker = _worker()
    calls = _chain(monkeypatch, functools.partial(_REAL_TAVILY, run=worker))

    results, attempts, backend = ws._run_search(QUERY, 6)

    assert backend == "tavily" and results == ROWS
    assert attempts == ["searxng:empty", "tavily:ok"]
    assert calls == ["searxng"], "the free fallbacks were not needed"
    assert worker.calls[0]["key"] == KEY


def test_negative_control_without_tavily_the_chain_reaches_the_fallbacks(permitted, monkeypatch):
    calls = _chain(monkeypatch, lambda q, n: (None, "tavily:not permitted"))
    _results, _attempts, backend = ws._run_search(QUERY, 6)
    assert backend != "tavily"
    assert "bing" in calls


# ── the worker's request ──────────────────────────────────────────────


def _request(handler, **extra):
    request = {"backend": "tavily", "query": QUERY, "count": 5, "base": None, "proxy": None,
               "key": KEY, "delay": 0, **extra}
    return search_http._request(request, transport=httpx.MockTransport(handler))


def test_the_worker_posts_the_query_with_the_key_in_a_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"results": [
            {"title": "Kuwait independence 1961", "url": "https://example.org/k", "content": "1961", "score": 0.9},
        ]}
        # A streamed body, as a real answer is (the worker reads it raw).
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=httpx.ByteStream(json.dumps(body).encode()))

    data = _request(handler)

    assert data["status"] == "ok"
    assert data["results"] == [{"title": "Kuwait independence 1961", "href": "https://example.org/k", "body": "1961"}]
    (sent,) = seen
    assert sent.method == "POST" and str(sent.url) == search_http.TAVILY_ENDPOINT
    assert sent.headers["authorization"] == "Bearer " + KEY
    assert KEY not in str(sent.url)
    body = json.loads(sent.content)
    assert body["query"] == QUERY and body["max_results"] == 5


def test_the_worker_never_follows_a_tavily_redirect():
    hops = []

    def handler(request):
        hops.append(str(request.url))
        return httpx.Response(307, headers={"location": search_http.TAVILY_ENDPOINT + "?again=1"})

    assert _request(handler)["status"] == "error"
    assert len(hops) == 1, "a reserved request is never sent twice"


def test_a_tavily_request_never_goes_through_the_scraping_proxy():
    active = lambda: "http://fixture-user:fixture-secret@proxy.example:1080"  # noqa: E731
    assert search_http._proxy_for("tavily", False, active) is None
    # Negative control: the scraping fallbacks do use it.
    assert search_http._proxy_for("bing", False, active) == active()


def test_the_worker_refuses_a_tavily_request_without_a_key(monkeypatch):
    import io
    import sys

    request = {"backend": "tavily", "query": QUERY, "count": 5, "base": None, "proxy": None,
               "parent_pid": 1, "lifetime": 5}
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(request).encode())})())
    with pytest.raises(ValueError, match="Tavily key"):
        search_http._worker()


# ── SearXNG silent for an hour is a page ──────────────────────────────


def test_searxng_silent_for_an_hour_pages_once_and_its_answer_resets_it(monkeypatch):
    import kazma_core.observability.ops_alerts as ops

    pages = []
    monkeypatch.setattr(ops, "alert", lambda key, title, detail, **kw: pages.append((key, title)))
    monkeypatch.setattr(ws, "_searxng_cache", {"base": None, "dead_until": 0.0, "note": ""})

    ws._note_searxng_silent(1000.0, "searxng:unavailable")
    ws._note_searxng_silent(1000.0 + ws.SEARXNG_SILENT_PAGE_S - 1, "searxng:unavailable")
    assert pages == []
    ws._note_searxng_silent(1000.0 + ws.SEARXNG_SILENT_PAGE_S, "searxng:http_502")
    ws._note_searxng_silent(1000.0 + 2 * ws.SEARXNG_SILENT_PAGE_S, "searxng:http_502")
    assert [key for key, _ in pages] == ["search.searxng_silent"], "one page per silence"

    ws._searxng_answered()
    ws._note_searxng_silent(9000.0, "searxng:unavailable")
    ws._note_searxng_silent(9000.0 + ws.SEARXNG_SILENT_PAGE_S, "searxng:unavailable")
    assert len(pages) == 2, "an answer ends the silence; the next one pages again"


def test_research_readiness_says_whether_tavily_is_on(permitted):
    from kazma_core.tools import research_readiness

    checks = {c["id"]: c for c in research_readiness.research_readiness(probe_search=False)["checks"]}
    assert checks["second_search"]["message"].endswith("on")
