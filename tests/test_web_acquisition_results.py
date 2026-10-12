"""Typed acquisition evidence, without live target or paid provider requests."""

from __future__ import annotations

import gzip
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kazma_core.web_acquire import FetchErrorCode, FetchResult, fetch_text

ru = importlib.import_module("kazma_core.tools.read_url")
wr = importlib.import_module("kazma_core.tools.web_research")
ki = importlib.import_module("kazma_core.stores.knowledge_ingest")


class Body(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


@pytest.fixture
def target(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf

    ru.clear_url_cache()
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    monkeypatch.delenv("KAZMA_FETCH_BACKEND", raising=False)
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    monkeypatch.setattr(ru, "_recover_hard_page", AsyncMock(return_value=None))
    requests = []
    original_client = httpx.AsyncClient

    def install(handler):
        def respond(request):
            requests.append(str(request.url))
            result = handler(request)
            result.extensions["network_stream"] = SimpleNamespace(
                get_extra_info=lambda name: ("93.184.216.34", 443))
            return result
        monkeypatch.setattr(pc, "get_scraping_client", lambda **kwargs:
                            original_client(transport=httpx.MockTransport(respond)))
        return requests
    yield install
    ru.clear_url_cache()


@pytest.mark.asyncio
async def test_literal_error_prefix_is_content_in_facade_and_chat(target):
    body = "Error: is a literal example in this reference page. Preserve these words."
    calls = target(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/plain"}))
    result = await fetch_text("https://first.example/reference")
    assert result.ok and not result.is_error_stub and result.text == body
    assert result.error is None and result.error_code is None
    assert result.backend == "httpx" and result.final_url == "https://first.example/reference"
    chat = await ru.read_url("https://first.example/reference")
    assert body in chat and "UNTRUSTED" in chat.upper()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_redirect_metadata_and_cache_keep_provenance_without_new_attempts(target):
    def serve(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/actual/page"})
        return httpx.Response(200, text='<html><body><p>Useful reference content.</p><a href="child?a=1&amp;b=2#part">Child</a></body></html>',
                              headers={"content-type": "text/html"})
    calls = target(serve)
    first = await fetch_text("https://first.example/start", purpose="research")
    assert first.ok and first.final_url == "https://first.example/actual/page"
    assert first.links == ("https://first.example/actual/child?a=1&b=2",)
    assert first.links_complete is True
    assert [a.http_status for a in first.attempts] == [302, 200]
    assert [a.outcome for a in first.attempts] == ["redirect", "response"]
    cached = await fetch_text("https://first.example/start", purpose="crawl")
    assert cached.cache_hit and not cached.attempts and cached.backend == "httpx"
    assert cached.final_url == first.final_url and cached.links == first.links
    assert cached.purpose == "crawl" and len(calls) == 2
    knowledge = await fetch_text("https://first.example/start", purpose="kb")
    assert knowledge.ok and not knowledge.cache_hit and len(calls) == 4
    assert knowledge.meta["profile"] == "knowledge"


@pytest.mark.asyncio
async def test_compressed_attempt_reports_observed_wire_and_decoded_bytes(target):
    raw = b"Readable article content with enough detail for a complete fixture."
    wire = gzip.compress(raw)
    target(lambda r: httpx.Response(200, stream=Body(wire), headers={
        "content-type": "text/plain", "content-encoding": "gzip"}))
    result = await fetch_text("https://first.example/gzip")
    assert result.ok and result.text == raw.decode()
    assert result.wire_bytes == len(wire) and result.decoded_bytes == len(raw)
    assert result.attempts[0].wire_bytes == len(wire)
    assert result.attempts[0].decoded_bytes == len(raw)


@pytest.mark.asyncio
async def test_partial_html_is_visible_and_never_indexed(target, monkeypatch):
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 100)
    calls = target(lambda r: httpx.Response(200, stream=Body(b"<html><body><p>" + b"Article " * 100),
                                          headers={"content-type": "text/html"}))
    result = await fetch_text("https://first.example/large")
    assert result.ok and result.truncated and result.quality == "partial"
    assert result.truncation_reasons == ("body_limit",)
    assert result.links_complete is False
    assert result.decoded_bytes == 100
    assert "partial: body_limit" in await ru.read_url("https://first.example/large")
    monkeypatch.setattr(ki, "_web_fetch_text", AsyncMock(return_value=result))
    render = AsyncMock(side_effect=AssertionError("must not silently re-fetch a budget-limited page"))
    monkeypatch.setattr(ki, "_render_with_playwright", render)
    text, status, reason = await ki._extract_page("https://first.example/large")
    assert text is None and status == "error" and "partial content was not indexed" in reason
    assert len(calls) == 2  # partial responses are not cached
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_json_is_refused_as_incomplete_data(target, monkeypatch):
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 12)
    target(lambda r: httpx.Response(200, stream=Body(b'{"message":"long JSON content"}'),
                                   headers={"content-type": "application/json"}))
    result = await fetch_text("https://first.example/api/status")
    assert not result.ok and result.error_code == FetchErrorCode.LIMIT
    assert result.truncated and not result.text


@pytest.mark.asyncio
async def test_json_http_error_status_is_explicit_without_modifying_json(target):
    body = '{"error":"not registered"}'
    target(lambda r: httpx.Response(404, text=body, headers={"content-type": "application/json"}))
    result = await fetch_text("https://first.example/api/domain")
    assert result.ok and result.http_status == 404 and result.quality == "json"
    assert result.text == body and result.attempts[0].http_status == 404
    assert "HTTP 404" in await ru.fetch_full_text("https://first.example/api/domain")


@pytest.mark.asyncio
async def test_challenge_with_http_200_cannot_be_successful_content(target):
    target(lambda r: httpx.Response(200, text="<html><title>Checking your browser</title><body>cf-browser-verification</body></html>",
                                   headers={"content-type": "text/html"}))
    result = await fetch_text("https://first.example/challenge")
    assert not result.ok and result.error_code == FetchErrorCode.CHALLENGE and not result.text


@pytest.mark.asyncio
async def test_crawl_uses_one_document_for_text_and_links(target, monkeypatch):
    def serve(request):
        child = '<a href="/child">Child</a>' if request.url.path == "/" else ""
        return httpx.Response(200, text=f"<html><body><p>Reference content for this document.</p>{child}</body></html>",
                              headers={"content-type": "text/html"})
    calls = target(serve)
    monkeypatch.setattr(wr, "_fetch_html", AsyncMock(side_effect=AssertionError("duplicate raw fetch")))
    index = await wr.crawl_site("https://first.example/", max_pages=2, max_depth=1,
                               delay_ms=0, save=False, respect_robots=False)
    assert "Pages fetched: 2 (ok=2)" in index
    assert calls == ["https://first.example/", "https://first.example/child"]


def test_document_link_budget_and_query_identity():
    from kazma_core.web_acquire.links import extract_document_links

    links, complete = extract_document_links(
        '<base href="/docs/"><a href="x?q=1#one">A</a><a href="x?q=2">B</a><a href="last">C</a>',
        "https://first.example/start", limit=2)
    assert links == ("https://first.example/docs/x?q=1", "https://first.example/docs/x?q=2")
    assert not complete
    links, complete = extract_document_links(
        '<base href="http://["><a href="safe" href="https://second.example/">Child</a>',
        "https://first.example/docs/")
    assert links == ("https://first.example/docs/safe",) and complete


@pytest.mark.asyncio
async def test_permanent_scope_failure_cannot_use_kb_secondary_renderer(monkeypatch):
    result = FetchResult(ok=False, url="https://first.example/", error="outside crawl scope",
                         error_code=FetchErrorCode.SCOPE)
    monkeypatch.setattr(ki, "_web_fetch_text", AsyncMock(return_value=result))
    render = AsyncMock(side_effect=AssertionError("scope bypass"))
    monkeypatch.setattr(ki, "_render_with_playwright", render)
    assert await ki._extract_page(result.url) == (None, "error", "outside crawl scope")
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_hosted_retry_attempts_keep_status_and_unknown_accounting(monkeypatch):
    from kazma_core.security import ssrf

    ru.clear_url_cache()
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "firecrawl")
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(ru, "_firecrawl_in_cooldown", lambda: False)
    original_client = httpx.AsyncClient
    sent = []
    def service(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"data": {"markdown": "Hosted fixture content. " * 8}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(service)))
    async def hosted(url):
        text = await ru._try_firecrawl(url)
        return ru._succeeded(text, recovery=True)
    monkeypatch.setattr(ru, "_fetch_full_text_impl", hosted)
    try:
        result = await fetch_text("https://first.example/service-fixture")
        assert result.ok and result.backend == "firecrawl" and result.quality == "unverified"
        assert [a.http_status for a in result.attempts] == [429, 200]
        assert [a.outcome for a in result.attempts] == ["failed", "success"]
        assert result.attempts[0].transient and all(a.remote for a in result.attempts)
        assert result.final_url is None and result.http_status is None
        assert result.usage is None and result.credits is None
        assert "third-party attempts: firecrawl" in result.notice()
        assert "usage/credits unavailable" in result.notice()
        assert len(sent) == 2
    finally:
        ru.clear_url_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", [False, True])
