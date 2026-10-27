"""The cron engine's durable fire record (Scheduled audit 2026-10-13).

Each test inverts one of the audit's witnesses (session-artifacts, not kept as
tests): it asserts the fixed behaviour, and where the old behaviour can be put
back in-process it runs that too as the negative control.

* SCH-E01 -- a failed agent turn is a failed fire: counted, alerted, never
  delivered as if it were the answer; a reminder needs no model at all.
* SCH-E04 -- a fire interrupted by a restart runs again (it was marked FAILED,
  delivered or not); a crash inside the old three-statement finalize stranded
  a daily job DONE; stop() drains fires; exec-task errors are retrieved;
  /health/activity counts fires and --when-idle waits for them.
* SCH-E06 -- a job late past its catch-up window is MISSED (never "done") and
  its chat gets exactly one notice; a late fire inside the window says so.
* SCH-E07 -- retention counts from when a job finished; recovery reads every
  running row.
* SCH-E12 / S07 -- the state-by-action matrix on the real store; an edit or a
  cancel during a fire is recorded and applied when the fire ends; a finished
  job comes back only through restore.
(The real-process crash matrix is tests/test_cron_crash_matrix.py.)
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

REMINDER = "Call the dentist to confirm Thursday"


class _ReplyGraph:
    def __init__(self, text: str = "Reminder: call the dentist.") -> None:
        self.text = text
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        msgs = list(state.get("messages", []))
        msgs.append({"role": "assistant", "content": self.text})
        return {"messages": msgs}


class _RaisingGraph:
    calls = 0

    async def ainvoke(self, state, config=None):
        type(self).calls += 1
        raise RuntimeError("provider down: no usable API key")


class _TurnFailedGraph:
    async def ainvoke(self, state, config=None):
        msgs = list(state.get("messages", []))
        warn = "⚠️ The model provider rejected the request (401). Check the API key."
        msgs.append({"role": "assistant", "content": warn})
        return {"messages": msgs, "turn_failed": True, "error_message": warn}


class _BlockingGraph:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config=None):
        self.entered.set()
        await self.release.wait()
        msgs = list(state.get("messages", []))
        msgs.append({"role": "assistant", "content": "late reply"})
        return {"messages": msgs}


@pytest.fixture
def capture(monkeypatch):
    """Captured chat delivery + ops alerts; the backend answers per ``mode``."""
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    state: dict[str, Any] = {"sent": [], "alerts": [], "mode": "ok"}

    async def backend(target_id, text, **kw):
        state["sent"].append((target_id, text))
        if state["mode"] == "refuse":
            return f"Error: telegram did not take the message for {target_id}"
        return f"sent:{target_id}"

    def alert(key, title, detail="", **kw):
        state["alerts"].append((key, title))
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", alert)
    return state


async def _store(tmp_path, name="cron.db") -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(tmp_path / name))
    await store.init()
    return store


async def _job(store, job_id) -> ScheduledJob:
    job = await store.get(job_id)
    assert job is not None
    return job


async def _insert(store, *, job_id, timing, next_run, status=JobStatus.PENDING, kind="task",
                  created_at=None, prompt=REMINDER, target="telegram:42", **extra):
    await store.insert(ScheduledJob(
        job_id=job_id, timing=timing, prompt=prompt, platform="telegram",
        thread_id="thread-a", status=status, next_run=next_run, kind=kind,
        created_at=created_at or datetime.now(UTC).isoformat(),
        delivery_target=target, **extra,
    ))


def _ago(**kw) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


async def _fire(sched: CronScheduler, store, job_id: str) -> ScheduledJob:
    await sched._execute(await _job(store, job_id))
    return await _job(store, job_id)


async def _settled(sched: CronScheduler, store, *, passes: int = 2, timeout: float = 10.0) -> None:
    """Wait until the loop has made *passes* whole passes with no fire in
    flight and nothing left to deliver -- a condition, never a bet on timing."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not (sched._poll_count >= passes and sched.active_fire_count() == 0
               and await store.pending_delivery_count() == 0):
        if loop.time() > deadline:
            raise AssertionError("the scheduler did not settle")
        await asyncio.sleep(0.02)


