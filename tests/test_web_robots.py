"""Real bounded HTTP robots loader, typed ladder and parser contract fixtures."""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from kazma_core.web_acquire import robots
from kazma_core.web_acquire.http import read_capped
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture
def robot_http(monkeypatch):
    """Replace the generic missing-policy fixture with the actual HTTP loader."""
    manager = robots._RobotsManager()
    importlib.import_module('kazma_core.tools.read_url').clear_url_cache()
    monkeypatch.setattr(robots, '_MANAGER', manager)
    monkeypatch.setattr('kazma_core.web_acquire.http.validate_url', lambda *a, **k: ('93.184.216.34',))
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    original = httpx.AsyncClient
    calls = []
    def install(handler):
        def respond(request):
            calls.append(request)
            result = handler(request)
            result.extensions['network_stream'] = SimpleNamespace(get_extra_info=lambda name: ('93.184.216.34', 443))
            return result
        monkeypatch.setattr('kazma_core.proxy.client.get_scraping_client',
                            lambda **kwargs: original(transport=httpx.MockTransport(respond)))
    return manager, install, calls


@pytest.mark.parametrize('rules, path, allowed', [
    ('User-agent: *\nDisallow: /private\nAllow: /private/public', '/private/public/a', True),
    ('User-agent: *\nAllow: /same\nDisallow: /same', '/same', True),
    ('User-agent: *\nDisallow: /Case', '/case', True),
    ('User-agent: *\nDisallow: /*.pdf$', '/x.pdf', False),
    ('User-agent: *\nDisallow: /*.pdf$', '/x.pdf?download=1', True),
    ('User-agent: *\nDisallow: /search?q=secret', '/search?q=secret', False),
    ('User-agent: *\nDisallow: /caf%C3%A9', '/café', False),
    ('User-agent: *\nDisallow: /café', '/caf%c3%a9', False),
    ('User-agent: *\nDisallow: /%70rivate', '/private', False),
    ('User-agent: *\nDisallow: /a%2Fb', '/a/b', True),
    ('User-agent: *\nDisallow: /a%2Fb', '/a%2fb', False),
    ('User-agent: *\nDisallow: /file%2A.html', '/file*.html', False),
    ('User-agent: *\nDisallow: /cost%24', '/cost$', False),
    ('User-agent: *\nDisallow:', '/any', True),
    ('Disallow: /\nUser-agent: *\nAllow: /', '/any', True),
    ('User-agent: *\nDisallow: /\nUser-agent: KAZMABOT\nAllow: /', '/any', True),
    ('User-agent: KazmaBot\nDisallow: /a\nUser-agent: KazmaBot\nDisallow: /b', '/b', False),
    ('User-agent: *\nSitemap: https://x.example/s.xml\nDisallow: /', '/any', False),
    ('User-agent: *\nDisallow: /private\nAllow: *private/public', '/private/public', False),
])
def test_rule_matching(rules, path, allowed):
    assert robots._parse(rules).allows('https://docs.example' + path) == allowed


@pytest.mark.asyncio
@pytest.mark.parametrize('status, code', [(404, None), (410, None), (401, 'robots_disallowed'),
    (403, 'robots_disallowed'), (429, 'robots_unavailable'), (500, 'robots_unavailable'), (503, 'robots_unavailable')])
async def test_missing_denied_and_failed_checks_are_distinct(robot_http, status, code):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(status, text='control response'))
    with robots.crawl_policy():
        if code:
            with pytest.raises(AcquisitionError) as failure:
                await robots.require_robots('https://docs.example/article')
            assert failure.value.code == code
        else:
            await robots.require_robots('https://docs.example/article')
        evidence = robots.robots_evidence()
        assert evidence['checks'][0]['http_status'] == status
        assert evidence['checks'][0]['status'] == ('missing' if status in (404, 410) else 'denied' if status in (401, 403) else 'unavailable')
    assert len(calls) == 1 and calls[0].url.path == '/robots.txt'


@pytest.mark.asyncio
async def test_crawl_default_denies_before_content_or_alternate_backend(robot_http, monkeypatch):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(200, text='User-agent: KazmaBot\nDisallow: /private'))
    ru = importlib.import_module('kazma_core.tools.read_url')
    recovery = AsyncMock(side_effect=AssertionError('No recovery over robots denial'))
    monkeypatch.setattr(ru, '_recover_hard_page', recovery)
    result = await ru.fetch_result('https://docs.example/private', purpose='crawl')
    assert not result.ok and result.error_code == 'robots_disallowed'
    assert result.evidence()['robots']['mode'] == 'enforced'
    assert result.evidence()['robots']['checks'][0]['metadata_requests'] == 1
    assert [request.url.path for request in calls] == ['/robots.txt']
    recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_content_redirect_cannot_enter_a_disallowed_path(robot_http, monkeypatch):
    _, install, calls = robot_http
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nDisallow: /private')
        return httpx.Response(302, headers={'Location': '/private'})
    install(handler)
    ru = importlib.import_module('kazma_core.tools.read_url')
    result = await ru.fetch_result('https://docs.example/public', purpose='crawl')
    assert not result.ok and result.error_code == 'robots_disallowed'
    assert [request.url.path for request in calls] == ['/robots.txt', '/public']
    assert all('KazmaBot' in request.headers['user-agent'] for request in calls)


