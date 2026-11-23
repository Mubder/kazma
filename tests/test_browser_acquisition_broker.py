"""Actual Chromium plus controlled HTTP fixtures; no external network targets."""

from __future__ import annotations

import asyncio
import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ru = importlib.import_module("kazma_core.tools.read_url")


@pytest.fixture
def origin():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/escape")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><p>Out of scope content must never be fetched.</p></body></html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


#: Whether this machine starts the bounded driver and has its Chromium: asked
#: once per process. Every test used to start a driver of its own under ten
#: seconds; on a loaded full run that start alone could take longer, and the
#: whole file errored. Room for a loaded machine, and one start instead of 22.
_READINESS: dict[str, object] = {}
_READY_BOUND_S = 45
#: Every render here is bounded under pytest's own 120 s (fast_test's
#: ``--timeout=120``, thread method: it ends the whole chunk process). A render
#: that stalls then fails its test, and its cancel ends the browser's process
#: tree at once (the driver's watchdog stops a cancelled render's driver):
#: the chunk goes on. The browser loop's own default is 180 s.
_RENDER_BOUND_S = 90


async def _driver_ready() -> bool:
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser_driver import BrowserDriver

    async def ready():
        driver = BrowserDriver()
        try:
            async with asyncio.timeout(_READY_BOUND_S):
                runtime = await driver.start()
                return Path(runtime.chromium.executable_path).is_file()
        finally:
            await driver.stop()
    return await run_in_browser_loop(ready(), timeout=_READY_BOUND_S + 15)


@pytest.fixture
async def chromium():
    pytest.importorskip("playwright.async_api")
    if "answer" not in _READINESS:
        try:
            _READINESS["answer"] = await _driver_ready()
        except Exception as exc:  # noqa: BLE001 -- kept and raised for every test, as each start raised
            _READINESS["answer"] = exc
    answer = _READINESS["answer"]
    if isinstance(answer, Exception):
        raise answer
    if not answer:
        pytest.skip("Chromium executable is not installed")


