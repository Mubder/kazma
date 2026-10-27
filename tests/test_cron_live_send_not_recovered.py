"""A send the scheduler is making is never recovered as interrupted (audit 2026-10-26 AUD-017).

A delivery's lease (``DELIVERY_LEASE_S``, 60 s) is not renewed while its
send runs, and every poll's recovery took ``state = 'sending' AND
(lease_owner != me OR lease_until < now)`` -- which matched the scheduler's
OWN live send once it outlived the lease. A Telegram send that retries (three
30 s reads, 429 waits) did: the row was marked ``unknown``, the job
``delivery_unknown``, an alert said the message "may not have arrived" over
one that did, and the send's own outcome was dropped. The scheduler now names
the sends it is making (``_sending``), and recovery leaves those alone.

The real scheduler and store on a fake clock: a reminder's send blocks, the
clock moves past the lease, the next poll's recovery runs, the send finishes.

Negative control: the recovery call as it was (no live sends named) marks the
same live send ``unknown``.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from typing import Any

import kazma_core.cron.store as store_mod
import pytest
from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

T0 = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.t = T0.timestamp()

    def time(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


@pytest.fixture
def platform(monkeypatch):
    """A Telegram that holds the first send until released (a slow, retrying send)."""
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    state: dict[str, Any] = {"entered": asyncio.Event(), "release": asyncio.Event(), "sent": [], "alerts": []}

    async def backend(target_id, text, **kw):
        state["entered"].set()
        await state["release"].wait()
        state["sent"].append(target_id)
        return f"sent:{target_id}"

    def alert(key, title, detail="", **kw):
        state["alerts"].append(key)
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", alert)
    return state


async def _a_send_past_its_lease(tmp_path, platform):
    clock = _Clock()
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"), clock=clock)
    await store.init()
    await store.insert(ScheduledJob(
        job_id="cron-slow", timing="5m", prompt="call the dentist", platform="telegram", thread_id="t",
        next_run=T0.isoformat(), kind="reminder", delivery_target="telegram:42"))
    sched = CronScheduler(store=store)
    fire = asyncio.create_task(sched._execute(await store.get("cron-slow")))
    await asyncio.wait_for(platform["entered"].wait(), 10)
    clock.t += store_mod.DELIVERY_LEASE_S + 60  # the send outlives its lease
    return store, sched, fire


async def test_the_next_poll_leaves_a_live_send_alone(tmp_path, platform):
    store, sched, fire = await _a_send_past_its_lease(tmp_path, platform)
    try:
        await sched._recover(boot=False)  # what the next poll does first
        assert (await store.deliveries_for("cron-slow"))[0]["state"] == "sending"
        platform["release"].set()
        await asyncio.wait_for(fire, 10)
        await sched._drain_alerts()
        row = (await store.deliveries_for("cron-slow"))[0]
        job = await store.get("cron-slow")
        assert row["state"] == "delivered" and platform["sent"] == ["telegram:42"]
        assert job.status == JobStatus.DONE and job.delivery_state == "delivered"
        assert "cron.delivery_unknown" not in platform["alerts"]
        assert sched._sending == set()
    finally:
        platform["release"].set()
        await store.close()


async def test_a_send_whose_sender_is_gone_is_still_recovered(tmp_path, platform):
    """The exception covers only sends this scheduler is making: the same
    row, once no longer live here (its task ended without a record), is
    recovered past its lease as before."""
    store, sched, fire = await _a_send_past_its_lease(tmp_path, platform)
    try:
        fire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fire
        assert sched._sending == set()
        await sched._recover(boot=False)
        assert (await store.deliveries_for("cron-slow"))[0]["state"] == "unknown"
    finally:
        platform["release"].set()
        await store.close()


async def test_negative_control_the_old_recovery_marks_the_live_send_unknown(tmp_path, platform):
    store, sched, fire = await _a_send_past_its_lease(tmp_path, platform)
    try:
        await store.recover_deliveries(owner=sched._owner)  # the call as it was until 2026-10-26
        assert (await store.deliveries_for("cron-slow"))[0]["state"] == "unknown"
        assert (await store.get("cron-slow")).status == JobStatus.DELIVERY_UNKNOWN
        platform["release"].set()
        await asyncio.wait_for(fire, 10)
        assert platform["sent"] == ["telegram:42"], "it arrived, yet the record says unknown"
        assert (await store.deliveries_for("cron-slow"))[0]["state"] == "unknown"
    finally:
        platform["release"].set()
        await store.close()
