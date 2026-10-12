"""Real SQLite and fresh-process crashes through the public research pipeline."""
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from kazma_core import config_store
from kazma_core.tools import research_pipeline as pipeline, read_url, research_files
from kazma_core.web_acquire import crawl_jobs, resources
from kazma_core.web_acquire import research_pipeline_job as stages
from kazma_core.web_acquire.result import FetchResult, AcquisitionError, FetchErrorCode
from kazma_core.security.process_budget import run_bounded_async

URLS = [f'https://source{i}.example/article' for i in range(3)]
OPTIONS = dict(depth='standard', max_sources=3, parallel_acquire=False)


def install(monkeypatch, root, store, calls, crash='', published=False, fixed=True):
    from kazma_core.tools import research_planner, research_synthesize
    import kazma_core.web_acquire as acquisition
    from kazma_core.web_acquire.serp import SearchResult
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setattr(pipeline, '_get_ws_root', lambda: root)
    monkeypatch.setattr(read_url, '_workspace_root', lambda: root)
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', lambda **kw: {'ready': True})
    monkeypatch.setattr(stages, '_scope_signature', lambda: 'fixed-fixture-scope')
    monkeypatch.setenv('KAZMA_RESEARCH_GAP_LOOP', '0')
    monkeypatch.setenv('KAZMA_BROWSER_NETWORK_MODE', 'compatibility')  # controlled sources; no real browser egress
    async def plan(*args, **kwargs):
        calls.append('plan')
        if crash == 'plan':
            os._exit(23)
        return research_planner.ResearchPlan(search_queries=['initial'])
    async def search(query, **kwargs):
        calls.append('search')
        if crash == 'search':
            os._exit(23)
        return SearchResult(ok=True, query=query, urls=URLS)
    async def fetch(url, **kwargs):
        calls.append(url)
        return FetchResult(ok=True, url=url, text='Complete source evidence. ' * 30, links_complete=True)
    async def synthesize(*args, **kwargs):
        calls.append('synthesis')
        if crash == 'synthesis':
            os._exit(23)
        return '## Executive summary\nEvidence from the sources.\n## Sources\n' + '\n'.join(URLS)
    publish = research_files.create_extract
    def publication(target, text):
        if (crash == 'source' and target.name == 'src-02.md') or (crash == 'report' and target.name == 'report.md'):
            if published:
                publish(target, text)
            os._exit(23)
        publish(target, text)
    monkeypatch.setattr(research_planner, 'plan_research_queries', plan)
    monkeypatch.setattr(acquisition, 'search', search)
    monkeypatch.setattr(read_url, 'fetch_result', fetch)
    monkeypatch.setattr(research_synthesize, 'synthesize_from_digests', synthesize)
    monkeypatch.setattr(research_files, 'create_extract', publication)
    if not fixed:
        original = stages._PipelineJob.effect
        async def unowned_planner(self, key, inputs, action, **kwargs):
            if key == 'plan':
                return await action()  # original planner boundary: no before-effect receipt
            return await original(self, key, inputs, action, **kwargs)
        monkeypatch.setattr(stages._PipelineJob, 'effect', unowned_planner)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    calls = []
    install(monkeypatch, tmp_path / 'workspace', store, calls)
    yield store, calls
    store.close()


