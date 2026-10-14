"""Cron deliveries go through a durable outbox (Scheduled audit SCH-E02, S05 delivery half).

The witness: a reminder the platform refused was recorded DONE, its result
text untouched, no alert, never retried; a CRITICAL log line was the only
trace. Now a refused delivery is retried with backoff, acknowledged only on
``sent:``, and after the bound ends ``undeliverable`` with ONE alert
(``cron.delivery_failed``); the job, the page and the daily digest say so. A
platform that recovers delivers exactly once.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import kazma_core.cron.store as store_mod
from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore


@pytest.fixture
def capture(monkeypatch):
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    state: dict[str, Any] = {"sent": [], "alerts": [], "refuse": 0}

    async def backend(target_id, text, **kw):
        state["sent"].append((target_id, text))
        if state["refuse"] == -1 or state["refuse"] > 0:
            if state["refuse"] > 0:
                state["refuse"] -= 1
            return f"Error: telegram did not take the message for {target_id}"
        return f"sent:{target_id}"

    def alert(key, title, detail="", **kw):
        state["alerts"].append((key, title, detail))
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", alert)
    # Retries without waiting: the backoff is checked separately.
    monkeypatch.setattr(store_mod, "_delivery_backoff", lambda attempts: 0.0)
    return state


class _Reply:
    async def ainvoke(self, state, config=None):
        return {"messages": [*state.get("messages", []), {"role": "assistant", "content": "Done: all checks green."}]}


async def _setup(tmp_path, *, kind="task", target="telegram:42"):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    await store.insert(ScheduledJob(
        job_id="cron-out", timing="2026-10-14T09:00", prompt="check the deploy", platform="telegram",
        thread_id="t", next_run=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(), kind=kind,
        delivery_target=target))
    sched = CronScheduler(store=store, graph_builder=lambda: _Reply())
    return store, sched


async def _drain(sched, n=10):
    for _ in range(n):
        await sched._drain_deliveries()
        await sched._alert_failed_deliveries()


async def test_a_refused_delivery_is_retried_then_undeliverable_with_one_alert(tmp_path, capture):
    capture["refuse"] = -1  # the platform refuses every attempt
    store, sched = await _setup(tmp_path)
    try:
        await sched._execute(await store.get("cron-out"))
        job = await store.get("cron-out")
        assert job.status == JobStatus.DELIVERING and job.status != JobStatus.DONE
        assert job.delivery_state == "retrying" and "did not take" in job.delivery_error
        await _drain(sched)
        job = await store.get("cron-out")
        assert job.status == JobStatus.UNDELIVERABLE and job.last_outcome == "undeliverable"
        assert len(capture["sent"]) == store_mod.DELIVERY_MAX_ATTEMPTS  # the retry history
        history = await store.deliveries_for("cron-out")
        assert history[0]["state"] == "undeliverable" and history[0]["attempts"] == store_mod.DELIVERY_MAX_ATTEMPTS
        assert [a[0] for a in capture["alerts"]] == ["cron.delivery_failed"]  # exactly one
        await _drain(sched)
        assert [a[0] for a in capture["alerts"]] == ["cron.delivery_failed"]
    finally:
        await store.close()


async def test_negative_control_the_old_order_ended_done_over_a_refusal(tmp_path, capture):
    """The fire as it was until 2026-10-14: send, read the answer only for
    the log level, then finalize DONE. The check above ("never DONE over a
    refusal, and one alert") fails on it."""
    from kazma_core.tools.send_message import send_failed, send_message

    capture["refuse"] = -1
    store, _sched = await _setup(tmp_path)
    try:
        answer = await send_message("telegram:42", "Done: all checks green.", backend="telegram")
        await store._db.execute(  # noqa: SLF001 -- the old _finalize's statement
            "UPDATE cron_jobs SET status = 'done', last_result = ? WHERE job_id = 'cron-out'", ("ok",))
        job = await store.get("cron-out")
        assert send_failed(answer) and job.status == JobStatus.DONE  # refused, yet "done"
        assert capture["alerts"] == [] and len(capture["sent"]) == 1  # no alert, never retried
    finally:
        await store.close()


async def test_a_platform_that_recovers_delivers_exactly_once(tmp_path, capture):
    capture["refuse"] = 2  # refuses twice, then takes it
    store, sched = await _setup(tmp_path)
    try:
        await sched._execute(await store.get("cron-out"))
        await _drain(sched)
        job = await store.get("cron-out")
        assert job.status == JobStatus.DONE and job.delivery_state == "delivered"
        assert len(capture["sent"]) == 3
        assert [s for s in capture["sent"] if s[1].startswith("Done")] == capture["sent"]
        assert capture["alerts"] == []
    finally:
        await store.close()


async def test_no_address_is_undeliverable_at_once_naming_the_job(tmp_path, capture, caplog):
    store, sched = await _setup(tmp_path, target="")
    try:
        await sched._execute(await store.get("cron-out"))
        job = await store.get("cron-out")
        assert job.status == JobStatus.UNDELIVERABLE
        assert capture["sent"] == []  # never a guessed address
        assert any(r.levelname == "CRITICAL" and "UNDELIVERABLE job cron-out" in r.getMessage()
                   for r in caplog.records)
        assert [a[0] for a in capture["alerts"]] == ["cron.delivery_failed"]
    finally:
        await store.close()


async def test_the_backoff_doubles_and_is_bounded() -> None:
    assert [store_mod._delivery_backoff(n) for n in range(1, 8)] == [30, 60, 120, 240, 480, 900, 900]


async def test_the_page_and_the_digest_show_it(tmp_path, capture, monkeypatch):
    capture["refuse"] = -1
    store, sched = await _setup(tmp_path)
    try:
        await sched._execute(await store.get("cron-out"))
        await _drain(sched)
        from kazma_ui.scheduled_api import _cron_item, _status_class

        item = _cron_item((await sched.list_jobs(scoped=False))[0])
        assert item["status"] == "undeliverable" and _status_class(item["status"]) == "failed"
        assert item["delivery_state"] == "undeliverable" and item["delivery_error"]
        assert item["actions"] == ["restore"]
    finally:
        await store.close()


def test_the_digest_counts_cron_outcomes(tmp_path, monkeypatch):
    import json

    from kazma_core.observability import daily_digest

    log = tmp_path / "kazma.log"
    now = datetime.now(UTC).isoformat()
    lines = [
        "[cron] outcome job=cron-a outcome=delivery_delivered status=-",
        "[cron] outcome job=cron-b outcome=delivery_undeliverable status=-",
        "[cron] outcome job=cron-c outcome=missed status=missed (due x)",
        "[cron] outcome job=cron-d outcome=turn_failed status=failed",
    ]
    log.write_text("\n".join(json.dumps({"timestamp": now, "message": m}) for m in lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_LOG_FILE", str(log))
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(tmp_path / "guard.log"))
    text = daily_digest.build_digest(hours=24)
    assert "Scheduled messages delivered: 1" in text
    assert "scheduled messages not delivered: 1" in text
    assert "scheduled jobs missed while Kazma was down: 1" in text
    assert "scheduled tasks that failed: 1" in text


def test_the_digest_reads_a_line_the_scheduler_writes():
    """The digest's cron line is one the code emits (tests/_emitted_lines.py);
    the renamed line is the negative control."""
    from kazma_core.observability import daily_digest
    from tests._emitted_lines import emitted_app_lines, product_roots

    lines = emitted_app_lines(product_roots())
    assert any(daily_digest._CRON_LINE.search(line) for line in lines)
    assert not any(daily_digest._CRON_LINE.search(line) for line in ["[cron] fire done job=1 outcome=1"])
