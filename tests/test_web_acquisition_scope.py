"""Crawl scope reaches extraction, redirects, rendering and cache boundaries."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import httpx
import pytest

ru = importlib.import_module("kazma_core.tools.read_url")


def in_scope(url):
    return urlsplit(url).hostname == "first.example"


@pytest.mark.asyncio
async def test_extraction_redirect_is_blocked_before_dns_or_target_get(monkeypatch):
    from kazma_core.web_acquire import fetch_text
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf

    dns = Mock(return_value=("93.184.216.34",))
    monkeypatch.setattr(ssrf, "validate_url", dns)
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    requests = []

    def respond(request):
        requests.append(str(request.url))
        stream = SimpleNamespace(get_extra_info=lambda name: ("93.184.216.34", 443))
        return httpx.Response(302, headers={"location": "https://second.example/escaped"},
                              extensions={"network_stream": stream})

    monkeypatch.setattr(pc, "get_scraping_client", lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=False))
    result = await fetch_text("https://first.example/", purpose="crawl", scope_check=in_scope)
    assert not result.ok
    assert "outside crawl scope" in result.error
    assert requests == ["https://first.example/"]
    assert [call.args[0] for call in dns.call_args_list] == ["https://first.example/"]

    # The actual crawler must propagate its scope even when the earlier raw
    # discovery pass fails, rather than starting an unrestricted second pass.
    wr = importlib.import_module("kazma_core.tools.web_research")
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=("", "https://first.example/")))
    requests.clear()
    out = await wr.crawl_site("https://first.example/", max_pages=1, same_domain_only=True,
                              save=False, delay_ms=0, respect_robots=False)
    assert "outside crawl scope" in out
    assert requests == ["https://first.example/"]


@pytest.mark.asyncio
async def test_scoped_fetch_cannot_consume_an_unscoped_redirect_cache(monkeypatch):
    from kazma_core.security import ssrf

    ru.clear_url_cache()
    ru._cache_put("https://second.example/", "Previously fetched redirected content")
    dns = Mock(side_effect=AssertionError("out-of-scope DNS"))
    monkeypatch.setattr(ssrf, "validate_url", dns)
    result = await ru.fetch_full_text("https://second.example/", scope_check=in_scope)
    assert result.startswith("Error:") and "outside crawl scope" in result
    dns.assert_not_called()
    ru.clear_url_cache()


@pytest.mark.asyncio
async def test_scoped_reader_refuses_hosted_helpers_even_when_operator_allows_them(monkeypatch):
    from kazma_core.web_acquire.scope import AcquisitionScope, active_scope

    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "jina,firecrawl")
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-not-a-real-key")
    token = active_scope.set(AcquisitionScope(in_scope))
    client = Mock(side_effect=AssertionError("must not make hosted request"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    try:
        assert await ru._try_jina_reader("https://first.example/") is None
        assert await ru._try_firecrawl("https://first.example/") is None
    finally:
        active_scope.reset(token)
    client.assert_not_called()


@pytest.mark.asyncio
async def test_scope_is_job_local_and_resets_after_cancellation(monkeypatch):
    from kazma_core.web_acquire.scope import active_scope

    entered = asyncio.Event()
    release = asyncio.Event()

    async def ladder(url):
        scope = active_scope.get()
        assert scope is not None
        assert scope.allows(url)
        entered.set()
        await release.wait()
        assert active_scope.get() is scope
        from kazma_core.web_acquire import FetchResult
        return FetchResult(ok=True, url=url, text="content")

    monkeypatch.setattr(ru, "_fetch_full_text_impl", ladder)
    first = asyncio.create_task(ru.fetch_full_text("https://first.example/", scope_check=in_scope))
    await entered.wait()
    assert active_scope.get() is None
    second = asyncio.create_task(ru.fetch_full_text(
        "https://second.example/", scope_check=lambda url: not in_scope(url)))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "content"
    assert active_scope.get() is None






@pytest.mark.asyncio
async def test_scope_checker_failure_refuses_acquisition(monkeypatch):
    from kazma_core.security import ssrf

    dns = Mock(side_effect=AssertionError("no DNS"))
    monkeypatch.setattr(ssrf, "validate_url", dns)
    def broken(url):
        raise RuntimeError("broken validator")
    text = await ru.fetch_full_text("https://first.example/", scope_check=broken)
    assert text == "Error: Crawl scope validator failed; acquisition was refused."
    dns.assert_not_called()




@pytest.mark.asyncio
async def test_actual_chromium_js_navigation_is_refused_across_browser_loop(monkeypatch):
    from pathlib import Path
    from urllib.parse import quote
    from kazma_core.security import ssrf
    from kazma_core.proxy import client as pc

    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("optional Chromium executable is not installed")
    page = "data:text/html," + quote(
        "<html><body>Scope fixture<script>location.href='https://second.example/escape';"
        "</script></body></html>")
    async def render(url):
        from kazma_core.web_acquire import FetchResult
        text = await ru._fetch_with_playwright(page)
        return FetchResult(ok=bool(text), url=url, text=text or "")
    monkeypatch.setattr(ru, "_fetch_full_text_impl", render)
    monkeypatch.setattr(pc, "playwright_proxy", lambda: None)
    dns = Mock(side_effect=AssertionError("must refuse navigation before public DNS"))
    monkeypatch.setattr(ssrf, "validate_url", dns)
    text = await ru.fetch_full_text("https://first.example/", scope_check=lambda url:
                                   in_scope(url) or url.startswith("data:text/html,"))
    assert "outside crawl scope: https://second.example/escape" in text
    dns.assert_not_called()
