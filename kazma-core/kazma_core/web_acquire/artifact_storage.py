"""Retained research storage admission independent of crawl journal eviction.

The allowance is sized with research admission (FA-266): every run admitted
(``resources.research_capacity``, ``_ACTIVE_RUNS``) reserves
``_RESERVATION`` while it runs, and settled reports have their own declared
share, so ``_LIMIT = _ACTIVE_RUNS x _RESERVATION + _SETTLED_SHARE``. Admission
(``admit``, called by ``research_runtime`` before a session row or a plan
exists) refuses only when settled reports have outgrown their share; a run it
admits always finds its reservation. The 2026-11 audit: 2 GiB held exactly
four reservations, so with one byte of settled reports the fourth admitted
run failed after its session row existed, telling the owner to delete reports
that were not the cause.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat as stat_types
import sys
from pathlib import Path

_RESERVATION = 512 * 1024**2
#: Research pipelines admitted at once (``resources._RESEARCH``; a gate holds
#: them equal).
_ACTIVE_RUNS = 4
#: What settled reports may hold beside the running reservations.
_SETTLED_SHARE = 1024**3
_LIMIT = _ACTIVE_RUNS * _RESERVATION + _SETTLED_SHARE

if __name__ != '__main__':
    from kazma_core.web_acquire.resources import _CapacityPool
    from kazma_core.web_acquire.process_worker import OwnedWorkers
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
    from kazma_core.web_acquire.durable_state import mutate
    _POOL = _CapacityPool('artifact inventory', 1, 4, wait_seconds=10)
    _OWNERS = OwnedWorkers('artifact-inventory', lambda: _POOL)


def _identity(path):
    return hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()


def _scan(root):
    reports = Path(root) / 'research' / 'reports'
    if not reports.exists():
        return {}
    resolved = Path(root).resolve()
    if not reports.resolve().is_relative_to(resolved):
        raise ValueError('Reports left workspace')
    rows, count, total, folders_seen = {}, 0, 0, 0
    with os.scandir(reports) as folders:
        for entry in folders:
            folders_seen += 1
            if folders_seen > 256 or entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                raise ValueError('Report inventory limit or link')
            base = Path(entry.path)
            if not base.resolve().is_relative_to(resolved):
                raise ValueError('Report inventory escaped workspace')
            size, pending = 0, [base]
            while pending:
                current = pending.pop()
                stat = current.lstat()
                if current.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 1024:
                    raise ValueError('Report inventory contains a reparse point')
                if stat_types.S_ISDIR(stat.st_mode):
                    with os.scandir(current) as children:
                        for child in children:
                            count += 1
                            if count > 8192 or len(pending) > 256:
                                raise ValueError('Report inventory file limit')
                            pending.append(Path(child.path))
                else:
                    if not stat_types.S_ISREG(stat.st_mode):
                        raise ValueError('Report inventory contains a non-regular file')
                    size += stat.st_size
                    total += stat.st_size
                    if size > _RESERVATION or total > _LIMIT:
                        raise ValueError('Report storage budget exceeded')
            rows[_identity(base)] = dict(path=str(base), root=str(resolved), bytes=size)
    return rows


def _inventory(owner, root):
    from kazma_core.security.process_budget import run_bounded
    import subprocess
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}
    payload = json.dumps(dict(root=str(root), parent_pid=os.getpid())).encode()
    if len(payload) > 16384:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research inventory path exceeds its budget.')
    try:
        receipt = run_bounded([sys.executable, '-I', os.path.abspath(__file__)], input=payload,
            env={key:value for key,value in os.environ.items() if key.lower() in allowed},
            timeout=15, memory_bytes=256 * 1024**2, output_bytes=1024**2,
            cancel_event=owner.cancelled, on_started=owner.attach, text=True)
        data = json.loads(receipt.stdout)
        if receipt.returncode != 0 or data.get('status') != 'ok':
            raise ValueError('Invalid inventory')
        rows = data['rows']
        if not isinstance(rows, dict) or len(rows) > 256 or any(type(row.get('bytes')) is not int or row['bytes'] < 0 or not Path(row['path']).is_relative_to(root) for row in rows.values()):
            raise ValueError('Invalid inventory receipt')
        return rows
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Bounded research inventory failed; new work refused. Inspect retained reports, links and storage limits.') from exc


def _live(row, jobs):
    """A reservation whose run is still running in a live process."""
    from kazma_core.web_acquire.crawl_jobs import _alive

    if not row.get('active') or not _alive(row['owner']):
        return False
    current = jobs[1].get(row.get('job_id'))
    return current is not None and current['status'] == 'running' and current['claim'] == row['claim']


def _recount(table, root, rows, jobs):
    """Replace every row of *root* that no running job holds with what the
    inventory measured (a finished or abandoned run's reservation becomes its
    folder's real size; a removed folder leaves the table -- never a file)."""
    for identity, row in list(table.items()):
        if row['root'] == str(root):
            if _live(row, jobs):
                continue
            if identity not in rows:
                del table[identity]  # only already removed reports; never delete files
    for identity, row in rows.items():
        previous = table.get(identity)
        if not previous or not _live(previous, jobs):
            table[identity] = dict(row, active=False)
        else:
            previous['bytes'] = max(previous['bytes'], row['bytes'])


def _mib(value: int) -> str:
    return f"{value / 1024**2:,.0f} MiB"


def _split(table, jobs):
    """(settled bytes, running reservations' bytes, running count)."""
    settled = running_bytes = running = 0
    for row in table.values():
        if _live(row, jobs):
            running += 1
            running_bytes += row['bytes']
        else:
            settled += row['bytes']
    return settled, running_bytes, running


async def admit(root):
    """Research admission's storage answer (FA-266), before any session row,
    plan or search: re-measure the reports of this workspace (and of any
    other with an abandoned reservation), then refuse only when settled
    reports have outgrown their share -- the running reservations always fit
    beside it. The refusal says which is which."""
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.crawl_jobs import call

    def stale_roots():
        from kazma_core import config_store
        rows = config_store.get_config_store().get_category('acquisition_artifacts').get('acquisition.artifacts', {})
        if not isinstance(rows, dict):
            return set()
        jobs = crawl_jobs._read()
        return {row['root'] for row in rows.values() if row.get('active') and not _live(row, jobs)}

    roots = {str(root)} | await call(stale_roots)
    measured = {}
    for each in sorted(roots):
        measured[each] = await _OWNERS.call(lambda owner, each=each: _inventory(owner, Path(each)))
    verdict = {}

    def change(table):
        jobs = crawl_jobs._read()
        for each, rows in measured.items():
            _recount(table, each, rows, jobs)
        verdict['settled'], verdict['running_bytes'], verdict['running'] = _split(table, jobs)

    await call(mutate, 'artifacts', change, entries=256)
    share = _LIMIT - _ACTIVE_RUNS * _RESERVATION
    if verdict['settled'] > share:
        raise AcquisitionError(FetchErrorCode.LIMIT, (
            f"Retained research storage allowance exhausted: settled reports hold {_mib(verdict['settled'])} "
            f"of their {_mib(max(share, 0))} share ({verdict['running']} running research job(s) keep "
            f"{_mib(verdict['running_bytes'])} reserved apart). Remove settled reports on the Research page "
            "before starting more research."))


async def reserve(job, root, directory):
    from kazma_core.web_acquire.crawl_jobs import call
    rows = await _OWNERS.call(lambda owner: _inventory(owner, root))
    key = _identity(directory)
    from kazma_core.web_acquire import crawl_jobs
    def change(table):
        jobs = crawl_jobs._read()
        _recount(table, root, rows, jobs)
        import psutil
        previous = table.get(key)
        if previous and _live(previous, jobs) and previous.get('claim') != job.token:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Research output has another live storage owner.')
        used = sum(row['bytes'] for identity, row in table.items() if identity != key)
        if used + _RESERVATION > _LIMIT:
            settled, running_bytes, running = _split({k: v for k, v in table.items() if k != key}, jobs)
            raise AcquisitionError(FetchErrorCode.LIMIT, (
                f"Retained research storage allowance exhausted ({_mib(_LIMIT)}): settled reports hold "
                f"{_mib(settled)} and {running} running research job(s) reserve {_mib(running_bytes)}; "
                f"this job needs {_mib(_RESERVATION)}. Remove settled reports before starting more work."))
        table[key] = dict(path=str(directory), root=str(root), bytes=_RESERVATION,
            active=True, claim=job.token, job_id=job.job_id, owner=[os.getpid(), psutil.Process().create_time()])
    await call(mutate, 'artifacts', change, entries=256)


async def settle(job, root, directory):
    from kazma_core.web_acquire.crawl_jobs import call
    rows = await _OWNERS.call(lambda owner: _inventory(owner, root))
    key = _identity(directory)
    def change(table):
        row = table.get(key)
        if row is None or row.get('claim') != job.token:
            raise AcquisitionError(FetchErrorCode.STORAGE, 'Research storage ownership changed; allowance was retained.')
        row.update(bytes=rows.get(key, {}).get('bytes', 0), active=False)
    await call(mutate, 'artifacts', change, entries=256)


def snapshot():
    from kazma_core import config_store
    data = config_store.get_config_store().get_category('acquisition_artifacts')
    rows = data.get('acquisition.artifacts', {})
    if not isinstance(rows, dict) or len(rows) > 256:
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Invalid retained research storage registry.')
    out = dict(scope='research reports across encountered workspaces', retained_directories=sum(Path(row['path']).name != 'index.json' for row in rows.values()),
        reserved_bytes=sum(row['bytes'] for row in rows.values()), limit_bytes=_LIMIT,
        per_active_job_bytes=_RESERVATION, settled_share_bytes=_LIMIT - _ACTIVE_RUNS * _RESERVATION,
        inventory_resources=_POOL.snapshot())
    # Settled reports and running reservations apart (FA-266): the total read
    # as "1.5 GB of reports" while nothing ran -- three abandoned reservations.
    try:
        from kazma_core.web_acquire import crawl_jobs
        settled, running_bytes, running = _split(rows, crawl_jobs._read())
        out.update(settled_bytes=settled, running_reserved_bytes=running_bytes, running_jobs=running)
    except (AcquisitionError, OSError, KeyError, TypeError, ValueError):
        out.update(settled_bytes=None, running_reserved_bytes=None, running_jobs=None)
    return out


if __name__ == '__main__':
    import runpy
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
        runpy.run_path(str(guard))['arm_deadline'](request['parent_pid'], 15)
        print(json.dumps(dict(status='ok', rows=_scan(request['root']))))
    except (OSError, ValueError, KeyError, TypeError):
        print('{"status":"error"}')
