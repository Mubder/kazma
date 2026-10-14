"""Durable controls survive new owners and refuse repeated/unknown effects."""
import asyncio
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kazma_core import config_store
from kazma_core.tools import research_session as sessions
from kazma_core.web_acquire import origin_state, origins, robots, robots_state, crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.security.process_budget import run_bounded_async


@pytest.fixture
def durable(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setattr(sessions, '_db_path', lambda: tmp_path / 'sessions.db')
    yield store
    store.close()


@pytest.mark.asyncio
async def test_origin_cooldown_survives_new_scheduler_and_redirect(durable):
    first = origins._OriginScheduler(durable=True)
    first.observe('https://a.example/', 302, httpx.Headers({'Retry-After': '120', 'Location': 'https://b.example/path'}))
    for url in ('https://a.example/new', 'https://b.example/new'):
        with pytest.raises(AcquisitionError, match='cooldown'):
            async with origins._OriginScheduler(durable=True).request(url):
                pytest.fail('cooldown lost on restart')


@pytest.mark.asyncio
async def test_origin_cross_owner_capacity_and_release(durable):
    key = origins._origin('https://limit.example/')
    one, two, three = (origin_state.queue(key) for _ in range(3))
    assert origin_state.start(key, one, 0) == 0
    assert origin_state.start(key, two, 0) == 0
    assert origin_state.start(key, three, 0) > 0
    origin_state.release(key, one)
    assert origin_state.start(key, three, 0) == 0
    origin_state.release(key, two)
    origin_state.release(key, three)
    assert not durable.get_category('acquisition_origins')['acquisition.origins'][origin_state._key(key)]['active']


@pytest.mark.parametrize('retry', ['9' * 300, '120'])
def test_server_delay_never_cleared_by_older_success(durable, retry):
    key = origins._origin('https://feedback.example/')
    scheduler = origins._OriginScheduler(durable=True)
    scheduler.observe('https://feedback.example/', 429, httpx.Headers({'retry-after': retry}))
    scheduler.observe('https://feedback.example/', 200, httpx.Headers())
    token = origin_state.queue(key)
    try:
        with pytest.raises(AcquisitionError, match='cooldown'):
            origin_state.start(key, token, 0)
    finally:
        origin_state.release(key, token)


@pytest.mark.asyncio
async def test_persisted_robots_rules_reused_without_http(durable, monkeypatch):
    manager = robots._RobotsManager(durable=True)
    async def load(*args):
        return robots._parse('User-agent: *\nDisallow: /secret\nCrawl-delay: 2')
    monkeypatch.setattr(manager, '_load', load)
    value, cached = await manager.get('https://rules.example/robots.txt', None)
    assert not cached and not value.allows('https://rules.example/secret')
    after_restart = robots._RobotsManager(durable=True)
    async def forbidden(*args):
        pytest.fail('durable robots policy was not reused')
    monkeypatch.setattr(after_restart, '_load', forbidden)
    saved, cached = await after_restart.get('https://rules.example/robots.txt', None)
    assert cached and saved.delay == 2 and not saved.allows('https://rules.example/secret')


def test_interrupted_robots_lookup_denies_until_retry(durable):
    value, claim = robots_state.begin('fixture')
    assert claim and value.status == 'unavailable'
    value, second = robots_state.begin('fixture')
    assert second is None and not value.allows('https://rules.example/')


def test_storage_unavailable_refuses_origin_before_dispatch(durable, monkeypatch):
    monkeypatch.setattr(durable, 'get_category', lambda *a: (_ for _ in ()).throw(ValueError('broken')))
    with pytest.raises(AcquisitionError, match='state unavailable'):
        origin_state.queue(origins._origin('https://broken.example/'))


def test_session_history_bound_is_visible_and_archive_does_not_release(durable, monkeypatch):
    monkeypatch.setattr(sessions, '_MAX_SESSIONS', 2)
    one = sessions.create_session('one')
    sessions.archive_session(one.id)
    sessions.create_session('two')
    with pytest.raises(AcquisitionError, match='history is full'):
        sessions.create_session('three')


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['plan', 'terminal'])
async def test_fresh_process_session_recovery_never_replays_completed_or_uncertain_work(durable, tmp_path, monkeypatch, crash):
    helper = Path(__file__).with_name('test_research_pipeline_recovery.py').resolve()
    core = Path(sessions.__file__).resolve().parents[2]
    root = tmp_path / 'workspace'
    program = f"""
import sys,runpy,asyncio,os
from pathlib import Path
sys.path.insert(0,{str(core)!r})
import pytest
from kazma_core import config_store
from kazma_core.tools import research_session as sessions
from kazma_core.workspace import binding
helpers=runpy.run_path({str(helper)!r})
mp=pytest.MonkeyPatch()
store=config_store.ConfigStore({str(tmp_path / 'settings.db')!r})
helpers['install'](mp,Path({str(root)!r}),store,[],crash={'plan' if crash == 'plan' else ''!r})
mp.setattr(sessions,'_db_path',lambda:Path({str(tmp_path / 'sessions.db')!r}))
mp.setattr(binding,'resolve_active_root',lambda:Path({str(root)!r}))
if {crash!r} == 'terminal':
    original=sessions.update_session
    def update(session_id,**fields):
        if fields.get('status') == 'done': os._exit(23)
        return original(session_id,**fields)
    mp.setattr(sessions,'update_session',update)
async def run():
    session=await sessions.start_deep_research('fixture topic',depth='standard',max_sources=3)
    await sessions._RUNNING[session.id]
asyncio.run(run())
"""
    # The child is a process of THIS install (its data dir): the crawl
    # journal is scoped per install since Knowledge audit KB-E07.
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path', 'kazma_data_dir'}
    receipt = await run_bounded_async([sys.executable, '-I', '-X', 'utf8', '-c', program], env={k:v for k,v in os.environ.items() if k.lower() in allowed}, timeout=45, output_bytes=32768, memory_bytes=1024**3, text=True)
    assert receipt.returncode == 23, receipt.stderr + receipt.stdout
    with monkeypatch.context() as control:
        control.setattr(sessions, '_reconcile_sessions', lambda connection: None)
        original_row, = sessions.list_sessions()
        assert original_row.status == 'running'  # original post-crash zombie row
    row, = sessions.list_sessions()
    assert row.status == 'interrupted' and row.meta['durable_job_id']
    calls = []
    runpy.run_path(str(helper))['install'](monkeypatch, root, durable, calls)
    monkeypatch.setattr('kazma_core.workspace.binding.resolve_active_root', lambda: root)
    recovered = await sessions.start_deep_research('', resume_session_id=row.id)
    task = sessions._RUNNING.get(recovered.id)
    if task:
        await task
    final = sessions.get_session(row.id)
    assert not calls
    if crash == 'plan':
        assert final.status == 'error' and 'uncertain' in final.error
    else:
        assert final.status == 'done' and final.report_path