@pytest.mark.asyncio
async def test_actual_http_redirect_is_blocked_before_browser_contacts_destination(monkeypatch, origin, chromium):
    from kazma_core.security import ssrf
    from kazma_core.proxy import client as pc
    from kazma_core.web_acquire import FetchResult, fetch_text

    base, native_hits = origin
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    # Real HTTPX broker with controlled peer evidence; native HTTP stays denied.
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    monkeypatch.setattr(pc, "playwright_proxy", lambda: None)
    broker_hits = []
    original_client = httpx.AsyncClient

    def response(request):
        broker_hits.append(request.url.path)
        result = (httpx.Response(302, headers={"location": "/escape"}) if request.url.path == "/start"
                  else httpx.Response(200, text="Unexpected destination"))
        result.extensions["network_stream"] = SimpleNamespace(
            get_extra_info=lambda name: ("93.184.216.34", 80))
        return result

    monkeypatch.setattr(pc, "get_scraping_client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(response)))

    async def render(url):
        text = await ru._fetch_with_playwright(url)
        return FetchResult(ok=bool(text), url=url, text=text or "")

    monkeypatch.setattr(ru, "_fetch_full_text_impl", render)
    result = await fetch_text(base + "/start", scope_check=lambda url: url == base + "/start")
    assert not result.ok and result.error_code == "scope_blocked"
    assert "/escape" not in native_hits + broker_hits
    assert native_hits == [], "Chromium must not open an unbrokered native HTTP connection"


@pytest.fixture
def broker_transport(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf

    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    calls = []
    original = httpx.AsyncClient
    def install(handler):
        async def respond(request):
            calls.append(request)
            response = handler(request)
            if hasattr(response, "__await__"):
                response = await response
            response.extensions["network_stream"] = SimpleNamespace(
                get_extra_info=lambda name: ("93.184.216.34", 443))
            return response
        monkeypatch.setattr(pc, "get_scraping_client", lambda **kwargs:
                            original(transport=httpx.MockTransport(respond)))
        return calls
    return install


async def render_fixture(url, *, scope=None, profile="generic"):
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser import render_document
    from kazma_core.web_acquire.scope import AcquisitionScope, active_scope

    token = active_scope.set(AcquisitionScope(scope)) if scope else None
    try:
        return await run_in_browser_loop(render_document(
            url,
            challenge=lambda html, status: False, profile=profile), timeout=_RENDER_BOUND_S)
    finally:
        if token is not None:
            active_scope.reset(token)


class _OwnedStream(httpx.AsyncByteStream):
    """A response body that never ends, whose close waits to be released:
    the render can only finish by being cancelled, and its cancellation only
    after the test lets the close end. Released from any thread, also before
    the body was ever read.

    The close runs to its end whatever cancels reach it, as a socket's close
    does. The broker re-delivers its cancel every half second
    (``bounded_cancel._REDELIVER_S``) for up to five; a close that a
    re-delivered cancel ended let the render finish before the test, on a
    loaded machine more than half a second behind, had seen the close begin.
    """

    def __init__(self):
        self.started, self.closing, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.release = threading.Event()

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield b'unreachable'

    async def aclose(self):
        self.closing.set()
        while not self.release.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue  # the close is not over: it ends when released
        self.closed.set()


#: How long the render may take to reach the brokered response: room for a
#: loaded machine (Chromium's start and navigation), inside pytest's 120 s.
_STREAM_START_S = 60


async def _cancel_owned_stream(task, stream, pool, *, start_s, end_render):
    """Cancel a render owning a never-ending response, as the test checks it.

    ``end_render`` is the test's cleanup: whatever failed, the render task is
    cancelled and the body released, so the test ends at once. The first
    version released the body only when it had been read and never cancelled
    the render: when the brokered response did not start in its ten seconds
    (a loaded machine), the failed assertion went on to wait for the render,
    which then sat in the body until its navigation's 30-second deadline and
    the broker's 5-second cancel bound -- on a loaded machine, each stretched.
    """
    try:
        assert await asyncio.to_thread(stream.started.wait, start_s), 'Brokered HTTP stream did not start'
        task.cancel()
        assert await asyncio.to_thread(stream.closing.wait, 30), 'Cancelled response was not closed'
        assert not task.done(), 'Cancellation returned while the HTTP stream was still owned'
        assert pool.snapshot()['active'] == 1
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), 'Repeated cancellation interrupted teardown'
    finally:
        if end_render:
            task.cancel()
            stream.release.set()
        elif stream.started.is_set():
            stream.release.set()
        result = await asyncio.gather(task, return_exceptions=True)
    return result


def _owned_cancel_fixture(broker_transport, monkeypatch, *, launch_delay=0.0):
    from kazma_core.web_acquire import resources
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('real fixture browsers', 1, 1)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owners = []
    original_launch = BrowserJob._launch_impl
    async def launch(owner):
        owners.append(owner)
        await asyncio.sleep(launch_delay)  # a loaded machine's slow start
        await original_launch(owner)
    monkeypatch.setattr(BrowserJob, '_launch_impl', launch)
    stream = _OwnedStream()
    broker_transport(lambda request: httpx.Response(
        200, headers={'content-type': 'text/html'}, stream=stream))
    return pool, owners, stream


def _assert_owner_cleaned(pool, owners):
    assert len(owners) == 1
    owner = owners[0]
    assert owner.context is owner.browser is owner.pw is owner._deny_server is None
    assert owner._capacity_lease is None
    assert pool.snapshot()['active'] == pool.snapshot()['waiting'] == 0


@pytest.mark.asyncio
async def test_real_chromium_cancel_joins_http_stream_and_owned_teardown(chromium, broker_transport, monkeypatch):
    pool, owners, stream = _owned_cancel_fixture(broker_transport, monkeypatch)
    task = asyncio.create_task(render_fixture('https://docs.example/owned-cancel'))
    result = await _cancel_owned_stream(task, stream, pool, start_s=_STREAM_START_S, end_render=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert stream.closed.is_set()
    _assert_owner_cleaned(pool, owners)


@pytest.mark.asyncio
async def test_a_response_that_never_starts_still_ends_the_test(chromium, broker_transport, monkeypatch):
    """The start bound runs out (the render is held back past it): the test
    fails its precondition and ends, the browser owner cleaned up."""
    pool, owners, stream = _owned_cancel_fixture(broker_transport, monkeypatch, launch_delay=3)
    task = asyncio.create_task(render_fixture('https://docs.example/owned-cancel'))
    async with asyncio.timeout(60):
        with pytest.raises(AssertionError, match='did not start'):
            await _cancel_owned_stream(task, stream, pool, start_s=0.5, end_render=True)
    assert task.done()
    _assert_owner_cleaned(pool, owners)


@pytest.mark.asyncio
async def test_control_the_first_cleanup_waits_on_the_render_in_the_body(chromium, broker_transport, monkeypatch):
    """The first version's cleanup on the same slow start: the precondition
    fails, and the cleanup goes on waiting for the render, which has reached
    the body and nothing ends (its navigation's own deadline would, 30 s
    later); the test then ends that render itself."""
    pool, owners, stream = _owned_cancel_fixture(broker_transport, monkeypatch, launch_delay=3)
    task = asyncio.create_task(render_fixture('https://docs.example/owned-cancel'))
    cleanup = asyncio.ensure_future(_cancel_owned_stream(task, stream, pool, start_s=0.5, end_render=False))
    try:
        assert await asyncio.to_thread(stream.started.wait, _STREAM_START_S), 'the render never reached the body'
        done, _ = await asyncio.wait({cleanup}, timeout=2)
        assert not done and not task.done(), 'the first cleanup waits on a render held in the body'
    finally:
        task.cancel()
        stream.release.set()
        await asyncio.gather(cleanup, return_exceptions=True)
    _assert_owner_cleaned(pool, owners)


@pytest.mark.asyncio
async def test_real_browser_admission_caps_independent_jobs_before_driver_launch(chromium, monkeypatch):
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire import resources
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('real fixture browsers', 2, 1)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    launched = []
    original = BrowserJob._launch_impl
    async def launch(owner):
        launched.append(owner)
        await original(owner)
    monkeypatch.setattr(BrowserJob, '_launch_impl', launch)

    async def verify():
        first, second, third = BrowserJob(), BrowserJob(), BrowserJob()
        waiting = None
        try:
            await first._start_impl()
            await second._start_impl()
            waiting = asyncio.create_task(third._start_impl())
            async with asyncio.timeout(5):
                while pool.snapshot()['waiting'] != 1:
                    await asyncio.sleep(0)
            assert launched == [first, second], 'Queued job launched a driver before admission'
            assert first.context is not second.context
            await first.context.add_cookies([{'name': 'private', 'value': 'first', 'url': 'https://docs.example'}])
            assert not await second.context.cookies(), 'Browser owners must not share cookies'
            await first._close_impl()
            await waiting
            assert launched == [first, second, third]
            assert pool.snapshot()['active'] == 2 and pool.snapshot()['waiting'] == 0
        finally:
            if waiting is not None and not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            for owner in (first, second, third):
                await owner._close_impl()
        assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0

    await run_in_browser_loop(verify(), timeout=_RENDER_BOUND_S)


@pytest.mark.asyncio
async def test_real_chromium_proxy_refusal_precedes_http_dispatch_and_cleans_owner(chromium, monkeypatch):
    from unittest.mock import Mock
    from kazma_core.web_acquire import resources
    from kazma_core.web_acquire.browser import BrowserJob

    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy')
    config = {'provider': 'anyip', 'username': 'fixture-account', 'password': ''}
    monkeypatch.setattr('kazma_core.config_store.get_config_store', lambda:
                        SimpleNamespace(get=lambda key, default=None: dict(config) if key == 'proxy' else default))
    construct = Mock(side_effect=AssertionError('Route failure cannot create an HTTP client'))
    monkeypatch.setattr('httpx.AsyncClient', construct)
    pool = resources._CapacityPool('fixture proxy browser', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owners = []
    original = BrowserJob._launch_impl
    async def launch(owner):
        owners.append(owner)
        await original(owner)
    monkeypatch.setattr(BrowserJob, '_launch_impl', launch)
    result = (await render_fixture('https://docs.example/refuse-proxy')).result
    assert not result.ok and result.error_code == 'proxy_routing_error'
    construct.assert_not_called()
    assert len(owners) == 1 and owners[0].browser is owners[0].pw is owners[0]._deny_server is None
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_real_chromium_honors_another_jobs_origin_cooldown_and_cleans_owner(chromium, broker_transport, monkeypatch):
    from kazma_core.web_acquire.origins import origin_scheduler
    from kazma_core.web_acquire.browser import BrowserJob

    url = 'https://docs.example/cooled-navigation'
    origin_scheduler().observe(url, 429, {'retry-after': '120'})
    calls = broker_transport(lambda request: pytest.fail('Chromium bypassed another job\'s origin cooldown'))
    owners = []
    original = BrowserJob._launch_impl
    async def launch(owner):
        owners.append(owner)
        await original(owner)
    monkeypatch.setattr(BrowserJob, '_launch_impl', launch)
    result = (await render_fixture(url)).result
    assert not result.ok and result.error_code == 'origin_throttled', result.evidence()
    assert not calls and origin_scheduler().snapshot()['active'] == 0
    assert len(owners) == 1
    assert owners[0].context is owners[0].browser is owners[0].pw is owners[0]._deny_server is None
    assert owners[0]._capacity_lease is None


@pytest.mark.asyncio
async def test_redirect_retains_final_url_relative_assets_and_repeated_cookies(chromium, broker_transport):
    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers=[("location", "/docs/final"),
                                               ("set-cookie", "a=one; Path=/"),
                                               ("set-cookie", "b=two; Path=/")])
        if request.url.path == "/docs/app.js":
            return httpx.Response(200, headers={"content-type": "application/javascript"},
                                  text="document.getElementById('content').textContent='Rendered relative asset: '+document.cookie;")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p id='content'>loading</p><script src='app.js'></script>")
    calls = broker_transport(respond)
    document = await render_fixture("https://docs.example/start", scope=lambda url: url.startswith("https://docs.example/"))
    result = document.result
    assert result.ok, result.error
    assert result.final_url == "https://docs.example/docs/final" and result.http_status == 200
    assert "Rendered relative asset:" in result.text and "a=one" in result.text and "b=two" in result.text
    assert [r.url.path for r in calls][:3] == ["/start", "/docs/final", "/docs/app.js"]
    assert "a=one" in calls[2].headers.get("cookie", "") and "b=two" in calls[2].headers.get("cookie", "")


@pytest.mark.asyncio
async def test_lazy_tabs_capture_each_replaced_panel(chromium, broker_transport):
    html = '''<html><body><p>Documentation content is long enough for extraction.</p>
    <button role="tab" aria-controls="panel" onclick="document.getElementById('panel').innerHTML='<pre>PYTHON panel</pre>'">Python</button>
    <button role="tab" aria-controls="panel" onclick="document.getElementById('panel').innerHTML='<pre>JAVASCRIPT panel</pre>'">JS</button>
    <section id="panel"><pre>INITIAL panel</pre></section><script>void 0</script></body></html>'''
    broker_transport(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=html))
    result = (await render_fixture("https://docs.example/tabs", profile="knowledge")).result
    assert result.ok and not result.truncated, result.evidence()
    assert all(panel in result.text for panel in ("INITIAL panel", "PYTHON panel", "JAVASCRIPT panel"))


@pytest.mark.asyncio
async def test_resource_failure_is_visible_and_private_redirect_is_never_fetched(chromium, broker_transport, monkeypatch):
    from kazma_core.security import ssrf

    def validate(url, **kwargs):
        if "127.0.0.1" in url:
            raise ssrf.SSRFError("Private destination refused")
        return ("93.184.216.34",)
    monkeypatch.setattr(ssrf, "validate_url", validate)
    def respond(request):
        if request.url.path == "/app.js":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Readable article text</p><script src='/app.js'></script>")
    calls = broker_transport(respond)
    result = (await render_fixture("https://docs.example/article")).result
    assert not result.ok and result.error_code == "security_blocked", result.evidence()
    assert all(request.url.host != "127.0.0.1" for request in calls)


@pytest.mark.asyncio
async def test_read_only_browser_refuses_post_and_marks_partial(chromium, broker_transport):
    html = "<p>Readable page article</p><script>fetch('/write', {method:'POST', body:'denied'}).catch(()=>{});</script>"
    calls = broker_transport(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=html))
    result = (await render_fixture("https://docs.example/article")).result
    assert result.ok and result.truncated and "browser_request_failed" in result.truncation_reasons
    assert all(request.method == "GET" for request in calls)
    assert any(entry["error_code"] == "unsupported_content" for entry in result.meta["browser_network"]["errors"])


