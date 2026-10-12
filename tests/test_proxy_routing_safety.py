"""A selected proxy can fail, but may never become an unnoticed direct route."""

import importlib
import asyncio
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from kazma_core.proxy import client as pc
from kazma_core.proxy import registry
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture(autouse=True)
def enabled_proxy(monkeypatch):
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy,jina,firecrawl')
    monkeypatch.setattr(registry, '_current_provider', None)
    monkeypatch.setattr(registry, '_current_name', '')


@pytest.fixture
def proxy_config(monkeypatch):
    data = {'provider': 'anyip', 'username': 'fixture-user', 'password': 'fixture-password'}
    reads = []
    def get(key, default=None):
        reads.append(key)
        if key == 'proxy':
            return dict(data)
        if key.startswith('proxy.'):
            return data.get(key[6:], default)
        return default
    store = SimpleNamespace(get=get)
    monkeypatch.setattr('kazma_core.config_store.get_config_store', lambda: store)
    return data, reads, store


@pytest.mark.parametrize('change', [
    {'provider': 'misspelled'}, {'password': ''}, {'username': ''},
    {'password': 'vault://unresolved'}, {'port': 'not-a-port'},
    {'host': 'proxy.example/path'}, {'port': '70000'},
    {'password': {'wrong': 'type'}}, {'session_sticky': 'false'},
    {'host': 'safe.example@other.example'}, {'host': 'proxy.example%2fpath'},
])
def test_bad_selected_proxy_refuses_before_client_construction(monkeypatch, proxy_config, change):
    data, _, _ = proxy_config
    data.update(change)
    construct = Mock(side_effect=AssertionError('No direct client may be constructed'))
    monkeypatch.setattr('httpx.AsyncClient', construct)
    with pytest.raises(AcquisitionError) as failure:
        pc.get_scraping_client()
    assert failure.value.code == 'proxy_routing_error'
    assert 'fixture-password' not in str(failure.value)
    construct.assert_not_called()


def test_config_store_failure_cannot_switch_to_direct(monkeypatch):
    monkeypatch.setattr('kazma_core.config_store.get_config_store', Mock(side_effect=RuntimeError('fixture-password')))
    with pytest.raises(AcquisitionError, match='proxy') as failure:
        pc.get_active_proxy_url()
    assert 'fixture-password' not in str(failure.value)


def test_provider_constructor_failure_cannot_switch_to_direct(monkeypatch, proxy_config):
    monkeypatch.setitem(registry._PROVIDERS, 'anyip', Mock(side_effect=RuntimeError('fixture-password')))
    with pytest.raises(AcquisitionError):
        pc.get_active_proxy_url()
    assert registry._current_provider is None


def test_provider_config_failure_is_not_an_unconfigured_provider(monkeypatch):
    from kazma_core.proxy.brightdata import BrightDataProvider

    monkeypatch.setattr('kazma_core.config_store.get_config_store', Mock(side_effect=RuntimeError('store down')))
    with pytest.raises(AcquisitionError):
        BrightDataProvider().get_proxy_url()


def test_one_route_uses_one_coherent_config_read(proxy_config):
    _, reads, _ = proxy_config
    url = pc.get_active_proxy_url()
    assert 'fixture-user' in url
    assert reads == ['proxy']


def test_none_is_the_only_direct_provider(proxy_config):
    data, _, _ = proxy_config
    data['provider'] = 'none'
    assert pc.get_active_proxy_url() is None


def test_local_only_never_resolves_proxy_config(monkeypatch):
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    store = Mock(side_effect=AssertionError('Local-only must not inspect proxy credentials'))
    monkeypatch.setattr('kazma_core.config_store.get_config_store', store)
    assert pc.get_active_proxy_url() is None
    store.assert_not_called()


@pytest.mark.asyncio
async def test_read_url_exposes_route_failure_before_network_or_remote_recovery(monkeypatch, proxy_config):
    ru = importlib.import_module('kazma_core.tools.read_url')
    data, _, _ = proxy_config
    data['password'] = ''
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    client = Mock(side_effect=AssertionError('No HTTP client after route failure'))
    monkeypatch.setattr('httpx.AsyncClient', client)
    ru.clear_url_cache()
    try:
        result = await ru.fetch_result('https://docs.example/proxy-failure')
        assert not result.ok and result.error_code == 'proxy_routing_error'
        assert not result.attempts
        assert 'fixture-password' not in result.legacy_text()
        client.assert_not_called()
    finally:
        ru.clear_url_cache()


def test_readiness_fails_visibly_for_a_broken_selected_proxy(proxy_config):
    from kazma_core.tools.research_readiness import research_readiness

    data, _, _ = proxy_config
    data['password'] = ''
    result = research_readiness()
    check = next(item for item in result['checks'] if item['id'] == 'proxy')
    assert not result['ready'] and not check['ok'] and check['level'] == 'error'


