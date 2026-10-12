"""Retained research storage admission independent of crawl journal eviction."""
from __future__ import annotations

import hashlib
import json
import os
import stat as stat_types
import sys
from pathlib import Path

_LIMIT = 2 * 1024**3
_RESERVATION = 512 * 1024**2

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


async def reserve(job, root, directory):
    from kazma_core.web_acquire.crawl_jobs import call, _alive
    rows = await _OWNERS.call(lambda owner: _inventory(owner, root))
    key = _identity(directory)
    from kazma_core.web_acquire import crawl_jobs
    def live(row, jobs):
        if not row.get('active') or not _alive(row['owner']):
            return False
        current = jobs[1].get(row.get('job_id'))
        return current is not None and current['status'] == 'running' and current['claim'] == row['claim']
    def change(table):
        jobs = crawl_jobs._read()
        for identity, row in list(table.items()):
            if row['root'] == str(root):
                if live(row, jobs):
                    continue
                if identity not in rows:
                    del table[identity]  # only already removed reports; never delete files
        for identity, row in rows.items():
            previous = table.get(identity)
            if not previous or not live(previous, jobs):
                table[identity] = dict(row, active=False)
            else:
                previous['bytes'] = max(previous['bytes'], row['bytes'])
        import psutil
        previous = table.get(key)
        if previous and live(previous, jobs) and previous.get('claim') != job.token:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Research output has another live storage owner.')
        used = sum(row['bytes'] for identity, row in table.items() if identity != key)
        if used + _RESERVATION > _LIMIT:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Retained research storage allowance exhausted (2GiB); remove settled reports before starting more work.')
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
    return dict(scope='research reports across encountered workspaces', retained_directories=sum(Path(row['path']).name != 'index.json' for row in rows.values()),
        reserved_bytes=sum(row['bytes'] for row in rows.values()), limit_bytes=_LIMIT,
        per_active_job_bytes=_RESERVATION, inventory_resources=_POOL.snapshot())


if __name__ == '__main__':
    import runpy
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
        runpy.run_path(str(guard))['arm_deadline'](request['parent_pid'], 15)
        print(json.dumps(dict(status='ok', rows=_scan(request['root']))))
    except (OSError, ValueError, KeyError, TypeError):
        print('{"status":"error"}')
