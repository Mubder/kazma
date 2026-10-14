"""The cron engine reads one clock, and it can be given one (Scheduled Stage 5).

The Stage 5 qualification soak (``scripts/scheduled_soak.py``) runs the real
scheduler and store through seven simulated days in minutes. Before this
seam, the engine read ``time.time()`` and ``datetime.now(UTC)`` in a dozen
places and slept with ``asyncio.sleep``, so days of fake time could only be
had by patching those. Now every time the engine records or compares, and
the poll loop's wait, come from a :class:`kazma_core.cron.clock.CronClock`.

Two checks:

* **Behaviour.** A scheduler on a fake clock set months away from the real
  one books a reminder, fires it and delivers it once. The lateness, the
  event times, the job's times and the heartbeat are all in fake time. A
  single read of the real clock would leave the job undue, and it would
  never fire.
* **Gate.** No read of the wall clock, and no ``asyncio.sleep``, is left in
  the engine's modules except the two declared below. A planted read of the
  old shape fails the gate (the negative control).
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from datetime import UTC, datetime
from pathlib import Path

from kazma_core.cron.clock import SYSTEM_CLOCK, CronClock
from kazma_core.cron.scheduler import CronScheduler, JobStatus, SQLiteCronStore

REPO = Path(__file__).resolve().parents[1]
_ENGINE = ("kazma-core/kazma_core/cron/scheduler.py", "kazma-core/kazma_core/cron/store.py")

#: Clock reads the engine keeps on purpose: (file, enclosing scope) -> why.
_DECLARED = {
    ("scheduler.py", "parse_timing"): (
        "a parser kept for callers outside the engine; it reads a caller's own 'now' when given none"),
    ("store.py", "ScheduledJob"): (
        "the default creation time of a row built outside schedule() (an import, a test); schedule() "
        "passes the clock's time"),
}
_CLOCK_CALLS = {("time", "time"), ("time", "monotonic"), ("datetime", "now"), ("datetime", "utcnow"),
                ("asyncio", "sleep")}


class _FakeClock:
    """Fake wall time; ``sleep`` waits until the test moves the clock past it."""

    def __init__(self, start: datetime) -> None:
        self.t = start.timestamp()
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []

    def time(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiters.append((self.t + seconds, fut))
        await fut

    def advance(self, seconds: float) -> None:
        self.t += seconds
        for deadline, fut in list(self.waiters):
            if deadline <= self.t:
                self.waiters.remove((deadline, fut))
                if not fut.done():
                    fut.set_result(None)


async def _until(cond, timeout: float = 10.0) -> None:
    """Wait (real time) until *cond* (sync or async) holds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        held = cond()
        if inspect.isawaitable(held):
            held = await held
        if held:
            return
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def test_the_engine_runs_on_the_clock_it_is_given(tmp_path, monkeypatch):
    sm = importlib.import_module("kazma_core.tools.send_message")
    sent: list[tuple[str, str]] = []

    async def backend(target_id, text, **kw):
        sent.append((target_id, text))
        return f"sent:{target_id}"

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    start = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)  # months away from the real clock
    clock = _FakeClock(start)
    assert isinstance(clock, CronClock)
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"), clock=clock)
    await store.init()
    sched = CronScheduler(store=store, poll_interval=30)  # the store's clock, not given again
    try:
        assert sched._clock is clock
        booked = await sched.schedule("2h", "Water the plants", platform="telegram", delivery_target="telegram:42",
                                      kind="reminder", tenant_id="default", zone=UTC, zone_name="UTC")
        assert booked["next_run"] == "2027-03-01T11:00:00+00:00"
        job = await store.get(booked["job_id"])
        assert job is not None and job.created_at.startswith("2027-03-01T09:00:00")
        await sched.start()
        assert sched.started_at == clock.t
        await _until(lambda: len(clock.waiters) == 1)  # the loop polled once and sleeps on the clock
        assert sent == []  # not due yet: nothing reads the real clock's 2026
        clock.advance(2 * 3600 + 10)  # 11:00:10, ten seconds after it was due

        async def delivered() -> bool:
            row = await store.get(booked["job_id"])
            return row is not None and row.status == JobStatus.DONE

        await _until(delivered)
        assert sent == [("telegram:42", "⏰ Reminder\n\nWater the plants")]
        done = await store.get(booked["job_id"])
        assert done is not None and done.last_fire_at == "2027-03-01T11:00:10+00:00"
        assert done.finished_at == "2027-03-01T11:00:10+00:00"
        assert await store.lateness(since=start.timestamp()) == [10.0]
        events = await store.list_events(booked["job_id"])
        assert events and all(start.timestamp() <= e["at"] <= clock.t for e in events)
        assert sched.liveness()["last_poll_age_s"] == 0.0
        clock.t += 5  # time passes, no poll yet
        assert sched.liveness()["last_poll_age_s"] == 5.0
    finally:
        await sched.stop()
        await store.close()


def test_a_store_and_scheduler_default_to_the_real_clock(tmp_path):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    assert store.clock is SYSTEM_CLOCK
    assert CronScheduler(store=store)._clock is SYSTEM_CLOCK
    other = _FakeClock(datetime(2027, 1, 1, tzinfo=UTC))
    assert CronScheduler(store=store, clock=other)._clock is other


def clock_reads(source: str) -> list[tuple[str, int, str]]:
    """``(enclosing scope, line, call)`` of every wall-clock read or
    ``asyncio.sleep`` in *source*. The scope is the innermost function or
    class, or ``<module>``."""
    out: list[tuple[str, int, str]] = []

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and isinstance(child.func.value, ast.Name) \
                    and (child.func.value.id, child.func.attr) in _CLOCK_CALLS:
                out.append((scope, child.lineno, f"{child.func.value.id}.{child.func.attr}"))
            walk(child, inner)

    walk(ast.parse(source), "<module>")
    return out


def test_the_engine_reads_no_clock_but_its_own():
    found: set[tuple[str, str]] = set()
    offenders: list[str] = []
    for rel in _ENGINE:
        name = Path(rel).name
        for scope, line, call in clock_reads((REPO / rel).read_text(encoding="utf-8")):
            if (name, scope) in _DECLARED:
                found.add((name, scope))
                continue
            offenders.append(f"{rel}:{line} {scope}: {call}()")
    assert not offenders, (
        "The cron engine reads the wall clock directly. Read self._clock / self.clock (time(), now(), "
        "sleep()) so a fake clock drives it, or declare the read in _DECLARED with why:\n  "
        + "\n  ".join(offenders))
    assert found == set(_DECLARED), f"stale declarations: {set(_DECLARED) - found}"


def test_negative_control_the_gate_sees_the_old_reads():
    """The poll loop's old heartbeat and wait, and the store's old lease."""
    old = (
        "import asyncio, time\n"
        "from datetime import UTC, datetime\n"
        "class CronScheduler:\n"
        "    async def _loop(self):\n"
        "        self.last_poll_at = time.time()\n"
        "        await asyncio.sleep(self._poll_interval)\n"
        "class SQLiteCronStore:\n"
        "    async def finish_fire(self, now=None):\n"
        "        now = now or datetime.now(UTC)\n"
    )
    assert [(s, c) for s, _, c in clock_reads(old)] == [
        ("_loop", "time.time"), ("_loop", "asyncio.sleep"), ("finish_fire", "datetime.now")]
