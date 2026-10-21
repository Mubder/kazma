"""Local, durable progress/result alerts; delivery reuses the existing ops path.

No provider probes, no job execution, and no inference that silence means failure.
The bounded ledger is committed before requesting external notification delivery.
Delivery interrupted after that commit is uncertain and is never blindly replayed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import quote

from kazma_core.web_acquire.durable_state import mutate
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.config_availability import _storage_failures

logger = logging.getLogger(__name__)
_TASK = None
_LAUNCHED_AT = None
_POLL_SECONDS = 15
_DEFAULTS = dict(enabled=True, stall_seconds=180, low_credit_threshold=100,
                 notify_completed=True)
_TERMINAL = {'done', 'partial', 'error', 'cancelled', 'interrupted'}
_LABELS = {
    'done': ('Research completed', 'اكتمل البحث'),
    'completed': ('Crawl completed', 'اكتمل الزحف'),
    'partial': ('Partial results — review missing sources', 'نتائج جزئية — راجع المصادر الناقصة'),
    'error': ('Research failed — review saved details', 'فشل البحث — راجع التفاصيل المحفوظة'),
    'failed': ('Crawl failed — review saved details', 'فشل الزحف — راجع التفاصيل المحفوظة'),
    'cancelled': ('Research cancelled', 'أُلغي البحث'),
    'interrupted': ('Run interrupted — explicit recovery required', 'انقطع التنفيذ — يلزم استئناف صريح'),
    'stalled': ('No observed progress — inspect the run', 'لا يوجد تقدم مرصود — راجع التنفيذ'),
    'resumed': ('Progress resumed', 'استؤنف التقدم'),
    'low_credits': ('Provider credits are low', 'رصيد الخدمة منخفض'),
    'payment_required': ('Provider requires payment or credits', 'الخدمة تتطلب دفعاً أو رصيداً'),
    'rate_limited': ('Provider rate limit reached', 'تم بلوغ حد طلبات الخدمة'),
    'provider_failed': ('Provider request failed', 'فشل طلب الخدمة'),
    'allowance': ('Remote request allowance exhausted (not billed credits)', 'نفدت حصة الطلبات الخارجية (ليست رصيداً مفوتراً)'),
}


def settings():
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get('acquisition.monitor_settings', {})
    return validate_settings({**_DEFAULTS, **raw}) if isinstance(raw, dict) else validate_settings(raw)


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(_DEFAULTS):
        raise ValueError('Expected enabled, stall_seconds, low_credit_threshold and notify_completed.')
    if any(type(value[key]) is not bool for key in ('enabled', 'notify_completed')):
        raise ValueError('Monitoring switches must be booleans.')
    for key, minimum, maximum in (('stall_seconds', 30, 3600), ('low_credit_threshold', 0, 1_000_000_000)):
        if type(value[key]) is not int or not minimum <= value[key] <= maximum:
            raise ValueError(f'{key} must be an integer from {minimum} to {maximum}.')
    return dict(value)


def save_settings(value):
    from kazma_core.config_store import get_config_store

    value = validate_settings(value)
    get_config_store().set('acquisition.monitor_settings', value, category='acquisition_monitor')
    return value


def _read():
    from kazma_core.config_store import get_config_store

    state = get_config_store().get_category('acquisition_monitor').get('acquisition.monitor', {})
    if not isinstance(state, dict):
        raise ValueError('Acquisition monitor ledger is invalid.')
    return state


#: Crawl-journal jobs an agent runs inside a turn: the owner is not alerted.
_AGENT_RUN_KINDS = frozenset({'research_crawl', 'research_pipeline'})


def _collect():
    """Fresh local reads only. Session and pipeline rows describe ONE run.
    The last item is the keys of the jobs left to the agent that ran them."""
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import accounting, crawl_jobs, credits

    connection = research_session._conn()
    try:
        research_session._reconcile_sessions(connection)
        sessions = connection.execute("SELECT id, depth, status, stage, sources, created_at, updated_at, report_path, meta_json, length(summary)>0 AS has_summary FROM research_sessions WHERE depth != 'chat' ORDER BY created_at DESC LIMIT 257").fetchall()
    finally:
        connection.close()
    if len(sessions) > 256:
        raise ValueError('Research session monitoring exceeds its bounded history.')
    jobs = []
    linked = {json.loads(s['meta_json'] or '{}').get('durable_job_id') for s in sessions}
    for s in sessions:
        if s['depth'] == 'chat':
            continue  # Ordinary chat searches already answer in their own turn.
        jobs.append(dict(key='session:' + s['id'], status=s['status'], stage=s['stage'],
            created=s['created_at'], updated=s['updated_at'], sources=s['sources'],
            progress=[s['stage'], s['sources']], link='/research?session=' + quote(s['id'], safe=''),
            result_available=bool(s['report_path'] or s['has_summary']), kind='research'))
    crawl_jobs.recover()
    left_to_agent = set()
    for row in crawl_jobs._read()[1].values():
        if row['job_id'] in linked:
            continue
        if row.get('kind') in _AGENT_RUN_KINDS:
            left_to_agent.add('job:' + row['job_id'])
            # An agent's own call (crawl_site; run_research_pipeline outside
            # a Research session): its result goes back to the agent, which
            # answers in its turn. Alerting the owner too told of a problem
            # already handled ("Partial results -- Sources: 0" for a crawl a
            # swarm worker worked around, under a complete answer; live
            # 2026-10-20). The job stays on the Research page.
            continue
        counts = crawl_jobs._summary_counts(row)
        progress = row['progress']
        effects = row.get('effects', {})
        phase = progress.get('phase', 'starting')
        research = row.get('kind') in {'research_pipeline', 'research_crawl'}
        status = {'completed': 'done', 'failed': 'error'}.get(row['status'], row['status']) if research else row['status']
        jobs.append(dict(key='job:' + row['job_id'], status=status, stage=phase,
            created=row['created_at'], updated=row['updated_at'], sources=counts['fetched'],
            progress=[phase, counts, row['ingested'],
                      {key: effect['status'] for key, effect in effects.items()},
                      progress.get('discovered'), progress.get('visited')],
            link=('/research?job=' if research else '/knowledge?job=') + quote(row['job_id'], safe=''),
            result_available=row['fetched'] > 0 or effects.get('terminal-summary', {}).get('status') == 'completed', kind='research' if research else 'crawl'))
    return jobs, credits.snapshot(), accounting._read_evidence(), left_to_agent


def _identity(key):
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _severity(condition):
    return 'ERROR' if condition in {'error', 'failed', 'payment_required', 'provider_failed'} else (
        'INFO' if condition in {'done', 'completed', 'resumed', 'cancelled'} else 'WARNING')


def _observe_jobs(state, jobs, cfg, now, event):
    retained = set()
    for job in jobs:
        key, status = job['key'], job['status']
        retained.add(key)
        signature = hashlib.sha256(json.dumps([status, job['progress']], sort_keys=True).encode()).hexdigest()
        previous = state['jobs'].get(key)
        terminal = status in _TERMINAL | {'completed', 'failed'}
        baseline = previous is None and terminal and job['updated'] < state['started']
        if previous is not None and job['updated'] < previous.get('observed_at', 0):
            continue  # A late snapshot cannot undo a newer terminal result.
        if previous is None:
            previous = dict(signature=signature, since=min(job['updated'], now), status=status, stalled=False,
                            observed_at=job['updated'])
            state['jobs'][key] = previous
            # New terminal rows can finish between scans; do not lose their result.
            if terminal and not baseline:
                if cfg['notify_completed'] or status not in {'done', 'completed'}:
                    event(key, status, job['link'], sources=job['sources'], result_available=job['result_available'])
        elif previous['signature'] != signature:
            was_stalled = previous['stalled']
            previous.update(signature=signature, since=now, status=status, stalled=False,
                            observed_at=job['updated'])
            if terminal or was_stalled:
                if not terminal or cfg['notify_completed'] or status not in {'done', 'completed'}:
                    event(key, status if terminal else 'resumed', job['link'], sources=job['sources'], result_available=job['result_available'])
                else:
                    for old in state['events']:
                        if old['key'] == key and old['condition'] == 'stalled':
                            old['resolved'] = True
        if not terminal and not previous['stalled'] and now - previous['since'] >= cfg['stall_seconds']:
            previous['stalled'] = True
            event(key, 'stalled', job['link'], sources=job['sources'], result_available=job['result_available'])
        previous['observed_at'] = job['updated']
    state['jobs'] = {key: value for key, value in state['jobs'].items() if key in retained}
    return retained


def _observe_providers(state, balances, allowance, jobs, cfg, now, event):
    for balance in balances:
        key = 'balance:' + balance['provider']
        if balance.get('fresh') is not True or type(balance.get('remaining')) not in (int, float):
            continue  # A stale/unknown balance cannot prove recovery.
        low = balance['remaining'] <= cfg['low_credit_threshold']
        if low and not state['providers'].get(key):
            event(key, 'low_credits', '/settings?tab=system', provider=balance['provider'],
                  remaining=balance['remaining'], observed_at=balance.get('observed_at'))
        state['providers'][key] = low
    # Balance reads never probe a provider. Actual request failures are separate.
    for request in allowance['recent']:
        status = request.get('http_status')
        key = 'request:' + request['id']
        known = key in state['providers']
        previous = state['providers'].get(key)
        state['providers'][key] = status
        if type(status) is not int or status == previous:
            continue
        if not known and request.get('created', 0) < state['started']:
            continue
        condition = ('payment_required' if status == 402 else 'rate_limited' if status == 429
                     else 'provider_failed' if status in {401, 403} or status >= 500 else None)
        incident = 'failure:' + request['provider']
        if condition and state['providers'].get(incident) != condition:
            event(key, condition, '/settings?tab=system', provider=request['provider'], http_status=status)
            state['providers'][incident] = condition
        elif 200 <= status < 300:
            state['providers'][incident] = None
    from kazma_core.web_acquire.policy import acquisition_policy
    budget_keys = set()
    if acquisition_policy().providers and acquisition_policy().mode == 'local_first_with_remote_fallback':
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
        rows = [('day:' + day, allowance['days'].get(day))]
        rows += [('job:' + job['key'][4:], allowance['jobs'].get(job['key'][4:])) for job in jobs if job['key'].startswith('job:') and job['status'] == 'running']
        for key, value in rows:
            budget_keys.add(key)
            exhausted = value is not None and value['reserved'] >= value['limit']
            if exhausted and not state['providers'].get(key):
                event(key, 'allowance', '/settings?tab=system')
            state['providers'][key] = exhausted
    active_requests = {'request:' + r['id'] for r in allowance['recent']}
    state['providers'] = {key: value for key, value in state['providers'].items()
                          if key in active_requests | budget_keys or key.startswith(('balance:', 'failure:'))}


def tick(*, now=None):
    """One bounded reconciliation; fake-clock friendly, without launching work."""
    now = time.time() if now is None else now
    cfg = settings()
    if not cfg['enabled']:
        return []
    jobs, balances, allowance, left_to_agent = _collect()
    emitted = []

    def change(state):
        emitted.clear()  # Conditional-write conflicts may replay this callback.
        if now + 1 < state.get('checked_at', 0):
            raise ValueError('Clock moved backwards; monitor deadlines are uncertain.')
        state.setdefault('started', _LAUNCHED_AT or now)
        state.setdefault('jobs', {})
        state.setdefault('events', [])
        state.setdefault('providers', {})
        state['checked_at'] = now

        def event(key, condition, link, *, sources=None, result_available=False,
                  provider=None, remaining=None, observed_at=None, http_status=None):
            for old in state['events']:
                if old['key'] == key and old['condition'] == 'stalled':
                    old['resolved'] = condition != 'stalled'
            identity = _identity(key + ':' + condition + ':' + str(now))
            row = dict(id='acquisition-' + identity, key=key, condition=condition,
                timestamp=now, link=link, sources=sources, result_available=result_available,
                resolved=False, dispatch='uncertain', provider=provider, remaining=remaining,
                observed_at=observed_at, http_status=http_status)
            state['events'].append(row)
            emitted.append(row)

        retained = _observe_jobs(state, jobs, cfg, now, event)
        _observe_providers(state, balances, allowance, jobs, cfg, now, event)
        # An alert stored before an agent's own runs were left to the agent
        # stays in the bell for 30 days (live 2026-10-21: yesterday's
        # "Partial results -- Sources: 0" for a swarm worker's crawl_site,
        # raised 21 minutes before that rule was deployed). It is settled.
        for old in state['events']:
            if old['key'] in left_to_agent and not old['resolved']:
                old['resolved'] = True
        # Keep active warnings even when a burst of results ages out old history.
        active = [r for r in state['events'] if r['condition'] == 'stalled' and not r['resolved']
                  and r['key'] in retained]
        history = [r for r in state['events'] if r not in active and now - r['timestamp'] < 30 * 86400]
        state['events'] = sorted(history[-128:] + active, key=lambda r: r['timestamp'])

    mutate('monitor', change, entries=5, bytes_limit=2 * 1024**2)
    from kazma_core.observability.ops_alerts import alert
    grouped = {}
    for row in emitted:
        grouped.setdefault(row['condition'], []).append(row)
    outcomes = {}
    for condition, rows in grouped.items():
        title = _LABELS[condition][0]
        detail = '\n'.join(_detail(row, 'en') + ' ' + row['link'] for row in rows[:6])
        requested = alert('acquisition.' + condition, title, detail,
                          severity={'WARNING': 'warn', 'ERROR': 'error', 'INFO': 'info'}[_severity(condition)],
                          cooldown_s=0, on_delivery=_receipt_callback([row['id'] for row in rows]))
        for row in rows:
            outcomes[row['id']] = 'requested' if requested else 'not_requested'
    if outcomes:
        def settle(state):
            for row in state['events']:
                if row['id'] in outcomes and not row.get('delivery_observed'):
                    row['dispatch'] = outcomes[row['id']]
        mutate('monitor', settle, entries=5, bytes_limit=2 * 1024**2)
    return emitted


def _record_delivery(identities, accepted):
    def change(state):
        for row in state.get('events', []):
            if row['id'] in identities:
                row['dispatch'] = 'delivered' if accepted is True else 'failed' if accepted is False else 'uncertain'
                row['delivery_observed'] = True
    mutate('monitor', change, entries=5, bytes_limit=2 * 1024**2)


def _receipt_callback(identities):
    def receipt(accepted):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _record_delivery(identities, accepted)
        else:
            from kazma_core.background import spawn_background
            from kazma_core.web_acquire.crawl_jobs import call
            spawn_background(call(_record_delivery, identities, accepted), name='acquisition-alert-receipt')
    return receipt


def _detail(row, language):
    ar = language == 'ar'
    parts = [('التنفيذ: ' if ar else 'Run: ') + row['key']]
    if row.get('provider'):
        parts.append(row['provider'])
    if row.get('remaining') is not None:
        parts.append(('الرصيد المرصود: ' if ar else 'Observed credits: ') + str(row['remaining']) +
                     (' · وقت الرصد: ' if ar else ' · observed at: ') + str(row['observed_at']))
    if row.get('http_status'):
        parts.append('HTTP ' + str(row['http_status']))
    if row.get('sources') is not None:
        parts.append(('المصادر: ' if ar else 'Sources: ') + str(row['sources']))
    if row['condition'] == 'stalled':
        parts.append('قد يكون العمل جارياً؛ هذه ملاحظة وليست حكماً بالفشل.' if ar else
                     'Work may still be running; this observation does not mark the job failed.')
    if row['condition'] in {'done', 'completed', 'partial'}:
        parts.append(('توجد مخرجات محفوظة.' if ar else 'Saved output is available.') if row['result_available'] else
                     ('لا توجد مخرجات محفوظة؛ راجع تفاصيل التنفيذ.' if ar else 'No saved output; inspect the run details.'))
    if row.get('dispatch') == 'failed':
        parts.append('تعذر إيصال التنبيه الخارجي؛ راجع مسارات التنبيهات.' if ar else
                     'External alert was not delivered; check notification routes.')
    return ' · '.join(parts)


def recent_alerts(language='en'):
    """Adapt durable observations to the existing notification bell/banner."""
    try:
        return _recent_alerts(language)
    except (*_storage_failures(), AcquisitionError, ValueError, TypeError, KeyError, AttributeError):
        # A broken acquisition ledger must not erase unrelated system alerts.
        ar = language == 'ar'
        return [dict(id='acquisition-monitor-storage-unavailable',
            title='مراقبة الأبحاث والزحف غير متاحة' if ar else 'Research/crawl monitoring unavailable',
            subsystem='AcquisitionMonitor', status='unavailable',
            reason='تعذر قراءة التنبيهات المحفوظة؛ راجع التخزين وسجلات الخادم.' if ar else
                   'Saved alerts cannot be read; check storage and server logs.',
            callback_id='link:/settings?tab=system', button_text='الإعدادات' if ar else 'Settings',
            timestamp=time.time(), severity='ERROR')]


def _recent_alerts(language):
    ar = language == 'ar'
    result = []
    state = _read()
    for row in state.get('events', []):
        if row.get('resolved'):
            continue
        result.append(dict(id=row['id'], title=_LABELS[row['condition']][int(ar)],
            subsystem='Acquisition', status=row['condition'], reason=_detail(row, language),
            callback_id='link:' + row['link'], button_text='عرض التنفيذ والنتائج' if ar else 'View run and results',
            timestamp=row['timestamp'], severity=_severity(row['condition']), dispatch=row['dispatch']))
    checked = state.get('checked_at') or _LAUNCHED_AT
    if settings()['enabled'] and checked and time.time() - checked > 60:
        result.append(dict(id='acquisition-monitor-unavailable-' + str(int(checked)),
            title='مراقبة الأبحاث والزحف غير متاحة' if ar else 'Research/crawl monitoring unavailable',
            subsystem='AcquisitionMonitor', status='unavailable',
            reason='لم تُحدّث حالة المراقب؛ راجع سجلات الخادم.' if ar else 'The monitor has not refreshed; check server logs.',
            callback_id='link:/settings?tab=system', button_text='الإعدادات' if ar else 'Settings',
            timestamp=checked, severity='ERROR'))
    active = [r for r in result if r['status'] in {'stalled', 'unavailable'}]
    return [r for r in result if r not in active][-50:] + active


def monitoring_status():
    state = _read()
    return dict(settings=settings(), last_checked_at=state.get('checked_at'),
                local_only=True, provider_probes=False, retained_alerts=len(state.get('events', [])),
                dispatch_receipts='delivered means at least one platform accepted it; requested/uncertain are not delivery proof')


async def _loop():
    from kazma_core.web_acquire.crawl_jobs import call
    from kazma_core.observability.alerts import AlertDispatcher
    from kazma_core.observability.ops_alerts import alert

    while True:
        try:
            await call(tick)
            AlertDispatcher.resolve_alerts_for_subsystem('AcquisitionMonitor')
        except (*_storage_failures(), AcquisitionError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.error('Acquisition monitoring unavailable (%s)', type(exc).__name__, exc_info=True)
            AlertDispatcher.post_banner(subsystem='AcquisitionMonitor', title='Research/crawl monitoring unavailable',
                reason='Progress and result alerts cannot be verified. Check the settings database and server logs.',
                severity='ERROR', link='/settings?tab=system')
            alert('acquisition.monitor_unavailable', 'Research/crawl monitoring unavailable',
                  'Check the settings database and server logs.', severity='error')
        await asyncio.sleep(_POLL_SECONDS)


def start():
    global _TASK, _LAUNCHED_AT
    from kazma_core.memory.worker_bootstrap import background_schedulers_enabled
    from kazma_core.background import spawn_background

    if background_schedulers_enabled() and (_TASK is None or _TASK.done()):
        _LAUNCHED_AT = time.time()
        _TASK = spawn_background(_loop(), name='acquisition-progress-monitor', never_completes=True)


async def stop():
    global _TASK
    if _TASK is not None:
        task, _TASK = _TASK, None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
