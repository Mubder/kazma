"""Durable notifications are observations, never job execution or payment probes."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kazma_core import config_store
from kazma_core.observability import ops_alerts
from kazma_core.web_acquire import monitor


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / 'monitor.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: store)
    monkeypatch.setattr(monitor, '_LAUNCHED_AT', None)
    monkeypatch.setattr(monitor, 'time', SimpleNamespace(time=lambda: 1000))
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_only')
    rows = SimpleNamespace(jobs=[], balances=[], allowance=dict(recent=[], days={}, jobs={}), sent=[])
    monkeypatch.setattr(monitor, '_collect', lambda: copy.deepcopy((rows.jobs, rows.balances, rows.allowance)))
    monkeypatch.setattr(ops_alerts, 'alert', lambda *args, **kwargs: rows.sent.append((args, kwargs)) or True)
    rows.store = store
    yield rows
    store.close()


def job(status='running', updated=1000, identity='one', sources=0):
    return dict(key='session:' + identity, status=status, stage='reading', created=1000,
                updated=updated, sources=sources, progress=['reading', sources],
                link='/research?session=' + identity, result_available=sources > 0, kind='research')


def events():
    return monitor._read()['events']


def test_completion_persists_once_with_results_and_link_across_store_reopen(fixture):
    fixture.jobs = [job()]
    assert monitor.tick(now=1000) == []
    fixture.jobs = [job('done', 1010, sources=3)]
    assert monitor.tick(now=1010)[0]['condition'] == 'done'
    fixture.store.close()  # A process restart cannot erase the durable ledger.
    assert monitor.tick(now=1015) == []
    alert = monitor.recent_alerts('ar')[0]
    assert alert['title'] == 'اكتمل البحث'
    assert alert['callback_id'] == 'link:/research?session=one'
    assert alert['dispatch'] == 'requested'
    assert '3' in alert['reason'] and 'محفوظة' in alert['reason']
    assert len(fixture.sent) == 1


def test_quick_completion_between_scans_is_not_lost_but_old_history_is_quiet(fixture):
    fixture.jobs = [job('done', 900, 'old')]
    assert monitor.tick(now=1000) == []
    fixture.jobs.append(job('done', 1001, 'quick', sources=2))
    assert [r['key'] for r in monitor.tick(now=1015)] == ['session:quick']


@pytest.mark.parametrize('status', ['partial', 'error', 'cancelled', 'interrupted', 'failed', 'completed'])
def test_terminal_outcomes_are_distinct(fixture, status):
    fixture.jobs = [job()]
    monitor.tick(now=1000)
    fixture.jobs = [job(status, 1001)]
    row = monitor.tick(now=1002)[0]
    assert row['condition'] == status
    alert = monitor.recent_alerts()[0]
    assert alert['status'] == status
    if status in {'failed', 'error'}:
        assert alert['severity'] == 'ERROR'
    if status == 'partial':
        assert alert['severity'] == 'WARNING'
        assert 'No saved output' in alert['reason']


@pytest.mark.parametrize('completion_enabled', [True, False])
def test_heartbeat_does_not_reset_stall_clock_and_completion_resolves_warning(fixture, completion_enabled):
    monitor.save_settings(dict(monitor.settings(), notify_completed=completion_enabled))
    fixture.jobs = [job()]
    monitor.tick(now=1000)
    fixture.jobs[0]['updated'] = 1179  # Heartbeat-only writes are not progress.
    assert monitor.tick(now=1179) == []
    fixture.jobs[0]['updated'] = 1180
    assert monitor.tick(now=1180)[0]['condition'] == 'stalled'
    assert fixture.jobs[0]['status'] == 'running'  # Watchdog never fails the job.
    assert monitor.tick(now=1181) == []
    fixture.jobs = [job('done', 1182, sources=2)]
    assert len(monitor.tick(now=1182)) == int(completion_enabled)
    assert not any(r['status'] == 'stalled' for r in monitor.recent_alerts())


def test_progress_recovery_then_new_stall_and_stale_snapshot_cannot_undo_terminal(fixture):
    fixture.jobs = [job()]
    monitor.tick(now=1000)
    monitor.tick(now=1180)
    fixture.jobs = [job(updated=1181, sources=1)]
    assert monitor.tick(now=1181)[0]['condition'] == 'resumed'
    assert monitor.tick(now=1361)[0]['condition'] == 'stalled'
    fixture.jobs = [job('done', 1362, sources=1)]
    monitor.tick(now=1362)
    fixture.jobs = [job(updated=1360)]
    assert monitor.tick(now=1363) == []
    assert monitor._read()['jobs']['session:one']['status'] == 'done'


def test_fresh_credit_episode_survives_stale_unknown_and_only_fresh_recovery_resets(fixture):
    fixture.balances = [dict(provider='firecrawl', fresh=False, remaining=0)]
    assert monitor.tick(now=1000) == []
    fixture.balances[0].update(fresh=True, observed_at=1001)
    assert monitor.tick(now=1001)[0]['remaining'] == 0
    fixture.balances[0].update(fresh=False, remaining=None)
    assert monitor.tick(now=1002) == []
    fixture.balances[0].update(fresh=True, remaining=1)
    assert monitor.tick(now=1003) == []
    fixture.balances[0]['remaining'] = 101
    assert monitor.tick(now=1004) == []
    fixture.balances[0]['remaining'] = 100
    assert monitor.tick(now=1005)[0]['condition'] == 'low_credits'


@pytest.mark.parametrize(('http_status', 'condition'), [(402, 'payment_required'), (429, 'rate_limited'),
                         (401, 'provider_failed'), (403, 'provider_failed'), (503, 'provider_failed')])
def test_actual_request_failures_not_keys_trigger_provider_alerts(fixture, http_status, condition):
    fixture.allowance['recent'] = [dict(id='reserved', provider='jina', created=999, http_status=None)]
    monitor.tick(now=1000)
    fixture.allowance['recent'][0]['http_status'] = http_status
    assert monitor.tick(now=1001)[0]['condition'] == condition
    fixture.allowance['recent'].append(dict(id='second', provider='jina', created=1002, http_status=http_status))
    assert monitor.tick(now=1002) == []  # One incident, not a flood per failed URL.
    fixture.allowance['recent'].append(dict(id='recovered', provider='jina', created=1003, http_status=200))
    monitor.tick(now=1003)
    fixture.allowance['recent'].append(dict(id='third', provider='jina', created=1004, http_status=http_status))
    assert monitor.tick(now=1004)[0]['condition'] == condition
    assert 'HTTP ' + str(http_status) in monitor.recent_alerts()[-1]['reason']


def test_request_allowance_is_separate_from_credits_and_respects_remote_consent(fixture, monkeypatch):
    fixture.allowance['days'] = {'1970-01-01': dict(reserved=10, limit=10)}
    assert monitor.tick(now=1000) == []
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina')
    assert monitor.tick(now=1001)[0]['condition'] == 'allowance'
    assert 'not billed credits' in monitor.recent_alerts()[0]['title']


@pytest.mark.parametrize('accepted', [True, False, None])
def test_delivery_acknowledgement_cannot_be_overwritten_by_queued_status(fixture, monkeypatch, accepted):
    def alert(*args, on_delivery, **kwargs):
        on_delivery(accepted)  # Can arrive before alert() returns.
        return True
    monkeypatch.setattr(ops_alerts, 'alert', alert)
    fixture.jobs = [job('done', 1001)]
    monitor.tick(now=1000)
    expected = 'delivered' if accepted is True else 'failed' if accepted is False else 'uncertain'
    assert events()[0]['dispatch'] == expected
    if accepted is False:
        assert 'not delivered' in monitor.recent_alerts()[0]['reason']


def test_concurrent_conditional_writes_emit_one_notification(fixture):
    fixture.jobs = [job()]
    monitor.tick(now=1000)
    fixture.jobs = [job('done', 1001)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: monitor.tick(now=1001), range(2)))
    assert len(events()) == len(fixture.sent) == 1


def test_retention_keeps_active_stall_ahead_of_bursts_of_completion(fixture):
    fixture.jobs = [job()]
    monitor.tick(now=1000)
    monitor.tick(now=1180)
    for i in range(140):
        fixture.jobs.append(job('done', 1181 + i, str(i)))
        monitor.tick(now=1181 + i)
    assert len(events()) == 129
    assert any(r['status'] == 'stalled' for r in monitor.recent_alerts())


def test_backward_clock_fails_visibly_instead_of_resetting_deadlines(fixture):
    monitor.tick(now=1000)
    with pytest.raises(Exception, match='unavailable'):
        monitor.tick(now=900)
    assert monitor._read()['checked_at'] == 1000


@pytest.mark.parametrize('patch', [dict(enabled=1), dict(notify_completed='true'), dict(stall_seconds=True),
                                  dict(stall_seconds=29), dict(stall_seconds=3601),
                                  dict(low_credit_threshold=-1), dict(extra=True)])
def test_settings_reject_ambiguous_or_out_of_bounds_values(fixture, patch):
    with pytest.raises(ValueError):
        monitor.save_settings(dict(monitor.settings(), **patch))
    assert monitor.settings() == monitor._DEFAULTS


def test_disabling_monitoring_prevents_reads_and_notifications(fixture, monkeypatch):
    monitor.save_settings(dict(monitor.settings(), enabled=False))
    monkeypatch.setattr(monitor, '_collect', lambda: pytest.fail('Disabled monitor read jobs'))
    assert monitor.tick(now=1000) == []


def test_monitor_settings_routes_are_real_and_validate(fixture, tmp_path):
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_ui.settings import create_settings_router
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), fixture.store, Jinja2Templates(directory=str(tmp_path))))
    with TestClient(app) as client:
        assert client.get('/api/settings/acquisition/monitor').json()['provider_probes'] is False
        assert client.put('/api/settings/acquisition/monitor', json={'enabled': True}).status_code == 422
        value = dict(monitor.settings(), stall_seconds=45, notify_completed=False)
        assert client.put('/api/settings/acquisition/monitor', json=value).json() == value
        assert client.get('/api/settings/acquisition/monitor').json()['settings'] == value


def test_real_local_collection_links_session_and_pipeline_without_probes(fixture, monkeypatch, tmp_path):
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import crawl_jobs
    import httpx
    monkeypatch.setattr(research_session, '_db_path', lambda: tmp_path / 'research.db')
    durable = crawl_jobs.start('fixture', 'https://fixture.example/',
        dict(mode='local_only', schema=1, workspace=str(tmp_path), output='research/reports/pipeline-fixture'),
        kind='research_pipeline')
    session = research_session.create_session('Private topic never in notification', meta={'durable_job_id': durable.job_id})
    research_session.update_session(session.id, status='done', sources=2, summary='Private body', archived=True)
    research_session.create_session('Ordinary chat', depth='chat')
    # Legacy installs can hold hundreds of chat-search rows. They are outside
    # this monitor and must not consume its research observation allowance.
    connection = research_session._conn()
    try:
        connection.executemany("INSERT INTO research_sessions (id,topic,depth,status,created_at,updated_at,meta_json) VALUES (?,?,?,'done',1,1,'{}')",
            [('legacy-chat-' + str(i), 'Private chat', 'chat') for i in range(349)])
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(httpx, 'Client', lambda *a, **k: pytest.fail('Provider probe'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **k: pytest.fail('Provider probe'))
    # Exercise the real collector rather than the snapshot fixture.
    collected = _REAL_COLLECT()
    assert len(collected[0]) == 1
    assert collected[0][0]['key'] == 'session:' + session.id
    assert collected[0][0]['result_available'] is True
    assert 'Private' not in str(collected)


_REAL_COLLECT = monitor._collect


def test_scheduler_is_off_under_pytest_and_shutdown_joins(fixture, monkeypatch):
    from kazma_core.memory import worker_bootstrap
    monkeypatch.setattr(monitor, '_TASK', None)
    monkeypatch.setattr(worker_bootstrap, 'background_schedulers_enabled', lambda: False)
    monitor.start()
    assert monitor._TASK is None
    async def main():
        entered = asyncio.Event()
        async def loop():
            entered.set()
            await asyncio.Future()
        monkeypatch.setattr(monitor, '_loop', loop)
        monkeypatch.setattr(worker_bootstrap, 'background_schedulers_enabled', lambda: True)
        monitor.start()
        task = monitor._TASK
        monitor.start()
        assert monitor._TASK is task
        await entered.wait()
        await monitor.stop()
        assert task.done() and monitor._TASK is None
    asyncio.run(main())


def test_monitor_failure_has_a_banner_and_a_dead_watchdog_is_visible(fixture, monkeypatch):
    from kazma_core.observability.alerts import AlertDispatcher
    banners = []
    monkeypatch.setattr(monitor, '_LAUNCHED_AT', 900)
    monkeypatch.setattr(AlertDispatcher, 'post_banner', lambda **kwargs: banners.append(kwargs))
    def broken():
        raise OSError('fixture database unavailable')
    monkeypatch.setattr(monitor, 'tick', broken)
    async def finish(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, 'sleep', finish)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor._loop())
    assert banners[0]['subsystem'] == 'AcquisitionMonitor'
    assert fixture.sent[0][0][0] == 'acquisition.monitor_unavailable'
    assert monitor.recent_alerts()[0]['status'] == 'unavailable'


def test_broken_monitor_ledger_returns_visible_unavailability_without_breaking_other_alerts(fixture):
    fixture.store.set('acquisition.monitor', {'events': ['invalid']}, category='acquisition_monitor')
    assert monitor.recent_alerts('ar')[0]['status'] == 'unavailable'
    assert 'تعذر' in monitor.recent_alerts('ar')[0]['reason']


@pytest.mark.parametrize('same_relative_path_in_another_workspace', [False, True])
def test_standalone_result_link_never_opens_another_workspaces_same_named_report(
        fixture, monkeypatch, tmp_path, same_relative_path_in_another_workspace):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.tools import research_pipeline
    from kazma_core.web_acquire import crawl_jobs
    from kazma_ui.research_panel.routes import create_research_router
    output = 'research/reports/pipeline-saved'
    report = tmp_path / output / 'report.md'
    report.parent.mkdir(parents=True)
    report.write_text('Saved original research', encoding='utf-8')
    roots = [tmp_path]
    if same_relative_path_in_another_workspace:
        decoy = tmp_path / 'other' / output / 'report.md'
        decoy.parent.mkdir(parents=True)
        decoy.write_text('Different research', encoding='utf-8')
        roots.insert(0, tmp_path / 'other')
    monkeypatch.setattr(research_pipeline, '_candidate_report_roots', lambda: roots)
    durable = crawl_jobs.start('fixture', 'https://fixture.example/',
        dict(mode='local_only', schema=1, workspace=str(tmp_path), output=output), kind='research_pipeline')
    app = FastAPI()
    app.include_router(create_research_router())
    with TestClient(app) as client:
        result = client.get('/api/research/acquisition', params={'job_id': durable.job_id})
        assert result.status_code == 200
        link = result.json()['report_url']
        if same_relative_path_in_another_workspace:
            assert link is None
        else:
            assert client.get(link).text == 'Saved original research'


def test_an_agents_own_crawl_and_pipeline_raise_no_alert_but_a_knowledge_crawl_does(fixture, monkeypatch, tmp_path):
    """Live 2026-10-20: "Partial results -- Sources: 0" for a crawl_site a
    swarm worker ran and worked around, under its complete answer. The agent
    gets its own call's result; the owner's alerts are for work the owner
    started (a Research session, a Knowledge crawl)."""
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import crawl_jobs
    monkeypatch.setattr(research_session, '_db_path', lambda: tmp_path / 'research.db')
    options = dict(mode='local_only', schema=1, workspace=str(tmp_path), output='research/reports/pipeline-agent')
    crawl = dict(options, max_pages=5, max_depth=1, path_prefix='')
    crawl_jobs.start('agent-crawl', 'https://fixture.example/', crawl, kind='research_crawl')
    crawl_jobs.start('agent-pipeline', 'https://fixture.example/', options, kind='research_pipeline')
    kb = crawl_jobs.start('kb-library', 'https://docs.fixture.example/',
                          dict(mode='local_only', robots=False, max_pages=1, scope='tree', delay_ms=0))
    keys = [job['key'] for job in _REAL_COLLECT()[0]]
    assert keys == ['job:' + kb.job_id], keys


def test_negative_control_without_the_rule_the_agents_crawl_is_alerted(fixture, monkeypatch, tmp_path):
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import crawl_jobs
    monkeypatch.setattr(research_session, '_db_path', lambda: tmp_path / 'research.db')
    monkeypatch.setattr(monitor, '_AGENT_RUN_KINDS', frozenset())
    job = crawl_jobs.start('agent-crawl', 'https://fixture.example/',
                           dict(mode='local_only', schema=1, workspace=str(tmp_path), output='research/x',
                                max_pages=5, max_depth=1, path_prefix=''),
                           kind='research_crawl')
    assert 'job:' + job.job_id in [j['key'] for j in _REAL_COLLECT()[0]]