async def crash_child(tmp_path, stage, *, published=False, fixed=True):
    # Explicit import paths and credential-free environment; no real provider/network.
    module = Path(__file__).resolve()
    core = Path(pipeline.__file__).resolve().parents[2]
    program = f"""
import sys,runpy,asyncio
from pathlib import Path
sys.path.insert(0,{str(core)!r})
import pytest
from kazma_core import config_store
from kazma_core.tools import research_pipeline as pipeline
helpers=runpy.run_path({str(module)!r})
store=config_store.ConfigStore({str(tmp_path / 'settings.db')!r})
helpers['install'](pytest.MonkeyPatch(),Path({str(tmp_path / 'workspace')!r}),store,[],
                   crash={stage!r},published={published!r},fixed={fixed!r})
asyncio.run(pipeline.run_research_pipeline('fixture topic',**{OPTIONS!r}))
"""
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}
    result = await run_bounded_async([sys.executable, '-I', '-X', 'utf8', '-c', program],
        env={key: value for key, value in os.environ.items() if key.lower() in allowed},
        timeout=45, output_bytes=32768, memory_bytes=1024**3, text=True)
    assert result.returncode == 23, result.stderr + result.stdout
    row, = crawl_jobs.inspect_jobs()
    return row['job_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_crash_at_source_publication_preserves_receipt_and_resumes_only_pending(journal, tmp_path, published):
    _, calls = journal
    job_id = await crash_child(tmp_path, 'source', published=published)
    before = crawl_jobs.inspect_jobs(job_id)
    assert before['effects']['acquire:001']['status'] == 'completed'
    assert before['effects']['acquire:002']['status'] == 'running'
    assert before['effects']['acquire:002']['receipt']['ok'] is True
    assert 'Complete source evidence' not in json.dumps(before)
    output = tmp_path / 'workspace' / before['policy']['output']
    second = output / 'src-02.md'
    original = second.read_bytes() if second.exists() else None
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id)
    assert calls == [URLS[2], 'synthesis']
    assert result.startswith('# Deep research partial')
    after = crawl_jobs.inspect_jobs(job_id)
    assert after['status'] == 'partial' and after['effects']['acquire:002']['status'] == 'ambiguous'
    assert second.read_bytes() == original if published else not second.exists()
    assert (output / 'report.md').is_file()
    assert crawl_jobs.current_crawl.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['plan', 'search', 'synthesis', 'report'])
async def test_required_uncertain_stage_refuses_replay(journal, tmp_path, stage):
    _, calls = journal
    job_id = await crash_child(tmp_path, stage, published=True)
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id)
    assert result.startswith('Error:') and 'uncertain' in result and 'replay refused' in result
    assert not calls
    row = crawl_jobs.inspect_jobs(job_id)
    assert row['status'] == 'interrupted'
    assert any(effect['status'] == 'ambiguous' for effect in row['effects'].values())


@pytest.mark.asyncio
async def test_original_unowned_planner_is_repeated_after_crash(journal, tmp_path, monkeypatch):
    store, calls = journal
    install(monkeypatch, tmp_path / 'workspace', store, calls, fixed=False)
    job_id = await crash_child(tmp_path, 'plan', fixed=False)
    assert 'plan' not in crawl_jobs.inspect_jobs(job_id)['effects']
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id)
    assert result.startswith('# Deep research complete') and calls[0] == 'plan'


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['workspace', 'language', 'maximum', 'depth', 'parallel', 'export', 'configuration', 'provider'])
async def test_resume_refuses_changed_scope_before_any_effect(journal, tmp_path, monkeypatch, changed):
    _, calls = journal
    job_id = await crash_child(tmp_path, 'plan')
    options = dict(OPTIONS)
    if changed == 'workspace':
        monkeypatch.setattr(pipeline, '_get_ws_root', lambda: tmp_path / 'other')
    elif changed == 'configuration':
        monkeypatch.setattr(stages, '_scope_signature', lambda: 'changed')
    elif changed == 'provider':
        monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
        monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    else:
        key, value = {'language': ('language', 'ar'), 'maximum': ('max_sources', 4),
                      'depth': ('depth', 'deep'), 'parallel': ('parallel_acquire', True),
                      'export': ('export_docx', True)}[changed]
        options[key] = value
    result = await pipeline.run_research_pipeline('fixture topic', **options, resume_job_id=job_id)
    assert result.startswith('Error:') and 'changed' in result and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('tampered', ['source', 'stage-result', 'missing-source'])
async def test_completed_artifact_change_refuses_effects(journal, tmp_path, tampered):
    _, calls = journal
    job_id = await crash_child(tmp_path, 'source')
    row = crawl_jobs.inspect_jobs(job_id)
    effect = row['effects']['acquire:001']
    ref = effect['files'][0] if tampered in {'source', 'missing-source'} else effect['value']
    artifact = tmp_path / 'workspace' / ref['path']
    if tampered == 'missing-source':
        artifact.unlink()
    else:
        artifact.write_text('user edit', encoding='utf-8')
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id)
    assert result.startswith('Error:') and 'changed or is missing' in result and not calls


@pytest.mark.asyncio
async def test_receipt_failure_stops_before_publication_and_next_fetch(journal, monkeypatch, tmp_path):
    _, calls = journal
    async def refuse(self, key, result):
        raise AcquisitionError(FetchErrorCode.STORAGE, 'fixture journal failure')
    monkeypatch.setattr(stages._PipelineJob, 'receipt', refuse)
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS)
    assert result.startswith('Error:') and 'journal failure' in result
    assert calls == ['plan', 'search', URLS[0]]
    assert not list((tmp_path / 'workspace').rglob('src-*.md'))


