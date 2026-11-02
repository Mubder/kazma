"""A periodic job that is due runs soon after a boot, whatever the uptime.

A scheduler that sleeps its whole interval from process start never runs on
a server restarted more often than that interval. The restore drill found it
first (34 scheduler starts, no drill, three days). On 2026-09-28 the live
install -- reloaded several times a day -- had not sent its daily digest
once in a week, and its backup sweep, skipping a fresh backup at boot,
waited six hours from boot (the newest backup was nine hours old).

Every scheduler ``start_memory_worker`` starts is booted here on a virtual
clock with its job falling due half an hour later; the job must run within
forty minutes of the boot. A new scheduler must be added to ``CASES`` or the
enumeration check fails.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from kazma_core.memory import worker_bootstrap as wb

DUE_IN = 30 * 60  # the job falls due half an hour after the boot...
BOUND = 40 * 60  # ...and must have run within forty minutes of it
CAP = 8 * 24 * 3600  # a scheduler still waiting after eight days never runs


class _Worked(BaseException):
    """Raised by the work hook: ends the scheduler task (loops catch Exception)."""


@dataclass
class _Clock:
    base: float = field(default_factory=time.time)
    elapsed: float = 0.0
    worked_at: float | None = None

    def now(self) -> float:
        return self.base + self.elapsed


def _install_clock(monkeypatch, clock: _Clock) -> None:
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        clock.elapsed += max(0.0, float(delay or 0))
        if clock.elapsed > CAP:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(time, "time", clock.now)


def _hook(clock: _Clock):
    def work(*args, **kwargs):
        if clock.worked_at is None:
            clock.worked_at = clock.elapsed
        raise _Worked

    return work


async def _run_until_work(start: Callable[[], asyncio.Task | None], clock: _Clock) -> float | None:
    before = set(wb._scheduler_tasks)
    task = start()
    tasks = {task} if task is not None else set(wb._scheduler_tasks) - before
    assert tasks, "the scheduler started no task"
    done, pending = await asyncio.wait(tasks, timeout=20)
    for t in pending:
        t.cancel()
    return clock.worked_at


# ── how each scheduler is booted with its job due in DUE_IN ─────────────


def _backup(monkeypatch, clock):
    finished = clock.base - (wb._BACKUP_EXPORT_INTERVAL_HOURS * 3600 - DUE_IN)
    monkeypatch.setattr(wb, "_newest_backup_age_s", lambda: time.time() - finished)
    monkeypatch.setattr(wb, "_pg_dump_is_stale", lambda: False)
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", _hook(clock))
    return wb._start_backup_export_scheduler


def _digest(monkeypatch, clock):
    from datetime import UTC, datetime

    from kazma_core.cron import scheduler as cron_scheduler
    from kazma_core.observability import cadence, daily_digest

    # The digest is daily at its set time (FA-208): set it DUE_IN after the
    # boot, with yesterday's sent at yesterday's time.
    at = datetime.fromtimestamp(clock.base + DUE_IN, UTC)
    monkeypatch.setattr(cron_scheduler, "resolve_cron_zone", lambda: (UTC, "UTC"))
    monkeypatch.setattr(daily_digest, "_digest_time", lambda: (at.hour, at.minute))
    sent = clock.base + DUE_IN - at.second - 24 * 3600
    monkeypatch.setattr(cadence, "last_run", lambda key: sent)
    monkeypatch.setattr(daily_digest, "build_digest", _hook(clock))
    return lambda: asyncio.get_running_loop().create_task(daily_digest.digest_scheduler())


def _drill(monkeypatch, clock):
    from kazma_core.backup import restore_drill as rd

    ran = clock.base - (rd.DRILL_INTERVAL_HOURS * 3600 - DUE_IN)
    monkeypatch.setattr(rd, "_last_drill_run", lambda: ran)
    monkeypatch.setattr(rd, "_last_deep_drill_run", lambda: clock.base - 3600)
    monkeypatch.setattr(rd, "run_drill", _hook(clock))
    return lambda: asyncio.get_running_loop().create_task(rd.drill_scheduler())


def _ledger(monkeypatch, clock):
    from kazma_core.observability import firing_ledger as fl

    ran = clock.base - (fl.SWEEP_INTERVAL_HOURS * 3600 - DUE_IN)
    monkeypatch.setattr(fl, "_last_run_epoch", lambda: ran)
    monkeypatch.setattr(fl, "run_weekly_sweep", _hook(clock))
    return lambda: asyncio.get_running_loop().create_task(fl.ledger_scheduler())


def _enqueueing(starter_name: str, interval_s: float):
    """Schedulers that enqueue their work when their stamp says it is due
    (FA-149: they ran shortly after EVERY boot)."""

    def setup(monkeypatch, clock):
        from kazma_core.observability import cadence

        ran = clock.base - (interval_s - DUE_IN)
        monkeypatch.setattr(cadence, "last_run", lambda key: ran)
        monkeypatch.setattr(wb, "_distinct_tenants", lambda: ["default"])
        monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", _hook(clock))
        return getattr(wb, starter_name)

    return setup


def _session_purge(monkeypatch, clock):
    monkeypatch.setattr("kazma_core.security.web_sessions.purge_expired_sessions", _hook(clock))
    return wb._start_session_purge_scheduler


def _maintenance(monkeypatch, clock):
    async def sweep():
        _hook(clock)()

    monkeypatch.setattr(wb, "_run_maintenance_sweeps", sweep)
    return wb._start_commitment_gc_scheduler


#: Every scheduler start_memory_worker starts -> how to boot it with work due.
CASES: dict[str, Callable] = {
    "_start_backup_export_scheduler": _backup,
    "_start_daily_digest_scheduler": _digest,
    "_start_restore_drill_scheduler": _drill,
    "_start_firing_ledger_scheduler": _ledger,
    "_start_macro_sleep_scheduler": _enqueueing(
        "_start_macro_sleep_scheduler", wb._MACRO_SLEEP_INTERVAL_HOURS * 3600),
    "_start_reconsolidation_scheduler": _enqueueing(
        "_start_reconsolidation_scheduler", wb._RECONSOLIDATION_INTERVAL_HOURS * 3600),
    "_start_session_purge_scheduler": _session_purge,
    "_start_commitment_gc_scheduler": _maintenance,
}


def _started_by_boot() -> set[str]:
    tree = ast.parse(inspect.getsource(wb.start_memory_worker))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id.startswith("_start_") and node.func.id.endswith("_scheduler")
    }


def test_every_boot_scheduler_is_covered():
    started = _started_by_boot()
    assert len(started) >= 8, started  # the enumeration is not blind
    assert started == set(CASES), (
        f"add a case for {sorted(started - set(CASES))}; "
        f"drop the case for {sorted(set(CASES) - started)}"
    )


@pytest.mark.parametrize("name", sorted(CASES))
async def test_a_due_job_runs_soon_after_boot(name, monkeypatch):
    clock = _Clock()
    start = CASES[name](monkeypatch, clock)
    _install_clock(monkeypatch, clock)
    worked_at = await _run_until_work(start, clock)
    assert worked_at is not None, f"{name}: never ran in {CAP // 86400} days of uptime"
    assert worked_at <= BOUND, (
        f"{name}: ran {worked_at / 3600:.1f} h after boot although it was due "
        f"{DUE_IN // 60} minutes after it"
    )


async def test_the_old_digest_loop_fails_the_same_check(monkeypatch):
    """Negative control: the digest scheduler as it was until 2026-09-28
    slept a whole day from every boot."""
    from kazma_core.observability import daily_digest

    async def old_digest_scheduler():
        while True:
            try:
                await asyncio.sleep(daily_digest.DIGEST_INTERVAL_HOURS * 3600)
                daily_digest.build_digest()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- a copy of the old loop
                await asyncio.sleep(300)

    clock = _Clock()
    monkeypatch.setattr(daily_digest, "build_digest", _hook(clock))
    _install_clock(monkeypatch, clock)
    worked_at = await _run_until_work(
        lambda: asyncio.get_running_loop().create_task(old_digest_scheduler()), clock
    )
    assert worked_at is not None and worked_at > BOUND  # a whole day, not 30 minutes


# ── the helper ─────────────────────────────────────────────────────────


def test_the_wait_is_counted_from_the_last_run(monkeypatch):
    from kazma_core.observability import cadence

    stamps = {"never": None, "due": 1_000.0, "fresh": 99_000.0}
    monkeypatch.setattr(cadence, "last_run", lambda key: stamps[key])
    now = 100_000.0
    assert cadence.seconds_until_due("never", 3600, settle_s=600, now=now) == 600
    assert cadence.seconds_until_due("due", 3600, settle_s=600, now=now) == 600
    assert cadence.seconds_until_due("fresh", 3600, settle_s=600, now=now) == 2600
    stamps["fresh"] = now - 3590  # due in ten seconds: at least a minute
    assert cadence.seconds_until_due("fresh", 3600, settle_s=600, now=now) == 60


def test_a_stamp_round_trips_through_the_settings_store():
    from kazma_core.observability import cadence

    cadence.stamp_run("observability.test_cadence.last", when=1234.5)
    assert cadence.last_run("observability.test_cadence.last") == 1234.5
    assert cadence.last_run("observability.test_cadence.never") is None


# ── a recent run waits (2026-11, FA-149) ─────────────────────────────


@pytest.mark.parametrize("starter, hours", [
    ("_start_macro_sleep_scheduler", "_MACRO_SLEEP_INTERVAL_HOURS"),
    ("_start_reconsolidation_scheduler", "_RECONSOLIDATION_INTERVAL_HOURS"),
])
async def test_a_job_that_just_ran_does_not_run_at_boot(starter, hours, monkeypatch):
    """The negative of the cases above: the stamp says it ran ten minutes
    ago, so a boot waits the rest of the interval (it used to run 60-180 s
    after every boot, several times a day)."""
    from kazma_core.observability import cadence

    clock = _Clock()
    interval = getattr(wb, hours) * 3600
    monkeypatch.setattr(cadence, "last_run", lambda key: clock.base - 600)
    monkeypatch.setattr(wb, "_distinct_tenants", lambda: ["default"])
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", _hook(clock))
    _install_clock(monkeypatch, clock)
    worked_at = await _run_until_work(getattr(wb, starter), clock)
    assert worked_at is not None and worked_at >= interval - 600 - 60


# ── the digest is sent at its time of day (2026-11, FA-208) ──────────


def _digest_wait(monkeypatch, *, now_hm, sent_hm_days_ago, at=(8, 0)):
    """The digest's wait at local time *now_hm* (UTC here), its last send
    *sent_hm_days_ago* ``(hour, minute, days ago)`` or None."""
    from datetime import UTC, datetime, timedelta

    from kazma_core.cron import scheduler as cron_scheduler
    from kazma_core.observability import cadence, daily_digest

    day = datetime(2026, 11, 2, tzinfo=UTC)
    now = day.replace(hour=now_hm[0], minute=now_hm[1]).timestamp()
    sent = None
    if sent_hm_days_ago is not None:
        h, m, ago = sent_hm_days_ago
        sent = (day.replace(hour=h, minute=m) - timedelta(days=ago)).timestamp()
    monkeypatch.setattr(cron_scheduler, "resolve_cron_zone", lambda: (UTC, "UTC"))
    monkeypatch.setattr(daily_digest, "_digest_time", lambda: at)
    monkeypatch.setattr(cadence, "last_run", lambda key: sent)
    monkeypatch.setattr(daily_digest, "DIGEST_INTERVAL_HOURS", 24)
    return daily_digest._seconds_until_digest(now=now)


def test_a_boot_before_the_time_waits_for_it(monkeypatch):
    assert _digest_wait(monkeypatch, now_hm=(7, 0), sent_hm_days_ago=(8, 10, 1)) == 3600


def test_a_boot_past_an_unsent_time_sends_after_settling(monkeypatch):
    from kazma_core.observability import daily_digest

    wait = _digest_wait(monkeypatch, now_hm=(9, 30), sent_hm_days_ago=(8, 10, 1))
    assert wait == daily_digest._SETTLE_S


def test_after_a_late_send_the_next_is_at_the_time_not_a_day_later(monkeypatch):
    """Sent at 08:10 (a boot's settle): the next is tomorrow 08:00, not
    08:10 -- the interval rule drifted ten minutes a restart."""
    wait = _digest_wait(monkeypatch, now_hm=(8, 10), sent_hm_days_ago=(8, 10, 0))
    assert wait == (23 * 60 + 50) * 60


def test_the_old_schedules_send_earlier_today_counts(monkeypatch):
    """Sent at 05:17 under the drifting schedule: today's 08:00 is not sent
    again at 17:00; the next is tomorrow 08:00."""
    wait = _digest_wait(monkeypatch, now_hm=(17, 0), sent_hm_days_ago=(5, 17, 0))
    assert wait == 15 * 3600


def test_negative_control_the_interval_rule_drifts(monkeypatch):
    from kazma_core.observability import cadence

    last = 1_000_000.0 + 8 * 3600 + 600  # sent at 08:10
    monkeypatch.setattr(cadence, "last_run", lambda key: last)
    wait = cadence.seconds_until_due("k", 24 * 3600, settle_s=600, now=last)
    assert wait == 24 * 3600  # the next lands at 08:10, then 08:20 after another settle


# ── loops end when Kazma stops (2026-11, FA-265) ─────────────────────


async def test_a_sleeping_loop_wakes_at_shutdown():
    from kazma_core import shutdown

    shutdown.reset_shutdown()
    try:
        task = asyncio.get_running_loop().create_task(shutdown.sleep_or_shutdown(6 * 3600))
        await asyncio.sleep(0)
        started = time.monotonic()
        shutdown.signal_shutdown()
        assert await asyncio.wait_for(task, timeout=5) is True
        assert time.monotonic() - started < 2
    finally:
        shutdown.reset_shutdown()


async def test_negative_control_a_plain_sleep_does_not_wake(monkeypatch):
    from kazma_core import shutdown

    shutdown.reset_shutdown()
    try:
        task = asyncio.get_running_loop().create_task(asyncio.sleep(6 * 3600))
        await asyncio.sleep(0)
        shutdown.signal_shutdown()
        done, _ = await asyncio.wait({task}, timeout=0.2)
        assert not done
        task.cancel()
    finally:
        shutdown.reset_shutdown()


async def test_a_short_sleep_returns_false():
    from kazma_core import shutdown

    shutdown.reset_shutdown()
    assert await shutdown.sleep_or_shutdown(0.01) is False
