"""Real response headers retain provider observations without inventing charges."""
import asyncio
import importlib

import httpx
import pytest

from kazma_core import config_store
from kazma_core.web_acquire import accounting, provider_usage
from kazma_core.web_acquire.result import FetchErrorCode


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: settings)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setenv('JINA_API_KEY', 'fixture-key')
    monkeypatch.setenv('KAZMA_JINA_READER', '1')
    yield settings
    settings.close()


@pytest.mark.parametrize('headers,expected', [([], None), ([('X-Usage-Tokens', '0')], 0),
    ([('X-Usage-Tokens', '42')], 42), ([('X-Usage-Tokens', '-1')], None),
    ([('X-Usage-Tokens', '١٢')], None), ([('X-Usage-Tokens', '1.5')], None),
    ([('X-Usage-Tokens', 'NaN')], None), ([('X-Usage-Tokens', '1000000000000')], None),
    ([('X-Usage-Tokens', '1'), ('X-Usage-Tokens', '2')], None),
    ([('X-Usage-Tokens', '1, 2')], None)])
def test_jina_header_requires_one_bounded_decimal_observation(headers, expected):
    observation = provider_usage._from_jina(httpx.Headers(headers, encoding='utf-8'))
    if expected is None:
        assert observation is None
    else:
        assert observation == dict(provider='jina', unit='tokens', reported=expected,
            source='X-Usage-Tokens', scope='service_response')


@pytest.mark.parametrize('reported', ['0', '42', None])
def test_readiness_keeps_historical_usage_visible_after_switch_to_local_only(store, monkeypatch, reported):
    from kazma_core.tools.research_readiness import research_readiness
    from kazma_core.web_acquire import crawl_jobs

    job = crawl_jobs.start('historical-usage', 'https://docs.example/',
        dict(mode='local_first_with_remote_fallback', providers=['jina'], robots=False,
             scope='tree', max_pages=1, delay_ms=0))
    token = crawl_jobs.current_crawl.set(job)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
                headers={'X-Usage-Tokens': reported} if reported is not None else {})),
                event_hooks=accounting.httpx_hooks('jina', 'fixture', asynchronous=False)) as client:
            client.get('https://r.jina.ai/https://docs.example/')
    finally:
        crawl_jobs.current_crawl.reset(token)
        job.finish('completed')
    store.close()  # Read the persisted receipt after reopening, not a live hook.
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    monkeypatch.setenv('KAZMA_RESEARCH_PREFLIGHT_LIVE', '0')
    def forbidden(*args, **kwargs):
        raise AssertionError('Cached readiness attempted a network call')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    before = accounting.snapshot(job.job_id)
    acquisition = research_readiness(job_id=job.job_id)['acquisition']
    assert acquisition['mode'] == 'local_only' and acquisition['remote_providers'] == []
    assert acquisition['usage_available'] is (reported is not None)
    if reported is None:
        assert acquisition['usage'] is None
    else:
        assert acquisition['usage']['observations'][0]['reported'] == int(reported)
        assert acquisition['usage']['complete'] is False
    assert acquisition['credits'] is None and not acquisition['credits_available']
    assert accounting.snapshot(job.job_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('asynchronous', [False, True])
async def test_real_hooks_store_reported_zero_and_unknown_separately_before_body(store, asynchronous):
    responses = [httpx.Response(429, headers={'X-Usage-Tokens': '0'}), httpx.Response(200)]
    def respond(request):
        return responses.pop(0)
    def sync():
        with accounting.remote_scope('observed'):
            with httpx.Client(transport=httpx.MockTransport(respond),
                    event_hooks=accounting.httpx_hooks('jina', 'fixture', asynchronous=False)) as client:
                client.get('https://r.jina.ai/https://docs.example/one')
                client.get('https://r.jina.ai/https://docs.example/two')
    if asynchronous:
        with accounting.remote_scope('observed'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                    event_hooks=accounting.httpx_hooks('jina', 'fixture', asynchronous=True)) as client:
                await client.get('https://r.jina.ai/https://docs.example/one')
                await client.get('https://r.jina.ai/https://docs.example/two')
    else:
        await asyncio.to_thread(sync)
    evidence = accounting.snapshot('observed')
    assert evidence['job_reserved'] == 2
    assert evidence['recent'][0]['provider_usage']['reported'] == 0
    assert evidence['recent'][1]['provider_usage'] is None
    assert evidence['provider_usage']['observations'][0]['reported'] == 0
    assert evidence['provider_usage']['complete'] is False
    assert evidence['provider_credits'] is None
    # Reopening the actual database retains the observation, rather than only
    # an in-process trace. Neither a header nor HTTP 429 establishes a charge.
    store.close()
    assert accounting.snapshot('observed')['recent'][0]['provider_usage']['reported'] == 0


@pytest.mark.asyncio
async def test_reported_usage_reaches_durable_job_projection_without_a_balance_call(store):
    from kazma_core.web_acquire import crawl_jobs

    job = crawl_jobs.start('lib', 'https://docs.example/',
        dict(mode='local_first_with_remote_fallback', providers=['jina'], robots=False,
            scope='tree', max_pages=1, delay_ms=0))
    token = crawl_jobs.current_crawl.set(job)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                httpx.Response(200, headers={'X-Usage-Tokens': '7'})),
                event_hooks=accounting.httpx_hooks('jina', 'fixture', asynchronous=True)) as client:
            await client.get('https://r.jina.ai/https://docs.example/')
        projected = crawl_jobs.projection(job.job_id)['acquisition']
        assert projected['usage']['observations'][0]['reported'] == 7
        assert projected['credits'] is None and projected['allowances']['job_reserved'] == 1
    finally:
        crawl_jobs.current_crawl.reset(token)
        job.finish('interrupted')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,status', [(False, 200), (True, 200), (False, 429), (False, 302)],
    ids=['useful-content', 'failed-body', 'quota-response', 'service-redirect'])
