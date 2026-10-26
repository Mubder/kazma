"""Cached operations must not turn health/status reads into live searches."""
import asyncio
import json
import os
import threading
from types import SimpleNamespace

import httpx
import pytest

from kazma_core import config_store
from kazma_core.tools import research_readiness


@pytest.fixture
def settings(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    monkeypatch.setenv('KAZMA_RESEARCH_PREFLIGHT_LIVE', '1')
    monkeypatch.setenv('KAZMA_SEARXNG_URL', 'http://owner:private-password@localhost:8080/?token=private-token')
    yield store
    store.close()


@pytest.mark.parametrize('surface', ['direct', 'health', 'api', 'explicit-live', 'original-env-override'])
def test_cached_readiness_does_not_probe_even_with_legacy_live_environment(settings, monkeypatch, surface):
    import kazma_core.web_acquire as acquisition
    calls = []
    async def search(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(ok=True, urls=['https://fixture.example/'], markdown='', error=None)
    monkeypatch.setattr(acquisition, 'search', search)
    if surface == 'health':
        from kazma_ui.health import _check_research_stack
        _check_research_stack()
    elif surface == 'api':
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_ui.research_panel.routes import create_research_router
        app = FastAPI(); app.include_router(create_research_router())
        with TestClient(app) as client:
            result = client.get('/api/research/ready')
        assert result.status_code == 200
    else:
        # The old environment branch overrode False; feed its actual decision
        # to the same controlled live probe as an original-boundary control.
        probe = surface == 'explicit-live'
        if surface == 'original-env-override':
            probe = probe or (os.environ.get('KAZMA_RESEARCH_PREFLIGHT_LIVE') or '').strip() in ('1', 'true', 'yes', 'on')
        research_readiness.research_readiness(probe_search=probe)
    assert len(calls) == (surface in {'explicit-live', 'original-env-override'})


def test_readiness_does_not_disclose_configured_search_url_credentials(settings):
    report = json.dumps(research_readiness.research_readiness(), ensure_ascii=False)
    assert 'private-password' not in report and 'private-token' not in report
    assert 'SearXNG URL configured' in report


@pytest.fixture
def observed(settings, monkeypatch):
    from kazma_core.web_acquire import accounting, crawl_jobs
    from kazma_core.web_acquire.result import FetchResult, FetchErrorCode

    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    job = crawl_jobs.start('operations', 'https://docs.example/',
        dict(mode='local_first_with_remote_fallback', providers=['jina'], robots=False, max_pages=1, scope='tree', delay_ms=0))
    job.frontier(['https://docs.example/'])
    job.begin_page('https://docs.example/')
    token = crawl_jobs.current_crawl.set(job)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request:
                httpx.Response(429, headers={'X-Usage-Tokens': '0'})),
                event_hooks=accounting.httpx_hooks('jina', 'fixture', asynchronous=False)) as client:
            client.get('https://r.jina.ai/https://docs.example/')
        job.receipt(FetchResult(ok=False, url='https://docs.example/', backend='jina',
            error='Fixture throttle', error_code=FetchErrorCode.HTTP))
        job.end_page('https://docs.example/', error='Fixture throttle')
        job.finish('partial')
    finally:
        crawl_jobs.current_crawl.reset(token)
    settings.close()
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    def forbidden(*args, **kwargs):
        raise AssertionError('A cached operation contacted a service')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    import kazma_core.web_acquire as acquisition
    monkeypatch.setattr(acquisition, 'search', forbidden)
    return job.job_id


@pytest.mark.parametrize('language', ['en', 'ar'])
def test_cached_status_surfaces_persisted_zero_errors_and_unknown_charges(settings, observed, language):
    from kazma_core.web_acquire import operations, accounting, crawl_jobs

    before = (settings.get_category(accounting._CATEGORY), settings.get_category(crawl_jobs._CATEGORY))
    report = operations.cached_status(observed)
    evidence = report['acquisition']
    assert evidence['mode'] == 'local_only' and evidence['remote_providers'] == []
    assert evidence['usage']['observations'][0]['reported'] == 0
    assert evidence['credits'] is None and evidence['status_read'] == 'cached_only'
    assert evidence['crawl_jobs']['failed'] == 1
    text = operations.format_cached_status(report, language=language)
    assert 'jina' in text and '=0' in text and observed in text
    assert 'http_error' in text and 'Fixture throttle' in text
    assert ('غير متاح' if language == 'ar' else 'unavailable') in text
    assert ('غير مؤكد' if language == 'ar' else 'uncertain') in text
    after = (settings.get_category(accounting._CATEGORY), settings.get_category(crawl_jobs._CATEGORY))
    assert before == after