async def test_actual_browser_metadata_survives_windows_loop_bridge(monkeypatch, login):
    from pathlib import Path
    from urllib.parse import quote
    from kazma_core.proxy import client as pc

    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("optional Chromium executable is not installed")
    title = "<head><title>Sign in | Fixture</title></head>" if login else ""
    form = '<form><input type="password"></form>' if login else ""
    page = "data:text/html," + quote('<html>' + title + '<body><p>' + "Browser fixture article. " * 10 +
                                    '</p><a href="https://first.example/child">Child</a>' + form + '</body></html>')
    async def render(url):
        text = await ru._recover_hard_page(page)
        return ru._succeeded(text, recovery=True)
    monkeypatch.setattr(ru, "_fetch_full_text_impl", render)
    monkeypatch.setattr(pc, "playwright_proxy", lambda: None)
    ru.clear_url_cache()
    try:
        result = await fetch_text("https://first.example/browser-fixture")
        assert result.backend == "chromium" and result.final_url == page
        if login:
            assert not result.ok and result.error_code == FetchErrorCode.LOGIN
            assert result.attempts[0].error_code == FetchErrorCode.LOGIN and not result.text
            return
        assert result.ok
        assert result.links == ("https://first.example/child",) and result.links_complete
        assert len(result.attempts) == 1 and result.attempts[0].backend == "chromium"
        assert result.attempts[0].final_url == page
    finally:
        ru.clear_url_cache()


