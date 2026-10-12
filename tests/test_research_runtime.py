"""Finite background/direct research and frozen publication scope."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kazma_core.tools import research_pipeline as pipeline, research_session as sessions
from kazma_core.web_acquire import resources, policy, crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError, FetchResult


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, '_RESEARCH', resources._CapacityPool('research pipelines', 1, 0))
    monkeypatch.setattr(resources, '_RESEARCH_SESSIONS', resources._CapacityPool('background research sessions', 2, 0))
    monkeypatch.setattr(sessions, '_db_path', lambda: tmp_path / 'sessions.db')
    monkeypatch.setattr(pipeline, '_get_ws_root', lambda: tmp_path)
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', lambda **kw: {'ready': True})
    return tmp_path


@pytest.mark.parametrize('fixed', [True, False], ids=['bounded-entry', 'original-unbounded-entry'])
def test_direct_pipeline_admission_is_shared_across_threads_before_planning(controlled, monkeypatch, fixed):
    from kazma_core.tools.research_planner import ResearchPlan
    started, release = threading.Event(), threading.Event()
    planned = []
    async def plan(topic, **kwargs):
        planned.append(topic)
        started.set()
        if topic == 'first':
            assert await asyncio.to_thread(release.wait, 15)
        return ResearchPlan(search_queries=[])
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', plan)
    run = pipeline.run_research_pipeline if fixed else pipeline.run_research_pipeline.__wrapped__.__wrapped__
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(asyncio.run, run('first'))
        try:
            assert started.wait(15)
            denied = asyncio.run(run('second'))
            if fixed:
                assert denied.startswith('Error:') and 'capacity exhausted' in denied
                assert planned == ['first']
            else:
                assert 'found no URLs' in denied and planned == ['first', 'second']
            assert len(list((controlled / 'research/reports').iterdir())) == (1 if fixed else 2)
        finally:
            release.set()
            first.result(timeout=15)
    assert resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('fixed', [True, False], ids=['bounded-start', 'original-unbounded-start'])
async def test_background_admission_refuses_before_session_or_task_creation(controlled, monkeypatch, fixed):
    entered, release = asyncio.Event(), asyncio.Event()
    async def run(*args, **kwargs):
        entered.set()
        await release.wait()
        return 'Error: fixture finished'
    monkeypatch.setattr(pipeline, 'run_research_pipeline', run)
    start = sessions.start_deep_research if fixed else sessions.start_deep_research.__wrapped__
    first = await start('first')
    second = await start('second')
    tasks = [sessions._RUNNING[first.id], sessions._RUNNING[second.id]]
    try:
        await asyncio.wait_for(entered.wait(), 15)
        if fixed:
            with pytest.raises(AcquisitionError, match='capacity exhausted'):
                await start('third')
        else:
            third = await start('third')
            tasks.append(sessions._RUNNING[third.id])
        assert len(sessions.list_sessions()) == len(sessions._RUNNING) == (2 if fixed else 3)
        assert resources.research_session_capacity().snapshot()['active'] == (2 if fixed else 0)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert resources.research_session_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_background_keeps_initial_workspace_and_operator_policy(controlled, monkeypatch):
    from kazma_core.workspace import binding
    from kazma_core.ide.workspace_scope import current_workspace_path
    first, second = controlled / 'first', controlled / 'second'
    first.mkdir();second.mkdir()
    monkeypatch.setattr(binding, '_WORKSPACE_ROOT', first)
    monkeypatch.setattr('kazma_core.stores.get_workspace_store', lambda:
                        SimpleNamespace(get_active_workspace=lambda: None))
    entered, release, observations = asyncio.Event(), asyncio.Event(), []
    async def run(*args, **kwargs):
        entered.set()
        await release.wait()
        observations.append((current_workspace_path(), policy.acquisition_policy().mode))
        return 'Error: fixture finished'
    monkeypatch.setattr(pipeline, 'run_research_pipeline', run)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    session = await sessions.start_deep_research('scope')
    task = sessions._RUNNING[session.id]
    try:
        await asyncio.wait_for(entered.wait(), 15)
        binding.configure_workspace(str(second))
        monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
        monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    finally:
        release.set()
        await task
    assert observations == [(str(first.resolve()), 'local_only')]
    assert current_workspace_path() is None


@pytest.mark.asyncio
async def test_background_cancelled_before_its_first_step_releases_ownership(controlled, monkeypatch):
    called = []
    async def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('A cancelled-before-start task reached the pipeline')
    monkeypatch.setattr(pipeline, 'run_research_pipeline', forbidden)
    session = await sessions.start_deep_research('cancel before first step')
    task = sessions._RUNNING[session.id]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner = sessions._running_task(session.id)
    if owner is not None:
        await owner
    assert sessions.get_session(session.id).status == 'cancelled'
    assert not called and session.id not in sessions._RUNNING
    assert resources.research_session_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_cancel_before_background_creation_joins_the_session_commit(controlled, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = sessions.create_session
    def create(*args, **kwargs):
        started.set()
        assert release.wait(15)
        return original(*args, **kwargs)
    monkeypatch.setattr(sessions, 'create_session', create)
    joining = asyncio.Event()
    original_join = crawl_jobs.finish_owned
    async def join(receipt):
        joining.set()
        return await original_join(receipt)
    monkeypatch.setattr(crawl_jobs, 'finish_owned', join)
    task = asyncio.create_task(sessions.start_deep_research('cancelled before dispatch'))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        task.cancel()
        await asyncio.wait_for(joining.wait(), 10)
        assert not task.done() and resources.research_session_capacity().snapshot()['active'] == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    stored, = sessions.list_sessions()
    assert stored.status == 'cancelled' and stored.id not in sessions._RUNNING
    assert resources.research_session_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_start_api_reports_saturation_without_a_false_acceptance(controlled):
    from fastapi import FastAPI
    from kazma_ui.research_panel.routes import create_research_router
    leases = [await resources.research_session_capacity().acquire() for _ in range(2)]
    app = FastAPI()
    app.include_router(create_research_router())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://fixture') as client:
            response = await client.post('/api/research/sessions', json={'topic': 'not admitted'})
        assert response.status_code == 503
        assert response.json()['error_code'] == 'resource_limit' and not response.json()['ok']
        assert not sessions.list_sessions() and not sessions._RUNNING
    finally:
        for lease in leases:
            lease.release()


def test_same_topic_in_the_same_second_gets_distinct_report_directories(controlled, monkeypatch):
    instant = pipeline.datetime(2026, 10, 11, tzinfo=pipeline.UTC)
    monkeypatch.setattr(pipeline, 'datetime', SimpleNamespace(now=lambda zone: instant))
    first = pipeline._workspace_research_dir('same topic')
    second = pipeline._workspace_research_dir('same topic')
    assert first != second and first.is_dir() and second.is_dir()


@pytest.mark.asyncio
async def test_pipeline_deadline_joins_async_cleanup_and_releases_admission(controlled, monkeypatch):
    from kazma_core.web_acquire import research_runtime
    cleanup = asyncio.Event()
    async def plan(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
    from kazma_core.web_acquire import artifact_storage
    async def already_qualified_inventory(*args):
        return {}
    monkeypatch.setattr(artifact_storage._OWNERS, 'call', already_qualified_inventory)
    monkeypatch.setattr(research_runtime, '_DEADLINE_SECONDS', .05)
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', plan)
    report = await pipeline.run_research_pipeline('deadline')
    assert report.startswith('Error:') and 'deadline expired' in report and cleanup.is_set()
    assert resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_inner_timeout_is_not_mislabeled_as_the_pipeline_deadline(controlled, monkeypatch):
    async def plan(*args, **kwargs):
        raise TimeoutError('fixture inner timeout')
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', plan)
    with pytest.raises(TimeoutError, match='fixture inner timeout'):
        await pipeline.run_research_pipeline('inner timeout')
    assert resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_pipeline_preflight_does_not_read_settings_on_the_event_loop(controlled, monkeypatch):
    from kazma_core.tools.research_planner import ResearchPlan
    main_thread, observed = threading.get_ident(), []
    def ready(**kwargs):
        observed.append(threading.get_ident())
        return {'ready': True}
    async def plan(*args, **kwargs):
        return ResearchPlan(search_queries=[])
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', ready)
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', plan)
    report = await pipeline.run_research_pipeline('off-loop preflight')
    assert 'found no URLs' in report and observed and main_thread not in observed


@pytest.mark.asyncio
async def test_cancellation_keeps_capacity_until_the_preflight_worker_ends(controlled, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def ready(**kwargs):
        started.set()
        assert release.wait(15)
        finished.set()
        return {'ready': True}
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', ready)
    joining = asyncio.Event()
    original_join = crawl_jobs.finish_owned
    async def join(receipt):
        joining.set()
        return await original_join(receipt)
    monkeypatch.setattr(crawl_jobs, 'finish_owned', join)
    task = asyncio.create_task(pipeline.run_research_pipeline('cancelled preflight'))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        task.cancel()
        await asyncio.wait_for(joining.wait(), 10)
        assert not task.done() and resources.research_capacity().snapshot()['active'] == 1
        assert not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set() and resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_save_keeps_starting_workspace_when_fetch_switches_active_root(controlled, monkeypatch):
    from kazma_core.tools import read_url
    from kazma_core.workspace import binding
    first, second = controlled / 'first', controlled / 'second'
    first.mkdir();second.mkdir()
    monkeypatch.setattr(binding, '_WORKSPACE_ROOT', first)
    monkeypatch.setattr('kazma_core.stores.get_workspace_store', lambda:
                        SimpleNamespace(get_active_workspace=lambda: None))
    async def fetch(url, **kwargs):
        binding.configure_workspace(str(second))
        return FetchResult(ok=True, url=url, text='complete')
    monkeypatch.setattr(read_url, 'fetch_result', fetch)
    saved = await read_url.save_url_extract('https://docs.example/', path='article.md')
    assert saved.ok and len(list(first.rglob('*.md'))) == 1 and not list(second.rglob('*.md'))


@pytest.mark.asyncio
async def test_save_cancellation_waits_for_actual_atomic_publication(controlled, monkeypatch):
    from kazma_core.tools import read_url, research_files
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = research_files.create_extract
    def publish(target, text):
        started.set()
        assert release.wait(15)
        try:
            original(target, text)
        finally:
            finished.set()
    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text='complete')
    monkeypatch.setattr(read_url, '_workspace_root', lambda: controlled)
    monkeypatch.setattr(read_url, 'fetch_result', fetch)
    monkeypatch.setattr(research_files, 'create_extract', publish)
    joining = asyncio.Event()
    original_join = crawl_jobs.finish_owned
    async def join(receipt):
        joining.set()
        return await original_join(receipt)
    monkeypatch.setattr(crawl_jobs, 'finish_owned', join)
    task = asyncio.create_task(read_url.save_url_extract('https://docs.example/', path='article.md'))
    try:
        async with asyncio.timeout(15):
            while not started.is_set():
                await asyncio.sleep(.01)
        task.cancel()
        await asyncio.wait_for(joining.wait(), 10)
        assert not task.done() and not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set() and (controlled / 'research/article.md').is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [OSError('disk unavailable'), RuntimeError('settings unavailable'), ValueError('invalid receipt')])
async def test_failed_preflight_stops_before_planning_or_output(controlled, monkeypatch, failure):
    def ready(**kwargs):
        raise failure
    async def forbidden(*args, **kwargs):
        raise AssertionError('Failed preflight reached planning')
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', ready)
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', forbidden)
    stages = []
    report = await pipeline.run_research_pipeline('refuse broken readiness', progress_cb=lambda stage, message: stages.append(stage))
    assert report.startswith('Error:') and type(failure).__name__ in report
    assert stages == ['preflight', 'error'] and not (controlled / 'research').exists()
    assert resources.research_capacity().snapshot()['active'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [None, {}, {'ready': 'false'}, {'ready': 1}, {'ready': False}])
async def test_preflight_requires_an_explicit_boolean_ready_receipt(controlled, monkeypatch, receipt):
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', lambda **kwargs: receipt)
    async def forbidden(*args, **kwargs):
        raise AssertionError('Invalid or unready receipt reached planning')
    monkeypatch.setattr('kazma_core.tools.research_planner.plan_research_queries', forbidden)
    report = await pipeline.run_research_pipeline('invalid readiness')
    assert report.startswith('Error:') and not (controlled / 'research').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['bounded', 'original-dispatch', 'replace-failure', 'gap-fill'])
async def test_pipeline_limits_actual_publications_and_keeps_every_success(controlled, monkeypatch, scenario):
    from kazma_core.tools import read_url, research_planner, research_synthesize, research_evidence
    from kazma_core.web_acquire import serp
    from kazma_core.web_acquire.result import FetchErrorCode
    import kazma_core.web_acquire as acquisition

    maximum = 2 if scenario in {'bounded', 'original-dispatch'} else 3
    urls = [f'https://source{i}.example/article' for i in range(6)]
    followups = [f'https://gap{i}.example/article' for i in range(2)]
    async def plan(*args, **kwargs):
        return research_planner.ResearchPlan(search_queries=['initial'])
    async def search(query, **kwargs):
        return serp.SearchResult(ok=True, query=query, urls=followups if query == 'followup' else urls)
    monkeypatch.setattr(research_planner, 'plan_research_queries', plan)
    monkeypatch.setattr(acquisition, 'search', search)
    # Keep ranking deterministic so a failed low-index candidate can precede a
    # successful later candidate. Publication itself uses the real exclusive save.
    monkeypatch.setattr(acquisition, 'rank_urls', lambda candidates, *args, **kwargs:
                        [SimpleNamespace(url=url, score=1, reasons=[]) for url in candidates])
    monkeypatch.setattr(read_url, '_workspace_root', lambda: controlled)
    monkeypatch.setattr(read_url, '_safe_workspace_path', lambda path, url: controlled / path)
    monkeypatch.setattr(pipeline, '_register_paper', lambda meta: None)
    monkeypatch.setattr(research_evidence, 'extract_claims_from_file', lambda *args, **kwargs: [])
    async def digest(path):
        return 'Source digest'
    async def synthesize(*args, **kwargs):
        return '## Executive summary\nFixture synthesis.\n## Sources\n' + '\n'.join(urls)
    async def gaps(*args, **kwargs):
        return SimpleNamespace(method='fixture', needs_more=True, followup_queries=['followup'], gaps=[], to_dict=lambda: {})
    monkeypatch.setattr(read_url, 'digest_research_file', digest)
    monkeypatch.setattr(research_synthesize, 'synthesize_from_digests', synthesize)
    monkeypatch.setattr(research_planner, 'critique_synthesis_gaps', gaps)
    monkeypatch.setenv('KAZMA_RESEARCH_GAP_LOOP', '1' if scenario == 'gap-fill' else '0')
    monkeypatch.setenv('KAZMA_RESEARCH_ALLOW_THIN', '1')

    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    target = 4 if scenario == 'original-dispatch' else maximum
    async def fetch(url, **kwargs):
        observed.append(url)
        if len(observed) == target:
            entered.set()
        await release.wait()
        fails = scenario == 'replace-failure' and url == urls[0] or scenario == 'gap-fill' and url in urls and url != urls[1]
        return FetchResult(ok=not fails, url=url, text='Complete source evidence.',
            error='Fixture refusal' if fails else None, error_code=FetchErrorCode.SECURITY if fails else None)
    monkeypatch.setattr(read_url, 'fetch_result', fetch)
    if scenario == 'original-dispatch':
        async def original_dispatch(candidates, acquire, saved, maximum, concurrency, next_index, persist):
            await asyncio.gather(*[acquire(url, i + next_index) for i, url in enumerate(candidates)])
            await persist()
            return next_index + len(candidates)
        monkeypatch.setattr(pipeline, '_acquire_source_batches', original_dispatch)

    task = asyncio.create_task(pipeline.run_research_pipeline('publication budget', depth='deep', max_sources=maximum))
    try:
        await asyncio.wait_for(entered.wait(), 15)
    finally:
        release.set()
        report = await task
    assert report.startswith('# Deep research complete')
    directory, = (controlled / 'research/reports').iterdir()
    published = sorted(directory.glob('src-*.md'))
    records = json.loads((directory / 'sources.json').read_text(encoding='utf-8'))
    successes = [row for row in records if row['ok']]
    if scenario == 'original-dispatch':
        assert 4 <= len(published) <= len(urls) and len(successes) == maximum
    elif scenario == 'gap-fill':
        # Gap acquisitions must neither reuse src-02 nor lose the new receipts.
        assert {path.name for path in published} == {'src-02.md', 'src-07.md', 'src-08.md'}
        assert len(successes) == maximum
        assert {Path(row['path']).name for row in successes} == {path.name for path in published}
    else:
        assert len(published) == len(successes) == maximum
        assert len(observed) == maximum + (scenario == 'replace-failure')
        assert {Path(row['path']).name for row in successes} == {path.name for path in published}


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_write', [1, 2])
async def test_source_receipt_storage_failure_stops_before_another_batch(controlled, monkeypatch, fail_write):
    from kazma_core.tools import read_url, research_planner
    from kazma_core.web_acquire.serp import SearchResult
    from kazma_core.web_acquire.result import FetchErrorCode
    import kazma_core.web_acquire as acquisition

    urls = [f'https://source{i}.example/article' for i in range(6)]
    async def plan(*args, **kwargs):
        return research_planner.ResearchPlan(search_queries=['initial'])
    async def search(query, **kwargs):
        return SearchResult(ok=True, query=query, urls=urls)
    observed = []
    async def fetch(url, **kwargs):
        observed.append(url)
        return FetchResult(ok=False, url=url, error='Fixture refusal', error_code=FetchErrorCode.SECURITY)
    writes = []
    original = pipeline._write_source_metadata
    def persist(*args):
        writes.append(True)
        if len(writes) == fail_write:
            raise OSError('Fixture disk failure')
        original(*args)
    monkeypatch.setattr(research_planner, 'plan_research_queries', plan)
    monkeypatch.setattr(acquisition, 'search', search)
    monkeypatch.setattr(read_url, 'fetch_result', fetch)
    monkeypatch.setattr(pipeline, '_write_source_metadata', persist)
    report = await pipeline.run_research_pipeline('receipt failure', max_sources=3)
    assert report.startswith('Error:') and 'receipts could not be persisted' in report
    assert len(observed) == (0 if fail_write == 1 else 3)
    assert resources.research_capacity().snapshot()['active'] == 0


def test_source_metadata_replacement_failure_preserves_prior_complete_json(controlled, monkeypatch):
    target = controlled / 'sources.json'
    pipeline._write_source_metadata(target, [{'url': 'https://prior.example/', 'ok': True}])
    prior = target.read_bytes()
    def refuse(*args):
        raise OSError('Fixture replace failure')
    monkeypatch.setattr(pipeline.os, 'replace', refuse)
    with pytest.raises(OSError, match='replace failure'):
        pipeline._write_source_metadata(target, [{'url': 'https://new.example/', 'ok': True}])
    assert target.read_bytes() == prior
    assert not list(controlled.glob('.kazma-sources-*'))
