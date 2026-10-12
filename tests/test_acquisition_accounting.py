"""Remote effects cannot precede finite durable allowance reservations."""
import asyncio
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from kazma_core import config_store
from kazma_core.web_acquire import accounting as ledger
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: settings)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy,jina,firecrawl')
    yield settings
    settings.close()


def account():
    return ledger.account_fingerprint('proxy', 'http://fixture-user:fixture-secret@proxy.example:1080')


def test_concurrent_first_writers_cannot_overspend_utc_day(store, monkeypatch):
    monkeypatch.setenv('KAZMA_REMOTE_DAY_REQUESTS', '7')
    def attempt(i):
        try:
            with ledger.remote_scope(str(i)):
                return ledger.reserve('proxy', account())
        except AcquisitionError as error:
            assert error.code == 'resource_limit'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(attempt, range(24)))
    assert sum(value is not None for value in answers) == 7
    evidence = ledger.snapshot()
    assert evidence['day_reserved'] == 7
    assert len(evidence['recent']) == 7
    assert all(row['state'] == 'unknown' for row in evidence['recent'])


def test_separate_processes_cannot_reset_the_installation_day_allowance(store, tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from kazma_core.security.process_budget import run_bounded

    monkeypatch.setenv('KAZMA_REMOTE_DAY_REQUESTS', '2')
    core = str(Path(ledger.__file__).resolve().parents[2])
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    env.update(KAZMA_ACQUISITION_MODE='local_first_with_remote_fallback',
        KAZMA_ACQUISITION_REMOTE_PROVIDERS='proxy', KAZMA_REMOTE_DAY_REQUESTS='2')
    def launch(index):
        program = (f'import sys,json;sys.path.insert(0,{core!r});'
            'from kazma_core import config_store;from kazma_core.web_acquire import accounting;'
            'from kazma_core.web_acquire.result import AcquisitionError;'
            f'store=config_store.ConfigStore({str(tmp_path / "settings.db")!r});config_store.get_config_store=lambda:store\n'
            'try:\n'
            f' accounting.reserve("proxy",accounting.account_fingerprint("proxy","fixture"));outcome="reserved-{index}"\n'
            'except AcquisitionError as error:outcome=str(error.code)\n'
            'store.close();print(json.dumps({"outcome":outcome}))')
        return run_bounded([sys.executable, '-I', '-c', program], env=env, timeout=15,
            output_bytes=32768, memory_bytes=512 * 1024**2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        children = list(pool.map(launch, range(4)))
    assert all(child.returncode == 0 for child in children), [child.stderr for child in children]
    outcomes = [json.loads(child.stdout.decode().splitlines()[-1])['outcome'] for child in children]
    assert sum(value.startswith('reserved-') for value in outcomes) == 2
    assert outcomes.count('resource_limit') == 2
    assert ledger.snapshot()['day_reserved'] == 2


def test_nested_jobs_retry_redirect_failure_and_cancel_never_refund(store, monkeypatch):
    monkeypatch.setenv('KAZMA_REMOTE_JOB_REQUESTS', '3')
    with ledger.remote_scope('outer'):
        reservation = ledger.reserve('proxy', account())
        ledger._response_received(reservation, 429)
        with ledger.remote_scope('child-cannot-reset'):
            ledger.reserve('firecrawl', account())
        ledger.reserve('jina', account())
        with pytest.raises(AcquisitionError, match='allowance exhausted'):
            ledger.reserve('proxy', account())
    evidence = ledger.snapshot('outer')
    assert evidence['job_reserved'] == 3
    assert [row['http_status'] for row in evidence['recent']] == [429, None, None]
    assert evidence['provider_credits'] is None and evidence['provider_usage'] is None
    assert 'fixture-secret' not in json.dumps(evidence)


def test_active_allowance_cannot_widen_and_expired_scope_cannot_reset(store, monkeypatch):
    monkeypatch.setenv('KAZMA_REMOTE_JOB_REQUESTS', '1')
    with ledger.remote_scope('fixed'):
        ledger.reserve('proxy', account())
        monkeypatch.setenv('KAZMA_REMOTE_JOB_REQUESTS', '100')
        with pytest.raises(AcquisitionError, match='allowance exhausted'):
            ledger.reserve('proxy', account())
    with ledger.remote_scope('expired', 1):
        with pytest.raises(AcquisitionError, match='expired'):
            ledger.reserve('proxy', account())

    # Original boundary raised before CAS, discarding a cap narrowed to zero.
    # Re-enable in the environment must not widen that already-active day.
    monkeypatch.setenv('KAZMA_REMOTE_DAY_REQUESTS', '0')
    with pytest.raises(AcquisitionError, match='allowance exhausted'):
        ledger.reserve('proxy', account())
    assert ledger.snapshot()['day_limit'] == 0
    monkeypatch.setenv('KAZMA_REMOTE_DAY_REQUESTS', '1024')
    with pytest.raises(AcquisitionError, match='allowance exhausted'):
        ledger.reserve('proxy', account())


@pytest.mark.asyncio
async def test_async_hooks_account_actual_redirects_and_refuse_before_transport(store, monkeypatch):
    monkeypatch.setenv('KAZMA_REMOTE_JOB_REQUESTS', '2')
    sent = []
    def transport(request):
        sent.append(str(request.url))
        assert ledger.snapshot('redirect')['job_reserved'] == len(sent)
        return httpx.Response(302, headers={'Location': '/next'})
    with ledger.remote_scope('redirect'):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True,
                event_hooks=ledger.httpx_hooks('proxy', 'fixture-secret', asynchronous=True)) as client:
            with pytest.raises(AcquisitionError, match='allowance exhausted'):
                await client.get('https://example.com/start')
    assert len(sent) == 2
    assert [row['http_status'] for row in ledger.snapshot('redirect')['recent']] == [302, 302]


@pytest.mark.asyncio
async def test_database_failure_refuses_remote_socket(store, monkeypatch):
    sent = []
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(store, 'batch_set', unavailable)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sent.append(request)),
            event_hooks=ledger.httpx_hooks('jina', 'fixture', asynchronous=True)) as client:
        with pytest.raises(AcquisitionError, match='ledger unavailable'):
            await client.get('https://r.jina.ai/https://example.com')
    assert not sent


