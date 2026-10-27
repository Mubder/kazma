"""A healthy long scheduled fire is never "stuck: nothing is running it" (audit 2026-10-26 AUD-019).

A fire's lease was a fixed 600 s, never renewed, while a task may run for
``cron.task_budget_seconds`` (up to 3,600 s); the run supervisor judged the
fire's owner by ``lease_until > now`` with ``dead_after_s = 120``. A healthy
15-minute task fire therefore got a "stuck" incident card with a Cancel
button. Now the lease covers the task budget plus a margin, and the adapter
asks the scheduler whether its own fire task still runs
(``CronScheduler.fire_owner_alive``); the lease decides only for a fire this
scheduler does not own. A running fire is judged from its claim (the
occurrence it serves), not a recurring job's previous fire.

The real scheduler, store and run registry on a fake clock, with a turn that
works for 15 minutes.

Negative control: the old lease (600 s) and the old owner rule (the lease
alone), run inline at the same moment, judge the same fire dead.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import kazma_core.cron.settings as cron_settings_mod
import pytest
from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore
from kazma_core.runs import registry
from kazma_core.runs.contract import Verdict, judge
from kazma_core.runs.kinds import KINDS

T0 = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
BUDGET_S = 1800.0


class _Clock:
    def __init__(self) -> None:
        self.t = T0.timestamp()

    def time(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


class _WorkingTurn:
    """A graph whose turn works until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config=None):
        self.started.set()
        await self.release.wait()
        return {"messages": [*state.get("messages", []), {"role": "assistant", "content": "All checks green."}]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    settings = cron_settings_mod.CronSettings(catchup_seconds=3600, task_budget_seconds=BUDGET_S,
                                              max_jobs_per_tenant=500, max_jobs_per_thread=50,
                                              fire_without_hitl=True)
    monkeypatch.setattr(cron_settings_mod, "cron_settings", lambda: settings)
    sent: list[str] = []

    async def backend(target_id, text, **kw):
        sent.append(target_id)
        return f"sent:{target_id}"

    import importlib

    sm = importlib.import_module("kazma_core.tools.send_message")
    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(importlib.import_module("kazma_core.observability.ops_alerts"), "alert",
                        lambda *a, **k: True)
    return {"tmp": tmp_path, "monkeypatch": monkeypatch}


async def _a_fire_fifteen_minutes_in(world) -> tuple[Any, ...]:
    clock = _Clock()
    store = SQLiteCronStore(db_path=str(world["tmp"] / "cron.db"), clock=clock)
    await store.init()
    # A daily job whose PREVIOUS fire was yesterday.
    await store.insert(ScheduledJob(
        job_id="cron-long", timing="daily at 9am", prompt="audit the deploy", platform="telegram",
        thread_id="t", next_run=T0.isoformat(), kind="task", delivery_target="telegram:42",
        last_fire_at=datetime.fromtimestamp(T0.timestamp() - 86400, UTC).isoformat()))
    turn = _WorkingTurn()
    sched = CronScheduler(store=store, graph_builder=lambda: turn)
    world["monkeypatch"].setattr("kazma_core.cron.scheduler.get_cron_scheduler", lambda: sched)
    fire = asyncio.create_task(sched._execute(await store.get("cron-long")))
    await asyncio.wait_for(turn.started.wait(), 10)
    claimed_at = clock.t
    clock.t += 15 * 60  # a healthy fire, fifteen minutes into its turn
    return clock, store, sched, turn, fire, claimed_at


async def _the_run(clock):
    listing = await registry.list_runs(kinds=("scheduled_job",), now=clock.t)
    assert not listing.errors, listing.errors
    return next(r for r in listing.runs if r.native_id == "cron-long")


async def test_a_long_fire_is_alive_and_its_lease_covers_the_budget(world):
    clock, store, sched, turn, fire, claimed_at = await _a_fire_fifteen_minutes_in(world)
    try:
        job = await store.get("cron-long")
        assert job.status == JobStatus.RUNNING
        assert job.lease_until >= claimed_at + BUDGET_S, "the lease covers the whole task budget"
        run = await _the_run(clock)
        assert run.owner_alive is True
        assert run.verdict not in (Verdict.DEAD, Verdict.STALLED)
        assert run.started_at == T0.timestamp(), "judged from its claim, not yesterday's fire"
        turn.release.set()
        await asyncio.wait_for(fire, 10)
        assert (await store.get("cron-long")).status == JobStatus.PENDING  # the next day's occurrence
    finally:
        turn.release.set()
        await store.close()


async def test_a_fire_whose_task_ended_is_dead(world):
    """Ours, and no task runs it: dead at once, whatever its lease says."""
    clock, store, sched, turn, fire, _claimed = await _a_fire_fifteen_minutes_in(world)
    try:
        fire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fire
        sched._in_flight.discard("cron-long")
        assert (await store.get("cron-long")).status == JobStatus.RUNNING
        run = await _the_run(clock)
        assert run.owner_alive is False and run.verdict == Verdict.DEAD
    finally:
        turn.release.set()
        await store.close()


async def test_another_owners_fire_is_judged_by_its_lease(world):
    clock, store, sched, turn, fire, _claimed = await _a_fire_fifteen_minutes_in(world)
    try:
        job = await store.get("cron-long")
        assert sched.fire_owner_alive(replace(job, fire_owner="cron-elsewhere")) is None
        assert sched.fire_owner_alive(job) is True
    finally:
        turn.release.set()
        await asyncio.wait_for(fire, 10)
        await store.close()


async def test_negative_control_the_old_lease_and_rule_call_it_dead(world):
    clock, store, sched, turn, fire, claimed_at = await _a_fire_fifteen_minutes_in(world)
    try:
        run = await _the_run(clock)
        old_lease_until = claimed_at + 600.0  # the fixed lease until 2026-10-26
        job = await store.get("cron-long")
        old_owner = bool(old_lease_until > clock.t)  # the old adapter's rule
        old_started = datetime.fromisoformat(job.last_fire_at).timestamp()  # last_fire_at first
        old = replace(run, owner_alive=old_owner, started_at=old_started, progress_at=old_started)
        assert old_owner is False
        assert judge(old, KINDS["scheduled_job"], clock.t) == Verdict.DEAD
    finally:
        turn.release.set()
        await asyncio.wait_for(fire, 10)
        await store.close()