@pytest.mark.asyncio
async def test_native_websocket_cannot_contact_owned_origin(chromium, broker_transport, origin):
    base, hits = origin
    html = f"<p>Network containment fixture</p><script>new WebSocket('{base.replace('http:', 'ws:')}/native');</script>"
    broker_transport(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=html))
    result = (await render_fixture("https://docs.example/article")).result
    assert result.ok, result.error
    assert result.truncated and "browser_websocket_denied" in result.truncation_reasons
    assert hits == [], "Native WebSocket must not reach a destination outside the broker"


_FANOUT_FETCHES = "<p>Fanout fixture</p><script>for(let i=0;i<50;i++)fetch('/asset'+i).catch(()=>{});</script>"
#: Fifty parser-blocking scripts after the fetches: DOMContentLoaded, which
#: ``page.goto`` waits for, needs every one of them, so more requests than the
#: limit are intercepted before the page can be read, however slow the machine.
_FANOUT_BLOCKING = _FANOUT_FETCHES + "".join(f"<script src='/script{i}.js'></script>" for i in range(50))


def _fanout_answer(html):
    def respond(request):
        if request.url.path.endswith(".js"):
            return httpx.Response(200, headers={"content-type": "application/javascript"}, text="void 0;")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
    return respond


def _late_interceptions(monkeypatch, seconds):
    """Deliver every intercepted request but the document's *seconds* late: a
    loaded machine's CDP events, reaching the broker after the page was read."""
    from kazma_core.web_acquire.browser_network import BrowserNetwork

    received = BrowserNetwork._received

    def late(self, event):
        if event.get("resourceType") == "Document":
            received(self, event)
        else:
            asyncio.get_running_loop().call_later(seconds, received, self, event)
    monkeypatch.setattr(BrowserNetwork, "_received", late)


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [0, 3], ids=["prompt", "late-events"])
async def test_browser_request_fanout_is_bounded_before_queued_dispatch(chromium, broker_transport, monkeypatch, late):
    """The page cannot be read before its fan-out is intercepted: at most the
    limit's requests reach the network and the render fails ``resource_limit``,
    also when the interceptions arrive seconds late. With fetches alone the
    outcome depended on how soon the interceptions reached the broker (a
    loaded full run failed this test once, 2026-11-22; the control below
    reads the page first when they come late)."""
    from kazma_core.web_acquire.browser_network import BrowserNetwork

    monkeypatch.setattr(BrowserNetwork, "MAX_REQUESTS", 3)
    if late:
        _late_interceptions(monkeypatch, late)
    calls = broker_transport(_fanout_answer(_FANOUT_BLOCKING))
    result = (await render_fixture("https://docs.example/article")).result
    assert not result.ok and result.error_code == "resource_limit", result.evidence()
    assert len(calls) <= 3


