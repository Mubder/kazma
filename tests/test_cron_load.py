"""1,000 pending jobs, 50 due at once: every due one fires within one pass (Scheduled audit SCH-E14).

The bound: with 1,000 jobs booked and 50 of them due at the same moment, all
50 are delivered by the first pass that sees them -- their lateness is the
time a pass takes plus the fires themselves, never further poll intervals --
and the due query reads the ``(status, next_run)`` index rather than the
table. Before, a pass started at most ``max_concurrent`` fires (4) and left
the rest for later passes: 50 due reminders took 13 passes, six minutes at
the default 30-second interval. The negative control runs that rule.
"""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

PENDING, DUE = 1000, 50
#: The bound for the 50 due fires, wall clock (measured 2026-10-14 on a
#: developer machine at below-normal priority: under a second, the 1,000
#: bookings included). Half the default 30-second interval, so a pass that
#: left fires for the next one cannot meet it.
FIRE_BOUND_S = 15.0


@pytest.fixture
def delivered(monkeypatch):
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    sent: list[str] = []

    async def backend(target_id, text, **kw):
        sent.append(text)
        return f"sent:{target_id}"

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", lambda *a, **k: True)
    return sent


async def _booked(tmp_path) -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    now = datetime.now(UTC)
    for i in range(PENDING):
        due = now - timedelta(seconds=1) if i < DUE else now + timedelta(days=1, minutes=i)
        await store.insert(ScheduledJob(
            job_id=f"cron-load-{i:04d}", timing="2026-10-14T09:00", prompt=f"Reminder {i}",
            platform="telegram", thread_id=f"t{i % 40}", status=JobStatus.PENDING,
            next_run=due.isoformat(), kind="reminder", created_at=now.isoformat(),
            delivery_target="telegram:42"))
    return store


async def _until(cond, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


async def test_fifty_due_among_a_thousand_fire_in_one_pass(tmp_path, delivered):
    store = await _booked(tmp_path)
    # A long interval: a second pass cannot help within the bound.
    sched = CronScheduler(store=store, poll_interval=60.0)
    try:
        started = time.monotonic()
        await sched.start()
        await _until(lambda: len(delivered) >= DUE and sched.active_fire_count() == 0, FIRE_BOUND_S)
        took = time.monotonic() - started
        passes = sched._poll_count
        late = sorted(await store.lateness(since=0.0))
        remaining = await store.count_jobs()
    finally:
        await sched.stop()
        await store.close()
    assert len(delivered) == DUE and took <= FIRE_BOUND_S
    assert passes <= 1, f"the due jobs took {passes} passes"
    p95 = late[int(0.95 * (len(late) - 1))]
    assert p95 <= FIRE_BOUND_S + 1, late[-5:]
    assert remaining == PENDING - DUE  # the rest still wait, untouched


def test_the_due_query_reads_the_index(tmp_path):
    asyncio.run(_close(_booked(tmp_path)))
    conn = sqlite3.connect(tmp_path / "cron.db")
    try:
        plan = " ".join(str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM cron_jobs WHERE status = 'pending' AND next_run IS NOT NULL "
            "AND next_run != '' AND next_run <= ? ORDER BY next_run LIMIT 50", ("2030",)))
    finally:
        conn.close()
    assert "idx_cron_jobs_due" in plan and "SCAN cron_jobs" not in plan, plan


async def _close(coro) -> None:
    store = await coro
    await store.close()


class _OldThrottle(CronScheduler):
    """The pass as it was until 2026-10-14: at most max_concurrent fires
    started per pass."""

    async def _poll_once(self) -> None:
        await self._recover(boot=False)
        now = datetime.now(UTC)
        sem = self._sem or asyncio.Semaphore(self._max_concurrent)
        for job in await self._store.due_jobs(now=now, limit=50):
            if job.job_id in self._in_flight:
                continue
            if len(self._in_flight) >= self._max_concurrent:
                break
            self._in_flight.add(job.job_id)
            task: Any = asyncio.create_task(self._execute_bounded(job, sem))
            self._exec_tasks.add(task)
            task.add_done_callback(self._exec_done(job.job_id))
        await self._drain_deliveries()


async def test_negative_control_the_old_pass_needs_many_passes(tmp_path, delivered):
    store = await _booked(tmp_path)
    sched = _OldThrottle(store=store, poll_interval=0.05)
    try:
        await sched.start()
        await _until(lambda: len(delivered) >= DUE and sched.active_fire_count() == 0, 60.0)
        passes = sched._poll_count
    finally:
        await sched.stop()
        await store.close()
    # ceil(50 / 4) passes: at the default 30-second interval, six minutes.
    assert passes >= 12, passes
