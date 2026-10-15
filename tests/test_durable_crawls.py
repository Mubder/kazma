"""Durable crawl recovery against real settings databases and child crashes."""
import asyncio
import contextlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kazma_core import config_store
from kazma_core.security.process_budget import run_bounded
from kazma_core.web_acquire import crawl_jobs, policy
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    yield store
    store.close()


def snapshot():
    return dict(mode='local_only', providers=[], robots=True, scope='tree', max_pages=3, delay_ms=0)


def crawl_jobs_data_dir():
    """The data dir this test process's install id lives in."""
    from kazma_core.paths import data_dir

    return data_dir()


@pytest.mark.parametrize('wave', range(4))
def test_first_writers_reserve_installation_capacity_atomically(journal, wave):
    # Eight LIBRARIES: one library takes one crawl at a time since KB-E14,
    # so the installation's four-job capacity is measured across libraries.
    def create(i):
        try:
            return crawl_jobs.start(f'lib{i}', 'https://docs.example/tree/', snapshot(), job_id=str(i))
        except AcquisitionError as error:
            return error
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(create, range(8)))
    admitted = [job for job in outcomes if isinstance(job, crawl_jobs._CrawlJob)]
    try:
        assert len(admitted) == 4
        assert all(error.code == FetchErrorCode.LIMIT for error in outcomes if isinstance(error, AcquisitionError))
        assert len(crawl_jobs.inspect_jobs()) == 4
    finally:
        for job in admitted:
            job.finish('interrupted')


def test_recovery_survives_real_process_death_without_replaying_unknown_effects(journal, tmp_path):
    # A fresh interpreter writes the real product journal then dies mid-page.
    core = str(Path(crawl_jobs.__file__).resolve().parents[2])
    database = str(tmp_path / 'settings.db')
    program = (f'import sys,os;sys.path.insert(0,{core!r});'
        'from kazma_core import config_store;from kazma_core.web_acquire import crawl_jobs;'
        f'store=config_store.ConfigStore({database!r});config_store.get_config_store=lambda:store;'
        f'job=crawl_jobs.start("lib","https://docs.example/tree/",{snapshot()!r},job_id="crash");'
        'job.frontier(["https://docs.example/tree/a?q=1","https://docs.example/tree/a?q=2","https://docs.example/tree/c"]);'
        'job.begin_page("https://docs.example/tree/a?q=1");job.end_page("https://docs.example/tree/a?q=1",new=2);'
        'job.begin_page("https://docs.example/tree/a?q=2");os._exit(17)')
    allowed = {'systemroot','windir','temp','tmp','path'}
    # The child is a process of THIS install (its data dir): a crawl owned on
    # this install and host is taken over only when its process is gone
    # (KB-E07); one owned elsewhere waits for its lease.
    result = run_bounded([sys.executable,'-I','-c',program],env={**{k:v for k,v in os.environ.items() if k.lower() in allowed}, 'KAZMA_BROWSER_NETWORK_MODE': 'compatibility',
        'KAZMA_DATA_DIR': str(crawl_jobs_data_dir())},
        timeout=10,output_bytes=32768,memory_bytes=512*1024**2)
    assert result.returncode == 17, result.stderr
    assert crawl_jobs.inspect_jobs('crash')['status'] == 'running'  # until the next admission
    resumed = crawl_jobs.start('lib','https://docs.example/tree/',snapshot(),job_id='crash',resume=True)
    try:
        row = resumed.read()
        # Re-pointed (KB-E02): a Knowledge page is published in one
        # transaction, so the page the crash left uncertain is replayed on an
        # EXPLICIT resume; the settled page is not. Research crawls keep
        # refusing the replay (tests/test_research_crawl_recovery.py).
        assert [page['status'] for page in row['pages']] == ['completed','pending','pending']
        assert row['fetched'] == 1 and row['ingested'] == 2
        assert not resumed.begin_page(row['pages'][0]['url'])
        assert resumed.begin_page(row['pages'][1]['url'])
        assert resumed.begin_page(row['pages'][2]['url'])
    finally:
        resumed.finish('interrupted')


