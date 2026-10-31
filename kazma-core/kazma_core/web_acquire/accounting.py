"""Durable, finite remote request allowances; never inferred provider charges.

Reservations precede dispatch and are never refunded. A missing completion
means unknown, including cancellation, process death and failed persistence.
Daily/job counters survive clipping of the bounded recent-attempt history.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import math
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps

from kazma_core.config_availability import _storage_failures
from kazma_core.web_acquire.durable_state import refuse_vaulted
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_core.web_acquire import provider_usage

_KEY, _REV, _CATEGORY = 'acquisition.allowances', 'acquisition.allowances_revision', 'acquisition_allowances'
_scope = ContextVar('remote_acquisition_allowance_scope', default=None)
_RETENTION, _MAX_JOBS, _MAX_RECENT, _MAX_BYTES = 31 * 86400, 4096, 256, 4 * 1024**2


@contextmanager
def remote_scope(job_id=None, started_at=None):
    """Nested page/browser/search work shares the caller's finite allowance."""
    value = _scope.get() or (job_id or uuid.uuid4().hex, started_at or time.time())
    token = _scope.set(value)
    try:
        yield
    finally:
        _scope.reset(token)


def remote_job(function):
    @wraps(function)
    async def run(*args, **kwargs):
        with remote_scope():
            return await function(*args, **kwargs)
    return run


def limits():
    try:
        job = int(os.environ.get('KAZMA_REMOTE_JOB_REQUESTS', '256'))
        day = int(os.environ.get('KAZMA_REMOTE_DAY_REQUESTS', '1024'))
        if not 0 <= job <= 1024 or not 0 <= day <= 4096:
            raise ValueError
        return job, day
    except ValueError:
        raise AcquisitionError(FetchErrorCode.POLICY,
            'Remote request limits must be integers: job 0–1024, UTC day 0–4096. No remote request was dispatched.') from None


def account_fingerprint(provider, credential):
    return hashlib.sha256((provider + '\0' + credential).encode()).hexdigest()[:24]


def _storage_error():
    return AcquisitionError(FetchErrorCode.STORAGE,
        'Remote allowance ledger unavailable; remote dispatch refused. Check the settings database.')


def _read():
    from kazma_core import config_store

    store = config_store.get_config_store()
    if not isinstance(store, config_store.ConfigStore):
        raise _storage_error()
    data = store.get_category(_CATEGORY)
    state, revision = data.get(_KEY, {'days': {}, 'jobs': {}, 'recent': [], 'balances': {}}), data.get(_REV, 0)
    if isinstance(state, dict):
        state.setdefault('balances', {})  # first ledger version had no balance cache
    if (not isinstance(state, dict) or set(state) != {'days', 'jobs', 'recent', 'balances'}
            or type(revision) is not int or revision < 0 or not _valid(state)):
        raise _storage_error()
    return store, copy.deepcopy(state), revision


def _valid(state):
    if (not isinstance(state['days'], dict) or len(state['days']) > 32
            or not isinstance(state['jobs'], dict) or len(state['jobs']) > _MAX_JOBS
            or not isinstance(state['balances'], dict) or len(state['balances']) > 16
            or not isinstance(state['recent'], list) or len(state['recent']) > _MAX_RECENT):
        return False
    for row in list(state['days'].values()) + list(state['jobs'].values()):
        if (not isinstance(row, dict) or any(type(row.get(key)) is not int or row[key] < 0
                for key in ('reserved', 'limit')) or type(row.get('created')) not in (int, float)
                or not math.isfinite(row['created']) or row['reserved'] > 4096 or row['limit'] > 4096):
            return False
    if any(not _valid_balance(row) for row in state['balances'].values()):
        return False
    return all(isinstance(row, dict) and isinstance(row.get('id'), str)
        and row.get('state') in {'unknown', 'response_received'} and type(row.get('units')) is int
        and row['units'] > 0 and (row.get('provider_usage') is None or
            row.get('provider') == 'jina' and provider_usage._valid(row['provider_usage'])) for row in state['recent'])