async def test_jina_trace_retains_header_on_body_failure_and_does_not_recharge_cache(store, monkeypatch, failure, status):
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    original_client, actual_ladder, calls = httpx.AsyncClient, ru._fetch_full_text_impl, []
    class Dropped(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadError('fixture dropped body')
            yield b''
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={'X-Usage-Tokens': '42'},
            stream=Dropped() if failure else httpx.ByteStream(b'Useful hosted fixture article. ' * 8))
    def client(**kwargs):
        kwargs.pop('transport', None)
        return original_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    async def hosted(url):
        text = await ru._try_jina_reader(url)
        return ru._succeeded(text, recovery=True) if text else ru._failed(FetchErrorCode.READ, 'Fixture body failed')
    monkeypatch.setattr(ru, '_fetch_full_text_impl', hosted)
    ru.clear_url_cache()
    try:
        result = await ru.fetch_result('https://docs.example/usage-fixture')
        assert result.ok is (not failure and status == 200)
        assert result.attempts[0].usage['reported'] == 42
        assert result.usage['observations'][0]['reported'] == 42 and result.credits is None
        assert 'Jina reported tokens 42' in result.notice() and 'credits unavailable' in result.notice()
        assert accounting.snapshot()['recent'][0]['provider_usage']['reported'] == 42
        if result.ok:
            monkeypatch.setattr(ru, '_fetch_full_text_impl', actual_ladder)
            cached = await ru.fetch_result(result.url)
            assert cached.cache_hit and not cached.attempts and cached.usage is None
            assert len(calls) == 1 and accounting.snapshot()['day_reserved'] == 1
    finally:
        ru.clear_url_cache()


@pytest.mark.asyncio
async def test_cancellation_after_headers_retains_reported_usage_and_reservation(store, monkeypatch):
    ru = importlib.import_module('kazma_core.tools.read_url')
    from kazma_core.web_acquire.trace import FetchTrace, active_trace

    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    original_client, reading, closed = httpx.AsyncClient, asyncio.Event(), asyncio.Event()
    class Waiting(httpx.AsyncByteStream):
        async def __aiter__(self):
            reading.set()
            await asyncio.Event().wait()
            yield b''
        async def aclose(self):
            closed.set()
    def client(**kwargs):
        kwargs.pop('transport', None)
        return original_client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={'X-Usage-Tokens': '0'}, stream=Waiting())), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    trace = FetchTrace('https://docs.example/cancelled')
    token = active_trace.set(trace)
    try:
        with accounting.remote_scope('cancelled-usage'):
            work = asyncio.create_task(ru._try_jina_reader(trace.url))
            await asyncio.wait_for(reading.wait(), 10)
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
    finally:
        active_trace.reset(token)
    assert closed.is_set()
    assert trace.attempts[0].error_code == 'cancelled'
    assert trace.attempts[0].usage['reported'] == 0
    evidence = accounting.snapshot('cancelled-usage')
    assert evidence['job_reserved'] == 1 and evidence['provider_credits'] is None
    assert evidence['provider_usage']['observations'][0]['reported'] == 0


def test_invalid_persisted_observation_refuses_new_dispatch(store):
    from kazma_core.web_acquire.result import AcquisitionError

    with accounting.remote_scope('tampered'):
        reservation = accounting.reserve('jina', accounting.account_fingerprint('jina', 'fixture-key'))
    accounting._response_received(reservation, 200, usage=provider_usage._from_jina(httpx.Headers({'X-Usage-Tokens': '1'})))
    state = store.get('acquisition.allowances')
    state['recent'][0]['provider_usage']['reported'] = True
    store.set('acquisition.allowances', state, category='acquisition_allowances')
    with pytest.raises(AcquisitionError) as raised:
        accounting.reserve('jina', accounting.account_fingerprint('jina', 'fixture-key'))
    assert raised.value.code == FetchErrorCode.STORAGE


@pytest.mark.asyncio
async def test_local_policy_refuses_jina_before_transport_or_allowance(store, monkeypatch):
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    def forbidden(*args, **kwargs):
        raise AssertionError('Local-only attempted a hosted client')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    assert await ru._try_jina_reader('https://docs.example/') is None
    assert accounting.snapshot()['day_reserved'] == 0
    assert accounting.snapshot()['provider_usage'] is None
