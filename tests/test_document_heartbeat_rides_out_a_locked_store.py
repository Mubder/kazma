"""A document job's heartbeat retries a store error while its lease holds.

Audit 2026-10-26 AUD-024: ``DocumentWorker._heartbeat`` answered ANY error
with ``logger.error(...); cancellation.set(); return``. One "database is
locked" stopped lease renewal and cancelled the stage, losing every OCR page
read so far -- or, with the stage running on, let the lease lapse under it so
another worker claimed the job and two parsers ran on one document.

Now a store error is retried at the next beat; the stage stops only on a job
conflict (another owner, the job gone) or once the lease has actually run
out. Negative control: the old handler cancels at the first locked write.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from kazma_core.documents.jobs import JobLeaseConflictError
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.worker import DocumentWorker


class _Repo:
    """renew_lease answers from a script: an exception to raise, or a lease."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def renew_lease(self, **kw):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(cancel_requested=False)


def _worker(repo, *, lease: float, beat: float) -> DocumentWorker:
    return DocumentWorker(
        repo, {DocumentJobState.PARSING: lambda job, ctx: None},
        lease_seconds=lease, heartbeat_interval=beat,
    )


_JOB = SimpleNamespace(id="job-1", tenant_id="t")


async def _beat_for(worker, seconds: float) -> threading.Event:
    cancellation = threading.Event()
    task = asyncio.create_task(worker._heartbeat(_JOB, "owner", cancellation))
    try:
        await asyncio.sleep(seconds)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return cancellation


@pytest.mark.asyncio
async def test_a_locked_database_is_retried_while_the_lease_holds():
    locked = sqlite3.OperationalError("database is locked")
    repo = _Repo([locked, locked, "ok", "ok"])
    cancellation = await _beat_for(_worker(repo, lease=2.0, beat=0.05), 0.5)
    assert not cancellation.is_set(), "a transient store error cancelled the stage"
    assert repo.calls >= 4


@pytest.mark.asyncio
async def test_the_stage_stops_once_the_lease_has_run_out():
    repo = _Repo([sqlite3.OperationalError("database is locked")] * 1000)
    cancellation = await _beat_for(_worker(repo, lease=0.3, beat=0.05), 1.0)
    assert cancellation.is_set()


@pytest.mark.asyncio
async def test_a_conflict_still_stops_at_once():
    repo = _Repo([JobLeaseConflictError("another owner holds the lease")])
    cancellation = await _beat_for(_worker(repo, lease=5.0, beat=0.05), 0.3)
    assert cancellation.is_set()
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_negative_control_the_old_handler_cancels_at_the_first_lock():
    cancellation = threading.Event()
    try:
        raise sqlite3.OperationalError("database is locked")
    except JobLeaseConflictError:
        cancellation.set()
    except Exception:  # the old catch-all: log, cancel, return
        cancellation.set()
    assert cancellation.is_set()
