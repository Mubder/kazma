"""Ownership and admission under cancellation; no provider requests."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kazma_core.web_acquire import resources
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult


async def queued(pool, count):
    async with asyncio.timeout(2):
        while pool.snapshot()['waiting'] != count:
            await asyncio.sleep(0)


@pytest.fixture
def browser_loop(monkeypatch):
    import kazma_core.playwright_loop as bridge

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(bridge, "_IS_WINDOWS", True)
    monkeypatch.setattr(bridge, "_get_loop", lambda: loop)
    try:
        yield bridge, loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(3)
        loop.close()


@pytest.mark.asyncio
async def test_cancel_does_not_return_before_owned_async_cleanup(browser_loop):
    bridge, loop = browser_loop
    entered, cleaning, cleaned = threading.Event(), threading.Event(), threading.Event()
    release = None
    async def work():
        nonlocal release
        release = asyncio.Event()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()
    task = asyncio.create_task(bridge.run_in_browser_loop(work()))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        assert await asyncio.to_thread(cleaning.wait, 2)
        assert not task.done(), "Cancellation returned while owned browser cleanup was still running"
        task.cancel()  # repeated operator cancel must not interrupt teardown
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        if release is not None:
            loop.call_soon_threadsafe(release.set)
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_capacity_is_fifo_bounded_and_duplicate_release_cannot_overgrant():
    pool = resources._CapacityPool('fixture', 2, 2)
    first, second = await pool.acquire(), await pool.acquire()
    third, fourth = asyncio.create_task(pool.acquire()), asyncio.create_task(pool.acquire())
    await queued(pool, 2)
    with pytest.raises(AcquisitionError, match='queue is full'):
        await pool.acquire()
    first.release()
    granted = await third
    first.release()
    assert not fourth.done() and pool.snapshot()['active'] == 2
    second.release()
    final = await fourth
    granted.release()
    final.release()
    assert pool.snapshot()['active'] == pool.snapshot()['waiting'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('during_grant', [False, True])
async def test_cancelled_waiter_never_leaks_a_pending_or_granted_slot(during_grant):
    pool = resources._CapacityPool('fixture', 1, 1)
    first = await pool.acquire()
    waiter = asyncio.create_task(pool.acquire())
    await queued(pool, 1)
    if during_grant:
        first.release()  # delivery is scheduled, but the receiver has not resumed
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    first.release()
    async with await pool.acquire():
        assert pool.snapshot()['active'] == 1
    assert pool.snapshot()['active'] == pool.snapshot()['waiting'] == 0


@pytest.mark.asyncio
async def test_admission_timeout_returns_typed_resource_limit_and_removes_waiter():
    pool = resources._CapacityPool('fixture', 1, 1, wait_seconds=0.01)
    first = await pool.acquire()
    try:
        with pytest.raises(AcquisitionError, match='admission wait expired') as failure:
            await pool.acquire()
        assert failure.value.code == 'resource_limit' and failure.value.transient
        assert pool.snapshot()['waiting'] == 0
    finally:
        first.release()


@pytest.mark.asyncio
async def test_capacity_grants_cross_event_loop_without_sharing_asyncio_primitives(browser_loop):
    bridge, _ = browser_loop
    pool = resources._CapacityPool('fixture', 1, 1)
    first = await pool.acquire()
    async def remote():
        async with await pool.acquire():
            return threading.get_ident()
    task = asyncio.create_task(bridge.run_in_browser_loop(remote()))
    await queued(pool, 1)
    first.release()
    assert await task != threading.get_ident()
    assert pool.snapshot()['active'] == pool.snapshot()['waiting'] == 0


@pytest.mark.asyncio
async def test_browser_owner_can_finish_while_regular_acquisitions_are_saturated(monkeypatch):
    import importlib
    from kazma_core.web_acquire.browser import BrowserJob

    ru = importlib.import_module('kazma_core.tools.read_url')
    regular = resources._CapacityPool('fixture regular', 1, 0)
    owned = resources._CapacityPool('fixture owner', 1, 0)
    browsers = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', regular)
    monkeypatch.setattr(resources, '_BROWSER_ACQUISITIONS', owned)
    monkeypatch.setattr(resources, '_BROWSERS', browsers)
    async def ladder(url):
        return FetchResult(ok=True, url=url, text='finished owned page')
    monkeypatch.setattr(ru, '_fetch_full_text_impl', ladder)
    held = await regular.acquire()
    try:
        async with BrowserJob() as owner:
            owner._capacity_lease = await browsers.acquire()
            result = await ru.fetch_result('https://capacity.example/owned')
            assert result.ok and result.text == 'finished owned page'
        assert browsers.snapshot()['active'] == owned.snapshot()['active'] == 0
    finally:
        held.release()
        ru.clear_url_cache()


@pytest.mark.asyncio
async def test_raw_metadata_uses_reserved_owner_capacity_and_no_proxy_resolution_before_admission(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.web_acquire import http
    from kazma_core.web_acquire.browser import BrowserJob

    regular = resources._CapacityPool('fixture regular', 1, 0)
    owned = resources._CapacityPool('fixture owner', 1, 0)
    browsers = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', regular)
    monkeypatch.setattr(resources, '_BROWSER_ACQUISITIONS', owned)
    monkeypatch.setattr(resources, '_BROWSERS', browsers)
    monkeypatch.setattr(http, 'validate_url', lambda *a, **k: ('93.184.216.34',))
    calls = []
    def proxy():
        calls.append('proxy')
        return None
    def respond(request):
        calls.append('request')
        response = httpx.Response(200, text='metadata')
        response.extensions['network_stream'] = SimpleNamespace(
            get_extra_info=lambda name: ('93.184.216.34', 443))
        return response
    monkeypatch.setattr(pc, 'get_active_proxy_url', proxy)
    monkeypatch.setattr(pc, 'get_scraping_client', lambda **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    held = await regular.acquire()
    try:
        with pytest.raises(AcquisitionError):
            await http.fetch_public('https://capacity.example/metadata')
        assert calls == []
        async with BrowserJob() as owner:
            owner._capacity_lease = await browsers.acquire()
            result = await http.fetch_public('https://capacity.example/metadata')
            assert result.text == 'metadata'
        assert calls == ['proxy', 'request']
        assert owned.snapshot()['active'] == browsers.snapshot()['active'] == 0
    finally:
        held.release()


@pytest.mark.asyncio
async def test_saturation_is_visible_before_any_network_and_cache_hits_need_no_slot(monkeypatch):
    import importlib

    ru = importlib.import_module('kazma_core.tools.read_url')
    pool = resources._CapacityPool('fixture acquisitions', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    ladder = AsyncMock(side_effect=AssertionError('No dispatch while saturated'))
    monkeypatch.setattr(ru, '_fetch_full_text_impl', ladder)
    held = await pool.acquire()
    try:
        result = await ru.fetch_result('https://capacity.example/new')
        assert not result.ok and result.error_code == 'resource_limit' and not result.attempts
        assert 'capacity exhausted' in result.error
        ru._cache_put('https://capacity.example/cached', 'cached reference')
        cached = await ru.fetch_result('https://capacity.example/cached')
        assert cached.ok and cached.cache_hit and not cached.attempts
        scoped = await ru.fetch_result('https://capacity.example/cached', scope_check=lambda url: True)
        assert not scoped.ok and scoped.error_code == 'resource_limit'
        ladder.assert_not_awaited()
    finally:
        held.release()
        ru.clear_url_cache()


@pytest.mark.asyncio
async def test_browser_capacity_is_retained_during_failed_cleanup_and_released_after_retry(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    owner._capacity_lease = await pool.acquire()
    owner.context, owner.browser, owner.pw = AsyncMock(), AsyncMock(), AsyncMock()
    owner.context.close.side_effect = RuntimeError('context stuck')
    owner.browser.close.side_effect = RuntimeError('browser stuck')
    owner.pw.stop.side_effect = RuntimeError('driver stuck')
    with pytest.raises(AcquisitionError, match='quarantined'):
        await owner._close_impl()
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 1
    with pytest.raises(AcquisitionError):
        await pool.acquire()
    owner.pw.stop.side_effect = None
    await owner._close_impl()
    assert owner.context is owner.browser is owner.pw is owner._capacity_lease is None
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_unexpected_child_close_error_still_closes_browser_and_driver(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    owner._capacity_lease = await pool.acquire()
    owner.context, owner.browser, owner.pw = AsyncMock(), AsyncMock(), AsyncMock()
    owner.context.close.side_effect = ValueError('unexpected child close failure')
    await owner._close_impl()
    assert owner.context is owner.browser is owner.pw is None
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_interrupted_teardown_quarantines_capacity_until_a_cleanup_retry(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    owner._capacity_lease = await pool.acquire()
    owner.context, owner.browser, owner.pw = AsyncMock(), AsyncMock(), AsyncMock()
    entered = asyncio.Event()
    async def hang():
        entered.set()
        await asyncio.Event().wait()
    owner.context.close.side_effect = hang
    cleanup = asyncio.create_task(owner._close_impl())
    await entered.wait()
    cleanup.cancel()
    result = await asyncio.gather(cleanup, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 1
    owner.context.close.side_effect = None
    await owner._close_impl()
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_native_deny_endpoint_bounds_accepted_tasks_and_joins_owned_handlers(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    owner._capacity_lease = await pool.acquire()
    release = asyncio.Event()
    async def blocked_handler(reader, writer):
        owner._deny_writers.add(writer)
        try:
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            owner._deny_writers.discard(writer)
    monkeypatch.setattr(owner, '_deny_native', blocked_handler)
    owner._deny_server = await asyncio.start_server(owner._native_connected, '127.0.0.1', 0)
    port = owner._deny_server.sockets[0].getsockname()[1]
    clients = []
    cleanup = None
    try:
        clients = await asyncio.gather(*(asyncio.open_connection('127.0.0.1', port) for _ in range(20)))
        async with asyncio.timeout(3):
            while owner.native_denials != 20 or len(owner._deny_writers) != 16:
                await asyncio.sleep(0)
        assert len(owner._deny_tasks) == 16
        cleanup = asyncio.create_task(owner._close_impl())
        async with asyncio.timeout(3):
            while not owner._deny_closing:
                await asyncio.sleep(0)
        assert not cleanup.done() and pool.snapshot()['active'] == 1
        release.set()
        await cleanup
        assert not owner._deny_tasks and not owner._deny_writers and owner._deny_server is None
        assert pool.snapshot()['active'] == 0
    finally:
        release.set()
        if cleanup is not None:
            await cleanup
        await owner._close_impl()
        for _, writer in clients:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for _, writer in clients), return_exceptions=True)


@pytest.mark.asyncio
async def test_browser_limit_precedes_launch_and_failed_launch_releases_capacity(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    first, second = BrowserJob(), BrowserJob()
    async def launch_first():
        first.context = AsyncMock()
    monkeypatch.setattr(first, '_launch_impl', launch_first)
    launch_second = AsyncMock(side_effect=RuntimeError('failed launch'))
    monkeypatch.setattr(second, '_launch_impl', launch_second)
    await first._start_impl()
    try:
        with pytest.raises(AcquisitionError):
            await second._start_impl()
        launch_second.assert_not_awaited()
    finally:
        await first._close_impl()
    with pytest.raises(RuntimeError, match='failed launch'):
        await second._start_impl()
    assert pool.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_failed_partial_launch_cannot_replace_quarantined_driver(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    driver = AsyncMock()
    driver.stop.side_effect = RuntimeError('driver did not stop')
    async def partial_launch():
        owner.pw = driver
        raise RuntimeError('launch interrupted after driver start')
    launch = AsyncMock(side_effect=partial_launch)
    monkeypatch.setattr(owner, '_launch_impl', launch)
    with pytest.raises(AcquisitionError, match='quarantined'):
        await owner._start_impl()
    assert owner._closed and owner.pw is driver
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 1
    with pytest.raises(RuntimeError, match='already closed'):
        await owner._start_impl()
    launch.assert_awaited_once()
    driver.stop.side_effect = None
    await owner._close_impl()
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_endpoint_cleanup_error_is_typed_and_keeps_quarantined_capacity(monkeypatch):
    from kazma_core.web_acquire.browser import BrowserJob

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    owner = BrowserJob()
    owner._capacity_lease = await pool.acquire()
    server = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock(side_effect=RuntimeError('endpoint stuck')))
    owner._deny_server = server
    with pytest.raises(AcquisitionError, match='quarantined'):
        await owner._close_impl()
    assert owner._deny_server is server
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 1
    server.wait_closed.side_effect = None
    await owner._close_impl()
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_escalate_to_remote_recovery(monkeypatch):
    import importlib

    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setattr(ru, '_fetch_with_playwright_impl', AsyncMock(side_effect=AcquisitionError(
        FetchErrorCode.LIMIT, 'Browser cleanup incomplete; capacity quarantined')))
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ru, '_try_firecrawl', fire)
    monkeypatch.setattr(ru, '_try_jina_reader', jina)
    async def ladder(url):
        await ru._recover_hard_page(url)
        raise AssertionError('Failed local cleanup must terminate the acquisition')
    monkeypatch.setattr(ru, '_fetch_full_text_impl', ladder)
    result = await ru.fetch_result('https://capacity.example/cleanup')
    assert not result.ok and result.error_code == 'resource_limit' and result.backend == 'chromium'
    assert result.attempts[-1].error_code == 'resource_limit'
    fire.assert_not_awaited()
    jina.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('local renderer defect'), ImportError('required Kazma module missing')])
async def test_own_renderer_error_cannot_be_hidden_by_remote_recovery(monkeypatch, failure):
    import importlib

    pytest.importorskip('playwright.async_api')
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setattr(ru, '_fetch_with_playwright_impl', AsyncMock(side_effect=failure))
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ru, '_try_firecrawl', fire)
    monkeypatch.setattr(ru, '_try_jina_reader', jina)
    with pytest.raises(type(failure), match=str(failure)):
        await ru._recover_hard_page('https://capacity.example/defect')
    fire.assert_not_awaited()
    jina.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_renderer_capacity_refusal_keeps_retryability_and_cannot_call_hosted(monkeypatch):
    import importlib

    pytest.importorskip('playwright.async_api')
    ru = importlib.import_module('kazma_core.tools.read_url')
    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ru, '_try_firecrawl', fire)
    monkeypatch.setattr(ru, '_try_jina_reader', jina)
    async def ladder(url):
        await ru._recover_hard_page(url)
        raise AssertionError('Saturated renderer cannot escalate remotely')
    monkeypatch.setattr(ru, '_fetch_full_text_impl', ladder)
    held = await pool.acquire()
    try:
        result = await ru.fetch_result('https://capacity.example/real-renderer')
        assert not result.ok and result.error_code == 'resource_limit' and result.transient
        assert result.backend == 'chromium' and 'queue is full' in result.error
        assert result.attempts[-1].error_code == 'resource_limit'
        fire.assert_not_awaited()
        jina.assert_not_awaited()
    finally:
        held.release()


def test_readiness_exposes_actual_quarantined_capacity(monkeypatch):
    from kazma_core.tools.research_readiness import research_readiness

    pool = resources._CapacityPool('fixture browsers', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    with pool._lock:
        lease = pool._lease_locked()
    lease.quarantine()
    try:
        result = research_readiness()
        assert not result['ready']
        assert result['acquisition']['resources']['browsers']['quarantined'] == 1
        check = next(entry for entry in result['checks'] if entry['id'] == 'acquisition_capacity')
        assert not check['ok'] and check['level'] == 'error'
    finally:
        lease.release()


@pytest.mark.asyncio
async def test_bridge_deadline_joins_cleanup_before_reporting_timeout(browser_loop):
    bridge, _ = browser_loop
    cleaned = threading.Event()
    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    with pytest.raises(TimeoutError):
        await bridge.run_in_browser_loop(work(), timeout=0.05)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_broker_observes_completed_background_protocol_failure():
    from kazma_core.web_acquire.browser_network import BrowserNetwork

    broker = BrowserNetwork(SimpleNamespace())
    failure = RuntimeError('owned page-close protocol failure')
    async def fail():
        raise failure
    task = asyncio.create_task(fail())
    broker.tasks.add(task)
    broker.idle.clear()
    task.add_done_callback(broker._finished)
    await asyncio.gather(task, return_exceptions=True)
    assert broker.failure is failure and broker.closed
    assert not broker.tasks and broker.idle.is_set()


@pytest.mark.asyncio
async def test_failed_render_finalizer_is_not_replayed_with_watchdog_disarmed(monkeypatch):
    from unittest.mock import Mock
    from kazma_core.web_acquire import browser

    page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError('fixture navigation failure')),
        close=AsyncMock())
    broker = SimpleNamespace(__aenter__=AsyncMock(),
        aclose=AsyncMock(side_effect=RuntimeError('fixture close failure')),
        failure=None, root_status=None, via_proxy=False, evidence=lambda: {})
    driver = SimpleNamespace(watch=Mock(), cleanup_window=Mock(), cleanup_done=Mock(),
        transport=SimpleNamespace(failure=None), evidence=lambda: {})
    owner = SimpleNamespace(_render_lock=asyncio.Lock(), _start_impl=AsyncMock(), _driver=driver,
        context=SimpleNamespace(new_page=AsyncMock(return_value=page)))
    monkeypatch.setattr(browser, 'BrowserNetwork', lambda *a, **k: broker)
    result = await browser._render_in_job(owner, 'https://docs.example/',
        challenge=lambda *a: False, profile='generic')
    assert not result.result.ok and 'fixture close failure' in result.result.error
    broker.aclose.assert_awaited_once()
    driver.cleanup_window.assert_called_once()
    driver.cleanup_done.assert_not_called()
