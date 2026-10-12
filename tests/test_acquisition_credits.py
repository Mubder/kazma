"""Provider evidence is separate from request allowances and never silently refreshed."""
import asyncio

import httpx
import pytest

from kazma_core import config_store
from kazma_core.web_acquire import accounting, credits
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture
def service(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'firecrawl')
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-key-one')
    monkeypatch.delenv('KAZMA_FIRECRAWL_URL', raising=False)
    original = httpx.AsyncClient
    sent = []
    def install(handler):
        async def transport(request):
            sent.append(request)
            result = handler(request)
            if asyncio.iscoroutine(result):
                return await result
            return result
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
            transport=httpx.MockTransport(transport), event_hooks=kwargs['event_hooks'], trust_env=False))
    yield install, sent, store
    store.close()


@pytest.mark.asyncio
async def test_zero_balance_is_observed_with_scope_and_cached_without_provider_poll(service, monkeypatch):
    install, sent, _ = service
    install(lambda request: httpx.Response(200, json={'success': True, 'data': {'remainingCredits': 0}}))
    assert credits.snapshot()[0]['status'] == 'not_checked' and not sent
    result = await credits.refresh_firecrawl_balance()
    assert result[0]['remaining'] == 0 and result[0]['fresh'] is True
    assert result[0]['scope'] == 'authenticated_team_via_credential' and result[0]['observed_at']
    assert str(sent[0].url) == 'https://api.firecrawl.dev/v2/team/credit-usage'
    assert sent[0].headers['Authorization'] == 'Bearer fixture-key-one'
    for _ in range(5):
        assert credits.snapshot()[0]['remaining'] == 0
        assert (await credits.refresh_firecrawl_balance())[0]['remaining'] == 0
    assert len(sent) == 1 and accounting.snapshot()['day_reserved'] == 1
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-key-two')
    assert credits.snapshot()[0]['remaining'] is None
    assert credits.snapshot()[0]['status'] == 'not_checked'


@pytest.mark.asyncio
@pytest.mark.parametrize('body,status', [
    ({'success': True, 'data': {'remainingCredits': True}}, 200),
    ({'success': True, 'data': {'remainingCredits': float('nan')}}, 200),
    ({'success': True, 'data': {'remaining_credits': 123}}, 200),
    ({'success': False, 'error': 'fixture-key-one'}, 402),
])
async def test_invalid_or_failed_balance_is_unknown_and_never_a_guessed_zero(service, body, status):
    import json
    install, _, _ = service
    install(lambda request: httpx.Response(status, content=json.dumps(body), headers={'Content-Type': 'application/json'}))
    result = (await credits.refresh_firecrawl_balance())[0]
    assert result['status'] == 'unavailable' and result['remaining'] is None and not result['fresh']
    assert 'fixture-key-one' not in str(result)
    assert accounting.snapshot()['day_reserved'] == 1


@pytest.mark.asyncio
async def test_balance_freshness_expires_without_making_a_provider_call(service, monkeypatch):
    install, sent, _ = service
    install(lambda request: httpx.Response(200, json={'success': True, 'data': {'remainingCredits': 17}}))
    result = (await credits.refresh_firecrawl_balance())[0]
    now = result['observed_at']
    monkeypatch.setattr(credits.time, 'time', lambda: now + 301)
    stale = credits.snapshot()[0]
    assert stale['status'] == 'stale' and not stale['fresh']
    assert stale['remaining'] == 17 and len(sent) == 1


@pytest.mark.asyncio
async def test_concurrent_refresh_uses_one_durable_claim(service):
    install, sent, _ = service
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={'success': True, 'data': {'remainingCredits': 17}})
    install(blocked)
    first = asyncio.create_task(credits.refresh_firecrawl_balance())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        second = await credits.refresh_firecrawl_balance()
        assert second[0]['status'] == 'refreshing' and len(sent) == 1
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_local_only_never_reads_account_store_or_dispatches_provider(service, monkeypatch):
    install, sent, _ = service
    install(lambda request: pytest.fail('No service call in local-only mode'))
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    monkeypatch.setattr(config_store, 'get_config_store', lambda: pytest.fail('No ledger lookup for disabled provider'))
    assert credits.snapshot()[0]['status'] == 'disabled'
    with pytest.raises(AcquisitionError, match='disabled'):
        await credits.refresh_firecrawl_balance()
    assert not sent


@pytest.mark.asyncio
async def test_cancelled_refresh_keeps_allowance_and_unknown_cache(service):
    install, _, _ = service
    entered = asyncio.Event()
    async def blocked(request):
        entered.set()
        await asyncio.Future()
    install(blocked)
    task = asyncio.create_task(credits.refresh_firecrawl_balance())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert accounting.snapshot()['day_reserved'] == 1
    assert accounting.snapshot()['recent'][0]['state'] == 'unknown'
    assert credits.snapshot()[0]['remaining'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status,hint', [(401, 'API key'), (402, 'account credits'), (429, 'wait'), (503, 'retry later')])
async def test_failed_credit_refresh_exposes_safe_actionable_http_error_on_all_status_surfaces(service, status, hint):
    from kazma_core.web_acquire.operations import format_cached_status

    install, sent, _ = service
    install(lambda request: httpx.Response(status, text='secret fixture-key-one provider body'))
    result = (await credits.refresh_firecrawl_balance())[0]
    assert result['remaining'] is None and not result['fresh']
    assert f'HTTP {status}' in result['error'] and hint in result['error']
    assert 'fixture-key-one' not in result['error'] and 'provider body' not in result['error']
    for language in ('en', 'ar'):
        text = format_cached_status({'acquisition': {'provider_balances': [result]}}, language=language)
        assert result['error'] in text
        assert ('آخر خطأ في الجلب' if language == 'ar' else 'Last acquisition error') in text
    assert len(sent) == 1 and accounting.snapshot()['day_reserved'] == 1