def _valid_balance(row):
    return (isinstance(row, dict) and isinstance(row.get('claim'), str)
        and type(row.get('attempted_at')) in (int, float) and math.isfinite(row['attempted_at'])
        and (row.get('observed_at') is None or type(row['observed_at']) in (int, float) and math.isfinite(row['observed_at']))
        and row.get('status') in {'refreshing', 'observed', 'unavailable'}
        and (row.get('remaining') is None or type(row['remaining']) in (int, float) and math.isfinite(row['remaining'])))


def _read_evidence():
    try:
        return _read()[1]
    except _storage_failures() as exc:
        raise _storage_error() from exc
    except (ValueError, TypeError) as exc:
        raise _storage_error() from exc


def _mutate(change):
    from kazma_core.config_store import ConfigRevisionConflict

    try:
        for _ in range(8):
            store, state, revision = _read()
            answer = change(state)
            if not _valid(state) or len(json.dumps(state, ensure_ascii=True)) > _MAX_BYTES:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Remote allowance ledger reached its storage limit; dispatch refused.')
            items = [(_KEY, state, _CATEGORY), (_REV, revision + 1, _CATEGORY)]
            refuse_vaulted(items)
            try:
                store.batch_set(items, expected=(_REV, revision))
                return answer
            except ConfigRevisionConflict:
                continue
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Remote allowance ledger is busy; dispatch refused.', transient=True)
    except _storage_failures() as exc:
        raise _storage_error() from exc
    except (ValueError, TypeError) as exc:
        raise _storage_error() from exc


def reserve(provider, account, *, units=1):
    """Reserve a request or a proven upper bound before any remote effect."""
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.policy import acquisition_policy

    financial_cap = os.environ.get('KAZMA_ACQUISITION_MAX_COST_USD')
    if financial_cap is not None:
        from decimal import Decimal, InvalidOperation
        try:
            cap = Decimal(financial_cap)
            if not cap.is_finite() or cap < 0:
                raise InvalidOperation
        except InvalidOperation:
            raise AcquisitionError(FetchErrorCode.POLICY, 'Invalid acquisition financial cap; remote dispatch refused.') from None
        raise AcquisitionError(FetchErrorCode.POLICY, 'A financial cap was requested, but this service has no verified per-request monetary upper bound. Remote dispatch refused; use local extraction or provider-enforced spending controls.')
    if provider not in {'proxy', 'firecrawl', 'jina'} or type(units) is not int or not 1 <= units <= 6:
        raise AcquisitionError(FetchErrorCode.POLICY, 'Invalid remote allowance request; dispatch refused.')
    if not acquisition_policy().permits(provider):
        raise AcquisitionError(FetchErrorCode.POLICY, 'Remote service refused by acquisition policy.')
    if not isinstance(account, str) or len(account) != 24 or any(c not in '0123456789abcdef' for c in account):
        raise AcquisitionError(FetchErrorCode.POLICY, 'Invalid remote account identity; dispatch refused.')
    job = crawl_jobs.current_crawl.get()
    identity, started = (job.job_id, job.created_at) if job else (_scope.get() or (uuid.uuid4().hex, time.time()))
    now = time.time()
    if not isinstance(identity, str) or len(identity) > 200 or not 0 <= now - started < _RETENTION:
        raise AcquisitionError(FetchErrorCode.POLICY, 'Remote job allowance expired or invalid; start a new job.')
    job_limit, day_limit = limits()
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    reservation = uuid.uuid4().hex
    def change(state):
        for group in ('days', 'jobs'):
            state[group] = {key: row for key, row in state[group].items() if now - row['created'] < _RETENTION}
        if identity not in state['jobs'] and len(state['jobs']) >= _MAX_JOBS:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Remote job allowance registry is full; dispatch refused.')
        rows = [state['days'].setdefault(day, dict(created=now, reserved=0, limit=day_limit)),
                state['jobs'].setdefault(identity, dict(created=started, reserved=0, limit=job_limit))]
        for row, cap in zip(rows, (day_limit, job_limit), strict=True):
            row['limit'] = min(row['limit'], cap)  # a policy change cannot widen an active allowance
        if any(row['reserved'] + units > row['limit'] for row in rows):
            # Commit narrowed caps even when this dispatch is denied. Raising
            # inside change would discard them and permit a later widening.
            return AcquisitionError(FetchErrorCode.LIMIT,
                'Remote request allowance exhausted; dispatch refused. Reserved requests include failed/uncertain attempts; provider credits are separate.')
        for row in rows:
            row['reserved'] += units
        state['recent'].append(dict(id=reservation, job_id=identity, provider=provider, account=account,
            units=units, created=now, state='unknown', http_status=None, provider_usage=None, provider_credits=None))
        state['recent'] = state['recent'][-_MAX_RECENT:]
        return reservation
    outcome = _mutate(change)
    if isinstance(outcome, AcquisitionError):
        raise outcome
    return outcome


