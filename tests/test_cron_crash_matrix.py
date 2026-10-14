"""A real process killed at each step of a fire (Scheduled audit SCH-E04).

The audit's witnesses simulated death with a BaseException; this kills a real
child process (``os._exit``) right after each committed step of a fire --
the claim, inside the agent turn, the fire's finish (outcome + outbox + next
occurrence in one transaction), the outbox claim, the platform's acceptance,
the acknowledgement -- then restarts a scheduler on the same cron.db and
checks the end state, for a one-off reminder and a daily agent task:

* exactly ONE delivery, the job finished (a one-off DONE; a daily job
  pending at its NEXT occurrence), no alert; or
* where the process died between claiming a send and recording it, ONE
  honest terminal state -- "delivery unknown", never resent -- with exactly
  one alert (``cron.delivery_unknown``).

It used to end FAILED ("stale RUNNING") with nothing sent and nobody told, or,
for a crash inside the three-statement daily finalize, a recurring job DONE
for good. The child runs this worktree's code: it is started WITHOUT ``-I``
and inherits ``PYTHONPATH`` from the test process.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

_CHILD = textwrap.dedent(r"""
    import asyncio, importlib, os, sys
    db, point, log = sys.argv[1], sys.argv[2], sys.argv[3]
    from kazma_core.cron import store as store_mod
    from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore

    def die():
        sys.stdout.flush()
        os._exit(17)

    sm = importlib.import_module("kazma_core.tools.send_message")

    async def backend(target, text, **kw):
        if point == "before_send":
            die()
        with open(log, "a", encoding="utf-8") as f:
            f.write(text.replace("\n", " ") + "\n")
            f.flush()
            os.fsync(f.fileno())
        if point == "after_send":
            die()
        return "sent:" + target

    sm._message_backends["telegram"] = backend

    class Graph:
        async def ainvoke(self, state, config=None):
            if point == "mid_turn":
                die()
            msgs = list(state.get("messages", []))
            msgs.append({"role": "assistant", "content": "answer from the task"})
            return {"messages": msgs}

    def after(name, at):
        real = getattr(SQLiteCronStore, name)

        async def wrapped(self, *a, **k):
            result = await real(self, *a, **k)
            if point == at:
                die()
            return result

        setattr(SQLiteCronStore, name, wrapped)

    after("claim_job", "after_claim")
    after("finish_fire", "after_finish")
    after("claim_delivery", "before_send_claimed")
    after("finish_delivery", "after_ack")

    async def main():
        store = SQLiteCronStore(db_path=db)
        await store.init()
        sched = CronScheduler(store=store, graph_builder=lambda: Graph())
        await sched._execute(await store.get("cron-crash"))
        await store.close()

    asyncio.run(main())
    os._exit(0)
""")

#: point -> (deliveries in all, alert keys, the job ends as expected?)
_EXPECT = {
    "after_claim": (1, []),
    "mid_turn": (1, []),
    "after_finish": (1, []),
    "before_send_claimed": (0, ["cron.delivery_unknown"]),
    "after_send": (1, ["cron.delivery_unknown"]),
    "after_ack": (1, []),
}


@pytest.fixture
def capture(monkeypatch):
    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    state: dict[str, Any] = {"sent": [], "alerts": []}

    async def backend(target_id, text, **kw):
        state["sent"].append(text)
        return f"sent:{target_id}"

    def alert(key, title, detail="", **kw):
        state["alerts"].append(key)
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", alert)
    return state


class _Graph:
    async def ainvoke(self, state, config=None):
        return {"messages": [*state.get("messages", []), {"role": "assistant", "content": "answer from the task"}]}


async def _seed(db: Path, *, daily: bool) -> datetime:
    store = SQLiteCronStore(db_path=str(db))
    await store.init()
    due = (datetime.now(UTC) - timedelta(minutes=2)).replace(microsecond=0)
    await store.insert(ScheduledJob(
        job_id="cron-crash", timing="daily at 9am" if daily else "2026-10-14T09:00",
        prompt="drink water", platform="telegram", thread_id="t", next_run=due.isoformat(),
        kind="task" if daily else "reminder", delivery_target="telegram:42"))
    await store.close()
    return due


def _run_child(tmp_path: Path, db: Path, point: str, log: Path) -> int:
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    env = dict(os.environ)  # PYTHONPATH -> this worktree's packages
    proc = subprocess.run([sys.executable, str(script), str(db), point, str(log)],
                          env=env, capture_output=True, text=True, timeout=120)
    return proc.returncode


async def _restart(db: Path) -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(db))
    await store.init()
    sched = CronScheduler(store=store, graph_builder=lambda: _Graph(), poll_interval=0.05)
    await sched.start()
    for _ in range(80):
        job = await store.get("cron-crash")
        if job and job.status != JobStatus.RUNNING and not await store.pending_delivery_count():
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    await sched.stop()
    return store


@pytest.mark.parametrize("daily", [False, True], ids=["one-off reminder", "daily task"])
@pytest.mark.parametrize("point", list(_EXPECT))
async def test_a_killed_fire_ends_with_one_delivery_or_one_honest_state(tmp_path, capture, point, daily):
    if point == "mid_turn" and not daily:
        pytest.skip("a reminder runs no turn")
    db, log = tmp_path / "cron.db", tmp_path / "sent.log"
    due = await _seed(db, daily=daily)
    code = _run_child(tmp_path, db, point, log)
    assert code == 17, f"the child did not die at {point} (exit {code})"
    store = await _restart(db)
    try:
        sent_by_child = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        deliveries, alerts = _EXPECT[point]
        assert len(sent_by_child) + len(capture["sent"]) == deliveries, (sent_by_child, capture["sent"])
        assert capture["alerts"] == alerts
        job = await store.get("cron-crash")
        if daily:
            # The recurrence advanced past the occurrence that fired: the next
            # 9am after it, never DONE, never the same occurrence again.
            assert job.status in (JobStatus.PENDING,), job.status
            nxt = datetime.fromisoformat(job.next_run)
            assert nxt > due and nxt > datetime.now(UTC) - timedelta(minutes=1)
            assert nxt.astimezone(UTC).hour == 9 and nxt - due < timedelta(days=1, hours=1)
        elif alerts:
            assert job.status == JobStatus.DELIVERY_UNKNOWN
        else:
            assert job.status == JobStatus.DONE
    finally:
        await store.close()


async def test_negative_control_the_old_recovery_marked_failed(tmp_path, capture):
    """Under the old rule a fire interrupted after its claim ended FAILED with
    nothing sent; the matrix above asserts one delivery instead."""
    db, log = tmp_path / "cron.db", tmp_path / "sent.log"
    await _seed(db, daily=False)
    assert _run_child(tmp_path, db, "after_claim", log) == 17
    store = SQLiteCronStore(db_path=str(db))
    await store.init()
    try:
        await store._db.execute(  # noqa: SLF001 — what _recover_stale_running did
            "UPDATE cron_jobs SET status = 'failed', last_result = 'Marked failed on scheduler restart "
            "(stale RUNNING)' WHERE status = 'running'")
        job = await store.get("cron-crash")
        assert job.status == JobStatus.FAILED and not log.exists() and capture["sent"] == []
    finally:
        await store.close()