def test_resume_rejects_changed_policy_and_live_owner(journal):
    job = crawl_jobs.start('lib','https://docs.example/tree/',snapshot(),job_id='one')
    try:
        with pytest.raises(AcquisitionError,match='Only an interrupted'):
            crawl_jobs.start('lib','https://docs.example/tree/',snapshot(),job_id='one',resume=True)
    finally:
        job.finish('interrupted')
    changed = dict(snapshot(),scope='domain')
    with pytest.raises(AcquisitionError,match='policy changed'):
        crawl_jobs.start('lib','https://docs.example/tree/',changed,job_id='one',resume=True)
    row = crawl_jobs.inspect_jobs('one')
    assert row['status'] == 'interrupted' and row['policy']['scope'] == 'tree'


def test_receipts_keep_query_identity_and_honest_unknowns_without_page_body(journal):
    job = crawl_jobs.start('lib','https://docs.example/tree/',snapshot())
    url = 'https://docs.example/tree/a?version=1&version=2'
    try:
        job.frontier([url])
        assert job.begin_page(url)
        job.receipt(FetchResult(ok=True,url=url,text='Private page body',backend='httpx',quality='static',char_count=17))
        job.end_page(url,new=1)
        row = crawl_jobs.inspect_jobs(job.job_id)
        receipt = row['pages'][0]['receipt']
        assert row['pages'][0]['url'] == url and receipt['url'] == url
        assert receipt['usage'] is None and receipt['credits'] is None
        assert 'Private page body' not in json.dumps(row) and 'text' not in receipt
        assert 'token' not in row and 'owner' not in row
    finally:
        job.finish('completed')


def test_registry_retention_and_byte_limits_are_real_refusals(journal, monkeypatch):
    for i in range(28):
        job = crawl_jobs.start('lib','https://docs.example/tree/',snapshot(),job_id=str(i))
        job.finish('completed')
    assert len(crawl_jobs.inspect_jobs()) == 24 and crawl_jobs.inspect_jobs('0') is None
    job = crawl_jobs.start('lib','https://docs.example/tree/',snapshot(),job_id='bounded')
    try:
        # Re-pointed (KB-E04): each job has its own byte budget, checked when
        # its frontier is persisted -- before anything is fetched -- and on
        # every write; the registry-wide value it used to rewrite is gone.
        with monkeypatch.context() as bounded:
            bounded.setattr(crawl_jobs,'_MAX_JOB_BYTES',len(json.dumps(job.read(),ensure_ascii=True))+70*1024)
            with pytest.raises(AcquisitionError,match='job budget'):
                job.frontier(['https://docs.example/'+'x'*7000])
            assert not job.read()['discovered']
    finally:
        job.finish('interrupted')