def test_route_error_traceback_does_not_expose_config_exception_secrets(monkeypatch):
    monkeypatch.setattr('kazma_core.config_store.get_config_store', Mock(side_effect=RuntimeError('fixture-password')))
    with pytest.raises(AcquisitionError) as failure:
        pc.get_active_proxy_url()
    rendered = ''.join(traceback.format_exception(failure.value))
    assert 'fixture-password' not in rendered and 'RuntimeError' not in rendered


def test_config_change_during_resolution_applies_on_next_call_only(monkeypatch, proxy_config):
    data, reads, _ = proxy_config
    provider = registry.get_proxy_provider()
    original = provider.is_configured
    def change_live():
        data['username'] = 'next-account'
        data['password'] = 'next-secret'
        return original()
    monkeypatch.setattr(provider, 'is_configured', change_live)
    reads.clear()
    first = pc.get_active_proxy_url()
    second = pc.get_active_proxy_url()
    assert 'fixture-user' in first and 'fixture-password' in first
    assert 'next-account' not in first and 'next-secret' not in first
    assert 'next-account' in second and 'next-secret' in second
    assert reads == ['proxy', 'proxy']


def test_concurrent_accounts_keep_their_own_config_snapshot(monkeypatch):
    from kazma_core.proxy.config import proxy_config_scope

    current = threading.local()
    barrier = threading.Barrier(2, timeout=3)
    def get(key, default=None):
        assert key == 'proxy'
        config = {'provider': 'anyip', 'username': current.account, 'password': current.account + '-secret'}
        barrier.wait()
        return config
    monkeypatch.setattr('kazma_core.config_store.get_config_store', lambda: SimpleNamespace(get=get))
    def resolve(account):
        current.account = account
        with proxy_config_scope():
            return pc.get_active_proxy_url()
    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = executor.map(resolve, ['first-account', 'second-account'])
    assert 'first-account' in one and 'second-account' not in one
    assert 'second-account' in two and 'first-account' not in two


def test_missing_proxy_dependency_is_a_route_failure(monkeypatch):
    construct = Mock(side_effect=ImportError('SOCKS dependency missing'))
    monkeypatch.setattr('httpx.AsyncClient', construct)
    with pytest.raises(AcquisitionError) as failure:
        pc.get_scraping_client(proxy='socks5://proxy.example:1080')
    assert failure.value.code == 'proxy_routing_error'


@pytest.mark.asyncio
async def test_search_cannot_construct_ddgs_or_continue_after_route_failure(monkeypatch, proxy_config):
    ws = importlib.import_module('kazma_core.tools.web_search')
    data, _, _ = proxy_config
    data['password'] = ''
    ddgs = Mock(side_effect=AssertionError('DDGS cannot be constructed after a route failure'))
    monkeypatch.setattr('duckduckgo_search.DDGS', ddgs)
    monkeypatch.setattr(ws, '_searxng_search', lambda *a: (None, 'local service unavailable'))
    bing = Mock(side_effect=AssertionError('Search cannot continue after a route refusal'))
    monkeypatch.setattr(ws, '_bing_search', bing)
    result = await ws.web_search('offline route test')
    assert result.startswith('Error: proxy_routing_error')
    ddgs.assert_not_called()
    bing.assert_not_called()


@pytest.mark.asyncio
async def test_settings_proxy_test_resolves_once_off_loop_and_probes_exact_route(monkeypatch, proxy_config):
    from kazma_core.settings_manager import SettingsManager
    from kazma_core.proxy import base

    _, reads, store = proxy_config
    main_thread = threading.get_ident()
    original = store.get
    def off_loop_get(*args):
        assert threading.get_ident() != main_thread
        return original(*args)
    store.get = off_loop_get
    routes = []
    async def probe(url):
        routes.append(url)
        return {'success': True, 'exit_ip': '93.184.216.34'}
    monkeypatch.setattr(base, 'probe_proxy', probe)
    result = await SettingsManager.test_proxy(object())
    assert result['success'] and 'fixture-user' in routes[0]
    assert reads == ['proxy']


@pytest.mark.asyncio
@pytest.mark.parametrize('body, success', [('93.184.216.34', True), ('not an IP', False), ('x' * 200, False)])
async def test_proxy_health_response_is_bounded_and_validated(monkeypatch, body, success):
    from kazma_core.proxy.base import probe_proxy

    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['proxy'] == 'http://proxy.example:1080' and not kwargs['follow_redirects']
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    monkeypatch.setattr(pc, 'get_scraping_client', client)
    result = await probe_proxy('http://proxy.example:1080')
    assert result['success'] == success
    if success:
        assert result['exit_ip'] == body
    else:
        assert body not in result['error']


