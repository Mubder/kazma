"""Centralized web_acquire façade — profiles + structured results."""

from __future__ import annotations

import pytest

from kazma_core.web_acquire import (
    CRAWL_PROFILES,
    extract_urls_from_search,
    get_crawl_profile,
)
from kazma_core.web_acquire.fetch import FetchResult, fetch_text
from kazma_core.web_acquire.profiles import profile_to_crawl_kwargs
from kazma_core.web_acquire.search import SearchResult, search


def test_crawl_profiles_known():
    assert "research_brief" in CRAWL_PROFILES
    assert "research_deep" in CRAWL_PROFILES
    assert "kb_site" in CRAWL_PROFILES
    p = get_crawl_profile("kb")
    assert p.name == "kb_site"
    assert p.max_pages >= 50
    brief = get_crawl_profile("research_brief")
    assert brief.max_pages <= 15
    kw = profile_to_crawl_kwargs(brief)
    assert kw["max_pages"] == brief.max_pages
    assert "same_domain_only" in kw


def test_extract_urls_from_search_markdown():
    md = """
# Results
- [A](https://example.org/a) — snip
- https://docs.python.org/3/
- https://example.org/a
"""
    urls = extract_urls_from_search(md)
    # example.com host filtered; python.org kept; dedup a
    assert "https://docs.python.org/3/" in urls
    assert urls.count("https://example.org/a") <= 1


@pytest.mark.asyncio
async def test_fetch_text_empty_url():
    r = await fetch_text("  ", purpose="test")
    assert isinstance(r, FetchResult)
    assert r.ok is False
    assert r.error


@pytest.mark.asyncio
async def test_fetch_text_uses_ladder(monkeypatch):
    async def _fake(url: str) -> str:
        return f"# page\n\ncontent for {url}"

    # tools/__init__.py can shadow submodule names with functions — use importlib.
    # Patch the PUBLIC alias — the façade's contract since the read_url
    # public-entry refactor (the private _fetch_full_text is impl detail).
    import importlib

    ru_mod = importlib.import_module("kazma_core.tools.read_url")
    monkeypatch.setattr(ru_mod, "fetch_full_text", _fake)
    r = await fetch_text("https://example.com/x", purpose="research")
    assert r.ok is True
    assert "content for" in r.text
    assert r.purpose == "research"
    assert r.char_count > 0
    assert r.latency_ms >= 0


@pytest.mark.asyncio
async def test_search_empty_query():
    r = await search("", purpose="research")
    assert isinstance(r, SearchResult)
    assert r.ok is False
    assert r.error


@pytest.mark.asyncio
async def test_search_wraps_web_search(monkeypatch):
    async def _fake(q: str, max_results: int = 8) -> str:
        return f"# Search: {q}\n\n- https://docs.python.org/3/ library"

    import importlib

    ws_mod = importlib.import_module("kazma_core.tools.web_search")
    monkeypatch.setattr(ws_mod, "web_search", _fake)
    r = await search("python", max_results=5, purpose="research")
    assert r.ok is True
    assert "python" in r.query
    assert any("python.org" in u for u in r.urls)