# ── SCH-E01: a failed turn is a failed fire ─────────────────────────────


async def test_provider_failure_is_a_counted_alerted_failure_never_delivered(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _RaisingGraph())
        await _insert(store, job_id="cron-daily01", timing="daily at 9am", next_run=_ago(minutes=1))
        job = await _fire(sched, store, "cron-daily01")
        assert job.failure_count == 1
        assert job.last_outcome == "turn_failed"
        assert job.status == JobStatus.PENDING  # the daily job keeps its next run
        assert datetime.fromisoformat(job.next_run) > datetime.now(UTC)
        assert capture["sent"] == []  # the stored instruction is NOT delivered as the answer
        assert [k for k, _ in capture["alerts"]] == ["cron.job_failure"]
    finally:
        await store.close()


async def test_turn_failed_one_off_fails_and_no_warning_is_delivered(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _TurnFailedGraph())
        await _insert(store, job_id="cron-once01", timing="2026-10-14T09:00", next_run=_ago(seconds=5))
        job = await _fire(sched, store, "cron-once01")
        assert job.status == JobStatus.FAILED and job.last_outcome == "turn_failed"
        assert capture["sent"] == []
        assert [k for k, _ in capture["alerts"]] == ["cron.job_failure"]
    finally:
        await store.close()


async def test_a_reminder_is_delivered_with_no_model_call(tmp_path, capture):
    """A reminder's message is fixed when it is booked: delivered as written,
    whatever the provider is doing."""
    store = await _store(tmp_path)
    _RaisingGraph.calls = 0
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _RaisingGraph())
        await _insert(store, job_id="cron-rem01", timing="2026-10-14T09:00", next_run=_ago(seconds=5),
                      kind="reminder")
        job = await _fire(sched, store, "cron-rem01")
        assert _RaisingGraph.calls == 0
        assert capture["sent"] == [("telegram:42", f"⏰ Reminder\n\n{REMINDER}")]
        assert job.status == JobStatus.DONE and job.last_outcome == "delivered"
    finally:
        await store.close()


async def test_the_budget_runs_out_after_three_failures_and_says_so(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _RaisingGraph())
        await _insert(store, job_id="cron-daily02", timing="daily at 9am", next_run=_ago(minutes=1))
        for _ in range(3):
            await store._db.execute("UPDATE cron_jobs SET next_run = ? WHERE job_id = 'cron-daily02'",  # noqa: SLF001
                                    (_ago(minutes=1),))
            job = await _fire(sched, store, "cron-daily02")
        assert job.status == JobStatus.FAILED and job.failure_count == 3
        assert [k for k, _ in capture["alerts"]] == ["cron.job_failure", "cron.job_failed"]
    finally:
        await store.close()


async def test_negative_control_the_old_produce_delivers_the_failure(tmp_path, capture, monkeypatch):
    """The check above catches the old behaviour: a turn's text delivered and
    the fire recorded as a success, whatever the turn said."""
    from kazma_core.cron.store import FireResult

    async def old_produce(self, job, settings, zone, zone_name, *, due, now):
        from kazma_core.agent.turn import run_agent_turn

        turn = await run_agent_turn(graph=_RaisingGraph(), thread_id="t", state={"messages": []},
                                    config={"configurable": {"thread_id": "t"}}, persist=False)
        return FireResult(success=True, outcome="delivering", result_text=turn.text or "",
                          body=f"⏰ Reminder\n\n{job.prompt}")

    monkeypatch.setattr(CronScheduler, "_produce", old_produce)
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _RaisingGraph())
        await _insert(store, job_id="cron-daily03", timing="daily at 9am", next_run=_ago(minutes=1))
        job = await _fire(sched, store, "cron-daily03")
        assert job.failure_count == 0 and capture["sent"] and capture["alerts"] == []
    finally:
        await store.close()


# ── SCH-E04: interrupted fires run again; stop() drains; errors are retrieved ──