@pytest.mark.asyncio
async def test_proxy_probe_and_settings_test_cannot_escape_local_only(monkeypatch):
    from kazma_core.proxy.base import probe_proxy
    from kazma_core.settings_manager import SettingsManager

    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    construct = Mock(side_effect=AssertionError('No paid health call or credentials in local-only mode'))
    monkeypatch.setattr('kazma_core.config_store.get_config_store', construct)
    monkeypatch.setattr('httpx.AsyncClient', construct)
    assert not (await probe_proxy('http://proxy.example:1080'))['success']
    assert not (await SettingsManager.test_proxy(object()))['success']
    construct.assert_not_called()


@pytest.mark.parametrize('url, local', [
    ('http://localhost:8088', True), ('http://[::1]:8088', True),
    ('http://searxng:8080', True), ('https://localhost.evil.example', False),
    ('https://evil.example/localhost', False), ('https://searxng:password@remote.example', False),
])
def test_local_searxng_exception_checks_host_identity(url, local):
    ws = importlib.import_module('kazma_core.tools.web_search')
    assert ws._is_loopback_base(url) == local


def test_ipv6_proxy_host_is_bracketed_in_http_and_browser_routes(proxy_config):
    data, _, _ = proxy_config
    data['host'] = '::1'
    url = pc.get_active_proxy_url()
    assert '@[::1]:1080' in url
    assert pc.playwright_proxy()['server'] == 'http://[::1]:1080'


@pytest.mark.asyncio
async def test_proxy_transport_error_cannot_trigger_browser_or_hosted_recovery(monkeypatch, proxy_config):
    from unittest.mock import AsyncMock
    from kazma_core.web_acquire.trace import classify_failure

    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    original = httpx.AsyncClient
    def failed(request):
        raise httpx.ProxyError('Proxy tunnel refused', request=request)
    monkeypatch.setattr(pc, 'get_scraping_client', lambda **kwargs:
                        original(transport=httpx.MockTransport(failed)))
    recover = AsyncMock(side_effect=AssertionError('Route failure cannot change to another route'))
    monkeypatch.setattr(ru, '_recover_hard_page', recover)
    ru.clear_url_cache()
    try:
        result = await ru.fetch_result('https://docs.example/proxy-tunnel-failure')
        assert not result.ok and result.error_code == 'proxy_routing_error' and result.transient
        assert result.attempts[-1].remote and result.attempts[-1].error_code == 'proxy_routing_error'
        assert classify_failure(httpx.ProxyError('refused')) == ('proxy_routing_error', True)
        recover.assert_not_awaited()
    finally:
        ru.clear_url_cache()


def test_real_store_category_snapshot_and_atomic_account_change(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore

    store = ConfigStore(tmp_path / 'proxy-config.db')
    monkeypatch.setattr('kazma_core.config_store._try_get_vault', lambda: None)
    monkeypatch.setattr('kazma_core.config_store.get_config_store', lambda: store)
    try:
        store.batch_set([('proxy.provider', 'anyip', 'proxy'), ('proxy.username', 'account-one', 'proxy'),
                         ('proxy.password', 'secret-one', 'proxy')])
        assert 'account-one' in pc.get_active_proxy_url()
        store.batch_set([('proxy.username', 'account-two', 'proxy'), ('proxy.password', 'secret-two', 'proxy')])
        url = pc.get_active_proxy_url()
        assert 'account-two' in url and 'secret-two' in url and 'account-one' not in url
        store.set('proxy.password', 'vault://unavailable', category='proxy')
        with pytest.raises(AcquisitionError):
            pc.get_active_proxy_url()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_proxy_probe_deadline_closes_stream_and_releases_capacity(monkeypatch):
    from kazma_core.proxy.base import probe_proxy
    from kazma_core.web_acquire import resources

    original_client, original_timeout = httpx.AsyncClient, asyncio.timeout
    pool = resources._CapacityPool('fixture proxy probes', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    closed = asyncio.Event()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b'unreachable'
        async def aclose(self):
            closed.set()
    monkeypatch.setattr(pc, 'get_scraping_client', lambda **kwargs:
                        original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowStream()))))
    def deadline(seconds):
        assert seconds == 15
        return original_timeout(0.01)
    monkeypatch.setattr(asyncio, 'timeout', deadline)
    result = await probe_proxy('http://proxy.example:1080')
    assert not result['success'] and result['error_code'] == 'acquisition_deadline'
    assert closed.is_set() and pool.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_proxy_probe_capacity_refusal_precedes_client_creation(monkeypatch):
    from kazma_core.proxy.base import probe_proxy
    from kazma_core.web_acquire import resources

    pool = resources._CapacityPool('fixture proxy probes', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    client = Mock(side_effect=AssertionError('No proxy probe dispatch while saturated'))
    monkeypatch.setattr(pc, 'get_scraping_client', client)
    held = await pool.acquire()
    try:
        result = await probe_proxy('http://proxy.example:1080')
        assert not result['success'] and result['error_code'] == 'resource_limit'
        client.assert_not_called()
    finally:
        held.release()
