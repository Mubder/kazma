"""Offline witnesses of the 2026-10-10 audit baseline, NOT remediation gates.

Run against audited revision 9796e53fbd66bb1ce827f9ccb90bf1e11f58fe53.
These assertions document its defects; keep remediation tests separate.
No external network, real provider key, browser launch, or install store.
"""

import asyncio
import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

ru = importlib.import_module("kazma_core.tools.read_url")
ki = importlib.import_module("kazma_core.stores.knowledge_ingest")
wr = importlib.import_module("kazma_core.tools.web_research")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in ("KAZMA_FIRECRAWL_API_KEY", "KAZMA_JINA_READER", "KAZMA_FETCH_BACKEND",
                "KAZMA_KB_JINA_FALLBACK", "KAZMA_KB_SCOPE_MODE"):
        monkeypatch.delenv(key, raising=False)
    ru.clear_url_cache()
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    yield
    ru.clear_url_cache()


@pytest.mark.asyncio
async def test_default_recovery_uses_jina_before_local_browser(monkeypatch):
    calls = []

    async def jina(url):
        calls.append("jina")
        return "remote answer"

    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    browser = AsyncMock(return_value="local answer")
    monkeypatch.setattr(ru, "_fetch_with_playwright", browser)
    assert await ru._recover_hard_page("https://audit.example/") == "remote answer"
    assert calls == ["jina"]
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_firecrawl_runs_before_http(monkeypatch):
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "audit-fake-key")
    provider = AsyncMock(return_value="remote answer")
    monkeypatch.setattr(ru, "_try_firecrawl", provider)
    assert await ru._fetch_via_optional_backends("https://audit.example/article") == "remote answer"
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_wrapper_discards_private_peer_evidence():
    from kazma_core.security.ssrf import SSRFError, assert_peer_public

    stream = Mock()
    stream.get_extra_info.return_value = ("127.0.0.1", 80)

    def respond(request):
        return httpx.Response(200, text="article", request=request,
                              extensions={"network_stream": stream})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        original = await client.get("https://audit.example/")
        with pytest.raises(SSRFError):
            assert_peer_public(original, url=str(original.url))
        wrapped, _, _ = await ru._get_capped(client, "https://audit.example/", 1000)
        assert wrapped.extensions == {}
        assert_peer_public(wrapped, url=str(wrapped.url))  # silently accepts


def test_peer_reader_uses_an_unsupported_httpcore_info_key():
    from httpcore._backends.sync import SyncStream
    from kazma_core.security.ssrf import assert_peer_public, peer_ip_from_response

    sock = Mock()
    sock.getpeername.return_value = ("127.0.0.1", 80)
    stream = SyncStream(sock)
    assert stream.get_extra_info("server_addr") == ("127.0.0.1", 80)
    response = httpx.Response(200, extensions={"network_stream": stream})
    assert peer_ip_from_response(response) is None
    assert_peer_public(response, url="https://audit.example/")


@pytest.mark.asyncio
async def test_httpx_connect_error_skips_recovery(monkeypatch):
    def broken(request):
        raise httpx.ConnectError("offline witness", request=request)

    monkeypatch.setattr(ru, "_fetch_via_optional_backends", AsyncMock(return_value=None))
    recovery = AsyncMock(return_value="recovered")
    monkeypatch.setattr(ru, "_recover_hard_page", recovery)
    monkeypatch.setattr("kazma_core.proxy.client.get_active_proxy_url", lambda: None)
    monkeypatch.setattr("kazma_core.proxy.client.get_scraping_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    result = await ru._fetch_full_text("https://audit.example/article")
    assert result.startswith("Error:")
    recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_crawl_index_omits_actual_failure_reason(monkeypatch):
    from kazma_core.web_acquire.fetch import FetchResult

    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=(None, "https://audit.example/")))
    monkeypatch.setattr(wr, "_central_fetch", AsyncMock(return_value=FetchResult(
        ok=False, url="https://audit.example/", error="UNIQUE_QUOTA_EXHAUSTED")))
    result = await wr.crawl_site("https://audit.example/", max_pages=1, save=False)
    assert "error" in result
    assert "UNIQUE_QUOTA_EXHAUSTED" not in result


@asynccontextmanager
async def no_browser():
    yield


def mocked_ingest(monkeypatch, pages, extract):
    monkeypatch.setattr(ki, "kb_discover_pages", AsyncMock(return_value=pages))
    monkeypatch.setattr(ki, "_extract_page", AsyncMock(side_effect=extract))
    monkeypatch.setattr(ki, "_pw_browser_scope", no_browser)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "_store_page", lambda *a: (1, 0, 1))
    store = Mock()
    store.list_source_urls.return_value = pages
    store.count_chunks.return_value = 3
    index = Mock()
    index.prune_sources_not_in.return_value = 2
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    return index