@pytest.mark.asyncio
async def test_control_fetches_alone_are_read_before_late_interceptions(chromium, broker_transport, monkeypatch):
    """The old fixture: its fan-out does not hold the document, so when the
    interceptions come late the page is read first and the limit is never
    reached. What the test checked depended on the machine's load."""
    from kazma_core.web_acquire.browser_network import BrowserNetwork

    monkeypatch.setattr(BrowserNetwork, "MAX_REQUESTS", 3)
    _late_interceptions(monkeypatch, 15)  # well past the page's reading, however slow
    broker_transport(_fanout_answer(_FANOUT_FETCHES))
    result = (await render_fixture("https://docs.example/article")).result
    assert result.ok and "Fanout fixture" in result.text, result.evidence()


@pytest.mark.asyncio
async def test_browser_compressed_resource_cannot_exceed_byte_reservation(chromium, broker_transport, monkeypatch):
    import gzip
    from kazma_core.web_acquire.browser_network import BrowserNetwork

    monkeypatch.setattr(BrowserNetwork, "MAX_RESPONSE", 2000)
    class Raw(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b"x" * 2_000_000)
    def respond(request):
        if request.url.path == "/app.js":
            return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/javascript"}, stream=Raw())
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Bounded document</p><script src='/app.js'></script>")
    broker_transport(respond)
    result = (await render_fixture("https://docs.example/article")).result
    assert not result.ok and result.error_code == "resource_limit", result.evidence()
    assert result.meta["browser_network"]["decoded_bytes"] < 2200