async def test_a_restart_mid_fire_fires_again_and_delivers_once(tmp_path, capture):
    store = await _store(tmp_path)
    graph = _BlockingGraph()
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: graph, poll_interval=0.05, drain_seconds=0.2)
        await _insert(store, job_id="cron-mid01", timing="2026-10-14T09:00", next_run=_ago(seconds=5))
        await sched.start()
        await asyncio.wait_for(graph.entered.wait(), 10)
        assert (await _job(store, "cron-mid01")).status == JobStatus.RUNNING
        assert sched.active_fire_count() == 1  # what /health/activity reports
        await sched.stop()  # drains within its bound, then stops the fire
        assert sched.active_fire_count() == 0
        assert (await _job(store, "cron-mid01")).status == JobStatus.RUNNING  # left for the next start

        boot = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await boot.start()
        for _ in range(100):
            if (await _job(store, "cron-mid01")).status == JobStatus.DONE:
                break
            await asyncio.sleep(0.05)
        await boot.stop()
        job = await _job(store, "cron-mid01")
        assert job.status == JobStatus.DONE and job.last_outcome == "delivered"
        assert len(capture["sent"]) == 1
        events = [e["event"] for e in await store.list_events("cron-mid01")]
        assert "fire_interrupted_retry" in events
    finally:
        await store.close()


async def test_a_daily_fire_advances_in_the_same_step_it_finishes(tmp_path, capture, monkeypatch):
    """The old finalize was three statements; a death after the first left a
    daily job DONE for good. Now one transaction: a failure inside it rolls
    back to RUNNING, and the restart fires the occurrence again."""
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph())
        await _insert(store, job_id="cron-day04", timing="daily at 9am", next_run=_ago(minutes=1))
        real = store._event  # noqa: SLF001

        async def die_on_finish(db, job_id, event, **kw):
            if event == "fire_finished":
                raise RuntimeError("process died inside the finish")
            return await real(db, job_id, event, **kw)

        monkeypatch.setattr(store, "_event", die_on_finish)
        with pytest.raises(RuntimeError):
            await sched._execute(await _job(store, "cron-day04"))
        monkeypatch.setattr(store, "_event", real)
        job = await _job(store, "cron-day04")
        assert job.status == JobStatus.RUNNING  # nothing half-written: no DONE
        assert await store.deliveries_for("cron-day04") == []  # and no delivery row either

        boot = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await boot.start()
        for _ in range(100):
            if (await _job(store, "cron-day04")).status == JobStatus.PENDING and capture["sent"]:
                break
            await asyncio.sleep(0.05)
        await boot.stop()
        job = await _job(store, "cron-day04")
        assert job.status == JobStatus.PENDING
        assert datetime.fromisoformat(job.next_run) > datetime.now(UTC)
        assert len(capture["sent"]) == 1
        assert "cron-day04" in {j.job_id for j in await store.list_active()}
    finally:
        await store.close()


async def test_an_exec_task_error_is_logged_and_the_fire_recovered(tmp_path, capture, monkeypatch, caplog):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await _insert(store, job_id="cron-err01", timing="2026-10-14T09:00", next_run=_ago(seconds=5))
        calls = {"n": 0}
        real = store.finish_fire

        async def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database is locked")
            return await real(*a, **kw)

        monkeypatch.setattr(store, "finish_fire", flaky)
        await sched.start()
        for _ in range(200):
            if (await _job(store, "cron-err01")).status == JobStatus.DONE:
                break
            await asyncio.sleep(0.05)
        await sched.stop()
        assert (await _job(store, "cron-err01")).status == JobStatus.DONE
        assert any("ended with an error" in r.getMessage() for r in caplog.records)
        assert len(capture["sent"]) == 1
    finally:
        await store.close()


async def test_health_activity_counts_fires_and_the_guard_waits_for_them(tmp_path, capture, monkeypatch):
    import kazma_core.cron.scheduler as cron_mod
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import kazma_ui.health as health

    class _Busy:
        def active_fire_count(self) -> int:
            return 1

    monkeypatch.setattr(cron_mod, "_cron_scheduler", _Busy())
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        body = c.get("/health/activity").json()
    assert body["active_fires"] == 1 and body["idle"] is False

    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_guard_for_test", Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py")
    guard = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["_guard_for_test"] = guard
    spec.loader.exec_module(guard)
    monkeypatch.setattr(guard, "_activity", lambda _url: {"active_turns": 0, "active_fires": 1})
    monkeypatch.setattr(guard.time, "sleep", lambda _s: None)
    assert guard._wait_until_idle("http://x/health", 0.0, lambda *a, **k: None) is False  # busy: a fire runs
    monkeypatch.setattr(guard, "_activity", lambda _url: {"active_turns": 0, "active_fires": 0})
    assert guard._wait_until_idle("http://x/health", 5.0, lambda *a, **k: None) is True