@pytest.mark.asyncio
async def test_typed_save_preserves_content_and_reports_existing_file(target, monkeypatch, tmp_path):
    body = "Error: is example content with sufficient words for this reference document."
    target(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/plain"}))
    monkeypatch.setattr(ru, "_workspace_root", lambda: tmp_path)
    saved = await ru.save_url_extract("https://first.example/save", path="notes/article.md")
    assert saved.ok and saved.acquisition.ok
    assert saved.saved_bytes == (tmp_path / "notes/article.md").stat().st_size
    loaded, path = ru._load_research_body(saved.path)
    assert loaded == body and path.is_file()
    assert "# Backend:" not in loaded
    again = await ru.save_url_extract("https://first.example/save", path="notes/article.md")
    assert not again.ok and again.error_code == FetchErrorCode.EXISTS
    assert (tmp_path / "notes/article.md").read_text(encoding="utf-8").endswith(body)


@pytest.mark.asyncio
async def test_automatic_save_refuses_partial_source_before_creating_directories(target, monkeypatch, tmp_path):
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 40)
    target(lambda r: httpx.Response(200, stream=Body(b"article " * 40), headers={"content-type": "text/plain"}))
    monkeypatch.setattr(ru, "_workspace_root", lambda: tmp_path)
    saved = await ru.save_url_extract("https://first.example/partial", path="notes/article.md")
    assert not saved.ok and saved.error_code == FetchErrorCode.LIMIT
    assert saved.acquisition.truncated and not (tmp_path / "notes").exists()