@pytest.mark.asyncio
async def test_frozen_failover_does_not_follow_settings_changed_during_primary(monkeypatch):
    from kazma_core.web_acquire.research_llm import ResearchRoute
    from kazma_core.agent import resilient_chat as module
    from kazma_core.llm_provider import LLMError
    from kazma_core import model_registry
    from kazma_core.agent import nonstop
    calls = []
    failover = SimpleNamespace(enabled=True, chain=['backup'], cooldown_seconds=0)
    class Client:
        def __init__(self, name):
            self.config = SimpleNamespace(model=name, api_key='original')
        async def chat(self, *args, **kwargs):
            calls.append((self.config.model, self.config.api_key))
            if self.config.model == 'primary':
                original.config.api_key = 'changed'
                fallback.config.api_key = 'changed'
                failover.chain = ['other']
                raise LLMError('retry', transient=True)
            return SimpleNamespace(content='saved route', usage={})
    original, fallback = Client('primary'), Client('backup')
    monkeypatch.setattr(model_registry, 'get_model_registry', lambda: SimpleNamespace(get_client=lambda name=None: fallback if name else original))
    monkeypatch.setattr(nonstop, 'get_nonstop_config', lambda: SimpleNamespace(failover=failover, ledger_enabled=False))
    monkeypatch.setattr(module, '_cooldowns', {})
    route = ResearchRoute(lambda: True)
    client, model, chain = route.resolve()
    result = await module.resilient_chat(client, messages=[], model=model, frozen_failover=chain, max_attempts=1)
    assert result.content == 'saved route'
    assert calls == [('primary','original'),('backup','original')]
    assert route.resolve() == (client, model, chain)


