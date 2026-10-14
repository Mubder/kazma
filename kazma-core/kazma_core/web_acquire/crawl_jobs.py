"""Bounded durable KB frontiers and outcomes; persistence precedes acquisition.

Uses the install's ConfigStore backend and its conditional batch, including
first-writer contention. No raw database or second settings singleton.

Layout (Knowledge audit KB-E04, 2026-10-14). Each job is its own settings
row, ``acquisition.crawl.<digest of the job id>``, with its own revision
(``acquisition.crawl.<digest>.rev``): a page's state change reads and writes
ITS job only. Until then every job lived in one value, ``acquisition.crawls``,
and each ``begin_page``, ``receipt``, ``end_page`` and ``progress`` read and
rewrote all of them (8.3 MB per page update at a full registry), and terminal
jobs were evicted only by count -- three finished 1000-page crawls held the
8 MiB budget and every later crawl and research job was refused. Admission
(starting, resuming, evicting, recovering, purging) still takes one
registry-wide revision (``acquisition.crawls_revision``), so the four-active
limit and the byte budget are decided in one transaction. A registry in the
old single value is moved into this layout at the first read.

Bounds: a job holds at most :data:`_MAX_JOB_BYTES` (checked on every write
and estimated when its frontier is persisted, so a crawl too large is refused
before anything is fetched); this install's jobs together at most
:data:`_MAX_BYTES`, and a new job is admitted only with room for a full one --
terminal jobs are evicted, oldest first, by bytes as well as by count, never
an active one. ``purge_terminal`` is the operator's purge.

Ownership (KB-E07): an owner records its install, host, process id and
process start time, and a lease renewed by each of its writes. A job owned
on this install and host is taken over only when its process is gone; one
owned by another install or host only when its lease lapsed -- and the
takeover fences it: the claim changes and the job's revision jumps, so the
old owner's next write is refused. Every read is scoped to this install: the
Knowledge Library it describes is this install's own SQLite and Chroma, even
when the settings store is a Postgres database several installs share.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import os
import socket
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

#: The registry before 2026-10-14: one value holding every job (moved in).
_KEY = 'acquisition.crawls'
_REVISION, _CATEGORY = 'acquisition.crawls_revision', 'acquisition_crawls'
_JOB_PREFIX = 'acquisition.crawl.'
_MAX_JOBS, _MAX_ACTIVE = 24, 4
#: One job's bound, and this install's whole registry's.
_MAX_JOB_BYTES = 4 * 1024**2
_MAX_BYTES = _MAX_ACTIVE * _MAX_JOB_BYTES
#: The receipt a Knowledge page keeps; a longer one is kept in compact form.
_KB_RECEIPT_BYTES = 2048
#: A frontier page's share of a job's bytes, estimated at admission.
_PAGE_BUDGET_BYTES = _KB_RECEIPT_BYTES + 512
#: A remote owner keeps its job this long after its last write.
_LEASE_S = 300.0
#: How far a takeover moves a job's revision: an owner that wrote meanwhile
#: never expects this value, so its next write is refused (the fence).
_FENCE = 1 << 20
#: Knowledge Library crawl kinds (one at a time per library).
_KB_KINDS = frozenset({'site', 'refresh'})
current_crawl = ContextVar('current_acquisition_crawl', default=None)
_TERMINAL = {'completed', 'partial', 'failed', 'interrupted'}
_ACTIVE, _ACTIVE_LOCK = set(), threading.Lock()


class CrawlLibraryBusy(AcquisitionError):
    """A crawl of this Knowledge Library is already running (KB-E14)."""

    def __init__(self, library: str) -> None:
        super().__init__(FetchErrorCode.LIMIT,
                         'A crawl of this library is already running; wait for it to finish.',
                         transient=True)
        self.library = library


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


def _job_key(job_id: str) -> str:
    return _JOB_PREFIX + hashlib.sha256(job_id.encode('utf-8')).hexdigest()[:32]


def _rev_key(job_id: str) -> str:
    return _job_key(job_id) + '.rev'


def _identity() -> tuple[str, str]:
    """(install, host) of this process."""
    try:
        from kazma_core.db.shared_store_peers import install_id

        install = install_id() or ''
    except (ImportError, OSError):
        install = ''
    return install, socket.gethostname()


def _owner() -> dict:
    install, host = _identity()
    return {'install': install, 'host': host, 'pid': os.getpid(),
            'started': psutil.Process().create_time(), 'lease_until': time.time() + _LEASE_S}


def _mine(row: dict, install: str) -> bool:
    """A row of this install (a row from before install scoping is too)."""
    return row.get('install') in (None, '', install)


def _store():
    store = config_store.get_config_store()
    if not isinstance(store, config_store.ConfigStore):
        raise _storage_error()
    return store


def _encoded(row) -> int:
    return len(json.dumps(row, ensure_ascii=True))


def _read_all(store):
    """Every job row (all installs) with its revision, the admission
    revision, and the old single-value registry when it is still there."""
    try:
        data = store.get_category(_CATEGORY)  # one fresh DB statement, bypasses read cache
    except (ValueError, TypeError) as exc:
        raise _storage_error() from exc
    revision = data.get(_REVISION, 0)
    if type(revision) is not int or revision < 0:
        raise _storage_error()
    rows, revs = {}, {}
    for key, value in data.items():
        if not key.startswith(_JOB_PREFIX):
            continue
        if key.endswith('.rev'):
            if type(value) is not int or value < 0:
                raise _storage_error()
            revs[key[:-4]] = value
        elif isinstance(value, dict) and value.get('evicted'):
            continue  # removed: its keys are deleted after the commit
        else:
            if not _valid_row(value):
                raise _storage_error()
            rows[key] = value
    jobs = {row['job_id']: (row, revs.get(key, 0)) for key, row in rows.items()}
    legacy = data.get(_KEY)
    if legacy is not None and (not isinstance(legacy, dict) or len(legacy) > _MAX_JOBS
                               or any(not _valid_row(row) for row in legacy.values())):
        raise _storage_error()
    return jobs, revision, legacy


def _read():
    """(store, this install's jobs by id, admission revision) -- the view
    every reader of the journal takes."""
    store = _store()
    jobs, revision = _migrate_legacy(store)
    install, _host = _identity()
    return store, {job_id: copy.deepcopy(row) for job_id, (row, _rev) in jobs.items()
                   if _mine(row, install)}, revision


#: The Knowledge Library's second crawl registry, retired (KB-E14): the API,
#: the page and ``/kb status`` read crawl state from this journal only.
_RETIRED_KB_JOBS_KEY = 'kb.ingest_jobs'
_retired_cleared = False


def _clear_retired(store) -> None:
    global _retired_cleared
    if _retired_cleared:
        return
    _retired_cleared = True
    try:
        store.delete(_RETIRED_KB_JOBS_KEY)
    except _storage_failures():
        _retired_cleared = False


def _migrate_legacy(store):
    """Move the old single-value registry into one row per job, once;
    returns ``(jobs, admission revision)`` as :func:`_read_all` read them."""
    _clear_retired(store)
    try:
        for _ in range(8):
            jobs, revision, legacy = _read_all(store)
            if not legacy:
                if legacy is not None:
                    try:
                        store.delete(_KEY)
                    except PermissionError:
                        pass  # a read-only diagnostic; the next writer removes it
                return jobs, revision
            items = [(_REVISION, revision + 1, _CATEGORY)]
            for job_id, row in legacy.items():
                if job_id not in jobs:
                    items.append((_job_key(job_id), row, _CATEGORY))
                    items.append((_rev_key(job_id), 1, _CATEGORY))
            items.append((_KEY, {}, _CATEGORY))
            try:
                store.batch_set(items, expected=(_REVISION, revision))
            except ConfigRevisionConflict:
                continue
            except PermissionError:
                # A read-only diagnostic: read the old value as it is.
                merged = dict(jobs)
                merged.update({job_id: (row, 0) for job_id, row in legacy.items() if job_id not in jobs})
                return merged, revision
            store.delete(_KEY)
        jobs, revision, _legacy = _read_all(store)
        return jobs, revision
    except _storage_failures() as exc:
        raise _storage_error() from exc


def _valid_row(row):
    if not isinstance(row, dict):
        return False
    required = {'job_id', 'library_id', 'seed', 'policy', 'pages', 'discovered', 'claim', 'owner', 'status',
        'created_at', 'updated_at', 'fetched', 'failed', 'ingested', 'skipped', 'pages_unchanged', 'progress'}
    if not required <= row.keys() or not isinstance(row['status'], str) or row['status'] not in _TERMINAL | {'running'}:
        return False
    if row['owner'] is not None and not isinstance(row['owner'], (list, dict)):
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


def _check_job_bytes(row) -> int:
    size = _encoded(row)
    if size > _MAX_JOB_BYTES:
        raise AcquisitionError(FetchErrorCode.LIMIT, f'Durable crawl job reached its {_MAX_JOB_BYTES // 1024**2}MiB budget; no further work was dispatched.')
    return size


def _mutate(change):
    """Registry-wide change under the admission revision: start, resume,
    evict, recover, purge. Only the rows it changes are written; a changed
    existing row's revision is moved by :data:`_FENCE`."""
    try:
        store = _store()
        all_jobs, revision = _migrate_legacy(store)
        install, _host = _identity()
        for attempt in range(8):
            if attempt:
                all_jobs, revision, _legacy = _read_all(store)
            mine = {job_id: entry for job_id, entry in all_jobs.items() if _mine(entry[0], install)}
            jobs = {job_id: copy.deepcopy(row) for job_id, (row, _rev) in mine.items()}
            answer = change(jobs)
            before_total = sum(_encoded(row) for row, _rev in mine.values())
            total = 0
            items = [(_REVISION, revision + 1, _CATEGORY)]
            for job_id, row in jobs.items():
                total += _check_job_bytes(row)
                before = mine.get(job_id)
                if before is not None and before[0] == row:
                    continue
                items.append((_job_key(job_id), row, _CATEGORY))
                items.append((_rev_key(job_id), (before[1] + _FENCE) if before else 1, _CATEGORY))
            if total > _MAX_BYTES and total > before_total:  # recovery and purges always pass
                raise AcquisitionError(FetchErrorCode.LIMIT, f'Durable crawl registry reached its {_MAX_BYTES // 1024**2}MiB budget; no further work was dispatched.')
            gone = [job_id for job_id in mine if job_id not in jobs]
            for job_id in gone:
                items.append((_job_key(job_id), {'evicted': True}, _CATEGORY))
                items.append((_rev_key(job_id), mine[job_id][1] + _FENCE, _CATEGORY))
            try:
                store.batch_set(items, expected=(_REVISION, revision))
            except ConfigRevisionConflict:
                continue
            for job_id in gone:
                store.delete(_job_key(job_id))
                store.delete(_rev_key(job_id))
            return copy.deepcopy(answer)
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl journal is busy; retry after active work finishes.', transient=True)
    except _storage_failures() as exc:
        raise _storage_error() from exc


def _read_job(store, job_id):
    snap = store.snapshot(_job_key(job_id))
    row = snap.get(_job_key(job_id))
    revision = snap.get(_rev_key(job_id), 0)
    if isinstance(row, dict) and row.get('evicted'):
        row = None
    if row is not None and (not _valid_row(row) or type(revision) is not int):
        raise _storage_error()
    return row, revision


def _mutate_job(job_id, change):
    """Change ONE job under its own revision: bytes written stay those of
    this job, whatever the other jobs hold (KB-E04)."""
    try:
        store = _store()
        for _ in range(8):
            row, revision = _read_job(store, job_id)
            answer = change(row)
            if row is None:
                return copy.deepcopy(answer)
            _check_job_bytes(row)
            try:
                store.batch_set([(_job_key(job_id), row, _CATEGORY), (_rev_key(job_id), revision + 1, _CATEGORY)],
                                expected=(_rev_key(job_id), revision))
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


def _owner_gone(row, now):
    """Whether a running job's owner may be taken over (KB-E07).

    This install and host: its process is gone (or it is this process and
    no longer holds the claim). Another install or host: its lease lapsed --
    a process elsewhere cannot be probed, and a live one keeps its job.
    """
    owner = row['owner']
    if owner is None:
        return True
    if isinstance(owner, list):
        # A row written before install scoping: it may be another install's
        # (a shared settings store), so its process is not probed -- it is
        # taken over once its last write is a lease old.
        with _ACTIVE_LOCK:
            if owner[0] == os.getpid() and row['claim'] not in _ACTIVE:
                return True
        return now - float(row.get('updated_at') or 0.0) > _LEASE_S
    install, host = _identity()
    if owner.get('install') == install and owner.get('host') == host:
        with _ACTIVE_LOCK:
            if owner.get('pid') == os.getpid() and owner.get('started') == psutil.Process().create_time():
                return row['claim'] not in _ACTIVE
        return not _alive([owner.get('pid'), owner.get('started')])
    return float(owner.get('lease_until') or 0.0) < now


def _recover(jobs):
    now = time.time()
    for row in jobs.values():
        if row['status'] == 'running' and _owner_gone(row, now):
            row.update(status='interrupted', owner=None, updated_at=now)
            _settle_discovery_unknown(row)
            for page in row['pages']:
                if page['status'] == 'running':
                    page.update(status='ambiguous', error='Process ended during acquisition/indexing; automatic replay refused.')


def _terminal_bytes_to_free(jobs, job_id):
    total = sum(_encoded(row) for key, row in jobs.items() if key != job_id)
    return total + _MAX_JOB_BYTES - _MAX_BYTES


def start(library, seed, policy, *, job_id=None, resume=False, kind=None):
    from kazma_core.web_acquire.policy import acquisition_policy
    policy = dict(policy, browser_network=acquisition_policy().browser_network)
    if not isinstance(seed, str) or len(seed) > 8192 or not isinstance(library, str) or len(library) > 200:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Crawl seed or library identity exceeds its input budget.')
    job_id = job_id or uuid.uuid4().hex
    if not isinstance(job_id, str) or not 1 <= len(job_id) <= 200:
        raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Invalid crawl job identity.')
    token = uuid.uuid4().hex
    owner = _owner()
    install, host = owner['install'], owner['host']
    now = time.time()
    def change(jobs):
        _recover(jobs)
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
        if sum(other['status'] == 'running' for other in jobs.values()) >= _MAX_ACTIVE:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Installation crawl limit reached (four active jobs); retry after a job finishes.', transient=True)
        if (kind or (row or {}).get('kind') or 'site') in _KB_KINDS and any(
                other['status'] == 'running' and other['library_id'] == library
                and other.get('kind', 'site') in _KB_KINDS and key != job_id for key, other in jobs.items()):
            raise CrawlLibraryBusy(library)
        # Retain a bounded number of outcomes, by count AND bytes, never an
        # active job: room is kept for this job to grow to its full budget.
        old = sorted((key for key, other in jobs.items() if other['status'] in _TERMINAL and key != job_id),
            key=lambda key: jobs[key]['updated_at'])
        while old and (len(jobs) >= _MAX_JOBS or _terminal_bytes_to_free(jobs, job_id) > 0):
            del jobs[old.pop(0)]
        if _terminal_bytes_to_free(jobs, job_id) > 0:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl registry is full of active work; retry after a job finishes.', transient=True)
        if row is None:
            row = dict(job_id=job_id, library_id=library, seed=seed, policy=policy, pages=[], discovered=False,
                created_at=now, fetched=0, failed=0, ingested=0, skipped=0, pages_unchanged=0, progress={})
            jobs[job_id] = row
        elif (row.get('kind', 'site') in _KB_KINDS):
            # A Knowledge page's effect is idempotent (one transaction per
            # page, KB-E02): an explicit resume replays the pages a crash
            # left uncertain.
            for page in row['pages']:
                if page['status'] == 'ambiguous':
                    page.update(status='pending', receipt=None, error='')
        if kind is not None:
            row['kind'] = kind
        row.update(status='running', claim=token, owner=owner, install=install, host=host, updated_at=now)
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
        try:
            row, _revision = _read_job(_store(), self.job_id)
        except _storage_failures() as exc:
            raise _storage_error() from exc
        if row is None or row['claim'] != self.token:
            raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl ownership changed; further work was refused.')
        return row

    def update(self, change):
        def apply(row):
            if row is None or row['claim'] != self.token or row['status'] != 'running':
                raise AcquisitionError(FetchErrorCode.POLICY, 'Crawl ownership ended; further work was refused.')
            answer = change(row)
            now = time.time()
            row['updated_at'] = now
            if isinstance(row.get('owner'), dict):
                row['owner']['lease_until'] = now + _LEASE_S  # every write renews the lease
            return answer
        return _mutate_job(self.job_id, apply)

    def frontier(self, urls):
        urls = list(dict.fromkeys(urls))
        if len(urls) > 1000 or any(not isinstance(url, str) or len(url) > 8192 for url in urls):
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl frontier exceeds 1000 URLs or its URL budget.')
        # Bounded at admission (KB-E04): the frontier with a full receipt per
        # page must fit the job's budget, or nothing is fetched.
        if sum(len(url) for url in urls) + len(urls) * _PAGE_BUDGET_BYTES > _MAX_JOB_BYTES - 64 * 1024:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable crawl frontier exceeds the job budget; crawl fewer pages.')
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
        compact = dict(ok=result.ok, error_code=result.error_code, backend=result.backend,
                       source_digest=data['source_digest'], evidence_clipped=True, quality=result.quality,
                       truncated=result.truncated, provider_usage=None, provider_credits=None)
        size = len(json.dumps(data, ensure_ascii=True, default=str))
        if size > 16 * 1024:
            data = compact
        def change(row):
            page = next((page for page in row['pages'] if page['url'] == (url or result.url)), None)
            if page is not None and page['status'] == 'running':
                # A Knowledge page keeps a short receipt, so 1000 pages fit
                # one job's budget (KB-E04).
                kb = row.get('kind', 'site') in _KB_KINDS
                page['receipt'] = compact if kb and size > _KB_RECEIPT_BYTES else data
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


def active_for_library(library_id):
    """Ids of this install's running Knowledge crawls of *library_id* (a
    library delete stops at one, KB-E02)."""
    return [row['job_id'] for row in _read()[1].values()
            if row['status'] == 'running' and row['library_id'] == library_id
            and row.get('kind', 'site') in _KB_KINDS]


def recover():
    """Reconcile dead owners; live processes remain exclusive owners."""
    _mutate(_recover)


def purge_terminal(*, kinds=None):
    """The operator's purge: remove this install's finished jobs (of
    *kinds*, every kind when None). Never an active one. Returns how many."""
    def change(jobs):
        gone = [key for key, row in jobs.items() if row['status'] in _TERMINAL
                and (kinds is None or row.get('kind', 'site') in kinds)]
        for key in gone:
            del jobs[key]
        return len(gone)
    return _mutate(change)


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
            'scope': 'this_install',
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
            'Crawl interrupted; resume continues pending pages and retries uncertain Knowledge pages.'
            if status == 'interrupted' else row['progress'].get('message', '')}
