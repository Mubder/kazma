"""Real journal, publication and process-death boundaries of research crawls."""
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from kazma_core import config_store
from kazma_core.tools import web_research as wr
from kazma_core.web_acquire import accounting, crawl_jobs, policy
from kazma_core.web_acquire.result import FetchResult
from kazma_core.security.process_budget import run_bounded_async

SEED = 'https://docs.example/'
ONE, TWO = SEED + 'one?q=1', SEED + 'two?q=2'
OPTIONS = dict(max_pages=3, max_depth=1, delay_ms=0, respect_robots=False)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setattr(wr, '_workspace_root', lambda: tmp_path / 'workspace')
    async def public(*args, **kwargs):
        return ('93.184.216.34',)
    monkeypatch.setattr('kazma_core.web_acquire.dns_worker.validate_public_url', public)
    yield store
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_actual_process_death_preserves_completed_and_uncertain_publication(journal, tmp_path, monkeypatch, published):
    # Crash the public crawl after seed completion and at the next actual
    # create_extract boundary. No fake journal or synthetic restored pages.
    core = str(Path(wr.__file__).resolve().parents[2])
    program = f'''
import sys,os,asyncio
os.environ['KAZMA_BROWSER_NETWORK_MODE']='compatibility'
from pathlib import Path
sys.path.insert(0,{core!r})
from kazma_core import config_store
from kazma_core.tools import web_research as wr, research_files
from kazma_core.web_acquire import dns_worker
from kazma_core.web_acquire.result import FetchResult
store=config_store.ConfigStore({str(tmp_path / 'settings.db')!r})
config_store.get_config_store=lambda:store
wr._workspace_root=lambda:Path({str(tmp_path / 'workspace')!r})
async def public(*args,**kwargs):return ('93.184.216.34',)
dns_worker.validate_public_url=public
async def fetch(url,**kwargs):
 return FetchResult(ok=True,url=url,text='private fixture body',links=({ONE!r},{TWO!r}) if url=={SEED!r} else (),links_complete=True)
wr._central_fetch=fetch
original=research_files.create_extract
def publish(target,text):
 if '# Source: {ONE}' in text:
  if {published!r}:original(target,text)
  os._exit(17)
 original(target,text)
research_files.create_extract=publish
asyncio.run(wr.crawl_site({SEED!r},**{OPTIONS!r}))
'''
    # The child is a process of THIS install (its data dir): the crawl
    # journal is scoped per install since Knowledge audit KB-E07.
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path', 'kazma_data_dir'}
    result = await run_bounded_async([sys.executable, '-I', '-c', program],
        env={key: value for key, value in os.environ.items() if key.lower() in allowed},
        timeout=45, output_bytes=32768, memory_bytes=1024**3, text=True)
    assert result.returncode == 17, result.stderr
    row, = crawl_jobs.inspect_jobs()
    assert row['kind'] == 'research_crawl'
    job_id = row['job_id']
    before = crawl_jobs.inspect_jobs(job_id)
    assert [page['status'] for page in before['pages']] == ['completed', 'running', 'pending']
    assert before['pages'][1]['receipt']['ok'] is True
    assert 'private fixture body' not in json.dumps(before)
    assert len(list((tmp_path / 'workspace').rglob('*.md'))) == 1 + int(published)
    requests = []
    async def fetch(url, **kwargs):
        requests.append(url)
        return FetchResult(ok=True, url=url, text='resumed article', links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site(SEED, **OPTIONS, resume_job_id=job_id)
    assert requests == [TWO]
    assert 'status=partial' in report and 'ambiguous' in report
    after = crawl_jobs.inspect_jobs(job_id)
    assert [page['status'] for page in after['pages']] == ['completed', 'ambiguous', 'completed']
    assert after['status'] == 'partial' and after['fetched'] == 2
    assert len(list((tmp_path / 'workspace').rglob('*.md'))) == 2 + int(published)
    assert crawl_jobs.current_crawl.get() is None


@pytest.mark.asyncio
async def test_cancelled_publication_is_joined_before_job_becomes_interrupted(journal, monkeypatch):
    from kazma_core.tools import research_files
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = research_files.create_extract
    joining = asyncio.Event()
    original_join = crawl_jobs.finish_owned
    async def observe_join(receipt):
        joining.set()
        return await original_join(receipt)
    def publish(target, text):
        started.set()
        assert release.wait(15)
        try:
            original(target, text)
        finally:
            finished.set()
    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text='complete article', links_complete=True)
    monkeypatch.setattr(research_files, 'create_extract', publish)
    monkeypatch.setattr(crawl_jobs, 'finish_owned', observe_join)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    task = asyncio.create_task(wr.crawl_site(SEED, **OPTIONS))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        job, = crawl_jobs.inspect_jobs()
        task.cancel()
        await asyncio.wait_for(joining.wait(), 10)
        task.cancel()
        checkpoint = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
        await checkpoint
        assert not task.done() and not finished.is_set()
        assert crawl_jobs.inspect_jobs(job['job_id'])['status'] == 'running'
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()
    row = crawl_jobs.inspect_jobs(job['job_id'])
    assert row['status'] == 'interrupted' and row['pages'][0]['status'] == 'ambiguous'
    assert row['pages'][0]['receipt']['ok'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['workspace', 'max_depth', 'robots', 'provider', 'save', 'prefix'])