@pytest.mark.parametrize('original_boundary', [False, True], ids=['scoped', 'original-unscoped-control'])
def test_cached_status_forbids_store_side_effects(settings, monkeypatch, original_boundary):
    from contextlib import nullcontext
    from kazma_core import diagnostic_scope
    from kazma_core.web_acquire import operations, accounting

    original = accounting.snapshot
    def write(*args, **kwargs):
        settings.set('operations.unwanted_write', 'changed')
        return original(*args, **kwargs)
    monkeypatch.setattr(accounting, 'snapshot', write)
    if original_boundary:
        monkeypatch.setattr(diagnostic_scope, 'read_only_diagnostic', lambda *args, **kwargs: nullcontext())
        operations.cached_status()
        assert settings.get('operations.unwanted_write') == 'changed'
    else:
        with pytest.raises(diagnostic_scope.DiagnosticWriteRefused):
            operations.cached_status()
        assert settings.get('operations.unwanted_write') is None


@pytest.mark.parametrize('language', ['en', 'ar'])
def test_cli_cached_status_uses_shared_real_evidence(settings, observed, language, capsys):
    from kazma_cli.acquisition import run
    assert run(['status', '--job', observed, '--language', language]) == 0
    text = capsys.readouterr().out
    assert observed in text and '=0' in text
    assert 'http_error' in text and 'Fixture throttle' in text
    assert ('قراءة محلية' if language == 'ar' else 'Local status read') in text
    assert run(['status', '--job', observed, '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['acquisition']['usage']['observations'][0]['reported'] == 0


def test_cli_dispatches_cached_status_and_reports_unknown_jobs(settings, observed, monkeypatch, capsys):
    from kazma_cli import main
    monkeypatch.setattr('kazma_core.env_files.load_env_files', lambda **_kw: None)
    monkeypatch.setattr(main.sys, 'argv', ['kazma', 'acquisition', 'status', '--json', '--no-banner'])
    with pytest.raises(SystemExit) as result:
        main.main()
    assert result.value.code == 0 and json.loads(capsys.readouterr().out)['acquisition']['status_read'] == 'cached_only'
    from kazma_cli.acquisition import run
    assert run(['status', '--job', 'missing', '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is False and report['error_code'] == 'invalid_url'


def test_cli_storage_failure_is_visible_without_echoing_secrets(settings, monkeypatch, capsys):
    from kazma_cli.acquisition import run
    def broken(*args, **kwargs):
        raise OSError('private credential in a backend error')
    monkeypatch.setattr('kazma_core.web_acquire.operations.cached_status', broken)
    assert run(['status', '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['error_code'] == 'status_unavailable' and 'OSError' in report['error']
    assert 'private credential' not in report['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'ar'])
async def test_real_terminal_widget_reads_cached_usage_and_relabels(settings, observed, language):
    from textual.app import App
    from textual.widgets import Input, Static
    from kazma_tui.acquisition_status import AcquisitionStatus
    class Host(App):
        def compose(self):
            yield AcquisitionStatus(language=language)
    app = Host()
    async with app.run_test(size=(120, 45)) as pilot:
        panel = app.query_one(AcquisitionStatus)
        panel.query_one(Input).value = observed
        await pilot.click('#acquisition-refresh')
        async with asyncio.timeout(15):
            while panel._loading or panel._report is None:
                await asyncio.sleep(.01)
        assert panel._report['acquisition']['usage']['observations'][0]['reported'] == 0
        result = panel.query_one('#acquisition-result', Static)
        result.update('[bold]untrusted service text[/bold]')
        assert result.visual.plain == '[bold]untrusted service text[/bold]'
        panel.set_language('ar')
        assert 'جلب الويب' in str(panel.query_one('#acquisition-title', Static).content)
        panel.set_language('en')
        assert 'Web acquisition' in str(panel.query_one('#acquisition-title', Static).content)


@pytest.mark.asyncio
async def test_terminal_read_retains_single_worker_through_repeated_cancellation(settings, monkeypatch):
    from textual.app import App
    from textual.widgets import Button
    from kazma_tui.acquisition_status import AcquisitionStatus
    from kazma_core.web_acquire import operations, crawl_jobs

    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def held(*args):
        calls.append(True); started.set()
        assert release.wait(15)
        finished.set()
        return {'ok': True}
    monkeypatch.setattr(operations, 'cached_status', held)
    joining = asyncio.Event()
    original_join = crawl_jobs.finish_owned
    async def join(receipt):
        joining.set()
        return await original_join(receipt)
    monkeypatch.setattr(crawl_jobs, 'finish_owned', join)
    class Host(App):
        def compose(self):
            yield AcquisitionStatus()
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        panel = app.query_one(AcquisitionStatus)
        await pilot.click('#acquisition-refresh')
        try:
            async with asyncio.timeout(15):
                while not started.is_set():
                    await asyncio.sleep(.01)
            panel._read_clicked(SimpleNamespace(stop=lambda: None))
            worker, = list(app.workers)
            worker.cancel()
            await asyncio.wait_for(joining.wait(), 10)
            worker.cancel()
            assert panel._loading and panel.query_one(Button).disabled and calls == [True] and not finished.is_set()
        finally:
            release.set()
            async with asyncio.timeout(15):
                while panel._loading:
                    await asyncio.sleep(.01)
        assert finished.is_set() and not panel.query_one(Button).disabled