# ── SCH-E06: missed is missed, with one notice; late says so ─────────────


async def test_a_job_past_its_catchup_window_is_missed_with_one_notice(tmp_path, capture):
    store = await _store(tmp_path)
    graph = _ReplyGraph()
    try:
        await _insert(store, job_id="cron-missed1", timing="2026-10-12T09:00:00", next_run=_ago(hours=25),
                      kind="reminder")
        sched = CronScheduler(store=store, graph_builder=lambda: graph, poll_interval=0.05)
        await sched.start()
        await _settled(sched, store)
        await sched.stop()
        job = await _job(store, "cron-missed1")
        assert job.status == JobStatus.MISSED and job.status != JobStatus.DONE
        assert graph.calls == 0
        assert len(capture["sent"]) == 1 and "Missed while Kazma was offline" in capture["sent"][0][1]
        assert REMINDER in capture["sent"][0][1]
        # A second start sends no second notice.
        again = CronScheduler(store=store, graph_builder=lambda: graph, poll_interval=0.05)
        await again.start()
        await _settled(again, store)
        await again.stop()
        assert len(capture["sent"]) == 1
    finally:
        await store.close()


async def test_a_recurring_job_missed_moves_on_with_one_notice(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        await _insert(store, job_id="cron-dmiss", timing="daily at 9am", next_run=_ago(hours=30), kind="reminder")
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await sched.start()
        await _settled(sched, store)
        await sched.stop()
        job = await _job(store, "cron-dmiss")
        assert job.status == JobStatus.PENDING and job.last_outcome == "missed"
        assert datetime.fromisoformat(job.next_run) > datetime.now(UTC)
        assert len(capture["sent"]) == 1
    finally:
        await store.close()


@pytest.mark.parametrize(("late", "missed"), [(timedelta(minutes=10), False), (timedelta(minutes=59), False),
                                              (timedelta(minutes=61), True), (timedelta(days=2), True)])
async def test_each_lateness_band(tmp_path, capture, late, missed):
    """Inside the default 60-minute window a reminder fires, saying how late;
    past it, it is missed. Never "done" without a delivery."""
    store = await _store(tmp_path)
    try:
        due = datetime.now(UTC) - late
        await _insert(store, job_id="cron-band", timing="2026-10-14T09:00", next_run=due.isoformat(),
                      kind="reminder")
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await sched.start()
        await _settled(sched, store)
        await sched.stop()
        job = await _job(store, "cron-band")
        assert len(capture["sent"]) == 1
        if missed:
            assert job.status == JobStatus.MISSED
        else:
            assert job.status == JobStatus.DONE
            assert "late)" in capture["sent"][0][1]  # "(due 2026-.. 09:00 UTC, 10 min late)"
    finally:
        await store.close()


async def test_negative_control_the_old_stale_skip_reads_as_done(tmp_path):
    """The old rule marked a stale one-off DONE with "Skipped" and no notice;
    the band check above would catch it (status DONE, nothing sent)."""
    store = await _store(tmp_path)
    try:
        await _insert(store, job_id="cron-old", timing="x", next_run=_ago(hours=25), kind="reminder")
        await store._db.execute(  # noqa: SLF001 — what the old poll loop did
            "UPDATE cron_jobs SET status = 'done', last_result = 'Skipped: job came due more than 24h ago' "
            "WHERE job_id = 'cron-old'")
        job = await _job(store, "cron-old")
        assert job.status == JobStatus.DONE and await store.deliveries_for("cron-old") == []
    finally:
        await store.close()


# ── SCH-E07: retention and recovery ──────────────────────────────────────


@pytest.mark.parametrize("rows", [0, 499, 5000])
async def test_purge_and_recovery_at_any_size(tmp_path, capture, rows):
    store = await _store(tmp_path)
    try:
        now = datetime.now(UTC)
        # An old job that failed TODAY keeps its failure.
        await _insert(store, job_id="cron-oldfail", timing="daily at 9am", next_run=now.isoformat(),
                      status=JobStatus.FAILED, created_at=(now - timedelta(days=30)).isoformat(),
                      finished_at=now.isoformat())
        # An old RUNNING job behind many newer rows is still recovered.
        await _insert(store, job_id="cron-oldrun", timing="2026-10-14T09:00", kind="reminder",
                      next_run=(now - timedelta(minutes=5)).isoformat(), status=JobStatus.RUNNING,
                      created_at=(now - timedelta(days=60)).isoformat(), fire_owner="dead-process")
        await store._db.executemany(  # noqa: SLF001 — bulk seed
            "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, status, created_at, next_run, "
            "finished_at) VALUES (?, '5m', 'p', 'telegram', 't', 'done', ?, ?, ?)",
            [(f"cron-d{i:05d}", (now - timedelta(seconds=i)).isoformat(), now.isoformat(), now.isoformat())
             for i in range(rows)])
        assert await store.purge_terminal_jobs(keep_last=10_000) == 0
        assert await store.get("cron-oldfail") is not None
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph(), poll_interval=0.05)
        await sched.start()
        for _ in range(100):
            if (await _job(store, "cron-oldrun")).status == JobStatus.DONE:
                break
            await asyncio.sleep(0.05)
        await sched.stop()
        assert (await _job(store, "cron-oldrun")).status == JobStatus.DONE
        assert len(capture["sent"]) == 1
    finally:
        await store.close()


