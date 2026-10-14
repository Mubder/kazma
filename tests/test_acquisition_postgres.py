"""Real PostgreSQL qualification for acquisition CAS and restart durability."""
import os
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kazma_core import config_store
from kazma_core.web_acquire import accounting, crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError


pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.environ.get('KAZMA_DB_BACKEND') != 'postgres', reason='Requires an explicitly isolated PostgreSQL fixture')]


@pytest.fixture
def database(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'unused-sqlite.db'))
    assert store._use_postgres(), 'A PostgreSQL qualification must never silently use SQLite'
    local, stores, lock = threading.local(), [store], threading.Lock()
    local.store = store
    def independent_store():
        if not hasattr(local, 'store'):
            local.store = config_store.ConfigStore(str(tmp_path / ('unused-' + uuid.uuid4().hex + '.db')))
            with lock:
                stores.append(local.store)
        return local.store
    # Distinct store locks must contend through actual PostgreSQL row locks.
    monkeypatch.setattr(config_store, 'get_config_store', independent_store)
    namespace = 'acquisition-fixture-' + uuid.uuid4().hex
    keys = []
    for module in (accounting, crawl_jobs):
        # _JOB_PREFIX: the crawl journal keeps one row per job since the
        # Knowledge audit (KB-E04); its rows are namespaced too.
        for attr in ('_KEY', '_REV', '_REVISION', '_JOB_PREFIX'):
            if hasattr(module, attr):
                key = namespace + '.' + attr + '.' + module.__name__.rsplit('.', 1)[-1]
                monkeypatch.setattr(module, attr, key)
                keys.append(key)
        monkeypatch.setattr(module, '_CATEGORY', namespace)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy')
    yield store
    for key in keys + list(store.get_category(namespace) or {}):
        store.delete(key)
    for instance in stores:
        instance.close()


def test_real_postgres_first_writers_share_finite_remote_allowance(database, monkeypatch):
    monkeypatch.setenv('KAZMA_REMOTE_DAY_REQUESTS', '5')
    def attempt(i):
        try:
            with accounting.remote_scope('process-job-' + str(i)):
                return accounting.reserve('proxy', accounting.account_fingerprint('proxy', 'fixture'))
        except AcquisitionError as error:
            assert error.code == 'resource_limit'
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(16)))
    assert sum(row is not None for row in results) == 5
    assert accounting.snapshot()['day_reserved'] == 5
    assert all(row['state'] == 'unknown' for row in accounting.snapshot()['recent'])


def test_real_postgres_crawl_first_writers_and_recovery(database):
    policy = dict(mode='local_only', providers=[], robots=True, scope='tree', max_pages=3, max_depth=2, delay_ms=0)
    # Re-pointed for Knowledge audit KB-E14 (2026-10-14): one crawl per
    # library, so the twelve first writers crawl twelve libraries; four win
    # the active slots as before.
    def start(i):
        try:
            return crawl_jobs.start('library-' + str(i), 'https://docs.example/tree/', policy, job_id=str(i))
        except AcquisitionError as error:
            assert error.code == 'resource_limit'
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = [job for job in executor.map(start, range(12)) if job]
    try:
        assert len(jobs) == 4
        job = jobs[0]
        job.frontier(['https://docs.example/tree/a', 'https://docs.example/tree/b'])
        job.begin_page('https://docs.example/tree/a')
        job.finish('interrupted')
        restored = crawl_jobs.start('library-' + job.job_id, 'https://docs.example/tree/', policy,
                                    job_id=job.job_id, resume=True)
        jobs[0] = restored
        # A Knowledge crawl's page is one transaction (KB-E02), so an explicit
        # resume fetches the uncertain page again.
        assert [page['status'] for page in restored.read()['pages']] == ['pending', 'pending']
    finally:
        for job in jobs:
            job.finish('interrupted')


@pytest.mark.asyncio
async def test_real_postgres_research_settlement_and_exclusive_resume(database, monkeypatch, tmp_path):
    import asyncio
    from kazma_core.tools import web_research
    from kazma_core.web_acquire.result import FetchResult

    seed = 'https://docs.example/'
    one, two = seed + 'one?q=1', seed + 'two?q=2'
    async def public(*args, **kwargs):
        return ('93.184.216.34',)
    async def initial(url, **kwargs):
        if url == one:
            raise asyncio.CancelledError()
        return FetchResult(ok=True, url=url, text='private body', links=(one, two), links_complete=True)
    monkeypatch.setattr('kazma_core.web_acquire.dns_worker.validate_public_url', public)
    monkeypatch.setattr(web_research, '_workspace_root', lambda: tmp_path)
    monkeypatch.setattr(web_research, '_central_fetch', initial)
    options = dict(max_pages=3, max_depth=1, save=False, delay_ms=0, respect_robots=False)
    with pytest.raises(asyncio.CancelledError):
        await web_research.crawl_site(seed, **options)
    row, = crawl_jobs.inspect_jobs()
    restored = crawl_jobs.inspect_jobs(row['job_id'])
    assert [page['status'] for page in restored['pages']] == ['completed', 'ambiguous', 'pending']
    entered, release, calls = asyncio.Event(), asyncio.Event(), []
    async def pending(url, **kwargs):
        calls.append(url)
        entered.set()
        await release.wait()
        return FetchResult(ok=True, url=url, text='private body', links_complete=True)
    monkeypatch.setattr(web_research, '_central_fetch', pending)
    task = asyncio.create_task(web_research.crawl_site(seed, **options, resume_job_id=row['job_id']))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        denied = await web_research.crawl_site(seed, **options, resume_job_id=row['job_id'])
        assert 'Only an interrupted' in denied and calls == [two]
    finally:
        release.set()
        report = await task
    assert 'status=partial' in report
    assert [page['status'] for page in crawl_jobs.inspect_jobs(row['job_id'])['pages']] == ['completed', 'ambiguous', 'completed']


def test_real_postgres_process_death_preserves_origin_and_unavailable_robots(database, tmp_path):
    import sys
    from pathlib import Path
    from kazma_core.web_acquire import origin_state, origins, robots_state
    from kazma_core.security.process_budget import run_bounded
    origin = origins._origin('https://' + uuid.uuid4().hex + '.example/')
    key = uuid.uuid4().hex
    core = str(Path(origin_state.__file__).resolve().parents[2])
    program = f"""
import os,sys
sys.path.insert(0,{core!r})
from kazma_core import config_store
from kazma_core.web_acquire import origin_state,robots_state
store=config_store.ConfigStore({str(tmp_path / 'unused-child.db')!r})
config_store.get_config_store=lambda:store
assert store._use_postgres()
token=origin_state.queue({origin!r})
assert origin_state.start({origin!r},token,0)==0
origin_state.observe({origin!r},429,120)
assert robots_state.begin({key!r})[1]
os._exit(19)
"""
    allowed={'path','systemroot','windir','temp','tmp','kazma_db_backend','kazma_database_url','kazma_test_allow_real_db'}
    result=run_bounded([sys.executable,'-I','-c',program],
        env={k:v for k,v in os.environ.items() if k.lower() in allowed},
        timeout=30,memory_bytes=512*1024**2,output_bytes=32768,text=True)
    assert result.returncode==19, result.stdout+result.stderr
    token=origin_state.queue(origin)
    try:
        with pytest.raises(AcquisitionError,match='cooldown'):
            origin_state.start(origin,token,0)
    finally:
        origin_state.release(origin,token)
    rule,claim=robots_state.begin(key)
    assert claim is None and rule.status=='unavailable' and not rule.allows('https://public.example/')