@pytest.mark.asyncio
async def test_durable_write_failure_prevents_any_acquisition(journal, monkeypatch):
    import sqlite3

    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    async def forbidden(*args,**kwargs):
        pytest.fail('Acquisition must not start without a durable job')
    # Admission checks the seed's address before the journal row (2026-10-15);
    # docs.example is a reserved name that resolves nowhere.
    monkeypatch.setattr(ssrf,'validate_url',lambda *args,**kwargs: ())
    monkeypatch.setattr(ki,'kb_discover_pages',forbidden)
    monkeypatch.setattr(journal,'batch_set',lambda *args,**kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    with pytest.raises(AcquisitionError) as failure:
        _ = [update async for update in ki.ingest_site('lib','https://docs.example/tree/')]
    assert failure.value.code == FetchErrorCode.STORAGE
    assert not crawl_jobs.inspect_jobs()


@pytest.mark.asyncio
async def test_public_crawl_resume_skips_completed_and_ambiguous_pages_and_resets_context(journal, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki
    import kazma_core.security.ssrf as ssrf
    pages = ['https://docs.example/tree/a?q=1','https://docs.example/tree/a?q=2','https://docs.example/tree/c']
    fetched, stored = [], []
    async def discover(*args,**kwargs):
        return pages
    async def extract(url,**kwargs):
        fetched.append(url)
        return '# Content\n'+'body '*40,'ok',''
    monkeypatch.setattr(ssrf,'validate_url',lambda *args,**kwargs: ())
    monkeypatch.setattr(ki,'kb_discover_pages',discover)
    monkeypatch.setattr(ki,'_extract_page',extract)
    monkeypatch.setattr(ki,'_kb_delay_ms',lambda:0)

    class _Library:  # the Knowledge store: the library exists and has room
        def library_exists(self, library_id): return True
        def ensure_capacity(self): return None
        def count_chunks(self, library_id): return 0
        def set_chunk_count(self, library_id, count): return None
    monkeypatch.setattr(ki,'get_knowledge_store',_Library)
    monkeypatch.setattr(ki,'get_knowledge_index',lambda:object())
    monkeypatch.setattr(ki,'_store_page',lambda *args: (stored.append(args[2]) or 1,0,1))
    monkeypatch.setattr(ki,'_pw_browser_scope',contextlib.nullcontext)
    iterator = ki.ingest_site('lib','https://docs.example/tree/',max_pages=3,job_id='resume')
    async for update in iterator:
        assert crawl_jobs.current_crawl.get() is None and policy._job_policy.get() is None
        if update.phase == 'fetch' and update.current_url == pages[1]:
            await iterator.aclose()  # a started page is conservatively ambiguous
            break
    assert fetched == stored == [pages[0]]
    assert crawl_jobs.projection('resume')['resume_available']
    assert crawl_jobs.projection('resume')['acquisition']['ambiguous'] == 1
    monkeypatch.setattr(ki,'kb_discover_pages',lambda *args,**kwargs: pytest.fail('Resume must reuse the frontier'))
    updates = [update async for update in ki.ingest_site('lib','https://docs.example/tree/',max_pages=3,job_id='resume',resume=True)]
    # Re-pointed (KB-E02): a Knowledge page is published in one transaction,
    # so the explicit resume replays the page the close left uncertain and
    # skips the completed one; nothing is fetched twice that had settled.
    assert fetched == stored == [pages[0],pages[1],pages[2]]
    assert updates[-1].fetched == 3 and updates[-1].failed == 0
    assert crawl_jobs.projection('resume')['acquisition']['ambiguous'] == 0
    assert crawl_jobs.current_crawl.get() is None and policy._job_policy.get() is None



def test_separate_processes_share_one_first_writer_capacity_budget(journal, tmp_path):
    import time
    core = str(Path(crawl_jobs.__file__).resolve().parents[2])
    release = tmp_path / 'release'
    allowed = {'systemroot','windir','temp','tmp','path'}
    # Processes of ONE install (its data dir), each crawling its own library
    # (one crawl per library since KB-E14).
    env = {**{k:v for k,v in os.environ.items() if k.lower() in allowed},
           'KAZMA_DATA_DIR': str(crawl_jobs_data_dir())}
    def launch(index):
        marker = tmp_path / f'child-{index}.json'
        program = (f'import sys,json,time;from pathlib import Path;sys.path.insert(0,{core!r});'
            'from kazma_core import config_store;from kazma_core.web_acquire import crawl_jobs;'
            'from kazma_core.web_acquire.result import AcquisitionError;'
            f'store=config_store.ConfigStore({str(tmp_path / "settings.db")!r});config_store.get_config_store=lambda:store\n'
            'job=None\n'
            'try:\n'
            f' job=crawl_jobs.start("lib{index}","https://docs.example/tree/",{snapshot()!r},job_id="process-{index}")\n'
            ' outcome="admitted"\n'
            'except AcquisitionError as error: outcome=str(error.code)\n'
            f'Path({str(marker)!r}).write_text(json.dumps(outcome))\n'
            'deadline=time.monotonic()+15\n'
            f'while not Path({str(release)!r}).exists():\n'
            ' assert time.monotonic()<deadline\n time.sleep(.01)\n'
            'if job:job.finish("completed")\n'
            'store.close()')
        return run_bounded([sys.executable,'-I','-c',program],env=env,timeout=20,
            output_bytes=32768,memory_bytes=512*1024**2)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(launch,i) for i in range(4)]
        try:
            deadline = time.monotonic()+15
            while not all((tmp_path/f'child-{i}.json').exists() for i in range(4)):
                assert time.monotonic()<deadline
                time.sleep(.01)
            assert [json.loads((tmp_path/f'child-{i}.json').read_text()) for i in range(4)] == ['admitted']*4
            futures.append(executor.submit(launch,4))
            while not (tmp_path/'child-4.json').exists():
                assert time.monotonic()<deadline
                time.sleep(.01)
            assert json.loads((tmp_path/'child-4.json').read_text()) == 'resource_limit'
        finally:
            release.touch()
        for future in futures:
            result = future.result()
            assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert len(crawl_jobs.inspect_jobs()) == 4
    assert all(row['status']=='completed' for row in crawl_jobs.inspect_jobs())


def test_corrupt_journal_is_unavailable_instead_of_an_empty_fresh_run(journal):
    journal.set(crawl_jobs._KEY, {'invalid': {'status': 'running'}}, category=crawl_jobs._CATEGORY)
    with pytest.raises(AcquisitionError) as failure:
        crawl_jobs.start('lib','https://docs.example/tree/',snapshot())
    assert failure.value.code == FetchErrorCode.STORAGE


@pytest.mark.asyncio
async def test_discovery_checkpoint_resumes_pending_bfs_without_metadata_or_unknown_replay(journal, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    seed, first, pending = ['https://docs.example/tree/' + path for path in ('', 'a', 'b')]
    entered, acquired, metadata = asyncio.Event(), [], []
    async def control(*args, **kwargs):
        metadata.append(args)
        return []
    async def fetch(url, **kwargs):
        acquired.append(url)
        if url == first:
            entered.set()
            await asyncio.Future()
        return FetchResult(ok=True, url=url, final_url=url, text='page',
            links=(first, pending) if url == seed else (), links_complete=True)
    monkeypatch.setattr(ki, '_llms_txt_discover', control)
    monkeypatch.setattr(ki, '_fetch_sitemaps', control)
    monkeypatch.setattr(ki, '_web_fetch_text', fetch)
    monkeypatch.setattr(ki, '_pw_browser_scope', contextlib.nullcontext)
    job = crawl_jobs.start('lib', seed, snapshot(), job_id='discovery')
    async def discover(owner):
        token = crawl_jobs.current_crawl.set(owner)
        try:
            return await ki.kb_discover_pages(seed)
        finally:
            crawl_jobs.current_crawl.reset(token)
    task = asyncio.create_task(discover(job))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job.finish('interrupted')
    state = crawl_jobs.inspect_jobs('discovery')['discovery']
    assert state['ambiguous'] == [first] and state['queue'] == [[pending, 1]]
    assert acquired == [seed, first] and len(metadata) == 2
    resumed = crawl_jobs.start('lib', seed, snapshot(), job_id='discovery', resume=True)
    try:
        assert await discover(resumed) == [seed, pending]
        assert acquired == [seed, first, pending] and len(metadata) == 2
        assert crawl_jobs.projection('discovery')['acquisition']['discovery_ambiguous'] == 1
    finally:
        resumed.finish('completed')


@pytest.mark.asyncio
@pytest.mark.parametrize('tier', ['llms', 'sitemap'])
async def test_metadata_resume_keeps_parsed_results_and_skips_unknown_requests(journal, monkeypatch, tier):
    from kazma_core.stores import knowledge_ingest as ki

    seed = 'https://docs.example/tree/start'
    root = 'https://docs.example'
    entered, hits = asyncio.Event(), []
    unknown = root + ('/llms.txt' if tier == 'llms' else '/child-a.xml')
    pending = root + ('/tree/llms.txt' if tier == 'llms' else '/child-b.xml')
    page = root + '/tree/retained.md?version=1&version=2'
    async def fetch(url, **kwargs):
        hits.append(url)
        if url == unknown:
            entered.set()
            await asyncio.Future()
        if url == root + '/sitemap.xml':
            return '<sitemapindex><sitemap><loc>/child-a.xml</loc></sitemap><sitemap><loc>/child-b.xml</loc></sitemap></sitemapindex>', url
        if url == pending:
            text = (f'[A retained page with an explanatory label]({page})' if tier == 'llms'
                    else f'<urlset><url><loc>{page.replace("&", "&amp;")}</loc></url></urlset>')
            return text, url
        return None, url
    monkeypatch.setattr(ki, '_http_get_text', fetch)
    job = crawl_jobs.start('lib', seed, snapshot(), job_id='metadata-' + tier)
    async def discover(owner):
        token = crawl_jobs.current_crawl.set(owner)
        try:
            if tier == 'llms':
                return await ki._llms_txt_discover(seed)
            return (await ki._fetch_sitemaps(seed)).pages
        finally:
            crawl_jobs.current_crawl.reset(token)
    task = asyncio.create_task(discover(job))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job.finish('interrupted')
    before = list(hits)
    assert crawl_jobs.inspect_jobs(job.job_id)['metadata'][unknown]['status'] == 'ambiguous'
    resumed = crawl_jobs.start('lib', seed, snapshot(), job_id=job.job_id, resume=True)
    try:
        assert await discover(resumed) == [page]
        assert hits == before + [pending]
        assert crawl_jobs.projection(job.job_id)['acquisition']['metadata_ambiguous'] == 1
        serialized = json.dumps(crawl_jobs.inspect_jobs(job.job_id))
        assert '<sitemapindex>' not in serialized and '<urlset>' not in serialized
    finally:
        resumed.finish('partial')


@pytest.mark.asyncio
async def test_metadata_storage_denial_precedes_fetch_and_corruption_is_visible(journal, monkeypatch):
    from kazma_core.web_acquire.discovery_metadata import _acquire

    job = crawl_jobs.start('lib', 'https://docs.example/tree/', snapshot())
    token = crawl_jobs.current_crawl.set(job)
    called = []
    async def fetch():
        called.append(True)
    def reject(*args, **kwargs):
        raise OSError('fixture disk full')
    original = journal.batch_set
    monkeypatch.setattr(journal, 'batch_set', reject)
    try:
        with pytest.raises(AcquisitionError) as error:
            await _acquire('https://docs.example/llms.txt', fetch)
        assert error.value.code == FetchErrorCode.STORAGE and not called
    finally:
        monkeypatch.setattr(journal, 'batch_set', original)
        crawl_jobs.current_crawl.reset(token)
        job.finish('interrupted')
    # Re-pointed (KB-E04): the job is its own settings row now.
    key = crawl_jobs._job_key(job.job_id)
    row = journal.get_category(crawl_jobs._CATEGORY)[key]
    row['metadata'] = {'https://docs.example/llms.txt': dict(status='completed', result={'pages': 'bad'})}
    journal.set(key, row, category=crawl_jobs._CATEGORY)
    with pytest.raises(AcquisitionError) as error:
        crawl_jobs.inspect_jobs()
    assert error.value.code == FetchErrorCode.STORAGE


@pytest.mark.asyncio
async def test_policy_snapshot_does_not_change_when_operator_env_changes_during_yield(journal, monkeypatch):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    # The seed's address is checked at admission (2026-10-15); docs.example
    # resolves nowhere.
    monkeypatch.setattr(ssrf,'validate_url',lambda *args,**kwargs: ())
    async def implementation(*args,**kwargs):
        assert not policy.acquisition_policy().permits('jina')
        yield ki.ProgressUpdate(phase='discover')
        assert not policy.acquisition_policy().permits('jina')
        yield ki.ProgressUpdate(phase='done',fetched=1)
    monkeypatch.setattr(ki,'_ingest_site_impl',implementation)
    iterator = ki.ingest_site('lib','https://docs.example/tree/')
    first = await anext(iterator)
    assert first.phase == 'discover' and policy._job_policy.get() is None
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE','local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS','jina')
    assert policy.acquisition_policy().permits('jina')
    assert (await anext(iterator)).phase == 'done'
    await iterator.aclose()
    assert policy._job_policy.get() is None