async def test_resume_refuses_changed_frozen_scope_before_fetch(journal, monkeypatch, tmp_path, changed):
    async def stop(url, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(wr, '_central_fetch', stop)
    with pytest.raises(asyncio.CancelledError):
        await wr.crawl_site(SEED, **OPTIONS, save=False)
    job, = crawl_jobs.inspect_jobs()
    options = dict(OPTIONS, save=False)
    if changed == 'workspace':
        monkeypatch.setattr(wr, '_workspace_root', lambda: tmp_path / 'other')
    elif changed == 'provider':
        monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
        monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    else:
        key, value = {'max_depth': ('max_depth', 2), 'robots': ('respect_robots', True),
                      'save': ('save', True), 'prefix': ('path_prefix', 'other')}[changed]
        options[key] = value
    requests = []
    async def forbidden(url, **kwargs):
        requests.append(url)
        raise AssertionError('Changed resume reached a fetch')
    monkeypatch.setattr(wr, '_central_fetch', forbidden)
    report = await wr.crawl_site(SEED, **options, resume_job_id=job['job_id'])
    assert report.startswith('Error:') and 'policy changed' in report and not requests
    assert crawl_jobs.inspect_jobs(job['job_id'])['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_storage_failure_before_receipt_prevents_publication_and_next_fetch(journal, monkeypatch, tmp_path):
    import sqlite3
    requests = []
    async def fetch(url, **kwargs):
        requests.append(url)
        monkeypatch.setattr(journal, 'batch_set', lambda *a, **k:
            (_ for _ in ()).throw(sqlite3.OperationalError('fixture disk full')))
        return FetchResult(ok=True, url=url, text='must not publish', links=(ONE,), links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site(SEED, **OPTIONS)
    assert report.startswith('Error:') and 'journal unavailable' in report
    assert requests == [SEED] and not list(tmp_path.rglob('*.md'))


@pytest.mark.asyncio
async def test_provider_policy_is_frozen_and_allowance_uses_the_durable_job(journal, monkeypatch):
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    requests = []
    async def fetch(url, **kwargs):
        requests.append(url)
        assert policy.acquisition_policy().permits('jina')
        monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
        reservation = await crawl_jobs.call(accounting.reserve, 'jina', accounting.account_fingerprint('jina', 'fixture'))
        await crawl_jobs.call(accounting._response_received, reservation, 200)
        return FetchResult(ok=True, url=url, text='complete', links=(ONE,) if url == SEED else (), links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    report = await wr.crawl_site(SEED, **OPTIONS, save=False)
    assert 'status=completed' in report, report
    job, = crawl_jobs.inspect_jobs()
    assert requests == [SEED, ONE]
    assert accounting.snapshot(job['job_id'])['job_reserved'] == 2
    assert policy.acquisition_policy().mode == 'local_only'


@pytest.mark.asyncio
async def test_research_jobs_do_not_appear_or_resume_as_kb_jobs(journal, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.kb_api import create_kb_router
    async def stop(url, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(wr, '_central_fetch', stop)
    with pytest.raises(asyncio.CancelledError):
        await wr.crawl_site(SEED, **OPTIONS, save=False)
    job, = crawl_jobs.inspect_jobs()
    app = FastAPI()
    app.include_router(create_kb_router())
    with TestClient(app) as client:
        assert client.get('/api/kb/jobs').json() == {'ok': True, 'jobs': []}
        status = client.get('/api/kb/jobs/' + job['job_id']).json()
        assert status['ok'] is False and 'research' in status['error']
        resume = client.post('/api/kb/jobs/' + job['job_id'] + '/resume').json()
        assert resume['ok'] is False and 'crawl_site' in resume['error']


@pytest.mark.asyncio
async def test_two_public_resumes_cannot_own_the_same_pending_page(journal, monkeypatch):
    async def first(url, **kwargs):
        if url == ONE:
            raise asyncio.CancelledError()
        return FetchResult(ok=True, url=url, text='article', links=(ONE, TWO), links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', first)
    with pytest.raises(asyncio.CancelledError):
        await wr.crawl_site(SEED, **OPTIONS, save=False)
    row, = crawl_jobs.inspect_jobs()
    entered, release, requests = asyncio.Event(), asyncio.Event(), []
    async def pending(url, **kwargs):
        requests.append(url)
        entered.set()
        await release.wait()
        return FetchResult(ok=True, url=url, text='article', links_complete=True)
    monkeypatch.setattr(wr, '_central_fetch', pending)
    task = asyncio.create_task(wr.crawl_site(SEED, **OPTIONS, save=False, resume_job_id=row['job_id']))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        denied = await wr.crawl_site(SEED, **OPTIONS, save=False, resume_job_id=row['job_id'])
        assert denied.startswith('Error:') and 'Only an interrupted' in denied
        assert requests == [TWO]
    finally:
        release.set()
        report = await task
    assert 'status=partial' in report and requests == [TWO]


@pytest.mark.asyncio
async def test_cancellation_during_terminal_commit_keeps_the_committed_outcome(journal, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = journal.batch_set
    def terminal(items, **kwargs):
        # Each job is its own row since Knowledge audit KB-E04.
        rows = [value for key, value, *_ in items
                if key.startswith(crawl_jobs._JOB_PREFIX) and isinstance(value, dict)]
        if any(row.get('status') == 'completed' for row in rows):
            started.set()
            assert release.wait(15)
        return original(items, **kwargs)
    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text='article', links_complete=True)
    monkeypatch.setattr(journal, 'batch_set', terminal)
    monkeypatch.setattr(wr, '_central_fetch', fetch)
    task = asyncio.create_task(wr.crawl_site(SEED, **OPTIONS, save=False))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        task.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    row, = crawl_jobs.inspect_jobs()
    assert row['status'] == 'completed'
    assert crawl_jobs.inspect_jobs(row['job_id'])['pages'][0]['status'] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['depth', 'duplicate', 'summary'])
async def test_invalid_restored_frontier_fails_closed_before_acquisition(journal, monkeypatch, corruption):
    async def stop(url, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(wr, '_central_fetch', stop)
    with pytest.raises(asyncio.CancelledError):
        await wr.crawl_site(SEED, **OPTIONS, save=False)
    # Each job is its own row since Knowledge audit KB-E04.
    job_id = crawl_jobs.inspect_jobs()[0]['job_id']
    key = crawl_jobs._job_key(job_id)
    row = journal.get_category(crawl_jobs._CATEGORY)[key]
    if corruption == 'depth':
        row['pages'][0]['depth'] = -1
    elif corruption == 'duplicate':
        row['pages'].append(dict(row['pages'][0]))
    else:
        row['pages'][0]['summary'] = {'text': 'hostile unbounded body'}
    journal.set(key, row, crawl_jobs._CATEGORY)
    calls = []
    async def forbidden(url, **kwargs):
        calls.append(url)
    monkeypatch.setattr(wr, '_central_fetch', forbidden)
    report = await wr.crawl_site(SEED, **OPTIONS, save=False, resume_job_id=job_id)
    assert report.startswith('Error:') and 'journal unavailable' in report and not calls
