"""Readiness sees a dead cron loop, and /metrics says what the fires did (Scheduled audit SCH-E11).

The witness: the cron check read the ``_running`` flag, which stays True after
the poll task dies, so readiness said "ok" over a scheduler that would never
fire again. Now the poll task must be alive and its last pass no older than
two intervals (plus a margin), and the scheduler exposes its fires, missed
jobs, lateness, outbox and heartbeat on /metrics.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore


@pytest.fixture
def delivered(monkeypatch):
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    sent: list[tuple[str, str]] = []

    async def backend(target_id, text, **kw):
        sent.append((target_id, text))
        return f"sent:{target_id}"

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", lambda *a, **k: True)
    return sent


async def _until(cond, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def _ready_with_only_cron(monkeypatch, sched):
    """Readiness with every other check answering ok, and this scheduler."""
    import kazma_core.cron.scheduler as sm
    from kazma_ui import health

    for name in ("check_config_store", "check_database", "check_swarm_engine", "check_model_registry",
                 "check_agent_runner", "check_mcp", "check_schedulers", "check_llm_provider"):
        monkeypatch.setattr(health, name, lambda name=name: {"status": "ok", "component": name})
    monkeypatch.setattr(sm, "get_cron_scheduler", lambda: sched)
    return health


async def test_a_dead_poll_loop_makes_readiness_degraded(tmp_path, monkeypatch, delivered):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    sched = CronScheduler(store=store, poll_interval=0.05)
    health = _ready_with_only_cron(monkeypatch, sched)
    try:
        await sched.start()
        await _until(lambda: sched.last_poll_at is not None)
        assert health.check_cron()["status"] == "ok"
        # The poll task dies; the flag the old check read stays True.
        sched._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sched._task
        assert sched._running is True
        cron = health.check_cron()
        assert cron["status"] == "degraded" and cron["state"] == "dead"
        ready = await health._readiness()
        assert ready.status_code == 200  # cron is not a chat dependency
        assert b'"status":"degraded"' in ready.body
    finally:
        await sched.stop()
        await store.close()


async def test_a_loop_that_stopped_polling_is_stale_within_two_intervals(tmp_path, monkeypatch, delivered):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    sched = CronScheduler(store=store, poll_interval=0.05)
    health = _ready_with_only_cron(monkeypatch, sched)
    try:
        await sched.start()
        await _until(lambda: sched.last_poll_at is not None)
        live = sched.liveness()
        # A pass older than two intervals plus the margin: stuck, not idle.
        sched.last_poll_at = time.time() - live["stale_after_s"] - 1
        cron = health.check_cron()
        assert cron["status"] == "degraded" and cron["state"] == "stale"
    finally:
        await sched.stop()
        await store.close()


async def test_negative_control_the_old_check_read_the_flag(tmp_path):
    """The old check: ``ok`` whenever ``_running`` -- true over a dead loop."""
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    sched = CronScheduler(store=store, poll_interval=0.05)
    try:
        await sched.start()
        sched._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sched._task
        old = {"status": "ok" if getattr(sched, "_running", False) else "stopped"}
        assert old["status"] == "ok"  # what readiness used to say
        assert sched.liveness()["state"] == "dead"  # what it says now
    finally:
        await sched.stop()
        await store.close()


async def test_metrics_count_fires_missed_lateness_and_the_heartbeat(tmp_path, monkeypatch, delivered):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    now = datetime.now(UTC)
    for job_id, late in (("cron-ontime", timedelta(minutes=5)), ("cron-gone", timedelta(hours=5))):
        await store.insert(ScheduledJob(
            job_id=job_id, timing="2026-10-14T09:00", prompt="Water the plants", platform="telegram",
            thread_id="t1", status=JobStatus.PENDING, next_run=(now - late).isoformat(), kind="reminder",
            created_at=now.isoformat(), delivery_target="telegram:42"))
    sched = CronScheduler(store=store, poll_interval=0.05)
    try:
        await sched.start()
        await _until(lambda: len(delivered) >= 2 and sched.active_fire_count() == 0)
        lines = await sched.metrics()
    finally:
        await sched.stop()
        await store.close()
    text = "\n".join(lines)
    # A reminder's fire ends handing its message to the outbox ("delivering").
    assert 'kazma_cron_fires_24h{outcome="delivering"} 1' in text, text
    assert 'kazma_cron_deliveries_24h{state="delivered"} 2' in text, text  # the reminder and the notice
    assert "kazma_cron_missed_24h 1" in text
    assert 'kazma_cron_fire_lateness_seconds{quantile="0.95"}' in text
    p95 = float(next(ln for ln in lines if 'quantile="0.95"' in ln).split()[-1])
    assert 240 <= p95 <= 600  # the on-time job was five minutes late
    assert "kazma_cron_outbox_pending 0" in text
    assert "kazma_cron_poll_age_seconds" in text


def test_the_metrics_page_carries_the_cron_lines(monkeypatch):
    import kazma_core.cron.scheduler as sm
    from kazma_ui.metrics import create_metrics_router

    class _Sched:
        async def metrics(self):
            return ["kazma_cron_missed_24h 3"]

    monkeypatch.setattr(sm, "get_cron_scheduler", lambda: _Sched())
    app = FastAPI()
    gateway = SimpleNamespace(metrics=SimpleNamespace(inbound_total=0, outbound_total=0, errors_total=0),
                              adapters={}, queue=SimpleNamespace(qsize=lambda: 0))
    app.include_router(create_metrics_router(gateway))
    assert "kazma_cron_missed_24h 3" in TestClient(app).get("/metrics").text