def test_strict_browser_refuses_unqualified_windows_before_driver_start(durable, monkeypatch):
    from kazma_core.web_acquire import browser_isolation
    monkeypatch.setenv('KAZMA_BROWSER_NETWORK_MODE', 'required')
    monkeypatch.setattr(browser_isolation.sys, 'platform', 'win32')
    with pytest.raises(AcquisitionError, match='launch refused'):
        browser_isolation.network_command(['node', 'run-driver'])


@pytest.mark.parametrize('cap', ['0', '1.25', 'NaN', '-1', 'invalid'])
def test_money_cap_cannot_dispatch_unknown_charges(durable, monkeypatch, cap):
    from kazma_core.web_acquire import accounting
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    monkeypatch.setenv('KAZMA_ACQUISITION_MAX_COST_USD', cap)
    with pytest.raises(AcquisitionError, match='cap'):
        accounting.reserve('jina', accounting.account_fingerprint('jina', 'fixture'))
    assert durable.get_category('acquisition_allowances') == {}


@pytest.mark.parametrize('budget, accepted', [('500', True), ('50000', True), ('50001', False), ('0', False), ('١٢', False)])
def test_jina_budget_has_finite_provider_enforced_ceiling(monkeypatch, budget, accepted):
    from kazma_core.web_acquire.provider_usage import jina_token_budget
    monkeypatch.setenv('KAZMA_JINA_TOKEN_BUDGET', budget)
    if accepted:
        assert jina_token_budget() == int(budget)
    else:
        with pytest.raises(AcquisitionError):
            jina_token_budget()


@pytest.mark.asyncio
async def test_retained_storage_accounts_legacy_reports_before_admitting_output(durable, tmp_path, monkeypatch):
    from kazma_core.web_acquire import artifact_storage
    from kazma_core.tools import research_pipeline as pipeline
    root = tmp_path / 'workspace'
    legacy = root / 'research' / 'reports' / 'legacy'
    legacy.mkdir(parents=True)
    (legacy / 'report.md').write_text('Saved owner report')
    calls = []
    helper = Path(__file__).with_name('test_research_pipeline_recovery.py')
    runpy.run_path(str(helper))['install'](monkeypatch, root, durable, calls)
    monkeypatch.setattr(artifact_storage, '_LIMIT', artifact_storage._RESERVATION + 1)
    result = await pipeline.run_research_pipeline('fixture topic', depth='standard', max_sources=3)
    assert result.startswith('Error:') and 'storage allowance exhausted' in result
    assert not calls and (legacy / 'report.md').read_text() == 'Saved owner report'


@pytest.mark.asyncio
async def test_settled_storage_survives_journal_eviction(durable, tmp_path, monkeypatch):
    from kazma_core.web_acquire import artifact_storage
    from kazma_core.tools import research_pipeline as pipeline
    root = tmp_path / 'workspace'
    helper = Path(__file__).with_name('test_research_pipeline_recovery.py')
    runpy.run_path(str(helper))['install'](monkeypatch, root, durable, [])
    for topic in ('first saved report', 'second saved report'):
        result = await pipeline.run_research_pipeline(topic, depth='standard', max_sources=3)
        assert result.startswith('# Deep research complete')
    before = artifact_storage.snapshot()
    assert before['retained_directories'] == 2 and 0 < before['reserved_bytes'] < 1024**2
    durable.batch_set([('acquisition.crawls', {}, 'acquisition_crawls')])
    assert artifact_storage.snapshot()['reserved_bytes'] == before['reserved_bytes']


