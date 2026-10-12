"""Origin feedback must survive clients, jobs, redirects and cancellation."""
from __future__ import annotations

import asyncio
import importlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from kazma_core.web_acquire.http import fetch_public, read_capped
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.web_acquire import origins


@pytest.mark.parametrize('header, expected', [
    ('0', 0), ('120', 120), (' 42 ', 42), ('-1', None), ('1.5', None),
    ('+2', None), ('١٢', None), ('garbage', None), ('', None), (None, None),
    ('Thu, 01 Jan 1970 00:02:00 GMT', 120), ('Thu, 01 Jan 1970 00:00:00 GMT', 0),
    ('Thursday, 01-Jan-70 00:02:00 GMT', 120), ('Thu Jan  1 00:02:00 1970', 120),
])
def test_retry_after_supports_http_dates_and_decimal_seconds(header, expected):
    assert origins._retry_after(header, 0) == expected


@pytest.mark.parametrize('left, right', [
    ('https://EXAMPLE.com.:443/a', 'https://example.com/b'),
    ('https://[2001:4860:4860:0:0:0:0:8888]/a', 'https://[2001:4860:4860::8888]/b'),
    ('https://bücher.example/a', 'https://xn--bcher-kva.example/b'),
])
def test_origin_identity_cannot_split_limits_by_spelling(left, right):
    assert origins._origin(left) == origins._origin(right)


@pytest.mark.asyncio
async def test_spacing_and_cancellation_are_shared_across_jobs():
    scheduler = origins.origin_scheduler()
    entered = asyncio.Event()
    started = []
    async def first_job():
        async with origins.polite_requests(0.04), scheduler.request('https://spaced.example/first'):
            started.append(time.monotonic())
            entered.set()
    await first_job()
    await entered.wait()
    async with scheduler.request('https://spaced.example/other-job'):
        # The second caller did not ask for a delay, but shares this origin's floor.
        assert time.monotonic() - started[0] >= 0.03
    token = origins.origin_delay.set(10)
    try:
        async with scheduler.request('https://cancel-spacing.example/first'):
            pass
    finally:
        origins.origin_delay.reset(token)
    async def blocked():
        async with scheduler.request('https://cancel-spacing.example/second'):
            pytest.fail('spacing bypassed')
    task = asyncio.create_task(blocked())
    async with asyncio.timeout(2):
        while scheduler.snapshot()['active'] != 1:
            await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scheduler.snapshot()['active'] == 0


def test_active_limit_is_shared_by_separate_event_loops_in_threads():
    scheduler = origins.origin_scheduler()
    lock = threading.Lock()
    entered, release = threading.Event(), threading.Event()
    active, peak, completions = [0], [0], []
    def worker():
        async def run():
            async with scheduler.request('https://cross-loop.example/'):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                    if active[0] == 2:
                        entered.set()
                await asyncio.to_thread(release.wait, 5)
                with lock:
                    active[0] -= 1
                    completions.append(True)
        asyncio.run(run())
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(3)
        assert peak[0] == 2
    finally:
        release.set()
        for thread in threads:
            thread.join(6)
    assert not any(thread.is_alive() for thread in threads)
    assert len(completions) == 3 and peak[0] == 2


@pytest.mark.asyncio
async def test_retry_after_stops_an_independent_client_before_dispatch():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={'Retry-After': '120'}, text='busy')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as first:
        response, _, _ = await read_capped(first, 'https://origin-feedback.example/first', 100)
        assert response.status_code == 429
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as second:
        with pytest.raises(AcquisitionError, match='origin|Origin'):
            await read_capped(second, 'https://origin-feedback.example/second', 100)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_redirect_retry_after_prevents_immediate_followup(monkeypatch):
    calls = []
    peer = SimpleNamespace(get_extra_info=lambda name: ('93.184.216.34', 443) if name == 'server_addr' else None)
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302 if len(calls) == 1 else 200,
            headers={'Location': '/destination', 'Retry-After': '120'}, text='response',
            extensions={'network_stream': peer})
    monkeypatch.setattr('kazma_core.web_acquire.http.validate_url', lambda *a, **k: ('93.184.216.34',))
    original = httpx.AsyncClient
    monkeypatch.setattr('kazma_core.proxy.client.get_scraping_client',
                        lambda **kwargs: original(transport=httpx.MockTransport(respond)))
    with pytest.raises(AcquisitionError, match='origin|Origin'):
        await fetch_public('https://redirect-delay.example/source')
    assert calls == ['https://redirect-delay.example/source']