async def test_negative_control_the_old_rules(tmp_path):
    """The old purge (by created_at) erased the fresh failure; the old
    recovery (list_all, LIMIT 500 newest first) never saw the old job."""
    store = await _store(tmp_path)
    try:
        now = datetime.now(UTC)
        await _insert(store, job_id="cron-oldfail", timing="daily at 9am", next_run=now.isoformat(),
                      status=JobStatus.FAILED, created_at=(now - timedelta(days=30)).isoformat())
        cur = await store._db.execute(  # noqa: SLF001 — the old purge statement
            "DELETE FROM cron_jobs WHERE status IN ('done','failed','cancelled') AND created_at < ?",
            ((now - timedelta(days=14)).isoformat(),))
        assert cur.rowcount == 1
        await _insert(store, job_id="cron-oldrun", timing="x", next_run=now.isoformat(), status=JobStatus.RUNNING,
                      created_at=(now - timedelta(days=60)).isoformat())
        for i in range(501):
            await _insert(store, job_id=f"cron-n{i}", timing="5m", next_run=now.isoformat(), status=JobStatus.DONE,
                          created_at=(now - timedelta(seconds=i)).isoformat())
        assert "cron-oldrun" not in {j.job_id for j in await store.list_all()}  # limit 500
        assert "cron-oldrun" in {j.job_id for j in await store.list_running()}  # the fixed read
    finally:
        await store.close()


# ── SCH-E12 / S07: the state machine ────────────────────────────────────


_STATES = [s.value for s in JobStatus]
_FINISHED = ("done", "failed", "cancelled", "undeliverable", "missed", "delivery_unknown")
#: action -> outcome per status. Every refusal names why; none reads "not found".
_MATRIX = {
    "edit": {"pending": "rescheduled", "running": "edit_pending", "delivering": "not_editable",
             **{s: "not_editable" for s in _FINISHED}},
    "cancel": {"pending": "cancelled", "running": "cancel_pending", "delivering": "cancelled",
               **{s: "not_cancellable" for s in _FINISHED}},
    "restore": {"pending": "not_finished", "running": "not_finished", "delivering": "not_finished",
                **{s: "restored" for s in _FINISHED}},
}


def test_the_matrix_covers_every_status() -> None:
    """A status added to JobStatus needs its row here (and in the page's map)."""
    assert all(set(row) == set(_STATES) for row in _MATRIX.values())