@pytest.mark.asyncio
async def test_browser_refuses_binary_document_with_typed_error(chromium, broker_transport):
    broker_transport(lambda request: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7"))
    result = (await render_fixture("https://docs.example/report.pdf")).result
    assert not result.ok and result.error_code == "unsupported_content", result.evidence()
    assert result.http_status == 200


@pytest.mark.asyncio
async def test_browser_job_cookie_continuity_across_scoped_pages(chromium, broker_transport):
    from kazma_core.web_acquire.browser import BrowserJob
    from kazma_core.web_acquire.trace import FetchTrace, active_trace

    def respond(request):
        if request.url.path == "/first":
            return httpx.Response(200, headers={"content-type": "text/html", "set-cookie": "session=owned; Path=/; HttpOnly; Secure"},
                                  text="<p>First owned cookie fixture</p>")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>Second owned cookie fixture</p>")
    calls = broker_transport(respond)
    async with BrowserJob():
        traces = []
        for path in ("first", "second"):
            trace = FetchTrace(url=f"https://docs.example/{path}")
            token = active_trace.set(trace)
            try:
                assert (await render_fixture(trace.url)).result.ok
                traces.append(trace)
            finally:
                active_trace.reset(token)
    second = next(request for request in calls if request.url.path == "/second")
    assert second.headers.get("cookie") == "session=owned"
    assert any(attempt.url.endswith("/second") for attempt in traces[1].attempts)
    assert all(not attempt.url.endswith("/second") for attempt in traces[0].attempts)


@pytest.mark.asyncio
async def test_head_retains_representation_metadata_without_reading_body(chromium, broker_transport):
    class NoBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("HEAD response body must not be read")
            yield b"unreachable"
    def respond(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": "9000", "content-encoding": "gzip"}, stream=NoBody())
        return httpx.Response(200, headers={"content-type": "text/html"}, text="""
            <p id="info">Waiting for metadata</p><script>
            fetch('/head', {method:'HEAD'}).then(r=>document.getElementById('info').textContent=
              'length='+r.headers.get('content-length')+' encoding='+r.headers.get('content-encoding'));
            </script>""")
    broker_transport(respond)
    result = (await render_fixture("https://docs.example/article")).result
    assert result.ok and not result.truncated, result.evidence()
    assert "length=9000 encoding=gzip" in result.text


@pytest.mark.asyncio
async def test_renderer_caps_parser_output_and_marks_partial(chromium, broker_transport, monkeypatch):
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser import render_document

    from kazma_core.web_acquire.document import PAGE_TEXT_MAX

    broker_transport(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text='x' * (PAGE_TEXT_MAX + 1)))
    result = (await run_in_browser_loop(render_document("https://docs.example/article",
        challenge=lambda html, status: False, profile='knowledge'), timeout=_RENDER_BOUND_S)).result
    assert result.ok and result.truncated and "text_limit" in result.truncation_reasons
    assert len(result.text) == PAGE_TEXT_MAX


@pytest.mark.asyncio
@pytest.mark.parametrize('redirect', [True, False], ids=['navigation', 'script'])
async def test_actual_chromium_obeys_robots_before_redirects_and_assets(chromium, broker_transport, monkeypatch, redirect):
    from kazma_core.web_acquire import robots

    monkeypatch.setattr(robots, '_MANAGER', robots._RobotsManager())
    monkeypatch.setattr('kazma_core.web_acquire.http.validate_url', lambda *a, **k: ('93.184.216.34',))
    def respond(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: KazmaBot\nDisallow: /private')
        if redirect:
            return httpx.Response(302, headers={'location': '/private'})
        return httpx.Response(200, headers={'content-type': 'text/html'},
                              text='<p>Public documentation remains readable.</p><script src="/private"></script>')
    calls = broker_transport(respond)
    with robots.crawl_policy():
        result = (await render_fixture('https://docs.example/article')).result
    assert all(request.url.path != '/private' for request in calls)
    assert all(request.headers['user-agent'] == robots.USER_AGENT for request in calls)
    assert [request.url.path for request in calls][:2] == ['/robots.txt', '/article']
    if redirect:
        assert not result.ok and result.error_code == 'robots_disallowed', result.evidence()
    else:
        assert result.ok and result.truncated and 'browser_request_failed' in result.truncation_reasons
        assert any(error['error_code'] == 'robots_disallowed' for error in result.meta['browser_network']['errors'])


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['form-post', 'private-redirect'])
async def test_native_automation_uses_same_broker_and_preserves_supported_form_body(monkeypatch, broker_transport, chromium, scenario):
    from kazma_skills.native.browser_automation import tools as native
    from kazma_core.security import ssrf
    def validator(url, **kwargs):
        if '127.0.0.1' in url:
            raise ssrf.SSRFError('Private redirect refused')
        return ('93.184.216.34',)
    monkeypatch.setattr(ssrf, 'validate_url', validator)
    def handler(request):
        if scenario == 'private-redirect':
            return httpx.Response(302, headers={'location': 'http://127.0.0.1:9/escape'})
        if request.method == 'POST':
            return httpx.Response(200, text='<html><body>Submission received</body></html>', headers={'content-type': 'text/html'})
        return httpx.Response(200, text='<html><body><form action="/submit" method="post"><input name="q" id="query"><button id="submit">Send</button></form></body></html>', headers={'content-type':'text/html'})
    calls = broker_transport(handler)
    await native._run_sync(native._close_sync)
    try:
        result = await native.browser_navigate('https://native.example/start')
        if scenario == 'private-redirect':
            assert 'Error:' in result and len(calls) == 1
        else:
            assert 'Navigated' in result
            result = await native.browser_fill_form({'#query': 'fixture'}, '#submit')
            assert 'Submission received' in result
            posted = [request for request in calls if request.method == 'POST']
            assert len(posted) == 1 and posted[0].content == b'q=fixture'
    finally:
        await native._run_sync(native._close_sync)
    assert native._state['page'] is None
