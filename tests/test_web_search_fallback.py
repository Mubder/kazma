"""web_search continues across backends on empty results + clear empty message."""

from __future__ import annotations

import importlib

import pytest

ws = importlib.import_module("kazma_core.tools.web_search")


def test_run_search_falls_through_empty_backends(monkeypatch):
    calls: list[str] = []

    def empty_searx(q, n):
        calls.append("searxng")
        return None, "searxng:empty"

    def empty_ddg(q, n):
        calls.append("duckduckgo")
        return None, "duckduckgo:empty"

    def hit_bing(q, n):
        calls.append("bing")
        return [{"title": "T", "href": "https://example.com", "body": "ok"}], "bing:ok"

    def no_wiki(q, n):
        calls.append("wikipedia")
        return None, "wikipedia:empty"

    monkeypatch.setattr(ws, "_searxng_search", empty_searx)
    monkeypatch.setattr(ws, "_ddg_search", empty_ddg)
    monkeypatch.setattr(ws, "_bing_search", hit_bing)
    monkeypatch.setattr(ws, "_wikipedia_search", no_wiki)

    results, attempts, backend = ws._run_search("OpenAI", 5)
    assert backend == "bing"
    assert len(results) == 1
    assert "duckduckgo:empty" in attempts
    assert calls == ["searxng", "duckduckgo", "bing"]


def test_run_search_all_empty_reports_attempts(monkeypatch):
    monkeypatch.setattr(ws, "_searxng_search", lambda q, n: (None, "searxng:empty"))
    monkeypatch.setattr(ws, "_ddg_search", lambda q, n: (None, "duckduckgo:empty"))
    monkeypatch.setattr(ws, "_bing_search", lambda q, n: (None, "bing:empty"))
    monkeypatch.setattr(ws, "_wikipedia_search", lambda q, n: (None, "wikipedia:empty"))

    results, attempts, backend = ws._run_search("OpenAI", 3)
    assert results == []
    assert backend == ""
    assert len(attempts) == 4


@pytest.mark.asyncio
async def test_web_search_empty_message_is_actionable(monkeypatch):
    monkeypatch.setattr(
        ws,
        "_run_search",
        lambda q, n: ([], ["duckduckgo:empty", "bing:empty"], ""),
    )
    out = await ws.web_search("OpenAI")
    assert "No web results" in out
    assert "Backends tried" in out
    assert "read_url" in out
    assert "KAZMA_SEARXNG_URL" in out


@pytest.mark.asyncio
async def test_web_search_formats_winner(monkeypatch):
    monkeypatch.setattr(
        ws,
        "_run_search",
        lambda q, n: (
            [{"title": "Python", "href": "https://python.org", "body": "lang"}],
            ["duckduckgo:empty", "bing:ok"],
            "bing",
        ),
    )
    out = await ws.web_search("Python")
    assert "Source: bing" in out
    assert "python.org" in out
    assert "Python" in out