@pytest.mark.asyncio
async def test_capped_ingest_prunes_unvisited_pages(monkeypatch):
    pages = ["https://audit.example/docs/", "https://audit.example/docs/a", "https://audit.example/docs/b"]
    index = mocked_ingest(monkeypatch, pages, lambda url: ("article", "ok", ""))
    updates = [u async for u in ki.ingest_site("audit", pages[0], max_pages=1)]
    args, kwargs = index.prune_sources_not_in.call_args
    assert args[1] == {pages[0]}
    assert kwargs["candidate_urls"] == set(pages)
    assert updates[-1].pruned_urls == 2


@pytest.mark.asyncio
async def test_done_update_clears_page_error_details(monkeypatch):
    pages = ["https://audit.example/docs/", "https://audit.example/docs/a"]
    mocked_ingest(monkeypatch, pages, lambda url: (None, "error", "UNIQUE_RATE_LIMIT"))
    updates = [u async for u in ki.ingest_site("audit", pages[0])]
    assert any(u.errors for u in updates)
    assert updates[-1].phase == "done"
    assert updates[-1].failed == 2
    assert updates[-1].errors == []


@pytest.mark.asyncio
async def test_nested_sitemap_is_not_traversed(monkeypatch):
    monkeypatch.setenv("KAZMA_JINA_READER", "0")
    monkeypatch.setattr(ki, "_llms_txt_discover", AsyncMock(return_value=[]))
    seed = "https://audit.example/docs/"
    children = [f"https://audit.example/sitemap-{i}.xml" for i in range(4)]
    xml = "<sitemapindex>" + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in children) + "</sitemapindex>"
    requested = []

    async def get(url, **kw):
        requested.append(url)
        return (xml if url.endswith("/sitemap.xml") else None), url

    monkeypatch.setattr(ki, "_http_get_text", get)
    monkeypatch.setattr(ki, "_bfs_discover", AsyncMock(return_value=[seed]))
    assert await ki.kb_discover_pages(seed) == [ki._canonical_page_url(seed)]
    assert not set(children) & set(requested)


@pytest.mark.asyncio
async def test_cancelled_ingest_leaves_discovery_running(monkeypatch):
    started = asyncio.Event()
    child = []

    async def discover(*a, **kw):
        child.append(asyncio.current_task())
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    generator = ki.ingest_site("audit", "https://audit.example/docs/")
    await anext(generator)
    consumer = asyncio.create_task(anext(generator))
    await started.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    try:
        assert not child[0].done()
    finally:
        child[0].cancel()
        await asyncio.gather(child[0], return_exceptions=True)
        await generator.aclose()


def test_query_pages_collapse_to_one_identity():
    assert ki._canonical_page_url("https://audit.example/docs?page=1") == ki._canonical_page_url(
        "https://audit.example/docs?page=2")


@pytest.mark.asyncio
async def test_crawl_overwrites_an_existing_extract(monkeypatch, tmp_path):
    from kazma_core.web_acquire.fetch import FetchResult

    url = "https://audit.example/docs/"
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=(None, url)))
    monkeypatch.setattr(wr, "_central_fetch", AsyncMock(return_value=FetchResult(ok=True, url=url, text="first")))
    monkeypatch.setattr(wr, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(wr, "_safe_workspace_path", lambda name, source: tmp_path / name)
    await wr.crawl_site(url, max_pages=1, save=True)
    target = next(tmp_path.rglob("*.md"))
    target.write_text("USER EDIT TO PRESERVE", encoding="utf-8")
    await wr.crawl_site(url, max_pages=1, save=True)
    assert "USER EDIT TO PRESERVE" not in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_httpx_backend_setting_still_allows_remote_recovery(monkeypatch):
    monkeypatch.setenv("KAZMA_FETCH_BACKEND", "httpx")
    provider = AsyncMock(return_value="remote answer")
    monkeypatch.setattr(ru, "_try_jina_reader", provider)
    assert await ru._recover_hard_page("https://audit.example/article") == "remote answer"
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_domain_crawl_fetches_external_redirect(monkeypatch):
    from kazma_core.web_acquire.fetch import FetchResult

    external = "https://other.example/article"
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=(None, external)))
    fetch = AsyncMock(return_value=FetchResult(ok=True, url=external, text="article"))
    monkeypatch.setattr(wr, "_central_fetch", fetch)
    await wr.crawl_site("https://audit.example/", max_pages=1, same_domain_only=True, save=False)
    fetch.assert_awaited_once_with(external, purpose="crawl")


@pytest.mark.asyncio
async def test_overlapping_jobs_close_each_others_browser_context(monkeypatch):
    contexts = []

    async def start(self):
        context = AsyncMock()
        contexts.append(context)
        ki._pw_shared.update(context=context, browser=None, pw=None)

    async def inline(coro):
        return await coro

    monkeypatch.setattr(ki._pw_browser_scope, "_start_impl", start)
    monkeypatch.setattr("kazma_core.playwright_loop.run_in_browser_loop", inline)
    first, second = ki._pw_browser_scope(), ki._pw_browser_scope()
    try:
        await first.__aenter__()
        await second.__aenter__()
        await first.__aexit__(None, None, None)
        contexts[1].close.assert_awaited_once()
        contexts[0].close.assert_not_awaited()
        assert ki._pw_shared["context"] is None
    finally:
        ki._pw_shared.update(context=None, browser=None, pw=None)