@pytest.mark.asyncio
async def test_explicit_optout_is_visible_and_does_not_load_rules(robot_http):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(200, headers={'content-type': 'application/json'}, json={'answer': 1}))
    ru = importlib.import_module('kazma_core.tools.read_url')
    with robots.crawl_policy(respect=False):
        result = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
    assert result.ok and result.evidence()['robots']['mode'] == 'disabled_by_operator'
    assert 'robots disabled by operator' in result.notice()
    assert [request.url.path for request in calls] == ['/data.json']


@pytest.mark.asyncio
@pytest.mark.parametrize('body, headers', [
    (b'<html>Challenge</html>', {}), (b'User-agent: *\x00\nAllow: /', {}),
    (b'\xff\xfe', {}), (b'User-agent: *\nAllow: /', {'content-type': 'text/html'}),
    (b'x' * (500 * 1024 + 1), {}),
], ids=['html', 'nul', 'invalid-utf8', 'html-type', 'oversized'])
async def test_malformed_or_clipped_policy_does_not_grant_permission(robot_http, body, headers):
    _, install, _ = robot_http
    install(lambda request: httpx.Response(200, content=body, headers=headers))
    with robots.crawl_policy():
        with pytest.raises(AcquisitionError) as failure:
            await robots.require_robots('https://docs.example/article')
    assert failure.value.code == 'robots_unavailable'


@pytest.mark.asyncio
async def test_robots_cross_origin_redirect_is_refused_without_contacting_destination(robot_http):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(302, headers={'Location': 'https://other.example/rules'}))
    with robots.crawl_policy():
        with pytest.raises(AcquisitionError) as failure:
            await robots.require_robots('https://docs.example/article')
    assert failure.value.code == 'robots_unavailable'
    assert len(calls) == 1 and calls[0].url.host == 'docs.example'


@pytest.mark.asyncio
async def test_scope_path_does_not_prevent_same_origin_control_fetch(robot_http):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(404) if request.url.path == '/robots.txt' else
            httpx.Response(200, headers={'content-type': 'application/json'}, json={'answer': 1}))
    ru = importlib.import_module('kazma_core.tools.read_url')
    result = await ru.fetch_result('https://docs.example/docs/page', purpose='crawl',
                                   scope_check=lambda url: url.startswith('https://docs.example/docs/'))
    assert result.ok, result.evidence()
    assert [request.url.path for request in calls] == ['/robots.txt', '/docs/page']


@pytest.mark.asyncio
async def test_cache_is_checked_before_returning_previously_allowed_content(robot_http, monkeypatch):
    manager, install, calls = robot_http
    install(lambda request: httpx.Response(200, text='User-agent: *\nDisallow: /'))
    ru = importlib.import_module('kazma_core.tools.read_url')
    cached = Mock(side_effect=AssertionError('Page cache cannot bypass current robots policy'))
    monkeypatch.setattr(ru, '_cache_result_get', cached)
    result = await ru.fetch_result('https://docs.example/article', purpose='kb')
    assert not result.ok and result.error_code == 'robots_disallowed'
    cached.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_owner_unblocks_shared_waiters_and_does_not_cache_permission(monkeypatch):
    manager = robots._RobotsManager()
    entered = asyncio.Event()
    closed = asyncio.Event()
    async def hanging(url, proxy):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(manager, '_fetch', hanging)
    owner = asyncio.create_task(manager.get('https://docs.example/robots.txt', None))
    await asyncio.wait_for(entered.wait(), 2)
    waiter = asyncio.create_task(manager.get('https://docs.example/robots.txt', None))
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    result, cached = await asyncio.wait_for(waiter, 2)
    assert result.status == 'unavailable' and closed.is_set()
    assert not manager._pending and not manager._cache


@pytest.mark.asyncio
async def test_invalid_environment_is_a_typed_policy_failure(robot_http, monkeypatch):
    monkeypatch.setenv('KAZMA_CRAWL_RESPECT_ROBOTS', 'maybe')
    ru = importlib.import_module('kazma_core.tools.read_url')
    result = await ru.fetch_result('https://docs.example/article', purpose='crawl')
    assert not result.ok and result.error_code == 'policy_error'


