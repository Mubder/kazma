"""No crash loses a scheduled job's alert: the alert outbox (Scheduled soak, 2026-10-15).

The Stage 5 soak harness found two windows in which an alert could be lost,
each a crash between two steps (``SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md``):

1. a message that ended undeliverable (or unknown) was marked "alerted"
   BEFORE its alert was raised -- a crash between the two dropped it;
2. a failed fire's alert (``cron.job_failure`` / ``cron.job_failed``) was
   raised AFTER ``finish_fire`` committed -- a crash between the two dropped
   it.

Now every such alert is a row of ``cron_alerts``, written in the transaction
of the state change that needs it, raised after the commit and marked sent
only once ``ops_alerts.alert`` returned. A crash after the commit loses
nothing (the next start raises it); a crash after the alert and before its
mark raises it once more -- the only duplicate, and only on a crash.

Each gap is driven through a REAL child process killed (``os._exit``) at the
window, then a scheduler restarted on the same cron.db: the alert is raised.
The negative controls run the old order in the same harness and lose it. The
child runs this worktree's code (started without ``-I``; it inherits
``PYTHONPATH``). In-process tests cover the drain's own rules, and the class
gates hold every alert of the cron engine to the outbox.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import logging
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore
from kazma_core.cron.store import FireResult

REPO = Path(__file__).resolve().parents[1]

#: The engine as it was until 2026-10-15, for the negative controls: no row
#: in the state change's transaction; the delivery alert marked, then
#: raised; the fire alert raised after ``finish_fire`` committed. Run in the
#: child and in the parent from this one text.
_OLD_ORDER = r'''
import asyncio


async def no_alert_row(self, db, **kwargs):
    """The store before the outbox: a state change queued no alert."""
    return None


async def old_alert_failed_deliveries(self, *args, **kwargs):
    """CronScheduler._alert_failed_deliveries until 2026-10-15: mark, then raise."""
    from kazma_core.observability.ops_alerts import alert

    db = self._store._conn()
    rows = await db.execute_fetchall(
        "SELECT * FROM cron_deliveries WHERE state IN ('undeliverable', 'unknown') AND alerted = 0 ORDER BY id")
    for row in rows:
        cur = await db.execute("UPDATE cron_deliveries SET alerted = 1 WHERE id = ? AND alerted = 0", (row["id"],))
        if not cur.rowcount:
            continue
        between_mark_and_alert()
        key = "cron.delivery_unknown" if row["state"] == "unknown" else "cron.delivery_failed"
        await asyncio.to_thread(alert, key, f"A scheduled message for job {row['job_id']} was not delivered")
    return 0


async def old_alert_on_failure(job_id, result, done):
    """CronScheduler._alert_on_failure until 2026-10-15: raised after the fire's commit."""
    from kazma_core.observability.ops_alerts import alert

    if result.success:
        return
    if done.get("budget_exhausted"):
        await asyncio.to_thread(alert, "cron.job_failed", f"Scheduled job {job_id} failed and stopped")
    elif done.get("failures") == 1 or done.get("status") == "failed":
        await asyncio.to_thread(alert, "cron.job_failure", f"Scheduled job {job_id} failed ({result.outcome})")