@pytest.mark.asyncio
async def test_hosted_helpers_cannot_bypass_source_cooldown(monkeypatch):
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-key')
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    ru._FIRECRAWL_COOLDOWN['until'] = 0
    origin = 'https://source-cooldown.example/'
    origins.origin_scheduler().observe(origin, 429, {'retry-after': '120'})
    dispatch = Mock(side_effect=AssertionError('Hosted backend cannot ignore source cooldown'))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(dispatch)))
    for helper in (ru._try_jina_reader, ru._try_firecrawl):
        with pytest.raises(AcquisitionError) as failure:
            await helper(origin)
        assert failure.value.code == 'origin_throttled'
    dispatch.assert_not_called()
    assert origins.origin_scheduler().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_typed_ladder_refuses_cooldown_without_browser_or_hosted_escalation(monkeypatch):
    from unittest.mock import AsyncMock
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    url = 'https://typed-cooldown.example/article'
    origins.origin_scheduler().observe(url, 429, {'retry-after': '120'})
    dispatch = Mock(side_effect=AssertionError('No HTTP request during origin cooldown'))
    original = httpx.AsyncClient
    monkeypatch.setattr('kazma_core.proxy.client.get_scraping_client',
                        lambda **kwargs: original(transport=httpx.MockTransport(dispatch)))
    recovery = AsyncMock(side_effect=AssertionError('No alternate backend during source cooldown'))
    monkeypatch.setattr(ru, '_recover_hard_page', recovery)
    result = await ru.fetch_result(url, purpose='crawl', scope_check=lambda target: True)
    assert not result.ok and result.error_code == 'origin_throttled' and result.transient
    assert 'retry in at least' in result.error and result.attempts[-1].error_code == 'origin_throttled'
    recovery.assert_not_awaited()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_origin_admission_timeout_refuses_dispatch_and_releases_waiter(monkeypatch):
    scheduler = origins.origin_scheduler()
    url = 'https://admission-deadline.example/'
    first, second = scheduler.request(url), scheduler.request(url)
    await first.__aenter__()
    await second.__aenter__()
    original = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original(0.01))
    try:
        with pytest.raises(AcquisitionError) as failure:
            async with scheduler.request(url):
                pytest.fail('admission timeout dispatched a request')
        assert failure.value.code == 'resource_limit'
        assert scheduler.snapshot()['waiting'] == 0
    finally:
        await first.__aexit__(None, None, None)
        await second.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize('delay', [float('nan'), float('inf'), -1])
async def test_invalid_spacing_is_a_policy_error(delay):
    with pytest.raises(AcquisitionError) as failure:
        async with origins.polite_requests(delay):
            pytest.fail('invalid delay accepted')
    assert failure.value.code == 'policy_error'


@pytest.mark.asyncio
async def test_firecrawl_preserves_long_retry_after_and_does_not_charge_another_attempt(monkeypatch):
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'firecrawl')
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-key')
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    ru._FIRECRAWL_COOLDOWN['until'] = 0
    original = httpx.AsyncClient
    dispatch = Mock(return_value=httpx.Response(429, headers={'Retry-After': '120'}, json={'error': 'busy'}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(dispatch)))
    real_sleep = asyncio.sleep
    retry_sleeps = []
    async def no_retry_sleep(seconds):
        if seconds != 1.5:
            return await real_sleep(seconds)
        retry_sleeps.append(seconds)
    monkeypatch.setattr(asyncio, 'sleep', no_retry_sleep)
    with pytest.raises(AcquisitionError) as failure:
        await ru._try_firecrawl('https://hosted-target.example/')
    assert failure.value.code == 'origin_throttled' and dispatch.call_count == 1
    assert retry_sleeps == [1.5]
    assert origins.origin_scheduler().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_multi_origin_requests_deduplicate_and_order_to_prevent_deadlock():
    scheduler = origins.origin_scheduler()
    async with scheduler.request_many('https://same.example/a', 'https://same.example/b'):
        assert scheduler.snapshot()['active'] == 1
    async def request(left, right):
        async with scheduler.request_many(left, right):
            await asyncio.sleep(0)
    async with asyncio.timeout(3):
        await asyncio.gather(*(request('https://a.example/', 'https://b.example/') if i % 2 else
                               request('https://b.example/', 'https://a.example/') for i in range(8)))
    assert scheduler.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_cross_origin_redirect_carries_retry_after_to_destination():
    scheduler = origins.origin_scheduler()
    scheduler.observe('https://first.example/source', 302,
                      {'retry-after': '120', 'location': 'https://second.example/dest'})
    with pytest.raises(AcquisitionError) as failure:
        async with scheduler.request('https://second.example/other'):
            pytest.fail('redirect destination dispatched early')
    assert failure.value.code == 'origin_throttled' and failure.value.transient