@pytest.mark.asyncio
async def test_browser_policy_refuses_hosted_navigation_even_with_allowlisted_service(monkeypatch):
    ru = importlib.import_module('kazma_core.tools.read_url')
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-only')
    client = Mock(side_effect=AssertionError('Cannot enforce each hosted navigation'))
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    with robots.crawl_policy(respect=True):
        assert await ru._try_jina_reader('https://docs.example/page') is None
        assert await ru._try_firecrawl('https://docs.example/page') is None
    client.assert_not_called()


@pytest.mark.asyncio
async def test_manual_and_crawler_cache_entries_do_not_share_user_agent_content(robot_http):
    _, install, calls = robot_http
    def respond(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(200, headers={'content-type': 'application/json'},
                              json={'agent': request.headers['user-agent']})
    install(respond)
    ru = importlib.import_module('kazma_core.tools.read_url')
    manual = await ru.fetch_result('https://docs.example/data.json')
    crawl = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
    repeated = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
    assert manual.ok and crawl.ok and repeated.ok
    assert 'KazmaBot' not in manual.text and 'KazmaBot' in crawl.text
    assert not crawl.cache_hit and repeated.cache_hit
    assert [request.url.path for request in calls] == ['/data.json', '/robots.txt', '/data.json']


@pytest.mark.asyncio
async def test_cached_receipt_does_not_claim_a_previous_pages_metadata_request(robot_http):
    _, install, _ = robot_http
    install(lambda request: httpx.Response(404) if request.url.path == '/robots.txt' else
            httpx.Response(200, headers={'content-type': 'application/json'}, json={'value': 1}))
    ru = importlib.import_module('kazma_core.tools.read_url')
    with robots.crawl_policy():
        first = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
        second = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
    assert first.evidence()['robots']['checks'][0]['metadata_requests'] == 1
    assert second.cache_hit and second.evidence()['robots']['checks'][0]['metadata_requests'] == 0
    assert 'no new acquisition requests' in second.notice()


@pytest.mark.asyncio
async def test_metadata_can_finish_when_all_content_acquisition_slots_are_held(robot_http):
    from contextlib import AsyncExitStack
    from kazma_core.web_acquire import resources

    _, install, calls = robot_http
    install(lambda request: httpx.Response(404))
    async with AsyncExitStack() as stack:
        pool = resources.acquisition_capacity()
        for _ in range(pool.capacity):
            await stack.enter_async_context(await pool.acquire())
        with robots.crawl_policy():
            await asyncio.wait_for(robots.require_robots('https://docs.example/page'), 2)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_policy_cache_expires_and_changed_rules_block_cached_content(robot_http, monkeypatch):
    _, install, calls = robot_http
    clock = [100.0]
    monkeypatch.setattr(robots, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def respond(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\n' + ('Allow: /' if clock[0] < 3600 else 'Disallow: /'))
        return httpx.Response(200, headers={'content-type': 'application/json'}, json={'value': 1})
    install(respond)
    ru = importlib.import_module('kazma_core.tools.read_url')
    assert (await ru.fetch_result('https://docs.example/data.json', purpose='crawl')).ok
    clock[0] += 3601
    refused = await ru.fetch_result('https://docs.example/data.json', purpose='crawl')
    assert refused.error_code == 'robots_disallowed' and not refused.ok
    assert [request.url.path for request in calls] == ['/robots.txt', '/data.json', '/robots.txt']


@pytest.mark.asyncio
async def test_metadata_failures_are_secret_safe_and_not_mistaken_for_missing(monkeypatch):
    manager = robots._RobotsManager()
    async def failed(url, proxy):
        raise httpx.ConnectError('proxy password fixture-secret token abc')
    monkeypatch.setattr(manager, '_fetch', failed)
    monkeypatch.setattr(robots, '_MANAGER', manager)
    with robots.crawl_policy():
        with pytest.raises(AcquisitionError) as failure:
            await robots.require_robots('https://docs.example/page')
        assert failure.value.code == 'robots_unavailable'
        assert 'fixture-secret' not in str(failure.value)
        assert robots.robots_evidence()['checks'][0]['status'] == 'unavailable'


@pytest.mark.asyncio
async def test_kb_progress_does_not_leak_or_reload_job_policy(monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki
    contexts = []
    async def discover(*args, **kwargs):
        contexts.append(robots.active_robots.get())
        return []
    monkeypatch.setattr(ki, 'kb_discover_pages', discover)
    monkeypatch.setattr('kazma_core.security.ssrf.validate_url', lambda *a, **k: ('93.184.216.34',))
    job = ki.ingest_site('fixture', 'https://docs.example/docs/')
    first = await anext(job)
    assert first.phase == 'discover' and robots.active_robots.get() is None
    monkeypatch.setenv('KAZMA_CRAWL_RESPECT_ROBOTS', '0')
    second = await anext(job)
    assert second.phase == 'discover' and robots.active_robots.get() is None
    assert contexts and contexts[0].enforce() is True
    await job.aclose()
    assert robots.active_robots.get() is None


@pytest.mark.asyncio
async def test_invalid_kb_policy_is_visible_progress_and_never_starts_discovery(monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki
    monkeypatch.setenv('KAZMA_CRAWL_RESPECT_ROBOTS', 'invalid')
    discover = AsyncMock(side_effect=AssertionError('Invalid policy must fail before discovery'))
    monkeypatch.setattr(ki, 'kb_discover_pages', discover)
    updates = [update async for update in ki.ingest_site('fixture', 'https://docs.example/')]
    assert len(updates) == 1 and updates[0].phase == 'error' and 'policy' in updates[0].message
    discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_metadata_singleflight_and_cancelled_waiter_leave_owner_usable(monkeypatch):
    from kazma_core.web_acquire.http import HttpFetch
    manager = robots._RobotsManager()
    entered, release = asyncio.Event(), asyncio.Event()
    loads = []
    async def fetch(url, proxy):
        loads.append(url)
        entered.set()
        await release.wait()
        return HttpFetch(httpx.Response(404, request=httpx.Request('GET', url)), '', url, False)
    monkeypatch.setattr(manager, '_fetch', fetch)
    owner = asyncio.create_task(manager.get('https://docs.example/robots.txt', None))
    await entered.wait()
    waiters = [asyncio.create_task(manager.get('https://docs.example/robots.txt', None)) for _ in range(32)]
    try:
        await asyncio.sleep(0)
        with pytest.raises(AcquisitionError, match='waiter limit'):
            await manager.get('https://docs.example/robots.txt', None)
        waiters[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiters[0]
        release.set()
        assert (await owner)[0].status == 'missing'
        assert all(value.status == 'missing' for value, cached in await asyncio.gather(*waiters[1:]))
        assert len(loads) == 1 and not manager._pending
    finally:
        release.set()
        for task in [owner, *waiters]:
            if not task.done():
                task.cancel()
        await asyncio.gather(owner, *waiters, return_exceptions=True)


@pytest.mark.asyncio
async def test_changed_proxy_route_never_reuses_another_routes_permission(monkeypatch):
    from kazma_core.web_acquire.http import HttpFetch
    manager = robots._RobotsManager()
    loads = []
    async def fetch(url, proxy):
        loads.append(proxy)
        text = 'User-agent: *\n' + ('Disallow: /' if proxy else 'Allow: /')
        return HttpFetch(httpx.Response(200, content=text.encode(), request=httpx.Request('GET', url)), text, url, False)
    monkeypatch.setattr(manager, '_fetch', fetch)
    url = 'https://docs.example/robots.txt'
    direct, cached = await manager.get(url, None)
    changed, cached_changed = await manager.get(url, 'http://user:fixture@proxy.example:80')
    assert direct.allows(url + '/page') and not changed.allows(url + '/page')
    assert not cached and not cached_changed and len(loads) == 2
    assert (await manager.get(url, None))[1]


@pytest.mark.asyncio
async def test_job_origin_budget_refuses_metadata_before_dispatch(robot_http):
    _, install, calls = robot_http
    install(lambda request: httpx.Response(404))
    with robots.crawl_policy() as context:
        context.origins.update(f'https://origin{i}.example:443' for i in range(64))
        with pytest.raises(AcquisitionError, match='origin limit'):
            await robots.require_robots('https://extra.example/page')
    assert calls == []


@pytest.mark.asyncio
async def test_crawl_delay_applies_to_shared_origin_dispatch(robot_http):
    from kazma_core.web_acquire.origins import origin_delay, origin_scheduler
    _, install, calls = robot_http
    install(lambda request: httpx.Response(200, text='User-agent: *\nCrawl-delay: 120\nAllow: /'))
    token = origin_delay.set(0)
    try:
        with robots.crawl_policy():
            await robots.require_robots('https://docs.example/page')
            assert origin_delay.get() == 120
            async with origin_scheduler().request('https://docs.example/page'):
                pass
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    async with origin_scheduler().request('https://docs.example/next'):
                        pytest.fail('Crawl-delay was shortened')
    finally:
        origin_delay.reset(token)
    assert len(calls) == 1
    assert origin_scheduler().snapshot()['active'] == 0