def _response_received(reservation, status, *, usage=None):
    if type(status) is not int or not 100 <= status <= 599:
        raise AcquisitionError(FetchErrorCode.PROVIDER_RESPONSE, 'Invalid remote response status.')
    if usage is not None and not provider_usage._valid(usage):
        raise AcquisitionError(FetchErrorCode.PROVIDER_RESPONSE, 'Invalid provider usage observation.')
    def change(state):
        row = next((row for row in state['recent'] if row['id'] == reservation), None)
        if row is not None:
            row.update(state='response_received', http_status=status, provider_usage=usage)
    _mutate(change)


def snapshot(job_id=None):
    """Read local evidence only: no provider call or estimated credit balance."""
    state = _read_evidence()
    day = datetime.now(timezone.utc).date().isoformat()
    recent = [row for row in state['recent'] if job_id is None or row['job_id'] == job_id]
    return dict(unit='reserved_request_upper_bound', limits=dict(zip(('job', 'utc_day'), limits(), strict=True)),
        day=day, day_reserved=state['days'].get(day, {}).get('reserved', 0),
        job_reserved=state['jobs'].get(job_id, {}).get('reserved') if job_id else None,
        day_limit=state['days'].get(day, {}).get('limit', limits()[1]),
        job_limit=state['jobs'].get(job_id, {}).get('limit', limits()[0]) if job_id else None,
        recent=recent, history_limit=_MAX_RECENT,
        provider_usage=provider_usage._summary([(row['id'], row.get('provider_usage')) for row in recent], complete=False),
        provider_credits=None)


def httpx_hooks(provider, credential, *, asynchronous):
    """Account redirects/retries individually, including proxy health probes."""
    account = account_fingerprint(provider, credential)
    def before(request):
        request.extensions['kazma_remote_reservation'] = reserve(provider, account)
    def after(response):
        usage = provider_usage._from_jina(response.headers) if provider == 'jina' else None
        _response_received(response.request.extensions['kazma_remote_reservation'], response.status_code, usage=usage)
    if not asynchronous:
        return {'request': [before], 'response': [after]}
    async def before_async(request):
        from kazma_core.web_acquire.crawl_jobs import call

        await call(before, request)
    async def after_async(response):
        from kazma_core.web_acquire.crawl_jobs import call

        await call(after, response)
    return {'request': [before_async], 'response': [after_async]}


def attach_hooks(kwargs, provider, credential, *, asynchronous):
    hooks = httpx_hooks(provider, credential, asynchronous=asynchronous)
    existing = kwargs.get('event_hooks') or {}
    kwargs['event_hooks'] = {key: hooks[key] + list(existing.get(key, [])) for key in hooks}
