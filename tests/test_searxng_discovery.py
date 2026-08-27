"""SearXNG multi-base discovery + dead-host cooldown for web_search."""

from __future__ import annotations

import importlib
import time

import pytest

ws = importlib.import_module("kazma_core.tools.web_search")


@pytest.fixture(autouse=True)
def _reset_searxng_cache():
    ws._searxng_cache.clear()
    ws._searxng_cache.update({"base": None, "dead_until": 0.0, "note": ""})
    yield
    ws._searxng_cache.clear()
    ws._searxng_cache.update({"base": None, "dead_until": 0.0, "note": ""})


def test_candidate_bases_include_env_and_defaults(monkeypatch):
    monkeypatch.setenv("KAZMA_SEARXNG_URL", "http://custom:9999/")
    bases = ws._searxng_candidate_bases()
    assert bases[0] == "http://custom:9999"
    assert "http://127.0.0.1:8088" in bases
    assert "http://searxng:8080" in bases
    # de-duped
    assert len(bases) == len(set(bases))


def test_searxng_prefers_cached_good_base(monkeypatch):
    calls: list[str] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {"title": "A", "url": "https://a.example", "content": "body"}
                ]
            }

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append(url)
        return _Resp()

    # Loopback bases so the code takes the httpx.get path (non-loopback
    # bases route through the scraping client, bypassing the httpx.get mock).
    monkeypatch.setattr(ws, "_searxng_candidate_bases", lambda: [
        "http://localhost:1",
        "http://localhost:2",
    ])
    ws._searxng_cache["base"] = "http://localhost:2"

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    results, note = ws._searxng_search("q", 3)
    assert results and results[0]["href"] == "https://a.example"
    assert "localhost:2" in note
    assert calls[0].startswith("http://localhost:2/")


def test_searxng_cooldown_skips_when_all_dead(monkeypatch):
    ws._searxng_cache["base"] = None
    ws._searxng_cache["dead_until"] = time.time() + 30
    ws._searxng_cache["note"] = "searxng:skipped"

    results, note = ws._searxng_search("q", 3)
    assert results is None
    assert "skipped" in note or "unavailable" in note


def test_searxng_all_fail_sets_cooldown(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(ws, "_searxng_candidate_bases", lambda: ["http://dead:1"])
    monkeypatch.setattr(httpx, "get", boom)
    results, note = ws._searxng_search("q", 2)
    assert results is None
    assert ws._searxng_cache["base"] is None
    assert ws._searxng_cache["dead_until"] > time.time()
    assert "dead:1" in note or "unavailable" in note


def test_searxng_empty_note_not_overwritten_by_dead_candidates(monkeypatch):
    """2026-08-27 report: the loop's LAST candidate (127.0.0.1:8080 —
    refused) overwrote the truthful 'empty@8088 (engines suspended)' note
    with 'unavailable', making a WORKING SearXNG look dead. The reachable
    note must win, the suspended engines must be surfaced, and a reachable
    instance must NOT trigger the 60s dead-cooldown."""
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "results": [],
                "unresponsive_engines": [
                    ["brave", "Suspended: too many requests"],
                    ["duckduckgo", "CAPTCHA"],
                ],
            }

    def fake_get(url, **k):
        if "8088" in url:
            return _Resp()
        raise httpx.ConnectError("refused")

    ws._searxng_cache.clear()
    ws._searxng_cache.update({"base": None, "dead_until": 0.0, "note": ""})
    monkeypatch.setattr(
        ws, "_searxng_candidate_bases",
        lambda: ["http://127.0.0.1:8088", "http://127.0.0.1:8080"],
    )
    monkeypatch.setattr(httpx, "get", fake_get)

    results, note = ws._searxng_search("obscure brand", 3)
    assert results is None
    assert note.startswith("searxng:empty@http://127.0.0.1:8088")
    assert "engines suspended" in note and "brave" in note
    assert "unavailable@http://127.0.0.1:8080" not in note
    # Reachable instance: no dead-cooldown, good base stays cached.
    assert ws._searxng_cache["base"] == "http://127.0.0.1:8088"
    assert ws._searxng_cache["dead_until"] == 0.0