@pytest.mark.asyncio
async def test_cancellation_joins_held_publication_before_interrupting_journal(journal, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = research_files.create_extract
    def held(target, text):
        if target.name == 'src-01.md':
            started.set()
            assert release.wait(15)
            try:
                return original(target, text)
            finally:
                finished.set()
        return original(target, text)
    monkeypatch.setattr(research_files, 'create_extract', held)
    task = asyncio.create_task(pipeline.run_research_pipeline('fixture topic', **OPTIONS))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(.01)
        task.cancel()
        assert not task.done() and not finished.is_set()
        row, = crawl_jobs.inspect_jobs()
        assert row['status'] == 'running'
        assert resources.research_capacity().snapshot()['active'] == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    row = crawl_jobs.inspect_jobs(row['job_id'])
    assert row['status'] == 'interrupted' and row['effects']['acquire:001']['status'] == 'ambiguous'
    assert finished.is_set() and resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_two_resumers_cannot_dispatch_the_same_pending_source(journal, tmp_path, monkeypatch):
    _, calls = journal
    job_id = await crash_child(tmp_path, 'source')
    entered, release = asyncio.Event(), asyncio.Event()
    fetch = read_url.fetch_result
    async def held(url, **kwargs):
        entered.set()
        await release.wait()
        return await fetch(url, **kwargs)
    monkeypatch.setattr(read_url, 'fetch_result', held)
    first = asyncio.create_task(pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        second = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=job_id)
        assert second.startswith('Error:') and 'active' in second.lower()
        assert not calls
    finally:
        release.set()
        result = await first
    assert result.startswith('# Deep research partial') and calls == [URLS[2], 'synthesis']


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['plan', 'source'])
async def test_projection_exposes_stage_uncertainty_without_network(journal, tmp_path, stage, monkeypatch):
    job_id = await crash_child(tmp_path, stage)
    crawl_jobs.recover()
    def no_remote(*args, **kwargs):
        pytest.fail('cached status dispatched remote work')
    from kazma_core.web_acquire import credits
    monkeypatch.setattr(credits, 'refresh_firecrawl_balance', no_remote)
    status = crawl_jobs.projection(job_id)
    summary, = crawl_jobs.inspect_jobs()
    assert summary['ambiguous'] == 1
    assert status['acquisition']['stages']['ambiguous'] == 1
    assert status['acquisition']['ambiguous'] == 1
    assert status['resume_available'] is (stage == 'source')
    if stage == 'plan':
        assert status['acquisition']['resume_blocked_stages'] == ['plan']
    else:
        assert status['fetched'] == summary['fetched'] == 1
        assert status['acquisition']['last_receipt']['ok'] is True


@pytest.mark.parametrize('broken', ['files', 'reservation', 'reservation-total'])
def test_invalid_stage_metadata_is_a_typed_storage_failure(journal, broken):
    store, _ = journal
    async def seed():
        async with stages.pipeline_job('fixture', Path(pipeline._get_ws_root()), OPTIONS) as job:
            await job.effect('plan', {}, lambda: asyncio.sleep(0, result='fixture'))
    asyncio.run(seed())
    row, = crawl_jobs.inspect_jobs()
    def corrupt(jobs):
        effect = jobs[row['job_id']]['effects']['plan']
        if broken == 'files':
            effect['files'] = {'path': 'wrong type'}
        elif broken == 'reservation':
            effect['reserved_bytes'] = 'wrong type'
        else:
            effect['reserved_bytes'] = 512 * 1024**2 + 1
        return jobs
    store.atomic_update(crawl_jobs._KEY, corrupt, category=crawl_jobs._CATEGORY)
    with pytest.raises(AcquisitionError) as error:
        crawl_jobs.inspect_jobs()
    assert error.value.code == FetchErrorCode.STORAGE