@pytest.mark.asyncio
async def test_success_in_flight_cannot_clear_cooldown_and_other_origin_is_free(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(origins, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 0))
    scheduler = origins.origin_scheduler()
    scheduler.observe('https://cooling.example/', 503, {'retry-after': '30'})
    scheduler.observe('https://cooling.example/earlier-flight', 200, {})
    async with scheduler.request('https://unrelated.example/'):
        assert scheduler.snapshot()['active'] == 1
    with pytest.raises(AcquisitionError):
        async with scheduler.request('https://cooling.example/'):
            pytest.fail('cooldown cleared by older success')
    clock[0] = 131
    async with scheduler.request('https://cooling.example/'):
        assert scheduler.snapshot()['active'] == 1
    assert scheduler.snapshot()['active'] == scheduler.snapshot()['waiting'] == 0


@pytest.mark.asyncio
async def test_huge_server_delay_refuses_without_overflow_or_clamping():
    scheduler = origins.origin_scheduler()
    scheduler.observe('https://huge-delay.example/', 429, {'retry-after': '9' * 500})
    with pytest.raises(AcquisitionError, match='beyond runtime') as failure:
        async with scheduler.request('https://huge-delay.example/'):
            pytest.fail('huge delay was shortened')
    assert failure.value.code == 'origin_throttled'


@pytest.mark.asyncio
async def test_active_and_queued_capacity_is_bounded_and_cancelled_waiters_release():
    scheduler = origins.origin_scheduler()
    url = 'https://concurrent-origin.example/'
    first, second = scheduler.request(url), scheduler.request(url)
    await first.__aenter__()
    await second.__aenter__()
    dispatched = []
    async def queued():
        async with scheduler.request(url):
            dispatched.append(True)
    waiters = [asyncio.create_task(queued()) for _ in range(32)]
    try:
        async with asyncio.timeout(3):
            while scheduler.snapshot()['waiting'] < 32:
                await asyncio.sleep(0)
        with pytest.raises(AcquisitionError) as failure:
            await queued()
        assert failure.value.code == 'resource_limit'
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        assert scheduler.snapshot()['waiting'] == 0 and not dispatched
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await first.__aexit__(None, None, None)
        await second.__aexit__(None, None, None)
    assert scheduler.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_cooldown_arriving_while_queued_is_rechecked_before_dispatch():
    scheduler = origins.origin_scheduler()
    url = 'https://queued-cooldown.example/'
    first, second = scheduler.request(url), scheduler.request(url)
    await first.__aenter__()
    await second.__aenter__()
    async def queued():
        async with scheduler.request(url):
            pytest.fail('queued call ignored newly received cooldown')
    waiter = asyncio.create_task(queued())
    try:
        async with asyncio.timeout(3):
            while scheduler.snapshot()['waiting'] != 1:
                await asyncio.sleep(0)
        scheduler.observe(url, 429, {'retry-after': '120'})
        await first.__aexit__(None, None, None)
        with pytest.raises(AcquisitionError) as failure:
            await waiter
        assert failure.value.code == 'origin_throttled'
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        await second.__aexit__(None, None, None)
    assert scheduler.snapshot()['active'] == scheduler.snapshot()['waiting'] == 0


@pytest.mark.asyncio
async def test_origin_table_refuses_eviction_of_active_or_cooling_origins():
    scheduler = origins._OriginScheduler(max_origins=1)
    scheduler.observe('https://occupied.example/', 503, {'retry-after': '120'})
    with pytest.raises(AcquisitionError, match='table is full'):
        async with scheduler.request('https://new.example/'):
            pytest.fail('active cooldown evicted to reset budget')


@pytest.mark.asyncio
async def test_body_timeout_keeps_its_classification_and_releases_origin_slot():
    async def failed(request):
        raise TimeoutError('body timeout')
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(TimeoutError, match='body timeout'):
            await read_capped(client, 'https://timed-body.example/', 100)
    assert origins.origin_scheduler().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_feedback_is_recorded_before_cancelled_body_and_stream_closes():
    closed = asyncio.Event()
    entered = asyncio.Event()
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b'unreachable'
        async def aclose(self):
            closed.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(429, headers={'Retry-After': '120'}, stream=Body()))) as client:
        task = asyncio.create_task(read_capped(client, 'https://cancelled-body.example/', 100))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed.is_set() and origins.origin_scheduler().snapshot()['active'] == 0
    with pytest.raises(AcquisitionError):
        async with origins.origin_scheduler().request('https://cancelled-body.example/next'):
            pytest.fail('cancelled body discarded observed cooldown')
