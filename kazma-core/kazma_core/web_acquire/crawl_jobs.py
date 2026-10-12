"""Bounded durable KB frontiers and outcomes; persistence precedes acquisition.

Uses the install's ConfigStore backend and its conditional batch, including
first-writer contention. No raw database or second settings singleton.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import os
import time
import uuid
import threading
from contextvars import ContextVar
from dataclasses import asdict

import psutil

from kazma_core import config_store
from kazma_core.config_store import ConfigRevisionConflict
from kazma_core.config_availability import _storage_failures
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_core.playwright_loop import finish_owned

_KEY, _REVISION, _CATEGORY = 'acquisition.crawls', 'acquisition.crawls_revision', 'acquisition_crawls'
_MAX_JOBS, _MAX_ACTIVE, _MAX_BYTES = 24, 4, 8 * 1024**2
current_crawl = ContextVar('current_acquisition_crawl', default=None)
_TERMINAL = {'completed', 'partial', 'failed', 'interrupted'}
_ACTIVE, _ACTIVE_LOCK = set(), threading.Lock()


async def call(function, *args, on_cancel=None, **kwargs):
    """Join a bounded durable statement before acknowledging cancellation."""
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    receipt = asyncio.gather(work, return_exceptions=True)
    try:
        result, = await asyncio.shield(receipt)
    except asyncio.CancelledError:
        try:
            await finish_owned(receipt)
        finally:
            if on_cancel is not None and receipt.done():
                result, = receipt.result()
                if not isinstance(result, BaseException):
                    await finish_owned(asyncio.to_thread(on_cancel, result))
        raise
    if isinstance(result, BaseException):
        raise result
    return result


def _storage_error():
    return AcquisitionError(FetchErrorCode.STORAGE, 'Durable crawl journal unavailable; acquisition stopped. Check storage/database readiness.')


def _read():
    store = config_store.get_config_store()
    if not isinstance(store, config_store.ConfigStore):
        raise _storage_error()
    try:
        data = store.get_category(_CATEGORY)  # one fresh DB statement, bypasses read cache
    except (ValueError, TypeError) as exc:
        raise _storage_error() from exc
    jobs, revision = data.get(_KEY, {}), data.get(_REVISION, 0)
    if not isinstance(jobs, dict) or type(revision) is not int or revision < 0:
        raise _storage_error()
    if len(jobs) > _MAX_JOBS or any(not _valid_row(row) for row in jobs.values()):
        raise _storage_error()
    return store, copy.deepcopy(jobs), revision


def _valid_row(row):
    if not isinstance(row, dict):
        return False
    required = {'job_id', 'library_id', 'seed', 'policy', 'pages', 'discovered', 'claim', 'owner', 'status',
        'created_at', 'updated_at', 'fetched', 'failed', 'ingested', 'skipped', 'pages_unchanged', 'progress'}
    if not required <= row.keys() or not isinstance(row['status'], str) or row['status'] not in _TERMINAL | {'running'}:
        return False
    pages = row['pages']
    if (not isinstance(pages, list) or len(pages) > 1000 or not isinstance(row['policy'], dict)
            or not isinstance(row['progress'], dict) or not isinstance(row['claim'], str) or len(row['claim']) != 32):
        return False
    if any(type(row[key]) is not int or row[key] < 0 for key in ('fetched', 'failed', 'ingested', 'skipped', 'pages_unchanged')):
        return False
    if row.get('discovery') is not None and not _valid_discovery(row['discovery']):
        return False
    if 'metadata' in row:
        from kazma_core.web_acquire.discovery_metadata import _valid

        if not _valid(row['metadata']):
            return False
    if 'metadata_limited' in row and type(row['metadata_limited']) is not bool:
        return False
    if not all(isinstance(page, dict) and isinstance(page.get('url'), str) and len(page['url']) <= 8192
            and page.get('status') in {'pending', 'running', 'completed', 'failed', 'ambiguous'} for page in pages):
        return False
    if row.get('kind') == 'research_crawl':
        from kazma_core.web_acquire.research_crawl import valid

        if not valid(row):
            return False
    if row.get('kind') == 'research_pipeline':
        from kazma_core.web_acquire.research_pipeline_job import valid

        if not valid(row):
            return False
    return True


def _valid_discovery(state):
    if not isinstance(state, dict) or state.get('stage') not in {'bfs', 'complete'}:
        return False
    if type(state.get('limited')) is not bool:
        return False
    if state['stage'] == 'bfs' and (not {'queue', 'known', 'ordered', 'extra', 'ambiguous', 'inflight', 'visited'} <= state.keys()
            or type(state['visited']) is not int or not 0 <= state['visited'] <= 4000):
        return False
    for key in ('known', 'ordered', 'extra', 'ambiguous'):
        values = state.get(key, [])
        if not isinstance(values, list) or len(values) > 4000 or any(not isinstance(url, str) or len(url) > 8192 for url in values):
            return False
    queue = state.get('queue', [])
    if not isinstance(queue, list) or len(queue) > 4000:
        return False
    nodes = queue + ([state['inflight']] if state.get('inflight') is not None else [])
    return all(isinstance(node, list) and len(node) == 2 and isinstance(node[0], str) and len(node[0]) <= 8192
        and type(node[1]) is int and 0 <= node[1] <= 20 for node in nodes)


def _settle_discovery_unknown(row):
    from kazma_core.web_acquire.discovery_metadata import _settle

    _settle(row)
    if row.get('kind') == 'research_pipeline':
        from kazma_core.web_acquire.research_pipeline_job import settle_unknown

        settle_unknown(row)
    state = row.get('discovery')
    if state is not None and state.get('inflight') is not None:
        state['ambiguous'].append(state['inflight'][0])
        state.update(inflight=None, limited=True)


def _mutate(change):
    try:
        for _ in range(8):
            store, jobs, revision = _read()
            answer = change(jobs)
            encoded = json.dumps(jobs, ensure_ascii=True)
            if len(encoded) > _MAX_BYTES:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl registry reached its 8MiB budget; no further work was dispatched.')
            try:
                store.batch_set([(_KEY, jobs, _CATEGORY), (_REVISION, revision + 1, _CATEGORY)], expected=(_REVISION, revision))
                return copy.deepcopy(answer)
            except ConfigRevisionConflict:
                continue
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl journal is busy; retry after active work finishes.', transient=True)
    except _storage_failures() as exc:
        raise _storage_error() from exc


def _alive(identity):
    try:
        process = psutil.Process(identity[0])
        return process.create_time() == identity[1] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, TypeError, ValueError, IndexError):
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Crawl owner identity could not be verified; concurrent resume was refused.') from None


def _recover(jobs):
    for row in jobs.values():
        with _ACTIVE_LOCK:
            abandoned_here = row['owner'] is not None and row['owner'][0] == os.getpid() and row['claim'] not in _ACTIVE
        if row['status'] == 'running' and (abandoned_here or not _alive(row['owner'])):
            row.update(status='interrupted', owner=None, updated_at=time.time())
            _settle_discovery_unknown(row)
            for page in row['pages']:
                if page['status'] == 'running':
                    page.update(status='ambiguous', error='Process ended during acquisition/indexing; automatic replay refused.')


def start(library, seed, policy, *, job_id=None, resume=False, kind=None):
    from kazma_core.web_acquire.policy import acquisition_policy
    policy = dict(policy, browser_network=acquisition_policy().browser_network)
    if not isinstance(seed, str) or len(seed) > 8192 or not isinstance(library, str) or len(library) > 200:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Crawl seed or library identity exceeds its input budget.')
    job_id = job_id or uuid.uuid4().hex
    if not isinstance(job_id, str) or not 1 <= len(job_id) <= 200:
        raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Invalid crawl job identity.')
    token = uuid.uuid4().hex
    owner = [os.getpid(), psutil.Process().create_time()]
    now = time.time()
    def change(jobs):
        _recover(jobs)
        # Retain a bounded number of outcomes; never evict active ownership.
        old = sorted((key for key, row in jobs.items() if row['status'] in _TERMINAL and key != job_id),
            key=lambda key: jobs[key]['updated_at'])
        while len(jobs) >= _MAX_JOBS and old:
            del jobs[old.pop(0)]
        row = jobs.get(job_id)
        if resume:
            if row is None or row['status'] != 'interrupted':
                raise AcquisitionError(FetchErrorCode.POLICY, 'Only an interrupted durable crawl can resume; active or completed work was refused.')
            if (row['library_id'], row['seed'], row['policy']) != (library, seed, policy):
                raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl scope, limits or acquisition policy changed; start a new crawl.')
            if kind is not None and row.get('kind', 'site') != kind:
                raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl kind changed; resume was refused.')
        elif row is not None:
            raise AcquisitionError(FetchErrorCode.EXISTS, 'Crawl job already exists; resume an interrupted job explicitly.')
        if sum(row['status'] == 'running' for row in jobs.values()) >= _MAX_ACTIVE:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Installation crawl limit reached (four active jobs); retry after a job finishes.', transient=True)
        if row is None:
            row = dict(job_id=job_id, library_id=library, seed=seed, policy=policy, pages=[], discovered=False,
                created_at=now, fetched=0, failed=0, ingested=0, skipped=0, pages_unchanged=0, progress={})
            jobs[job_id] = row
        if kind is not None:
            row['kind'] = kind
        row.update(status='running', claim=token, owner=owner, updated_at=now)
        return row
    with _ACTIVE_LOCK:
        _ACTIVE.add(token)
    started = False
    try:
        row = _mutate(change)
        started = True
    finally:
        if not started:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(token)
    return _CrawlJob(job_id, token, row['created_at'])


class _CrawlJob:
    def __init__(self, job_id, token, created_at):
        self.job_id, self.token = job_id, token
        self.created_at = created_at

    def read(self):
        row = _read()[1].get(self.job_id)
        if row is None or row['claim'] != self.token:
            raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl ownership changed; further work was refused.')
        return row

    def update(self, change):
        def apply(jobs):
            row = jobs.get(self.job_id)
            if row is None or row['claim'] != self.token or row['status'] != 'running':
                raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl ownership ended; further work was refused.')
            answer = change(row)
            row['updated_at'] = time.time()
            return answer
        return _mutate(apply)

    def frontier(self, urls):
        urls = list(dict.fromkeys(urls))
        if len(urls) > 1000 or any(not isinstance(url, str) or len(url) > 8192 for url in urls):
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl frontier exceeds 1000 URLs or its URL budget.')
        def change(row):
            if row['discovered']:
                raise AcquisitionError(FetchErrorCode.POLICY, 'Persisted crawl frontier is immutable.')
            row.update(pages=[{'url': url, 'status': 'pending', 'receipt': None} for url in urls], discovered=True)
            state = row.get('discovery')
            if state is not None:
                row['discovery'] = dict(stage='complete', ambiguous=state['ambiguous'], limited=state['limited'])
        self.update(change)

    def discovery(self, state):
        if not _valid_discovery(state):
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable discovery frontier exceeds its URL/depth/work budget.')
        def change(row):
            if row['discovered']:
                raise AcquisitionError(FetchErrorCode.POLICY, 'Completed discovery cannot be replaced.')
            row['discovery'] = copy.deepcopy(state)
        self.update(change)

    def begin_page(self, url):
        def change(row):
            page = next(page for page in row['pages'] if page['url'] == url)
            if page['status'] != 'pending':
                return False
            page['status'] = 'running'
            return True
        return self.update(change)

    def receipt(self, result, *, url=None):
        data = result.evidence()
        # No page body, links or credentials in status; faithful URL identity
        # stays in the frontier, while observed evidence remains bounded.
        data['attempts_clipped'] = len(data['attempts']) > 8
        data['attempts'] = data['attempts'][-8:]
        data['source_digest'] = hashlib.sha256(result.url.encode()).hexdigest()
        if len(json.dumps(data, ensure_ascii=True, default=str)) > 16 * 1024:
            data = dict(ok=result.ok, error_code=result.error_code, backend=result.backend,
                source_digest=data['source_digest'], evidence_clipped=True, quality=result.quality,
                truncated=result.truncated, provider_usage=None, provider_credits=None)
        def change(row):
            page = next((page for page in row['pages'] if page['url'] == (url or result.url)), None)
            if page is not None and page['status'] == 'running':
                page['receipt'] = data
        self.update(change)

    def end_page(self, url, *, error='', new=0, skipped=0, unchanged=False):
        def change(row):
            page = next(page for page in row['pages'] if page['url'] == url)
            if page['status'] != 'running':
                raise AcquisitionError(FetchErrorCode.POLICY, 'Page outcome already settled; duplicate completion refused.')
            page.update(status='failed' if error else 'completed', error=str(error)[:1000])
            row['failed' if error else 'fetched'] += 1
            row['ingested'] += new
            row['skipped'] += skipped
            row['pages_unchanged'] += int(unchanged)
        self.update(change)

    def progress(self, update):
        data = asdict(update)
        data['errors'] = data['errors'][-5:]
        data['message'] = data['message'][:2000]
        self.update(lambda row: row.update(progress=data))

    def finish(self, status):
        def change(row):
            _settle_discovery_unknown(row)
            for page in row['pages']:
                if page['status'] == 'running':
                    page.update(status='ambiguous', error='Crawl interrupted during acquisition/indexing; automatic replay refused.')
            row.update(status=status, owner=None)
        try:
            self.update(change)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(self.token)


def _summary_counts(row):
    if row.get('kind') != 'research_pipeline':
        return dict(discovered=len(row['pages']), fetched=row['fetched'], failed=row['failed'],
                    ambiguous=sum(page['status'] == 'ambiguous' for page in row['pages']))
    effects = row.get('effects', {})
    sources = [effect for key, effect in effects.items() if key.startswith('acquire:')]
    return dict(discovered=len(sources),
        fetched=sum(effect['status'] == 'completed' and bool((effect.get('receipt') or {}).get('ok')) for effect in sources),
        failed=sum(effect['status'] == 'failed' for effect in effects.values())
               + sum(effect['status'] == 'completed' and (effect.get('outcome_ok') is False
                     or (effect.get('receipt') or {}).get('ok') is False) for effect in sources),
        ambiguous=sum(effect['status'] == 'ambiguous' for effect in effects.values()))


def inspect_jobs(job_id=None):
    try:
        jobs = _read()[1]
        if job_id is not None:
            row = jobs.get(job_id)
            if row is None:
                return None
            row.pop('claim', None)
            row.pop('owner', None)
            return row
        return [{'job_id': row['job_id'], 'library_id': row['library_id'], 'kind': row.get('kind', 'site'), 'status': row['status'],
            **_summary_counts(row),
            'updated_at': row['updated_at']} for row in jobs.values()]
    except _storage_failures() as exc:
        raise _storage_error() from exc


def recover():
    """Reconcile dead owners; live processes remain exclusive owners."""
    _mutate(_recover)


def projection(job_id):
    from kazma_core.web_acquire import accounting, credits

    row = inspect_jobs(job_id)
    if row is None:
        return None
    pipeline = row.get('kind') == 'research_pipeline'
    effects = row.get('effects', {}) if pipeline else {}
    pages = [effect for key, effect in effects.items() if key.startswith('acquire:')] if pipeline else row['pages']
    ambiguous = [page for page in pages if page['status'] == 'ambiguous']
    blocked = [key for key, effect in effects.items() if effect['status'] == 'failed'
               or (effect['status'] == 'ambiguous' and not key.startswith('acquire:'))]
    stages = {state: sum(effect['status'] == state for effect in effects.values())
              for state in ('running', 'completed', 'failed', 'ambiguous')}
    fetched = sum(page['status'] == 'completed' and bool((page.get('receipt') or {}).get('ok'))
                  for page in pages) if pipeline else row['fetched']
    failed = _summary_counts(row)['failed'] if pipeline else row['failed']
    recent = next((page['receipt'] for page in reversed(pages) if page.get('receipt') is not None), None)
    allowance = accounting.snapshot(job_id) if row['policy'].get('mode') == 'local_first_with_remote_fallback' else None
    status = row['status']
    phase = row['progress'].get('phase', 'starting') if status == 'running' else {
        'completed': 'done', 'partial': 'done', 'failed': 'error', 'interrupted': 'interrupted'}[status]
    return {**row['progress'], 'job_id': job_id, 'library_id': row['library_id'], 'url': row['seed'], 'kind': row.get('kind', 'site'),
        'phase': phase, 'fetched': fetched, 'failed': failed + len(ambiguous),
        'ingested': row['ingested'], 'skipped': row['skipped'], 'discovered': len(pages),
        'resume_available': status == 'interrupted' and not blocked, 'finished_at': None if status == 'running' else row['updated_at'],
        'acquisition': {'durable': True, 'status': status, 'policy': row['policy'],
            'discovery_ambiguous': len((row.get('discovery') or {}).get('ambiguous', [])),
            'metadata_ambiguous': sum(record['status'] == 'ambiguous' for record in row.get('metadata', {}).values()),
            'discovery_limited': bool(row.get('metadata_limited') or (row.get('discovery') or {}).get('limited')),
            'pending': sum(page['status'] == 'pending' for page in pages),
            'ambiguous': stages['ambiguous'] if pipeline else len(ambiguous),
            **({'stages': stages, 'resume_blocked_stages': blocked,
                'resume_tool': 'run_research_pipeline', 'unstarted_stage_count': None} if pipeline else {}),
            'last_receipt': recent, 'usage': allowance.get('provider_usage') if allowance else None, 'credits': None,
            'provider_balances': credits.snapshot(),
            'allowances': allowance},
        'message': ('Research resume refused: a required stage is failed or uncertain.' if blocked else
                    'Research interrupted; explicit resume reuses verified stages and skips uncertain sources.')
            if pipeline and status == 'interrupted' else
            'Crawl interrupted; resume continues pending pages. Ambiguous effects are not replayed.'
            if status == 'interrupted' else row['progress'].get('message', '')}