@pytest.mark.parametrize("action", sorted(_MATRIX))
@pytest.mark.parametrize("status", _STATES)
async def test_state_by_action_matrix(tmp_path, status, action):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph())
        await _insert(store, job_id="cron-m", timing="2h", next_run=_ago(seconds=-3600),
                      status=JobStatus(status))
        if action == "edit":
            out = await sched.reschedule("cron-m", timing="3h")
        elif action == "cancel":
            out = await sched.cancel("cron-m")
        else:
            out = await sched.restore("cron-m", timing="4h")
        assert out["status"] == _MATRIX[action][status], (status, action, out)
        # The page offers exactly the legal actions (the server's list).
        from kazma_ui.scheduled_api import _cron_actions

        legal = {"edit": "edit", "cancel": "cancel", "restore": "restore"}[action]
        offered = legal in _cron_actions(status)
        succeeded = out["status"] in ("rescheduled", "edit_pending", "cancelled", "cancel_pending", "restored")
        assert offered == succeeded, (status, action)
    finally:
        await store.close()


async def test_a_stale_version_is_a_conflict(tmp_path):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _ReplyGraph())
        await _insert(store, job_id="cron-v", timing="2h", next_run=_ago(seconds=-3600))
        assert (await sched.reschedule("cron-v", prompt="first", expected_version=1))["status"] == "rescheduled"
        assert (await sched.reschedule("cron-v", prompt="stale", expected_version=1))["status"] == "conflict"
        assert (await sched.cancel("cron-v", expected_version=1))["status"] == "conflict"
        assert (await _job(store, "cron-v")).prompt == "first"
    finally:
        await store.close()


async def test_edit_and_cancel_during_a_blocked_fire_are_applied_after_it(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        graph = _BlockingGraph()
        sched = CronScheduler(store=store, graph_builder=lambda: graph, poll_interval=0.05)
        await _insert(store, job_id="cron-race", timing="2026-10-14T09:00", next_run=_ago(seconds=5))
        await sched.start()
        await asyncio.wait_for(graph.entered.wait(), 10)
        edit = await sched.reschedule("cron-race", timing="2h")
        assert edit["status"] == "edit_pending"
        graph.release.set()
        for _ in range(100):
            if not sched.active_fire_count():
                break
            await asyncio.sleep(0.05)
        await sched.stop()
        job = await _job(store, "cron-race")
        # The accepted 2h edit runs: the job is pending at its new time.
        assert job.status == JobStatus.PENDING and job.timing == "2h"
        assert datetime.fromisoformat(job.next_run) > datetime.now(UTC) + timedelta(minutes=100)

        graph2 = _BlockingGraph()
        sched2 = CronScheduler(store=store, graph_builder=lambda: graph2, poll_interval=0.05)
        await store._db.execute("UPDATE cron_jobs SET next_run = ? WHERE job_id = 'cron-race'",  # noqa: SLF001
                                (_ago(seconds=5),))
        await sched2.start()
        await asyncio.wait_for(graph2.entered.wait(), 10)
        assert (await sched2.cancel("cron-race"))["status"] == "cancel_pending"
        graph2.release.set()
        for _ in range(100):
            if not sched2.active_fire_count():
                break
            await asyncio.sleep(0.05)
        await sched2.stop()
        assert (await _job(store, "cron-race")).status == JobStatus.CANCELLED
    finally:
        await store.close()


async def test_negative_control_the_old_update_forced_pending(tmp_path):
    """The old update_job set status='pending' with any new time: an edit of
    a cancelled job brought it back. The matrix above refuses it."""
    store = await _store(tmp_path)
    try:
        await _insert(store, job_id="cron-c", timing="2h", next_run=_ago(seconds=-60), status=JobStatus.CANCELLED)
        await store._db.execute(  # noqa: SLF001 — the old statement
            "UPDATE cron_jobs SET timing = '5m', next_run = ?, status = 'pending' WHERE job_id = 'cron-c'",
            (_ago(seconds=-300),))
        assert (await _job(store, "cron-c")).status == JobStatus.PENDING
    finally:
        await store.close()