@pytest.mark.asyncio
async def test_failed_chat_and_save_keep_third_party_failure_receipt(monkeypatch):
    from kazma_core.security import ssrf

    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "firecrawl")
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(ru, "_firecrawl_in_cooldown", lambda: False)
    original_client = httpx.AsyncClient
    sent = []

    def service(request):
        sent.append(request)
        return httpx.Response(402, json={"error": "insufficient credits"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(service)))

    async def failed(url):
        assert await ru._try_firecrawl(url) is None
        return ru._failed(FetchErrorCode.EMPTY, "No backend produced readable content.")

    monkeypatch.setattr(ru, "_fetch_full_text_impl", failed)
    from kazma_core.web_acquire.robots import crawl_policy

    # This receipt contract deliberately permits hosted navigation; automated
    # saves otherwise enforce local robots checks and refuse hosted fallback.
    with crawl_policy(respect=False):
        outputs = (await ru.read_url("https://first.example/failed"),
                   await ru.read_url_to_file("https://first.example/failed"))
    for output in outputs:
        assert output.startswith("Error:")
        assert "firecrawl failed (HTTP 402)" in output
        assert "usage/credits unavailable" in output
        assert "test-not-a-real-key" not in output
    assert len(sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["jina", "firecrawl"])
@pytest.mark.parametrize("status", [200, 429])
async def test_hosted_compression_expansion_is_bounded_and_never_accepted(monkeypatch, provider, status):
    from kazma_core.security import ssrf
    from kazma_core.web_acquire.trace import active_trace, FetchTrace

    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", provider)
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("KAZMA_JINA_READER", raising=False)
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(ru, "_firecrawl_in_cooldown", lambda: False)
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 1000)
    stream = Body(gzip.compress(b"document " * 1_000_000))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, stream=stream,
            headers={"content-encoding": "gzip"}))))
    trace = FetchTrace("https://first.example/large")
    token = active_trace.set(trace)
    try:
        helper = ru._try_jina_reader if provider == "jina" else ru._try_firecrawl
        assert await helper(trace.url) is None
    finally:
        active_trace.reset(token)
    assert stream.closed and trace.recovered_source is None
    attempt, = trace.attempts
    assert attempt.remote and attempt.http_status == status and not attempt.transient
    assert attempt.error_code == FetchErrorCode.LIMIT and attempt.truncated
    assert attempt.decoded_bytes == 1000


@pytest.mark.asyncio
async def test_body_read_failure_retains_observed_status(target):
    class BrokenBody(Body):
        async def __aiter__(self):
            yield b"some content"
            raise httpx.ReadError("mid-stream drop")

    stream = BrokenBody(b"")
    target(lambda r: httpx.Response(200, stream=stream, headers={"content-type": "text/plain"}))
    result = await fetch_text("https://first.example/dropped")
    assert not result.ok and result.error_code == FetchErrorCode.READ and result.transient
    assert result.http_status == 200 and result.final_url == "https://first.example/dropped"
    attempt, = result.attempts
    assert attempt.http_status == 200 and attempt.error_code == FetchErrorCode.READ
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Sign in | Fixture", "تسجيل الدخول — مثال"])
async def test_login_screen_is_refused_without_browser_or_hosted_recovery(target, title):
    target(lambda r: httpx.Response(200, text=f'<html><title>{title}</title><body>'
                                   '<form><input type="password">Enter your credentials to continue.</form></body></html>',
                                   headers={"content-type": "text/html"}))
    result = await fetch_text("https://first.example/login")
    assert not result.ok and result.error_code == FetchErrorCode.LOGIN and not result.text
    assert len(result.attempts) == 1 and result.attempts[0].http_status == 200
    ru._recover_hard_page.assert_not_awaited()


def test_login_signal_requires_real_password_input_and_explicit_page_title():
    from kazma_core.web_acquire.quality import is_login_document

    assert not is_login_document('<title>Article about sign in</title><input type="password">')
    assert not is_login_document('<title>Sign in</title><code>&lt;input type="password"&gt;</code>')
    assert not is_login_document('<title>Sign in</title><input type>')
    assert not is_login_document('<title>Article</title><a href="/login">Sign in</a>')