def test_settings_status_is_local_and_credit_refresh_is_explicit(durable, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.research_panel.routes import create_research_router
    from kazma_core.web_acquire import credits
    calls = []
    async def refresh():
        calls.append(True)
    monkeypatch.setattr(credits, 'refresh_firecrawl_balance', refresh)
    app = FastAPI(); app.include_router(create_research_router())
    with TestClient(app) as client:
        status = client.get('/api/research/acquisition?language=ar')
        assert status.status_code == 200 and not calls
        assert 'قراءة محلية' in status.json()['text']
        refreshed = client.post('/api/research/acquisition/credits')
        assert refreshed.status_code == 200 and calls == [True]


def test_standalone_output_allowance_retains_uncertain_reservation(durable, tmp_path, monkeypatch):
    from kazma_core.web_acquire import output_allowance
    monkeypatch.setattr(output_allowance, '_LIMIT', 10)
    output_allowance.reserve(tmp_path / 'first', 7)
    with pytest.raises(AcquisitionError, match='exhausted'):
        output_allowance.reserve(tmp_path / 'second', 4)
    with pytest.raises(AcquisitionError, match='uncertain'):
        output_allowance.reserve(tmp_path / 'first', 1)
    assert not (tmp_path / 'first').exists()


def test_ambient_research_credentials_are_refused_before_sdk_lookup():
    from kazma_core.bedrock_llm import BedrockProvider
    from kazma_core.google_llm import GeminiProvider
    from kazma_core.web_acquire.research_llm import ResearchRoute
    for kind in (BedrockProvider, GeminiProvider):
        client = object.__new__(kind)
        client._use_ai_studio = False
        with pytest.raises(AcquisitionError, match='frozen credential route'):
            ResearchRoute._snapshot(client)


def test_opaque_sdk_fanout_is_skipped_before_construction(monkeypatch):
    import importlib
    search = importlib.import_module('kazma_core.tools.web_search')
    from kazma_core.proxy import client
    monkeypatch.setattr(client, 'get_active_proxy_url', lambda: None)
    assert search._ddg_search('fixture', 2) == (None, 'duckduckgo:skipped_opaque_origin_fanout')


@pytest.mark.asyncio
async def test_native_browser_refuses_workspace_or_tenant_switch_before_page_access(monkeypatch):
    from kazma_core.web_acquire import interactive
    monkeypatch.setattr(interactive, '_scope', lambda: ('first', 'tenant'))
    owner = interactive._Owner()
    monkeypatch.setattr(interactive, '_scope', lambda: ('other', 'tenant'))
    with pytest.raises(AcquisitionError, match='another workspace or tenant'):
        await owner.invoke(('goto',), ('https://public.example',), {})


def test_knowledge_disk_ceiling_refuses_growth_and_preserves_prior_rows(tmp_path, monkeypatch):
    import sqlite3
    from kazma_core.stores import knowledge
    monkeypatch.setattr(knowledge, '_MAX_KNOWLEDGE_BYTES', 1024**2)
    store = knowledge.KnowledgeStore(str(tmp_path / 'knowledge.db'))
    try:
        store.create_library('fixture', 'Fixture')
        with pytest.raises(sqlite3.DatabaseError, match='full'):
            store.upsert_chunk(dict(id='oversized', library_id='fixture', source_url='https://public.example',
                chunk_index=0, content_hash='fixture', content='large data ' * 200000))
        assert store.get_library('fixture') is not None
        assert store.count_chunks('fixture') == 0
        assert store.path.stat().st_size <= 1024**2
    finally:
        store.close()
