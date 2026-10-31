"""Behavioral remediation gates for the web acquisition industrial audit."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

ru = importlib.import_module("kazma_core.tools.read_url")
ki = importlib.import_module("kazma_core.stores.knowledge_ingest")
wr = importlib.import_module("kazma_core.tools.web_research")


async def _browser_command(browser):
    import psutil

    session = await browser.new_browser_cdp_session()
    try:
        info = await session.send("SystemInfo.getProcessInfo")
        pid = next(int(p["id"]) for p in info["processInfo"] if p["type"] == "browser")
        # Inspect only the browser process this test just launched.
        return await asyncio.to_thread(psutil.Process(pid).cmdline)
    finally:
        await session.detach()


def test_peer_address_uses_real_httpcore_adapter():
    from httpcore._backends.sync import SyncStream
    from kazma_core.security.ssrf import SSRFError, assert_peer_public, peer_ip_from_response

    sock = Mock()
    sock.getpeername.return_value = ("127.0.0.1", 80)
    response = httpx.Response(200, extensions={"network_stream": SyncStream(sock)})
    assert peer_ip_from_response(response) == "127.0.0.1"
    with pytest.raises(SSRFError, match="private"):
        assert_peer_public(response, url="https://public.example/")


@pytest.mark.asyncio
async def test_streaming_preserves_peer_evidence_after_close():
    from kazma_core.security.ssrf import SSRFError, assert_peer_public

    stream = Mock()
    stream.get_extra_info.return_value = ("169.254.169.254", 80)

    def respond(request):
        return httpx.Response(200, text="body", request=request,
                              extensions={"network_stream": stream})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SSRFError, match="private"):
            response, _, _ = await ru._get_capped(client, "https://public.example/", 1000)
            assert_peer_public(response, url="https://public.example/")


@pytest.mark.asyncio
async def test_pin_transport_never_resolves_an_unpinned_host(monkeypatch):
    from kazma_core.security.ssrf import SSRFError
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport

    network = AsyncMock()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    async with PinHostAsyncTransport({"allowed.example": "93.184.216.34"}) as transport:
        with pytest.raises(SSRFError, match="pin"):
            await transport.handle_async_request(httpx.Request("GET", "https://other.example/"))
    network.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["127.0.0.1", "169.254.169.254", "not-an-ip"])
async def test_pin_transport_refuses_bad_pin_before_dispatch(monkeypatch, pin):
    from kazma_core.security.ssrf import SSRFError
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport

    network = AsyncMock()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    async with PinHostAsyncTransport({"public.example": pin}) as transport:
        with pytest.raises(SSRFError, match="IP pin"):
            await transport.handle_async_request(httpx.Request("GET", "https://public.example/"))
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_discovery_pins_each_get_hop_without_head_preflight(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf
    from kazma_core.web_acquire.http import fetch_public

    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    sent = []
    pin_maps = []

    def network(request):
        sent.append((request.method, str(request.url), request.headers["host"]))
        peer = SimpleNamespace(get_extra_info=lambda name: ("93.184.216.34", 443))
        if request.headers["host"] == "seed.example":
            return httpx.Response(302, headers={"Location": "https://final.example/doc"},
                                  extensions={"network_stream": peer}, request=request)
        return httpx.Response(200, text="document", extensions={"network_stream": peer}, request=request)

    def client(**kw):
        pin_maps.append(kw["pin_hosts"])
        return httpx.AsyncClient(transport=httpx.MockTransport(network))

    monkeypatch.setattr(pc, "get_scraping_client", client)
    result = await fetch_public("https://seed.example/", max_bytes=1000)
    assert result.url == "https://final.example/doc"
    assert result.text == "document"
    assert [s[0] for s in sent] == ["GET", "GET"]
    assert pin_maps[0] == {"seed.example": "93.184.216.34", "final.example": "93.184.216.34"}


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["http://127.0.0.1/private", "https://outside.example/doc"])
async def test_redirect_policy_blocks_before_target_request(monkeypatch, target):
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf
    from kazma_core.web_acquire.http import fetch_public

    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    sent = []

    def network(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"Location": target}, request=request,
                              extensions={"network_stream": SimpleNamespace(
                                  get_extra_info=lambda name: ("93.184.216.34", 443))})

    monkeypatch.setattr(pc, "get_scraping_client", lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(network)))
    with pytest.raises((ssrf.SSRFError, ValueError)):
        await fetch_public("https://seed.example/", max_bytes=1000,
                           scope=lambda url: httpx.URL(url).host == "seed.example")
    assert sent == ["https://seed.example/"]


@pytest.fixture
def stored_crawl(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    store.create_library("audit", "Audit", seed_url="https://docs.example/tree/")
    index = KnowledgeIndex(store=store)
    monkeypatch.setattr(index, "_vector_store_for", lambda _: SimpleNamespace(available=False, delete=lambda *a, **k: None))
    pages = [f"https://docs.example/tree/{p}" for p in ("a", "b", "c")]
    for i, url in enumerate(pages):
        text = f"retained page {i}"
        index.index("audit", [{"id": f"audit:{i}", "library_id": "audit", "source_url": url,
                               "document_title": "Page", "section_header": "Section", "chunk_index": 0,
                               "content_hash": hashlib.sha256(text.encode()).hexdigest(), "has_code": False,
                               "char_count": len(text), "content": text}])
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", nullcontext)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(ki, "kb_discover_pages", AsyncMock(return_value=list(pages)))
    monkeypatch.setattr(ki, "_extract_page", AsyncMock(return_value=("new useful page " * 30, "ok", "")))
    yield store, pages
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["capped", "sparse", "failed", "complete_but_not_authoritative"])
async def test_refresh_never_equates_absent_discovery_with_deletion(stored_crawl, monkeypatch, scenario):
    store, pages = stored_crawl
    if scenario != "capped":
        monkeypatch.setattr(ki, "kb_discover_pages", AsyncMock(return_value=pages[:1]))
    if scenario == "failed":
        monkeypatch.setattr(ki, "_extract_page", AsyncMock(return_value=(None, "error", "rate_limited")))
    updates = [u async for u in ki.ingest_site("audit", "https://docs.example/tree/", max_pages=1 if scenario == "capped" else 20)]
    assert set(store.list_source_urls("audit")) == set(pages)
    # Re-pointed (Knowledge KB-S15): a crawl never prunes, so the always-zero
    # "pruned" counter was removed from the progress record and the page.
    assert not hasattr(updates[-1], "pruned_urls") and "pruned" not in updates[-1].message


@pytest.mark.asyncio
async def test_final_failure_keeps_all_url_reasons(stored_crawl, monkeypatch):
    _, pages = stored_crawl
    monkeypatch.setattr(ki, "_extract_page", AsyncMock(return_value=(None, "error", "quota_exhausted")))
    updates = [u async for u in ki.ingest_site("audit", "https://docs.example/tree/")]
    final = updates[-1]
    assert final.phase == "error"
    assert final.failed == len(pages)
    assert all(any(url in error and "quota_exhausted" in error for error in final.errors) for url in pages)


@pytest.mark.asyncio
async def test_cancel_joins_owned_discovery_task(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def discover(*a, **k):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    generator = ki.ingest_site("audit", "https://docs.example/tree/")
    await anext(generator)
    consumer = asyncio.create_task(anext(generator))
    await started.wait()
    consumer.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert stopped.is_set()
    finally:
        # Clean up the old implementation's leaked child in the negative run.
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task() and task.get_coro().__name__ == "discover":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await generator.aclose()


@pytest.mark.asyncio
async def test_recrawl_preserves_user_edit_and_reports_save_error(tmp_path, monkeypatch):
    from kazma_core.web_acquire.fetch import FetchResult

    url = "https://docs.example/tree/"
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=(None, url)))
    monkeypatch.setattr(wr, "_central_fetch", AsyncMock(return_value=FetchResult(ok=True, url=url, text="article")))
    monkeypatch.setattr(wr, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(wr, "_safe_workspace_path", lambda name, source: tmp_path / name)
    await wr.crawl_site(url, max_pages=1)
    target = next(tmp_path.rglob("*.md"))
    target.write_text("USER EDIT", encoding="utf-8")
    report = await wr.crawl_site(url, max_pages=1)
    assert target.read_text(encoding="utf-8") == "USER EDIT"
    assert "save_error" in report and "exist" in report.lower()


@pytest.mark.asyncio
async def test_crawl_surfaces_fetch_reason(monkeypatch):
    from kazma_core.web_acquire.fetch import FetchResult

    url = "https://docs.example/tree/"
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(return_value=(None, url)))
    monkeypatch.setattr(wr, "_central_fetch", AsyncMock(return_value=FetchResult(ok=False, url=url, error="HTTP 429: retry later")))
    result = await wr.crawl_site(url, max_pages=1, save=False)
    assert "HTTP 429: retry later" in result


def test_atomic_extract_publication_preserves_existing_file(tmp_path):
    from kazma_core.tools.research_files import create_extract

    target = tmp_path / "extracts" / "page.md"
    create_extract(target, "complete text")
    with pytest.raises(FileExistsError):
        create_extract(target, "replacement")
    assert target.read_text(encoding="utf-8") == "complete text"
    assert list(target.parent.iterdir()) == [target]


def test_failed_publication_leaves_no_partial_file(tmp_path, monkeypatch):
    from kazma_core.tools.research_files import create_extract

    monkeypatch.setattr("os.link", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        create_extract(tmp_path / "extracts" / "page.md", "complete text")
    assert list((tmp_path / "extracts").iterdir()) == []


@pytest.mark.asyncio
async def test_private_peer_is_refused_before_body_read():
    from kazma_core.security.ssrf import SSRFError
    from kazma_core.web_acquire.http import read_capped

    read = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            read.append(True)
            yield b"private information"

    def respond(request):
        peer = SimpleNamespace(get_extra_info=lambda _: ("169.254.169.254", 80))
        return httpx.Response(200, stream=Body(), request=request,
                              extensions={"network_stream": peer})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SSRFError, match="private"):
            await read_capped(client, "https://public.example/", 1000, require_peer=True)
    assert read == []


@pytest.mark.asyncio
@pytest.mark.parametrize("length,expected_truncated", [(1000, False), (1001, True), (100_000, True)])
async def test_streamed_cap_and_exact_boundary(length, expected_truncated):
    from kazma_core.web_acquire.http import read_capped

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for start in range(0, length, 127):
                yield b"x" * min(127, length - start)

        async def aclose(self):
            self.closed = True

    body = Body()
    def respond(request):
        return httpx.Response(200, stream=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response, text, truncated = await read_capped(client, "https://public.example/", 1000)
    assert len(response.content) == len(text) == min(length, 1000)
    assert truncated is expected_truncated
    assert body.closed


def test_gzip_expansion_is_bounded_across_members():
    import gzip
    from kazma_core.web_acquire.http import decompress_gzip

    assert decompress_gzip(gzip.compress(b"<xml/>"), 1000) == "<xml/>"
    bomb = gzip.compress(b"x" * 1000) + gzip.compress(b"y" * 1000)
    with pytest.raises(ValueError, match="decompressed byte limit"):
        decompress_gzip(bomb, 1500)


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
async def test_http_decompression_allocation_is_bounded(encoding):
    import gzip
    import tracemalloc
    import zlib
    from kazma_core.web_acquire.http import read_capped

    data = b"x" * 10_000_000
    if encoding == "gzip":
        wire = gzip.compress(data)
    else:
        compressor = zlib.compressobj(wbits=-15 if encoding == "raw-deflate" else 15)
        wire = compressor.compress(data) + compressor.flush()
    del data

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire[:1]  # raw-deflate detection must survive fragmentation
            yield wire[1:]

    def respond(request):
        return httpx.Response(200, stream=Body(), request=request,
                              headers={"Content-Encoding": encoding.replace("raw-", "")})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        tracemalloc.start()
        try:
            response, text, truncated = await read_capped(client, "https://example.com/", 1000)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert truncated and response.content == b"x" * 1000 and text == "x" * 1000
    assert peak < 2_000_000  # old HTTPX decode materialized the entire 10MB body


@pytest.mark.asyncio
@pytest.mark.parametrize("wire,encoding", [(b"garbage", "gzip"), (b"invalid", "br")])
async def test_invalid_or_unsupported_compression_is_visible(wire, encoding):
    from kazma_core.web_acquire.http import read_capped

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, stream=Body(), request=request,
                           headers={"Content-Encoding": encoding}))) as client:
        with pytest.raises(httpx.DecodingError):
            await read_capped(client, "https://example.com/", 1000)


def test_embedded_target_credentials_are_refused():
    from kazma_core.security.ssrf import SSRFError, validate_url

    with pytest.raises(SSRFError, match="embedded credentials"):
        validate_url("https://user:password@public.example/")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_real_httpx_network_failures_reach_local_recovery(monkeypatch, failure):
    ru.clear_url_cache()
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr("kazma_core.proxy.client.get_active_proxy_url", lambda: None)
    def fail(request):
        raise failure("connection interrupted", request=request)
    monkeypatch.setattr("kazma_core.proxy.client.get_scraping_client", lambda **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    recovery = AsyncMock(return_value="local recovered content")
    monkeypatch.setattr(ru, "_recover_hard_page", recovery)
    assert await ru.fetch_full_text("https://network.example/page") == "local recovered content"
    recovery.assert_awaited_once()
    ru.clear_url_cache()


@pytest.mark.asyncio
async def test_whole_ladder_deadline_cancels_unfinished_stage(monkeypatch):
    stopped = asyncio.Event()
    async def slow(url):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setenv("KAZMA_FETCH_DEADLINE_SECONDS", "1")
    monkeypatch.setattr(ru, "_fetch_full_text_impl", slow)
    result = await ru.fetch_full_text("https://slow.example/")
    assert result.startswith("Error: Acquisition deadline exceeded")
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_nested_sitemap_indexes_fetch_leaf_pages_once(monkeypatch):
    root = "https://docs.example"
    visited = []
    documents = {
        "/robots.txt": "Sitemap: https://docs.example/sitemap.xml",
        "/sitemap.xml": "<sitemapindex><sitemap><loc>/nested.xml</loc></sitemap></sitemapindex>",
        "/nested.xml": "<sitemapindex><sitemap><loc>/sitemap.xml</loc></sitemap><sitemap><loc>/leaf.xml</loc></sitemap></sitemapindex>",
        "/leaf.xml": "<urlset><url><loc>https://docs.example/article</loc></url></urlset>",
    }
    async def fetch(url, **kwargs):
        visited.append(url)
        return documents.get(url.removeprefix(root)), url
    monkeypatch.setattr(ki, "_http_get_text", fetch)
    result = await ki._fetch_sitemaps(root)
    assert result == [documents["/leaf.xml"]]
    assert visited.count(root + "/sitemap.xml") == 1
    assert visited.count(root + "/leaf.xml") == 1


@pytest.mark.asyncio
async def test_sitemap_frontier_has_a_finite_document_cap(monkeypatch):
    visited, messages = [], []
    async def fetch(url, **kwargs):
        visited.append(url)
        if url.endswith("/sitemap.xml"):
            return "<sitemapindex>" + "".join(f"<sitemap><loc>https://docs.example/{i}.xml</loc></sitemap>" for i in range(64)) + "</sitemapindex>", url
        if url.endswith("/robots.txt"):
            return None, url
        return "<urlset/>", url
    async def progress(message):
        messages.append(message)
    monkeypatch.setattr(ki, "_http_get_text", fetch)
    await ki._fetch_sitemaps("https://docs.example", on_progress=progress)
    assert len(visited) <= 65  # 64 documents plus robots
    assert any("partial sitemap discovery" in message for message in messages)


@pytest.mark.asyncio
async def test_oversized_sitemap_index_fetches_only_its_bounded_children(monkeypatch):
    """A 1,000-child index is cut to its first 64 children and discovery reads
    at most 64 sitemap documents. It used to refuse the whole crawl (an
    oversized sitemap failed every crawl of rfc-editor.org, 2026-10-31)."""
    visited, progress = [], []
    index = '<sitemapindex>' + ''.join(
        f'<sitemap><loc>https://docs.example/{i}.xml</loc></sitemap>' for i in range(1000)) + '</sitemapindex>'
    async def fetch(url, **kwargs):
        visited.append(url)
        return (index if url == 'https://docs.example/sitemap.xml' else None), url
    async def note(message):
        progress.append(message)
    monkeypatch.setattr(ki, '_http_get_text', fetch)
    await ki._fetch_sitemaps('https://docs.example', on_progress=note)
    sitemaps = [url for url in visited if not url.endswith('/robots.txt')]
    assert len(sitemaps) <= 64
    children = {f'https://docs.example/{i}.xml' for i in range(64)}
    assert all(url in children or url.startswith('https://docs.example/sitemap') or url.startswith('https://docs.example/docs/')
               for url in sitemaps)
    assert any('partial sitemap discovery' in message for message in progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_decoded", [False, True])
async def test_gzip_sitemap_handles_raw_file_and_decoded_content_encoding(monkeypatch, http_decoded):
    import gzip
    from kazma_core.web_acquire.http import HttpFetch

    xml = "<urlset><url><loc>https://docs.example/article</loc></url></urlset>"
    data = xml.encode() if http_decoded else gzip.compress(xml.encode())
    response = httpx.Response(200, content=data)
    fetch = AsyncMock(return_value=HttpFetch(response, xml if http_decoded else "binary", "https://docs.example/sitemap.xml.gz", False))
    monkeypatch.setattr("kazma_core.web_acquire.http.fetch_public", fetch)
    text, _ = await ki._http_get_text("https://docs.example/sitemap.xml.gz")
    assert text == xml


@pytest.mark.asyncio
async def test_scopes_own_browser_resources(monkeypatch):
    from kazma_core.playwright_loop import run_in_browser_loop

    first, second = ki._pw_browser_scope(), ki._pw_browser_scope()
    contexts = [AsyncMock(), AsyncMock()]
    first.context, second.context = contexts
    await first.__aenter__()
    try:
        assert ki._pw_scope.get() is first
        await second.__aenter__()
        try:
            assert ki._pw_scope.get() is second
            await second.__aexit__(None, None, None)
            contexts[1].close.assert_awaited_once()
            contexts[0].close.assert_not_awaited()
            assert ki._pw_scope.get() is first
            # The dedicated loop carries the caller's task-local owner.
            async def owner():
                return ki._pw_scope.get()
            assert await run_in_browser_loop(owner()) is first
        finally:
            if second._token is not None:
                await second.__aexit__(None, None, None)
    finally:
        await first.__aexit__(None, None, None)
    contexts[0].close.assert_awaited_once()
    assert ki._pw_scope.get() is None


@pytest.mark.asyncio
async def test_cancelling_cross_loop_browser_work_stops_coroutine(monkeypatch):
    import threading
    import kazma_core.playwright_loop as bridge

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(bridge, "_IS_WINDOWS", True)
    monkeypatch.setattr(bridge, "_get_loop", lambda: loop)
    started, stopped = threading.Event(), threading.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(bridge.run_in_browser_loop(work(), timeout=5))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join, 2)
        loop.close()




@pytest.mark.asyncio
async def test_partial_browser_launch_is_cleaned_up(monkeypatch):
    scope = ki._pw_browser_scope()
    driver, browser = AsyncMock(), AsyncMock()

    async def launch():
        scope.pw, scope.browser = driver, browser
        raise RuntimeError("context creation failed")

    monkeypatch.setattr(scope, "_launch_impl", launch)
    with pytest.raises(RuntimeError, match="context creation failed"):
        await scope._start_impl()
    browser.close.assert_awaited_once()
    driver.stop.assert_awaited_once()
    assert scope.pw is scope.browser is scope.context is None


@pytest.mark.asyncio
async def test_same_job_concurrent_browser_launches_have_one_owner(monkeypatch):
    scope = ki._pw_browser_scope()
    launches = []
    context = AsyncMock()
    async def launch():
        launches.append(True)
        await asyncio.sleep(0)
        scope.context = context
    monkeypatch.setattr(scope, "_launch_impl", launch)
    await asyncio.gather(scope._start_impl(), scope._start_impl())
    assert launches == [True]
    await scope._close_impl()
    context.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match="already closed"):
        await scope._start_impl()


@pytest.mark.asyncio
async def test_real_chromium_job_contexts_keep_cookies_and_teardown_separate():
    pytest.importorskip("playwright.async_api")
    from kazma_core.playwright_loop import run_in_browser_loop

    async with ki._pw_browser_scope() as first:
        try:
            await run_in_browser_loop(first._start_impl())
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed")
            raise
        async def add_cookie():
            assert "--no-sandbox" not in await _browser_command(first.browser)
            await first.context.add_cookies([{"name": "job", "value": "first", "url": "https://scope.example/"}])
        await run_in_browser_loop(add_cookie())
        async with ki._pw_browser_scope() as second:
            await run_in_browser_loop(second._start_impl())
            async def check_isolation():
                assert await second.context.cookies() == []
                assert (await first.context.cookies())[0]["value"] == "first"
            await run_in_browser_loop(check_isolation())
        async def first_is_still_usable():
            page = await first.context.new_page()
            await page.close()
        await run_in_browser_loop(first_is_still_usable())


@pytest.mark.asyncio
async def test_real_standalone_scrapers_launch_with_sandbox_enabled(monkeypatch):
    api = pytest.importorskip("playwright.async_api")
    from kazma_core.playwright_loop import run_in_browser_loop
    from urllib.parse import quote

    launch = api.BrowserType.launch
    launched = []
    async def checked_launch(self, *args, **kwargs):
        try:
            browser = await launch(self, *args, **kwargs)
        except api.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed")
            raise
        try:
            launched.append(await _browser_command(browser))
        except BaseException:
            await browser.close()
            raise
        return browser
    monkeypatch.setattr(api.BrowserType, "launch", checked_launch)
    body = "Local sandbox qualification article. " * 30
    target = "data:text/html," + quote("<html><body><article><p>" + body + "</p></article></body></html>")
    assert "qualification article" in (await run_in_browser_loop(ru._fetch_with_playwright_impl(target)) or "")
    assert "qualification article" in (await run_in_browser_loop(ki._render_with_playwright_impl(target, want_text=True)) or "")
    assert len(launched) == 2
    assert all("--no-sandbox" not in arguments for arguments in launched)