'''

_CHILD = textwrap.dedent(r"""
    import asyncio, importlib, os, sys
    db, scenario, mode, point, log = sys.argv[1:6]
    from kazma_core.cron import store as store_mod
    from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore

    def die():
        sys.stdout.flush()
        os._exit(17)

    def record_alert(key, title, detail="", **kw):
        with open(log, "a", encoding="utf-8") as f:
            f.write(key + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True

    importlib.import_module("kazma_core.observability.ops_alerts").alert = record_alert
    sm = importlib.import_module("kazma_core.tools.send_message")

    async def refuse(target, text, **kw):
        return "Error: the platform did not take the message"

    sm._message_backends["telegram"] = refuse
    store_mod.DELIVERY_MAX_ATTEMPTS = 1  # the first refusal ends the message undeliverable

    class Graph:
        async def ainvoke(self, state, config=None):
            raise RuntimeError("provider down: no usable API key")

    def die_after(name, at):
        real = getattr(SQLiteCronStore, name)

        async def wrapped(self, *a, **k):
            result = await real(self, *a, **k)
            if point == at:
                die()  # the state change committed; nothing after it ran
            return result

        setattr(SQLiteCronStore, name, wrapped)

    die_after({"fire": "finish_fire", "delivery": "finish_delivery",
               "unknown": "recover_deliveries"}[scenario], "after_state")
    real_mark = SQLiteCronStore.mark_alert_sent

    async def mark(self, *a, **k):
        if point == "before_mark":
            die()  # ops_alerts.alert returned (the log has it); the mark never commits
        return await real_mark(self, *a, **k)

    SQLiteCronStore.mark_alert_sent = mark

    if mode == "old":
        def between_mark_and_alert():
            if point == "after_mark":
                die()

        ns = {"between_mark_and_alert": between_mark_and_alert}
        exec(OLD_ORDER, ns)
        SQLiteCronStore._enqueue_alert = ns["no_alert_row"]
        CronScheduler._drain_alerts = ns["old_alert_failed_deliveries"]
        committed = SQLiteCronStore.finish_fire

        async def finish_then_alert(self, job_id, **k):
            done = await committed(self, job_id, **k)
            if done is not None:
                await ns["old_alert_on_failure"](job_id, k["result"], done)
            return done

        SQLiteCronStore.finish_fire = finish_then_alert

    async def main():
        store = SQLiteCronStore(db_path=db)
        await store.init()
        sched = CronScheduler(store=store, graph_builder=lambda: Graph())
        if scenario == "unknown":
            await sched.start()  # boot recovery finds the send a dead process claimed
            await sched.stop()
        else:
            await sched._execute(await store.get("cron-alert"))
        await store.close()

    asyncio.run(main())
    os._exit(0)
""")

#: scenario -> the alert its state change needs.
_KEY = {"fire": "cron.job_failure", "delivery": "cron.delivery_failed", "unknown": "cron.delivery_unknown"}


@pytest.fixture
def capture(monkeypatch):
    """Alert keys raised in this process; the platform refuses every message."""
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    raised: list[str] = []

    async def refuse(target_id, text, **kw):
        return "Error: the platform did not take the message"

    def alert(key, title, detail="", **kw):
        raised.append(key)
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", refuse)
    monkeypatch.setattr(ops, "alert", alert)
    return raised


class _NoGraph:
    async def ainvoke(self, state, config=None):
        raise AssertionError("no fire is due after the restart")


def _never(*_a: Any) -> None:
    return None


async def _seed(db: Path, scenario: str) -> None:
    store = SQLiteCronStore(db_path=str(db))
    await store.init()
    due = (datetime.now(UTC) - timedelta(minutes=2)).replace(microsecond=0).isoformat()
    task = scenario == "fire"
    await store.insert(ScheduledJob(
        job_id="cron-alert", timing="daily at 9am" if task else "2026-10-14T09:00",
        prompt="check the deploy" if task else "drink water", platform="telegram", thread_id="t",
        next_run=due, kind="task" if task else "reminder", delivery_target="telegram:42"))
    if scenario == "unknown":
        # A send a process had claimed when it died.
        assert await store.claim_job("cron-alert", owner="dead")
        done = await store.finish_fire("cron-alert", owner="dead", advance=_never, result=FireResult(
            success=True, outcome="delivering", body="drink water", target="telegram:42"))
        assert await store.claim_delivery(owner="dead", delivery_id=done["delivery_id"])
    await store.close()


def _run_child(tmp_path: Path, db: Path, scenario: str, mode: str, point: str, log: Path) -> int:
    script = tmp_path / "child.py"
    script.write_text(f"OLD_ORDER = {_OLD_ORDER!r}\n{_CHILD}", encoding="utf-8")
    env = dict(os.environ)  # PYTHONPATH -> this worktree's packages
    proc = subprocess.run([sys.executable, str(script), str(db), scenario, mode, point, str(log)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 17:
        print(proc.stdout, proc.stderr)
    return proc.returncode


def _old_order(monkeypatch) -> None:
    """The parent's restart runs the old engine too (its drain; no rows)."""
    ns: dict[str, Any] = {"between_mark_and_alert": lambda: None}
    exec(_OLD_ORDER, ns)  # noqa: S102 -- the negative control's own text, above
    monkeypatch.setattr(SQLiteCronStore, "_enqueue_alert", ns["no_alert_row"])
    monkeypatch.setattr(CronScheduler, "_drain_alerts", ns["old_alert_failed_deliveries"])


async def _restart(db: Path, *, passes: int = 3) -> SQLiteCronStore:
    """A scheduler started on the same cron.db, run until its outbox is empty."""
    store = SQLiteCronStore(db_path=str(db))
    await store.init()
    sched = CronScheduler(store=store, graph_builder=lambda: _NoGraph(), poll_interval=0.05)
    await sched.start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while not (sched._poll_count >= passes and await store.pending_alert_count() == 0):
        if loop.time() > deadline:
            break
        await asyncio.sleep(0.02)
    await sched.stop()
    return store


def _child_alerts(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# ── each gap, a real process killed in its window ───────────────────────


@pytest.mark.parametrize("point", ["after_state", "before_mark"])
@pytest.mark.parametrize("scenario", list(_KEY))
async def test_a_process_killed_in_the_window_still_raises_the_alert(tmp_path, capture, scenario, point):
    """``after_state``: killed right after the change that needs the alert
    committed (a failed fire's ``finish_fire``; a message's
    ``finish_delivery`` ending it undeliverable; boot recovery ending a send
    ``unknown``), before any alert. ``before_mark``: killed after
    ``ops_alerts.alert`` returned, before its row was marked sent -- the one
    window that raises it twice."""
    db, log = tmp_path / "cron.db", tmp_path / "alerts.log"
    await _seed(db, scenario)
    assert _run_child(tmp_path, db, scenario, "new", point, log) == 17, f"the child did not die at {point}"
    key = _KEY[scenario]
    assert _child_alerts(log) == ([key] if point == "before_mark" else [])
    store = await _restart(db)
    try:
        assert capture == [key]  # raised after the restart, once
        assert await store.pending_alert_count() == 0
        job = await store.get("cron-alert")
        assert job is not None and job.last_outcome  # the reason is on the job as before
        if scenario == "fire":
            assert job.status == JobStatus.PENDING and job.failure_count == 1
        else:
            assert job.status == (JobStatus.DELIVERY_UNKNOWN if scenario == "unknown" else JobStatus.UNDELIVERABLE)
    finally:
        await store.close()
    # Marked now: a further start raises nothing.
    store = await _restart(db, passes=2)
    try:
        assert capture == [key]
    finally:
        await store.close()


@pytest.mark.parametrize(("scenario", "point"), [("fire", "after_state"), ("delivery", "after_mark"),
                                                 ("unknown", "after_mark")])
async def test_negative_control_the_old_order_loses_the_alert(tmp_path, capture, monkeypatch, scenario, point):
    """The same kill against the old order: the fire's alert raised after its
    commit, a message's alert marked before it was raised. Nothing raises it,
    in the child or after the restart -- the assertion above fails."""
    db, log = tmp_path / "cron.db", tmp_path / "alerts.log"
    await _seed(db, scenario)
    assert _run_child(tmp_path, db, scenario, "old", point, log) == 17, f"the child did not die at {point}"
    _old_order(monkeypatch)
    store = await _restart(db)
    try:
        assert _child_alerts(log) == [] and capture == []  # lost
        job = await store.get("cron-alert")
        assert job is not None and job.status != JobStatus.RUNNING  # only the alert is gone
    finally:
        await store.close()


# ── the drain's own rules, in process ───────────────────────────────────


class _Clock:
    """A clock the test moves (the engine's seam, kazma_core.cron.clock)."""

    def __init__(self) -> None:
        self.t = float(int(time.time()))

    def time(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


async def _store(tmp_path: Path, clock: Any = None) -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"), clock=clock)
    await store.init()
    return store


async def _queue_failure(store: SQLiteCronStore, job_id: str, *, timing: str = "2026-10-14T09:00",
                         advance: Any = _never) -> None:
    """A fire that failed: its alert is queued in ``finish_fire``'s transaction."""
    await store.insert(ScheduledJob(job_id=job_id, timing=timing, prompt="p", platform="telegram", thread_id="t",
                                    next_run=store.clock.now().isoformat(), delivery_target="telegram:42"))
    assert await store.claim_job(job_id, owner="o")
    done = await store.finish_fire(job_id, owner="o", advance=advance,
                                   result=FireResult(success=False, outcome="error", result_text="boom"))
    assert done is not None


async def _alert_rows(store: SQLiteCronStore) -> list[dict[str, Any]]:
    return [dict(r) for r in await store._conn().execute_fetchall("SELECT * FROM cron_alerts ORDER BY id")]  # noqa: SLF001


async def test_the_alert_commits_with_the_failure_and_is_raised_once(tmp_path, capture):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store, graph_builder=lambda: _NoGraph())
        await _queue_failure(store, "cron-q1")
        rows = await _alert_rows(store)
        assert [(r["ops_key"], r["job_id"], r["sent_at"]) for r in rows] == [("cron.job_failure", "cron-q1", None)]
        assert rows[0]["dedupe"].startswith("fire:")
        assert capture == []  # queued, not yet raised
        assert await sched._drain_alerts() == 1 and capture == ["cron.job_failure"]
        assert await sched._drain_alerts() == 0 and capture == ["cron.job_failure"]
        assert (await _alert_rows(store))[0]["sent_at"] is not None
    finally:
        await store.close()


async def test_an_alert_that_cannot_be_raised_stays_queued_with_a_warning(tmp_path, monkeypatch, caplog):
    clock = _Clock()
    store = await _store(tmp_path, clock)
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    raised: list[str] = []

    def broken(key, title, detail="", **kw):
        raise RuntimeError("the alert channel is down")

    try:
        monkeypatch.setattr(ops, "alert", broken)
        sched = CronScheduler(store=store)
        await _queue_failure(store, "cron-w1")
        with caplog.at_level(logging.WARNING, logger="kazma_core.cron.scheduler"):
            assert await sched._drain_alerts() == 0
        assert any(r.levelno == logging.WARNING and "could not be raised" in r.getMessage()
                   and "cron-w1" in r.getMessage() for r in caplog.records)
        row = (await _alert_rows(store))[0]
        assert row["sent_at"] is None and row["attempts"] == 1 and "the alert channel is down" in row["last_error"]
        assert sched.alerts_pending == 1 and sched.liveness()["alerts_pending"] == 1
        assert "kazma_cron_alerts_pending 1" in await sched.metrics()
        # Within its backoff it waits; after it, it is raised once.
        monkeypatch.setattr(ops, "alert", lambda key, title, detail="", **kw: raised.append(key) or True)
        assert await sched._drain_alerts() == 0 and raised == []
        clock.t += 31
        assert await sched._drain_alerts() == 1 and raised == ["cron.job_failure"]
        assert await sched._drain_alerts() == 0 and raised == ["cron.job_failure"]
        assert "kazma_cron_alerts_pending 0" in await sched.metrics()
    finally:
        await store.close()


async def _flaky_mark(store: SQLiteCronStore, monkeypatch) -> None:
    real = store.mark_alert_sent
    calls = {"n": 0}

    async def flaky(alert_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await real(alert_id, **kw)

    monkeypatch.setattr(store, "mark_alert_sent", flaky)


async def test_a_mark_the_database_refused_is_made_later_never_raised_again(tmp_path, capture, monkeypatch):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store)
        await _queue_failure(store, "cron-k1")
        await _flaky_mark(store, monkeypatch)
        await sched._drain_alerts()  # raised; the mark refused (a held lock)
        assert capture == ["cron.job_failure"] and await store.pending_alert_count() == 1
        await sched._drain_alerts()  # marked; not raised again
        assert capture == ["cron.job_failure"] and await store.pending_alert_count() == 0
    finally:
        await store.close()


async def test_negative_control_without_the_memory_of_a_raised_row_it_is_raised_twice(tmp_path, capture,
                                                                                       monkeypatch):
    store = await _store(tmp_path)
    try:
        sched = CronScheduler(store=store)
        await _queue_failure(store, "cron-k2")
        await _flaky_mark(store, monkeypatch)
        await sched._drain_alerts()
        sched._alerts_raised.clear()  # noqa: SLF001 -- what a drain without it would know
        await sched._drain_alerts()
        assert capture == ["cron.job_failure", "cron.job_failure"]
    finally:
        await store.close()


class _NoLock:
    """A drain with no pass lock (the negative control)."""

    def locked(self) -> bool:
        return False

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.mark.parametrize("locked", [True, False], ids=["one pass at a time", "negative control: no lock"])
async def test_concurrent_passes_raise_each_alert_once(tmp_path, monkeypatch, locked):
    store = await _store(tmp_path)
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    raised: list[str] = []

    def slow(key, title, detail="", **kw):
        time.sleep(0.02)
        raised.append(title)
        return True

    try:
        monkeypatch.setattr(ops, "alert", slow)
        sched = CronScheduler(store=store)
        if not locked:
            sched._alert_lock = _NoLock()  # noqa: SLF001
        for i in range(5):
            await _queue_failure(store, f"cron-c{i}")
        await asyncio.gather(*(sched._drain_alerts() for _ in range(4)))
        if locked:
            assert sorted(raised) == sorted(set(raised)) and len(raised) == 5
        else:
            assert len(raised) > 5  # the same rows raised by several passes
    finally:
        await store.close()


@pytest.mark.parametrize("waits", [True, False], ids=["stop waits for the pass", "negative control: old stop"])
async def test_stop_lets_a_pass_in_progress_mark_what_it_raised(tmp_path, monkeypatch, waits):
    """stop() cancelled the poll task at once: a cancel landing between an
    alert and its mark would raise it again at the next start, with no
    crash. It now waits for the pass (bounded)."""
    store = await _store(tmp_path)
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    raised: list[str] = []

    def blocking(key, title, detail="", **kw):
        raised.append(key)
        entered.set()
        release.wait(5)
        returned.set()
        return True

    try:
        monkeypatch.setattr(ops, "alert", blocking)
        if not waits:
            monkeypatch.setattr(CronScheduler, "_no_alert_pass", lambda self, timeout=10.0: contextlib.nullcontext())
        sched = CronScheduler(store=store, poll_interval=3600)
        await _queue_failure(store, "cron-s1")
        sched._running = True  # noqa: SLF001
        sched._task = asyncio.create_task(sched._drain_alerts())  # noqa: SLF001 -- the poll loop, mid-pass
        assert await asyncio.to_thread(entered.wait, 5)
        stopping = asyncio.create_task(sched.stop())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        # stop() has reached its wait for the pass, or (the old stop) its cancel.
        while not (sched._task.done() or sched._alert_lock._waiters):  # noqa: SLF001
            assert loop.time() < deadline, "stop() never reached the pass"
            await asyncio.sleep(0.01)
        release.set()
        await stopping
        assert await asyncio.to_thread(returned.wait, 5)  # the alert's thread has returned either way
        assert raised == ["cron.job_failure"]
        assert await store.pending_alert_count() == (0 if waits else 1)
    finally:
        release.set()
        await store.close()


async def test_an_older_builds_unalerted_failures_are_queued_at_start(tmp_path, capture):
    """A cron.db an older build left (a message undeliverable, never alerted)
    has its alert queued when the store opens, once."""
    store = await _store(tmp_path)
    await store.insert(ScheduledJob(job_id="cron-o1", timing="2026-10-14T09:00", prompt="p", platform="telegram",
                                    thread_id="t", status=JobStatus.UNDELIVERABLE, delivery_target="telegram:42"))
    await store._conn().execute(  # noqa: SLF001 -- the row as the older build wrote it
        "INSERT INTO cron_deliveries (job_id, fire_key, purpose, target, body, state, created_at, last_error, alerted) "
        "VALUES ('cron-o1', 'cron-o1:x:result', 'result', 'telegram:42', 'hi', 'undeliverable', 0, 'Error: no', 0)")
    await store.close()
    for _ in range(2):
        store = await _store(tmp_path)
        try:
            rows = await _alert_rows(store)
            assert [(r["ops_key"], r["dedupe"]) for r in rows] == [("cron.delivery_failed", "delivery:1")]
            flags = await store._conn().execute_fetchall("SELECT alerted FROM cron_deliveries")  # noqa: SLF001
            assert [r["alerted"] for r in flags] == [1]
            await CronScheduler(store=store)._drain_alerts()
        finally:
            await store.close()
    assert capture == ["cron.delivery_failed"]


async def test_raised_alerts_follow_the_job_retention(tmp_path):
    clock = _Clock()
    store = await _store(tmp_path, clock)
    try:
        await _queue_failure(store, "cron-r1")  # a one-off: failed, finished now
        await _queue_failure(store, "cron-r2")
        await _queue_failure(store, "cron-r3", timing="daily at 9am",  # still active: its next run is ahead
                             advance=lambda *_a: clock.now() + timedelta(days=30))
        rows = {r["job_id"]: r["id"] for r in await _alert_rows(store)}
        assert await store.mark_alert_sent(rows["cron-r1"]) and await store.mark_alert_sent(rows["cron-r3"])
        clock.t += 15 * 86400
        await store.purge_terminal_jobs()
        left = {r["job_id"]: r["sent_at"] for r in await _alert_rows(store)}
        # Raised ones go with the window; one never raised is kept, whatever its age.
        assert left == {"cron-r2": None}
    finally:
        await store.close()


# ── the class gates ─────────────────────────────────────────────────────

#: The cron engine and the Scheduled surfaces that act on its jobs.
_ENGINE = ("kazma-core/kazma_core/cron/", "kazma-ui/kazma_ui/scheduled_api.py",
           "kazma-skills/kazma_skills/native/task_scheduler_cron/")
_OPS_ALERTS = "kazma_core.observability.ops_alerts"
#: Where the engine may name ``ops_alerts.alert`` itself, and why. Every
#: other alert is a row of the outbox, written with the change that needs it.
_DIRECT_ALERT_SITES = {
    ("kazma-core/kazma_core/cron/scheduler.py", "CronScheduler._raise_alert"):
        "the alert outbox's drain: raises one queued row, then marks it sent",
}


def _engine_files() -> list[Path]:
    out: list[Path] = []
    for entry in _ENGINE:
        path = REPO / entry
        out += sorted(path.rglob("*.py")) if entry.endswith("/") else [path]
    return out


def _alert_sites(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of every reference to ``ops_alerts.alert``:
    the name imported from it (any alias), or ``<module alias>.alert``."""
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _OPS_ALERTS:
            names |= {a.asname or a.name for a in node.names if a.name == "alert"}
        elif isinstance(node, ast.ImportFrom) and node.module == "kazma_core.observability":
            modules |= {a.asname or a.name for a in node.names if a.name == "ops_alerts"}
        elif isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == _OPS_ALERTS and a.asname}
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "import_module"
              and node.value.args and isinstance(node.value.args[0], ast.Constant)
              and node.value.args[0].value == _OPS_ALERTS):
            modules |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    sites: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = (*scope, child.name)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id in names:
                sites.append((".".join(scope) or "<module>", child.lineno))
            elif (isinstance(child, ast.Attribute) and child.attr == "alert" and isinstance(child.value, ast.Name)
                  and child.value.id in modules):
                sites.append((".".join(scope) or "<module>", child.lineno))
            visit(child, inner)

    visit(tree, ())
    return sites


def test_every_cron_engine_alert_goes_through_the_outbox():
    found: dict[tuple[str, str], list[int]] = {}
    for path in _engine_files():
        rel = path.relative_to(REPO).as_posix()
        for scope, line in _alert_sites(ast.parse(path.read_text(encoding="utf-8"))):
            found.setdefault((rel, scope), []).append(line)
    stray = {f"{rel}:{lines[0]} {scope}": lines for (rel, scope), lines in found.items()
             if (rel, scope) not in _DIRECT_ALERT_SITES}
    assert not stray, (
        "The cron engine raises an ops alert outside the alert outbox. A crash between its state change and "
        "the alert loses it. Queue it with SQLiteCronStore._enqueue_alert inside the change's transaction, "
        f"or declare the site in _DIRECT_ALERT_SITES with why it has no row: {stray}")
    stale = set(_DIRECT_ALERT_SITES) - set(found)
    assert not stale, f"declared alert sites that no longer exist: {stale}"


def test_the_alert_gate_sees_the_old_direct_calls():
    """Negative control: both old shapes, and the module-alias forms, are flagged."""
    planted = textwrap.dedent('''
        import importlib
        import kazma_core.observability.ops_alerts as oa
        from kazma_core.observability import ops_alerts

        class CronScheduler:
            async def _alert_on_failure(self, job, result, done):
                from kazma_core.observability.ops_alerts import alert
                await asyncio.to_thread(alert, "cron.job_failure", "failed")

            async def _alert_failed_deliveries(self):
                from kazma_core.observability.ops_alerts import alert as raise_alert
                for row in await self._store.unalerted_failures():
                    await self._store.mark_alerted(row["id"])
                    await asyncio.to_thread(raise_alert, "cron.delivery_failed", "lost")

        def other():
            ops_alerts.alert("cron.x", "t")
            oa.alert("cron.y", "t")
            importlib.import_module("kazma_core.observability.ops_alerts")

        def via_import_module():
            mod = importlib.import_module("kazma_core.observability.ops_alerts")
            mod.alert("cron.z", "t")
    ''')
    scopes = [scope for scope, _line in _alert_sites(ast.parse(planted))]
    assert scopes == ["CronScheduler._alert_on_failure", "CronScheduler._alert_failed_deliveries",
                      "other", "other", "via_import_module"], scopes


def _enqueues_outside_a_transaction(tree: ast.AST) -> list[str]:
    """``_enqueue_alert`` calls not inside ``async with self._tx()``, and
    ``cron_alerts`` inserts outside ``_enqueue_alert``."""
    bad: list[str] = []

    def in_tx(stack: list[ast.AST]) -> bool:
        for node in stack:
            if isinstance(node, ast.AsyncWith):
                for item in node.items:
                    expr = item.context_expr
                    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                            and expr.func.attr == "_tx"):
                        return True
        return False

    def visit(node: ast.AST, stack: list[ast.AST], fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "_enqueue_alert" and not in_tx(stack)):
                bad.append(f"{fn}:{child.lineno} _enqueue_alert outside a transaction")
            if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                    and "INTO cron_alerts" in child.value and fn != "_enqueue_alert"):
                bad.append(f"{fn}:{child.lineno} writes cron_alerts outside _enqueue_alert")
            visit(child, [*stack, child], name)

    visit(tree, [], "<module>")
    return bad


def test_an_alert_row_is_written_only_inside_its_changes_transaction():
    store_src = (REPO / "kazma-core/kazma_core/cron/store.py").read_text(encoding="utf-8")
    assert _enqueues_outside_a_transaction(ast.parse(store_src)) == []
    # The changes that need an alert are the ones that queue it.
    tree = ast.parse(store_src)
    callers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
               and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "_enqueue_alert" for n in ast.walk(fn))}
    assert {"finish_fire", "finish_delivery", "recover_deliveries"} <= callers, callers


def test_the_transaction_gate_sees_a_row_written_after_the_commit():
    """Negative control: the alert queued after the change's transaction, or
    by a raw insert elsewhere, is flagged."""
    planted = textwrap.dedent('''
        class Store:
            async def finish_fire(self, job_id):
                async with self._tx() as db:
                    await db.execute("UPDATE cron_jobs SET status = 'failed'")
                await self._enqueue_alert(self._conn(), dedupe="x", job_id=job_id, ops_key="k", title="t")

            async def sneak(self):
                await self._conn().execute("INSERT INTO cron_alerts (dedupe) VALUES ('y')")

            async def _enqueue_alert(self, db, **kw):
                await db.execute("INSERT OR IGNORE INTO cron_alerts (dedupe) VALUES (?)", (kw["dedupe"],))
    ''')
    bad = _enqueues_outside_a_transaction(ast.parse(planted))
    assert len(bad) == 2 and "finish_fire" in bad[0] and "sneak" in bad[1], bad