def test_scope_digest_tracks_account_changes_without_persisting_secrets(tmp_path, monkeypatch):
    from kazma_core import model_registry
    store = config_store.ConfigStore(str(tmp_path / 'scope.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    class Registry:
        def get_active_profile(self):
            return dict(provider='fixture', model='fixture', api_key='***')
    monkeypatch.setattr(model_registry, 'get_model_registry', lambda: Registry())
    try:
        monkeypatch.setenv('FIXTURE_API_KEY', 'account-one-secret')
        first = stages._scope_signature()
        monkeypatch.setenv('FIXTURE_API_KEY', 'account-two-secret')
        second = stages._scope_signature()
        assert len(first) == len(second) == 64 and first != second
        assert 'secret' not in first + second
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', ['', 'x' * 201, {'not': 'an ID'}])
async def test_invalid_resume_identity_refuses_before_admission(journal, identity):
    _, calls = journal
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS, resume_job_id=identity)
    assert result.startswith('Error: resume_job_id') and not calls and crawl_jobs.inspect_jobs() == []


@pytest.mark.asyncio
async def test_registration_storage_failure_is_visible_after_report_publication(journal, monkeypatch, tmp_path):
    def refuse(path, records):
        if path.name == 'index.json':
            raise OSError('fixture write denied')
        return metadata(path, records)
    metadata = pipeline._write_source_metadata
    monkeypatch.setattr(pipeline, '_write_source_metadata', refuse)
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS)
    assert result.startswith('Error:') and 'OSError' in result
    row, = crawl_jobs.inspect_jobs()
    assert row['status'] == 'interrupted'
    assert crawl_jobs.inspect_jobs(row['job_id'])['effects']['paper-registration']['status'] == 'failed'
    assert list((tmp_path / 'workspace').rglob('report.md'))


@pytest.mark.asyncio
async def test_cached_status_distinguishes_fetch_success_from_publication_failure(journal, monkeypatch):
    original = research_files.create_extract
    def denied(target, text):
        if target.name == 'src-01.md':
            raise OSError('fixture output denied')
        return original(target, text)
    monkeypatch.setattr(research_files, 'create_extract', denied)
    result = await pipeline.run_research_pipeline('fixture topic', **OPTIONS)
    assert result.startswith('# Deep research complete')
    summary, = crawl_jobs.inspect_jobs()
    row = crawl_jobs.inspect_jobs(summary['job_id'])
    source = row['effects']['acquire:001']
    assert source['receipt']['ok'] is True and source['outcome_ok'] is False
    assert summary['failed'] == crawl_jobs.projection(summary['job_id'])['failed'] == 1


def test_restore_decodes_the_verified_bytes_without_reopening(tmp_path, monkeypatch):
    directory = tmp_path / 'research' / 'reports' / 'pipeline-fixture'
    directory.mkdir(parents=True)
    target = directory / 'value.json'
    target.write_text('{"verified": true}', encoding='utf-8')
    job = stages._PipelineJob(None, tmp_path, directory, 'fixture')
    effect = dict(files=[], value=job.file_ref(target))
    original = Path.read_text
    def substituted(path, *args, **kwargs):
        return '{"verified": false}' if path == target else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', substituted)
    # Negative control: the former second-open boundary consumes substituted data.
    assert json.loads(target.read_text(encoding='utf-8')) == {'verified': False}
    assert job.restore(effect) == {'verified': True}


def test_restore_rejects_nonregular_descriptor_before_reading(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    directory = tmp_path / 'research' / 'reports' / 'pipeline-fixture'
    directory.mkdir(parents=True)
    target = directory / 'value.json'
    target.write_text('{}', encoding='utf-8')
    job = stages._PipelineJob(None, tmp_path, directory, 'fixture')
    effect = dict(files=[], value=job.file_ref(target))
    monkeypatch.setattr(stages.os, 'fstat', lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO))
    monkeypatch.setattr(stages.os, 'fdopen', lambda *a, **k: pytest.fail('Nonregular artifact was read'))
    with pytest.raises(AcquisitionError) as error:
        job.restore(effect)
    assert error.value.code == FetchErrorCode.STORAGE


def test_restore_value_is_bounded_during_the_verified_read(tmp_path):
    directory = tmp_path / 'research' / 'reports' / 'pipeline-fixture'
    directory.mkdir(parents=True)
    target = directory / 'value.json'
    target.write_bytes(b' ' * (stages._MAX_VALUE + 1))
    job = stages._PipelineJob(None, tmp_path, directory, 'fixture')
    effect = dict(files=[], value=job.file_ref(target))
    with pytest.raises(AcquisitionError) as error:
        job.restore(effect)
    assert error.value.code == FetchErrorCode.LIMIT