@pytest.mark.asyncio
async def test_cancellation_keeps_reserved_request_unknown(store):
    entered = asyncio.Event()
    async def blocked(request):
        entered.set()
        await asyncio.Future()
    with ledger.remote_scope('cancelled'):
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked),
                event_hooks=ledger.httpx_hooks('proxy', 'fixture', asynchronous=True)) as client:
            task = asyncio.create_task(client.get('https://example.com'))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert ledger.snapshot('cancelled')['recent'][0]['state'] == 'unknown'
    assert ledger.snapshot('cancelled')['job_reserved'] == 1


def test_history_clipping_does_not_reset_allowance(store, monkeypatch):
    monkeypatch.setattr(ledger, '_MAX_RECENT', 2)
    with ledger.remote_scope('history'):
        for _ in range(3):
            ledger.reserve('proxy', account())
    evidence = ledger.snapshot('history')
    assert evidence['job_reserved'] == 3 and len(evidence['recent']) == 2


def test_fixed_http_worker_reserves_six_request_upper_bound_before_launch(store, monkeypatch):
    from kazma_core.web_acquire import search_http
    from kazma_core.security import process_budget

    monkeypatch.setenv('KAZMA_REMOTE_JOB_REQUESTS', '5')
    monkeypatch.setattr('kazma_core.proxy.client.get_active_proxy_url', lambda: 'http://fixture:secret@proxy.example:1080')
    launched = []
    monkeypatch.setattr(process_budget, 'run_bounded', lambda *args, **kwargs: launched.append(args))
    with ledger.remote_scope('search'):
        with pytest.raises(AcquisitionError, match='allowance exhausted'):
            search_http.run_http_search('bing', 'fixture', 2)
    assert not launched and ledger.snapshot()['day_reserved'] == 0


def test_missing_postgres_pool_cannot_report_an_empty_healthy_ledger(store, monkeypatch):
    monkeypatch.setattr(store, '_use_postgres', lambda: True)
    monkeypatch.setattr(store, '_pg_pool', lambda: None)
    with pytest.raises(AcquisitionError, match='ledger unavailable'):
        ledger.snapshot()


def test_sdk_refuses_unbounded_proxy_effect_before_child_launch(store, monkeypatch):
    from kazma_core.web_acquire import search_sdk
    from kazma_core.security import process_budget

    launched = []
    monkeypatch.setattr(process_budget, 'run_bounded', lambda *args, **kwargs: launched.append(args))
    with pytest.raises(AcquisitionError, match='internal request count'):
        search_sdk._run_ddg('fixture', 2, 'http://fixture:secret@proxy.example:1080')
    assert not launched
