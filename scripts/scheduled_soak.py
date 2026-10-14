#!/usr/bin/env python3
"""Seven simulated days of the cron scheduler on a fake clock: the Scheduled qualification soak.

It qualifies the Stage 5 exit gate of
``docs/plans/SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md``. This file is the
harness for that run, not its result. It drives the REAL ``CronScheduler`` on
a PRIVATE ``SQLiteCronStore`` through ``--days`` simulated days (default 7)
in minutes of wall time. The store sits in a temporary data directory unless
``--data-dir`` names one. The run never touches an install's data, a database
server, a chat platform or the network.

The fake clock is the engine's own seam (``kazma_core.cron.clock``): due
checks, leases, backoff, events and the poll loop's wait all read it, so
nothing patches ``time`` or ``datetime``. The store is the real one, plus
one observer method: when the outbox hands out a message it notes which
delivery the next send serves.

What it runs:

* **Jobs.** Thousands of them (``--jobs``), booked the way the agent tool and
  the Scheduled page book them (``CronScheduler.schedule``):
  - reminders (no model) and agent tasks (a scripted graph);
  - one-offs at a UTC ``Z`` time, at an offset, in the operator's zone, or
    relative to the booking;
  - daily, weekday and weekly series, in friendly form or as an RRULE, some
    ending by ``count``/``until`` (COUNT/UNTIL);
  - in the operator's zone (America/New_York, which falls back on
    2026-11-01 inside the default window), in Asia/Kuwait and in UTC;
  - some with their own catch-up window.
  A fifth are booked during the run. Some are edited or cancelled mid-run
  through the versioned actions, and a stale version must be refused.
* **Provider failures.** A task's turn raises, fails, or stops at an
  approval nobody answers. In outage windows no graph can be built. A few
  jobs always fail, so their failure budget runs out.
* **Delivery failures.** The platform refuses or raises. Kazma dies
  mid-send, so the outbox must end ``unknown``. A few targets always refuse,
  and a few jobs have no address at all.
* **Database failures.** cron.db's write lock is held from another
  connection past the busy timeout, until a write of the scheduler has
  failed on it: a poll pass, or (armed to start while a turn is in flight)
  that fire's own finish.
* **Restarts.** One every ``--restart-hours``, each on a reopened store.
  Half are graceful (``stop()`` drains). Half are abrupt: everything is
  cancelled, nothing is drained, and a fire dies mid-turn or a send dies
  mid-send. One downtime is longer than the catch-up window
  (``--gap-minutes``), so jobs due in it are missed.
* **A dead poll loop**, once.

How it checks. Ground truth comes from the harness, not the scheduler: it
lists each job's due occurrences from the job's own definition, by its own
wall-time rule, and asks ``timing.py`` only for a second opinion at booking.
It compares that with cron.db's events, the outbox, and what the fake
platform took:

* Every due occurrence ends exactly once:
  - delivered;
  - missed, with exactly one notice;
  - failed, undeliverable or delivery-unknown, with its reason on the job
    and exactly one alert;
  - or cancelled by the harness.
  Nothing is delivered twice, nothing is silently absent, and nothing fires
  at a time it was not due.
* A missed occurrence was due while the scheduler was down for longer than
  its window.
* A series across the clock change fires once per local day, at the
  intended local time.
* p95 fire lateness is at most one poll interval plus the scripted turn
  time. It is measured in fake seconds, on fires that were due while the
  scheduler was up.
* Readiness (``kazma_ui.health.check_cron``) is degraded within one poll
  interval of a dead loop.

The run stops at the first violation. A seed fixes the jobs, the plan and
every injected failure. Only which write a lock hold fails could vary with
wall time; two runs of the defaults gave the same report but for wall time
and memory.

The report (``--report``, JSON) is rewritten whole as the run goes
(``status: running``) and once more at the end (``passed`` or ``failed``).
It holds:

* the fake window;
* jobs and occurrences by kind and outcome, and fires;
* lateness;
* deliveries by outcome;
* alerts by key;
* restarts;
* injected failures by kind;
* the dead-loop detection time;
* every violation;
* wall time and resource peaks.

The ledger (``--ledger``, default beside the report) keeps what was recorded,
so a run can be checked again with :func:`analyse`. Exit 0 when the run is
healthy, else 1.

Usage:
  python scripts/scheduled_soak.py --report scheduled-soak.json
  python scripts/scheduled_soak.py --days 2 --jobs 300 --poll-seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import heapq
import itertools
import json
import logging
import math
import os
import random
import re
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

# The shared report helpers sit beside this file; a runner that does not put
# the script's folder on the path (runpy, a priority wrapper) still finds them.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from soak_report import finished, latency_summary, percentile, process_tree_sample, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

#: The operator's zone the jobs are booked in: it falls back on 2026-11-01.
OPERATOR_ZONE = "America/New_York"
DEFAULT_START = "2026-10-31T12:00:00Z"
#: A fire's outcome when it handed its message to the outbox.
SUCCESS = "delivering"
TERMINAL_DELIVERY = frozenset({"delivered", "undeliverable", "unknown", "cancelled"})
_JOB_ID = re.compile(r"cron-[0-9a-f]{12}")
_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_PROGRESS_WALL_S = 60.0

#: The delivery the next send serves: set when the outbox hands one out.
_CLAIMED: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("soak_claimed", default=None)


def _draw(seed: int, *parts: object) -> float:
    """A uniform draw in [0, 1) fixed by the seed and *parts* (order-free)."""
    digest = hashlib.sha256("|".join(map(str, (seed, *parts))).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _iso(dt: datetime) -> str:
    """An instant as cron.db writes it (``norm_iso``: UTC, whole seconds)."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _zone(name: str) -> Any:
    return UTC if name == "UTC" else ZoneInfo(name)


# ── ground truth: the harness's own wall-time rule ───────────────────────


def _wall_candidates(wall: datetime, zone_name: str) -> list[datetime]:
    """Every UTC instant whose wall time in *zone_name* is *wall* (0, 1 or 2)."""
    zone = _zone(zone_name)
    day = wall.date()
    offsets = {zone.utcoffset(datetime.combine(day + timedelta(days=k), wall_time(12))) for k in (-1, 1)}
    out = set()
    for off in offsets:
        instant = (wall - off).replace(tzinfo=UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            out.add(instant)
    return sorted(out)


def wall_instant(day: date, hour: int, minute: int, zone_name: str) -> datetime:
    """When a series due at *hour*:*minute* on *day* fires.

    The earlier instant when the clock shows that time twice. When the clock
    skips it, the same wall time shifted forward by the jump.
    """
    wall = datetime.combine(day, wall_time(hour, minute))
    hits = _wall_candidates(wall, zone_name)
    if hits:
        return hits[0]
    before = _zone(zone_name).utcoffset(datetime.combine(day - timedelta(days=1), wall_time(12)))
    return (wall - before).replace(tzinfo=UTC)


def occurrences(spec: dict[str, Any], first: datetime, upto: datetime) -> list[datetime]:
    """The series' due instants in [*first*, *upto*), with its count and until applied."""
    if spec["type"] == "once":
        at = _dt(spec["at"])
        return [at] if first <= at < upto else []
    zone = _zone(spec["zone"])
    days = set(spec.get("days") or range(7))
    until = date.fromisoformat(spec["until"]) if spec.get("until") else None
    count = spec.get("count")
    out: list[datetime] = []
    seen = 0
    day = first.astimezone(zone).date() - timedelta(days=1)
    last = upto.astimezone(zone).date() + timedelta(days=1)
    while day <= last:
        if day.weekday() in days and (until is None or day <= until):
            at = wall_instant(day, spec["h"], spec["m"], spec["zone"])
            if at >= first:
                seen += 1
                if count and seen > count:
                    break
                if at < upto:
                    out.append(at)
        day += timedelta(days=1)
    return out


def first_after(spec: dict[str, Any], after: datetime) -> datetime | None:
    """The first fire strictly after *after*, as a booking or an edit resolves it."""
    if spec["type"] == "once":
        at = _dt(spec["at"])
        return at if at > after else None
    found = occurrences({**spec, "count": None}, after + timedelta(seconds=1), after + timedelta(days=9))
    return found[0] if found else None


def _product_series(text: str, first: datetime, upto: datetime) -> list[datetime]:
    """The product's own series (``timing.next_occurrence``): a second opinion."""
    from kazma_core.cron.timing import next_occurrence, parse_recurrence

    rec = parse_recurrence(text)
    out = [first]
    while rec is not None:
        nxt = next_occurrence(rec, zone=ZoneInfo(OPERATOR_ZONE), after=out[-1], series_start=first)
        if nxt is None or nxt >= upto:
            break
        out.append(nxt)
    return [d for d in out if d < upto]


# ── the fake clock ────────────────────────────────────────────────────────


class FakeClock:
    """Simulated wall time. ``sleep`` parks the caller until the harness
    advances past its deadline; the harness knows who is parked."""

    def __init__(self, start: float) -> None:
        self._now = float(start)
        self._heap: list[tuple[float, int, asyncio.Future[None], asyncio.Task[Any] | None]] = []
        self._seq = itertools.count()
        self._parked: set[asyncio.Task[Any]] = set()

    def time(self) -> float:
        return self._now

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._now, UTC)

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        task = asyncio.current_task()
        heapq.heappush(self._heap, (self._now + seconds, next(self._seq), fut, task))
        if task is not None:
            self._parked.add(task)
        try:
            await fut
        finally:
            if task is not None:
                self._parked.discard(task)
            if not fut.done():
                fut.cancel()

    def parked(self, task: asyncio.Task[Any]) -> bool:
        return task in self._parked

    def next_deadline(self) -> float | None:
        while self._heap and self._heap[0][2].done():
            heapq.heappop(self._heap)
        if not self._heap or not math.isfinite(self._heap[0][0]):
            return None
        return self._heap[0][0]

    def advance_to(self, when: float) -> None:
        self._now = max(self._now, float(when))
        while self._heap and self._heap[0][0] <= self._now:
            _, _, fut, task = heapq.heappop(self._heap)
            if not fut.done():
                # Unparked at once: a woken task is running, not waiting.
                if task is not None:
                    self._parked.discard(task)
                fut.set_result(None)


# ── the fake graph and platform ──────────────────────────────────────────


class _Graph:
    """A scripted agent: answers, raises, fails its turn, or stops at an
    approval, after ``--turn-seconds`` of fake time."""

    def __init__(self, soak: Soak) -> None:
        self.soak = soak
        self.paused: set[str] = set()

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        thread = str(((config or {}).get("configurable") or {}).get("thread_id") or "")
        return await self.soak._turn(thread.removeprefix("cron-"), thread, state, self)

    async def aget_state(self, config: dict[str, Any]) -> Any:
        thread = str(((config or {}).get("configurable") or {}).get("thread_id") or "")
        if thread in self.paused:
            self.paused.discard(thread)
            intr = SimpleNamespace(value={"type": "hitl_approval", "tool": "file_write", "args": {}},
                                   id="soak-interrupt")
            return SimpleNamespace(next=("tool_worker",), tasks=[SimpleNamespace(interrupts=[intr])], values={})
        return SimpleNamespace(next=(), tasks=[], values={})


def _observed_store_class() -> type:
    """The real store, plus one observer: which delivery a send serves."""
    from kazma_core.cron.store import SQLiteCronStore

    class ObservedStore(SQLiteCronStore):
        async def claim_delivery(self, **kwargs: Any) -> dict[str, Any] | None:
            row = await super().claim_delivery(**kwargs)
            if row is not None:
                _CLAIMED.set({k: row[k] for k in ("id", "job_id", "fire_key", "purpose", "due_at", "attempts")})
            return row

    return ObservedStore


class _LogTally(logging.Handler):
    """Counts what the product logged at WARNING and above while the soak ran."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.counts: Counter[str] = Counter()
        #: Writes that failed on a held lock; and of them, fires that failed.
        self.locked = 0
        self.locked_fires = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a tally must never fail a log call
            msg = str(record.msg)
        key = re.sub(r"cron-[0-9a-f]{12}|soak:[\w-]+|\d+(\.\d+)?", "#", msg)[:100]
        self.counts[f"{record.levelname} {record.name}: {key}"] += 1
        text = msg
        if record.exc_info and record.exc_info[1] is not None:
            text += f" {record.exc_info[1]}"
        if "database is locked" in text:
            self.locked += 1
            if "ended with an error" in msg:
                self.locked_fires += 1


# ── the soak ──────────────────────────────────────────────────────────────


class Soak:
    """One run: the clock, the store and scheduler, the plan and the record."""

    def __init__(self, args: argparse.Namespace, data_dir: Path) -> None:
        self.args = args
        self.seed = int(args.seed)
        self.data_dir = data_dir
        self.db_path = data_dir / "cron.db"
        self.t0 = float(int(_dt(args.start).timestamp()))
        self.run_end = self.t0 + float(args.days) * 86400
        self.drain_end = self.run_end + float(args.drain_minutes) * 60
        self.clock = FakeClock(self.t0)
        self.poll = float(args.poll_seconds)
        self.turn_s = float(args.turn_seconds)
        self.graph = _Graph(self)
        self.store: Any = None
        self.sched: Any = None
        self.zone: Any = None
        self.zone_name = ""
        self.default_window = 3600
        self.budget = 3
        self.jobs: dict[str, dict[str, Any]] = {}
        self.fires: list[dict[str, Any]] = []
        self.missed: list[dict[str, Any]] = []
        self.ended: set[tuple[str, str]] = set()
        self.deliveries: dict[int, dict[str, Any]] = {}
        self.alerts: list[dict[str, Any]] = []
        self.visibility: list[dict[str, Any]] = []
        self.violations: list[str] = []
        self.up: list[list[float | None]] = []
        self.restarts: Counter[str] = Counter()
        self.injected: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()
        self.turn_attempts: Counter[str] = Counter()
        self.turns_in_flight = 0
        self.hung_sends = 0
        self.arm_turn_until: float | None = None
        self.arm_send_until: float | None = None
        self.arm_downtime = 60
        self.outage = 0
        self.last_event_id = 0
        self.last_claim: dict[str, float] = {}
        self.arm_lock: dict[str, Any] | None = None
        self.lock_held: dict[str, Any] | None = None
        self.observer: sqlite3.Connection | None = None
        self.pending_visibility: list[tuple[str, str, dict[str, Any]]] = []
        self.dead_loop: dict[str, Any] = {}
        self.lock_threads: list[threading.Thread] = []
        self.samples: list[dict[str, Any]] = []
        self.downtime_s = 0.0
        self.booking_refused: list[str] = []
        self.edits: Counter[str] = Counter()
        self.gap_minutes = 0.0
        self.change_at = self._clock_change()
        self.tally = _LogTally()
        self._lock = threading.Lock()
        self._wall_start = time.monotonic()
        self._busy = False

    # ── helpers ──────────────────────────────────────────────────────────

    def _violate(self, text: str) -> None:
        stamp = _iso(self.clock.now())
        self.violations.append(f"[{stamp}] {text}")

    def _clock_change(self) -> str | None:
        """When the operator's zone changes its clock inside the window, if it does."""
        zone = ZoneInfo(OPERATOR_ZONE)
        t = self.t0
        before = datetime.fromtimestamp(t, UTC).astimezone(zone).utcoffset()
        while t < self.drain_end:
            t += 900
            off = datetime.fromtimestamp(t, UTC).astimezone(zone).utcoffset()
            if off != before:
                return _iso(datetime.fromtimestamp(t, UTC))
        return None

    def _alert(self, key: str, title: str, detail: str = "", *args: Any, **kwargs: Any) -> bool:
        """The ops alert, captured: its key, the job it names and when."""
        found = _JOB_ID.search(str(title)) or _JOB_ID.search(str(detail))
        with self._lock:
            self.alerts.append({"key": key, "job": found.group(0) if found else "", "at": self.clock.time(),
                                "severity": str(kwargs.get("severity", ""))})
        return True

    def _build_graph(self, capabilities: str = "read") -> _Graph:
        """The graph a task's fire gets; in an outage window there is none."""
        if self.outage > 0:
            with self._lock:
                self.injected["provider_outage_fires"] += 1
            raise RuntimeError("soak: the model provider is unavailable")
        return self.graph

    async def _turn(self, job_id: str, thread: str, state: dict[str, Any], graph: _Graph) -> dict[str, Any]:
        plan = self.jobs.get(job_id) or {}
        key = str(plan.get("key") or job_id)
        n = self.turn_attempts[key]
        self.turn_attempts[key] += 1
        rate = float(self.args.fail_rate)
        if plan.get("broken"):
            mode = "raise" if n % 2 else "turn_failed"
        else:
            u = _draw(self.seed, "turn", key, n)
            mode = ("raise" if u < rate * 0.4 else "turn_failed" if u < rate * 0.8
                    else "interrupt" if u < rate else "ok")
        self.turns_in_flight += 1
        try:
            await self.clock.sleep(self.turn_s)
        finally:
            self.turns_in_flight -= 1
        messages = list(state.get("messages") or [])
        if mode == "raise":
            self.injected["turn_raise"] += 1
            raise RuntimeError("soak: the model provider dropped the connection")
        if mode == "turn_failed":
            self.injected["turn_failed"] += 1
            warning = "⚠️ soak: the model provider rejected the request (401)."
            return {"messages": [*messages, {"role": "assistant", "content": warning}], "turn_failed": True,
                    "error_message": warning}
        if mode == "interrupt":
            self.injected["turn_interrupt"] += 1
            graph.paused.add(thread)
            return {"messages": messages}
        return {"messages": [*messages, {"role": "assistant", "content": f"Soak task {key}: all checks green."}]}

    async def _send(self, target_id: str, text: str, **kwargs: Any) -> str:
        """The platform: takes, refuses or raises; or Kazma dies while it sends."""
        claim = _CLAIMED.get()
        self.counts["send_attempts"] += 1
        if claim is None:
            self._violate(f"a message reached the platform outside the outbox ({target_id})")
            return "Error: soak: no outbox claim"
        did = int(claim["id"])
        info = self._delivery(did, job=claim["job_id"], purpose=claim["purpose"], due=claim["due_at"])
        if self.arm_send_until is not None:
            self.arm_send_until = None
            self.hung_sends += 1
            self.injected["send_died"] += 1
            info["hung"] = True
            await self.clock.sleep(math.inf)  # Kazma dies here: the harness kills it at the next settle
        if ":dead-" in target_id:
            self.injected["send_refused_dead_target"] += 1
            return "Error: soak platform: the bot was removed from this chat"
        key = (self.jobs.get(str(claim["job_id"])) or {}).get("key", claim["job_id"])
        u = _draw(self.seed, "send", key, claim["due_at"], claim["purpose"], claim["attempts"])
        if u < self.args.refuse_rate:
            self.injected["send_refused"] += 1
            return "Error: soak platform did not take the message"
        if u < self.args.refuse_rate + self.args.raise_rate:
            self.injected["send_raised"] += 1
            raise ConnectionError("soak platform: connection reset")
        info["accepted"] = int(info.get("accepted", 0)) + 1
        if info["accepted"] > 1:
            self._violate(f"delivery {did} of {claim['job_id']} reached the platform {info['accepted']} times")
        return f"sent:{target_id}"

    def _delivery(self, did: int, **fields: Any) -> dict[str, Any]:
        info = self.deliveries.setdefault(did, {"state": "pending", "accepted": 0, "attempts": 0})
        for k, v in fields.items():
            if v is not None and not info.get(k):
                info[k] = v
        return info

    # ── the store and the scheduler ──────────────────────────────────────

    async def _open(self) -> None:
        from kazma_core.cron.scheduler import CronScheduler

        self.store = _observed_store_class()(db_path=str(self.db_path), clock=self.clock)
        await self.store.init()
        self.sched = CronScheduler(store=self.store, graph_builder=self._build_graph, poll_interval=self.poll,
                                   max_concurrent=int(self.args.max_concurrent), drain_seconds=30.0)
        await self.sched.start()
        self.up.append([self.clock.time(), None])
        if self.observer is None:
            self.observer = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None,
                                            check_same_thread=False)

    def _end_up(self) -> None:
        if self.up and self.up[-1][1] is None:
            self.up[-1][1] = self.clock.time()

    async def _close_store(self) -> None:
        if self.store is not None:
            await self.store.close()
        self.store = None
        self.sched = None

    async def _stop_graceful(self) -> None:
        """``stop()``: the fires in flight finish (their turns take fake time)."""
        stop = asyncio.create_task(self.sched.stop())
        while not stop.done():
            await self._settle()
            if stop.done():
                break
            nxt = self.clock.next_deadline()
            if nxt is None:
                await asyncio.sleep(0.002)
            else:
                self.clock.advance_to(nxt)
        await stop
        self._end_up()
        await self._observe()
        await self._close_store()

    async def _kill(self) -> None:
        """A process death: every task cancelled, nothing drained or finished."""
        sched = self.sched
        tasks = [t for t in (sched._task, *sched._exec_tasks) if t is not None and not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._end_up()
        await self._observe()
        await self._close_store()

    async def _restart(self, kind: str, downtime: float) -> None:
        if kind in ("graceful", "gap", "after_dead_loop"):
            await self._stop_graceful()
        else:
            await self._kill()
        self.restarts[kind] += 1
        self.downtime_s += downtime
        self.clock.advance_to(self.clock.time() + downtime)  # nothing runs while Kazma is down
        await self._open()

    # ── settling and observing ───────────────────────────────────────────

    def _quiet(self) -> bool:
        """Nothing left to run until the fake clock moves: the poll loop and
        every fire holding a slot wait on the clock."""
        if self._busy:
            return False
        sched = self.sched
        if sched is None:
            return True
        loop_task = sched._task
        if loop_task is not None and not loop_task.done() and not self.clock.parked(loop_task):
            return False
        active = [t for t in sched._exec_tasks if not t.done()]
        parked = sum(1 for t in active if self.clock.parked(t))
        return parked >= min(len(active), sched._max_concurrent)

    async def _settle(self) -> bool:
        deadline = time.monotonic() + float(self.args.settle_timeout)
        quiet = 0
        while quiet < 3:
            await asyncio.sleep(0)
            if self._quiet():
                quiet += 1
                continue
            quiet = 0
            if time.monotonic() > deadline:
                self._violate(f"the scheduler did not settle within {self.args.settle_timeout:.0f} s of wall time")
                return False
            await asyncio.sleep(0.0005)
        return True

    async def _observe(self) -> None:
        """Read cron.db's new events, and check what each non-delivered one
        shows on the job (what the Scheduled page reads)."""
        if self.observer is None:
            return
        rows = self.observer.execute(
            "SELECT id, job_id, at, event, detail FROM cron_events WHERE id > ? ORDER BY id",
            (self.last_event_id,)).fetchall()
        for eid, job_id, at, event, detail in rows:
            self.last_event_id = int(eid)
            if job_id not in self.jobs:
                continue
            d = json.loads(detail or "{}")
            self._on_event(int(eid), job_id, float(at), str(event), d)
        pending, self.pending_visibility = self.pending_visibility, []
        for job_id, what, ref in pending:
            await self._check_visible(job_id, what, ref)

    def _on_event(self, eid: int, job_id: str, at: float, event: str, d: dict[str, Any]) -> None:
        self.counts[f"event_{event}"] += 1
        if event == "fire_claimed":
            # The fire starts here: its lateness is measured from this, not
            # taken from the scheduler's own figure.
            self.last_claim[job_id] = at
        elif event == "fire_finished":
            key = (job_id, str(d.get("due") or ""))
            if key in self.ended:
                self._violate(f"{self.jobs[job_id]['key']} ({job_id}) due {key[1]} ended a second time "
                              f"(fire, {d.get('outcome')})")
            self.ended.add(key)
            rec = {"job": job_id, "due": key[1], "outcome": d.get("outcome"), "status": d.get("status"),
                   "delivery": d.get("delivery_id"), "failures": d.get("failures"),
                   "lateness": float(d.get("lateness_s") or 0.0), "claimed_at": self.last_claim.pop(job_id, None),
                   "at": at, "seq": eid}
            self.fires.append(rec)
            if rec["delivery"] is not None:
                self._delivery(int(rec["delivery"]), job=job_id, purpose="result", due=key[1], created=at)
            if rec["outcome"] != SUCCESS:
                self.pending_visibility.append((job_id, "failed", rec))
            elif rec["delivery"] is None and rec["status"] != "cancelled":
                self._violate(f"{self.jobs[job_id]['key']} ({job_id}) due {key[1]}: a successful fire left no message")
        elif event == "missed":
            key = (job_id, str(d.get("due") or ""))
            if key in self.ended:
                self._violate(f"{self.jobs[job_id]['key']} ({job_id}) due {key[1]} ended a second time (missed)")
            self.ended.add(key)
            rec = {"job": job_id, "due": key[1], "delivery": d.get("delivery_id"), "status": d.get("status"),
                   "at": at, "seq": eid}
            self.missed.append(rec)
            if rec["delivery"] is not None:
                self._delivery(int(rec["delivery"]), job=job_id, purpose="missed", due=key[1], created=at)
            self.pending_visibility.append((job_id, "missed", rec))
        elif event.startswith("delivery_"):
            did = d.get("delivery_id")
            if did is None:
                return
            state = event.removeprefix("delivery_")
            info = self._delivery(int(did), job=job_id, purpose=d.get("purpose"))
            info["state"] = state
            info["attempts"] = max(int(info.get("attempts") or 0), int(d.get("attempt") or 0))
            if d.get("error"):
                info["error"] = str(d["error"])[:200]
            if state in ("undeliverable", "unknown"):
                self.pending_visibility.append((job_id, state, {"delivery": int(did)}))

    async def _check_visible(self, job_id: str, what: str, ref: dict[str, Any]) -> None:
        """A non-delivered occurrence must show its reason on the job."""
        job = await self.sched.get_job(job_id) if self.sched is not None else None
        ok, why = False, "the job is gone"
        if job is not None:
            rows = {int(r["id"]): r for r in job.get("deliveries") or []}
            if what == "failed":
                ok = job.get("last_outcome") == ref.get("outcome") and bool(str(job.get("last_result") or "").strip())
                why = f"last_outcome={job.get('last_outcome')!r} last_result={str(job.get('last_result'))[:60]!r}"
            elif what == "missed":
                notice = rows.get(int(ref["delivery"])) if ref.get("delivery") is not None else None
                ok = job.get("last_outcome") == "missed" and notice is not None and notice.get("purpose") == "missed"
                if ok and ref.get("status") == "missed":
                    ok = job.get("status") == "missed" and bool(str(job.get("last_result") or "").strip())
                why = f"status={job.get('status')!r} last_outcome={job.get('last_outcome')!r} notice={notice is not None}"
            else:
                row = rows.get(int(ref["delivery"]))
                ok = (row is not None and row.get("state") == what and bool(str(row.get("last_error") or "").strip())
                      and job.get("delivery_state") == what and bool(str(job.get("delivery_error") or "").strip()))
                why = (f"row={None if row is None else (row.get('state'), str(row.get('last_error'))[:40])} "
                       f"delivery_state={job.get('delivery_state')!r} delivery_error="
                       f"{str(job.get('delivery_error'))[:40]!r}")
        self.visibility.append({"job": job_id, "what": what, "ok": ok, "detail": why})
        if not ok:
            self._violate(f"{self.jobs[job_id]['key']} ({job_id}) {what}: the reason is not on the job ({why})")

    # ── booking ──────────────────────────────────────────────────────────

    def _series(self, rng: random.Random, now: datetime) -> tuple[dict[str, Any], str, str]:
        """A timing the harness can enumerate exactly: (spec, text, form)."""
        zr = rng.random()
        zone = OPERATOR_ZONE if zr < 0.5 else ("Asia/Kuwait" if zr < 0.75 else "UTC")
        if rng.random() < self.args.oneoff_share or self.run_end - now.timestamp() < 4 * 3600:
            return self._one_off(rng, now)
        for _ in range(8):
            hour, minute = rng.randrange(24), rng.randrange(60)
            if zone == OPERATOR_ZONE and rng.random() < 0.15:
                hour = 1  # inside the fall-back fold: once a day, the first time
            r = rng.random()
            days = [] if r < 0.45 else [0, 1, 2, 3, 4] if r < 0.6 else sorted(rng.sample(range(7), rng.randint(1, 3)))
            rrule = rng.random() < 0.3
            count = until = None
            end = rng.random()
            if end < (0.3 if rrule else 0.1):
                count = rng.randint(2, 5)
            elif end < (0.6 if rrule else 0.2):
                local_today = now.astimezone(_zone(zone)).date()
                until = (local_today + timedelta(days=rng.randint(1, int(self.args.days) + 1))).isoformat()
            spec = {"type": "weekly" if days else "daily", "days": days, "h": hour, "m": minute, "zone": zone,
                    "count": count, "until": until}
            if first_after(spec, now) is None:
                continue
            return spec, self._recurring_text(spec, rrule, rng), "rrule" if rrule else "friendly"
        return self._one_off(rng, now)

    def _recurring_text(self, spec: dict[str, Any], rrule: bool, rng: random.Random) -> str:
        pinned = spec["zone"] != OPERATOR_ZONE
        if rrule:
            parts = [f"FREQ={'WEEKLY' if spec['days'] else 'DAILY'}"]
            if spec["days"]:
                parts.append("BYDAY=" + ",".join(_RRULE_DAYS[d] for d in spec["days"]))
            parts += [f"BYHOUR={spec['h']}", f"BYMINUTE={spec['m']}"]
            if spec["count"]:
                parts.append(f"COUNT={spec['count']}")
            if spec["until"]:
                parts.append("UNTIL=" + spec["until"].replace("-", ""))
            if pinned:
                parts.append(f"TZID={spec['zone']}")
            return "RRULE:" + ";".join(parts)
        if rng.random() < 0.5:
            clock = f"{spec['h']:02d}:{spec['m']:02d}"
        else:
            clock = f"{spec['h'] % 12 or 12}:{spec['m']:02d}{'am' if spec['h'] < 12 else 'pm'}"
        if not spec["days"]:
            text = f"{rng.choice(['daily', 'every day'])} at {clock}"
        elif spec["days"] == [0, 1, 2, 3, 4]:
            text = f"weekdays at {clock}"
        else:
            names = ",".join(_DAY_NAMES[d] for d in spec["days"])
            text = f"{rng.choice(['every', 'weekly on'])} {names} at {clock}"
        if spec["until"]:
            text += f" until {spec['until']}"
        if spec["count"]:
            text += f" count {spec['count']}"
        if pinned:
            text += f" tz {spec['zone']}"
        return text

    def _one_off(self, rng: random.Random, now: datetime) -> tuple[dict[str, Any], str, str]:
        """A one-off: at a UTC ``Z`` time, at a Kuwait offset, in the
        operator's zone (never inside the fold, which is refused by design),
        or relative to now. ``zone`` says how it was written."""
        remaining = max(600.0, self.run_end - now.timestamp())
        style = rng.choice(["z", "offset", "naive", "relative"])
        if style == "relative":
            if rng.random() < 0.6 or remaining < 2 * 3600:
                n = rng.randint(1, max(1, int(min(remaining, 2 * 86400) // 60) - 1))
                at, text = now + timedelta(minutes=n), f"{n}m"
            else:
                n = rng.randint(1, max(1, int(remaining // 3600) - 1))
                at, text = now + timedelta(hours=n), f"{n}h"
            return {"type": "once", "at": _iso(at), "zone": "relative"}, text, style
        at = (now + timedelta(seconds=rng.uniform(300, remaining))).replace(second=0, microsecond=0)
        at += timedelta(minutes=1)
        if style == "naive":
            local = at.astimezone(ZoneInfo(OPERATOR_ZONE)).replace(tzinfo=None)
            if len(_wall_candidates(local, OPERATOR_ZONE)) != 1:
                at += timedelta(hours=2)
                local = at.astimezone(ZoneInfo(OPERATOR_ZONE)).replace(tzinfo=None)
            return {"type": "once", "at": _iso(at), "zone": OPERATOR_ZONE}, local.strftime("%Y-%m-%dT%H:%M"), style
        if style == "z":
            return ({"type": "once", "at": _iso(at), "zone": "UTC"},
                    at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), style)
        return ({"type": "once", "at": _iso(at), "zone": "Asia/Kuwait"},
                at.astimezone(ZoneInfo("Asia/Kuwait")).isoformat(timespec="minutes"), style)

    def _job_traits(self, i: int, rng: random.Random) -> dict[str, Any]:
        key = f"j{i:05d}"
        kind = "task" if rng.random() < self.args.task_share else "reminder"
        u = rng.random()
        a = self.args
        mode = ("dead" if u < a.dead_target_rate else "none" if u < a.dead_target_rate + a.no_target_rate
                else "heal" if u < a.dead_target_rate + a.no_target_rate + a.heal_target_rate else "ok")
        target = {"ok": f"soak:{key}", "dead": f"soak:dead-{key}"}.get(mode, "")
        thread = f"soak-orphan-{key}" if mode == "none" else f"soak-th{i // 20}"
        catchup = rng.choice([5, 15, 30, 240]) if rng.random() < 0.2 else 0
        arabic = rng.random() < 0.3
        if kind == "reminder":
            prompt = f"تذكير {key}: اتصل بالطبيب" if arabic else f"Soak reminder {key}: water the plants"
        else:
            prompt = f"مهمة {key}: لخص حالة النشر" if arabic else f"Soak task {key}: summarise the deploy status"
        return {"key": key, "kind": kind, "target_mode": mode, "target": target, "thread": thread,
                "tenant": f"soak-t{i // 400}", "catchup_min": catchup, "prompt": prompt,
                "broken": kind == "task" and rng.random() < a.broken_rate}

    async def _book(self, i: int, *, mid_run: bool) -> None:
        rng = random.Random(f"{self.seed}:job:{i}")
        traits = self._job_traits(i, rng)
        now = self.clock.now()
        spec, text, form = self._series(rng, now)
        try:
            res = await self.sched.schedule(
                timing=text, prompt=traits["prompt"], platform="soak", thread_id=traits["thread"],
                delivery_target=traits["target"], kind=traits["kind"], capabilities="read",
                catchup_minutes=traits["catchup_min"] or None, tenant_id=traits["tenant"], actor="soak",
                source="soak", zone=self.zone, zone_name=self.zone_name)
        except ValueError as exc:
            self.booking_refused.append(f"{traits['key']} {text!r}: {exc}")
            self._violate(f"booking {traits['key']} ({text!r}) was refused: {exc}")
            return
        first = first_after(spec, now)
        if first is None or _iso(first) != res["next_run"]:
            self._violate(f"booking {traits['key']} ({text!r}): the scheduler's first fire {res['next_run']} is "
                          f"not the harness's {None if first is None else _iso(first)}")
        self._second_opinion(traits["key"], spec, text, first)
        self.jobs[res["job_id"]] = {
            "key": traits["key"], "kind": traits["kind"], "target_mode": traits["target_mode"],
            "broken": traits["broken"], "catchup_s": traits["catchup_min"] * 60, "booked_at": _iso(now),
            "mid_run": mid_run, "form": form, "zone": spec.get("zone", "UTC"),
            "segments": [{"spec": spec, "text": text, "first": res["next_run"], "end": None}],
            "cancelled_at": None, "cancel_ok": [],
        }

    def _second_opinion(self, key: str, spec: dict[str, Any], text: str, first: datetime | None) -> None:
        """Ask timing.py for the same series; a disagreement is a violation."""
        if spec["type"] == "once" or first is None:
            return
        upto = datetime.fromtimestamp(self.drain_end, UTC)
        mine = [_iso(d) for d in occurrences(spec, first, upto)]
        theirs = [_iso(d) for d in _product_series(text, first, upto)]
        self.counts["second_opinions"] += 1
        if mine != theirs:
            self._violate(f"{key} ({text!r}): the harness's series {mine[:4]} and timing.py's {theirs[:4]} disagree")

    # ── the operator's actions mid-run ───────────────────────────────────

    async def _edit(self, n: int, *, cancel: bool) -> None:
        """An edit or a cancel through the versioned actions (the page's)."""
        rng = random.Random(f"{self.seed}:{'cancel' if cancel else 'edit'}:{n}")
        now = self.clock.now()
        cands = []
        active = [j for j in await self.store.list_active() if j.job_id in self.jobs]
        for job in sorted(active, key=lambda j: self.jobs[j.job_id]["key"]):  # the seed's order, not uuid's
            status = job.status.value
            if cancel or status == "running" or (
                    status == "pending" and job.next_run and _dt(job.next_run) > now + timedelta(seconds=2 * self.poll)):
                cands.append(job)
        if not cands:
            return
        job = rng.choice(cands)
        entry = self.jobs[job.job_id]
        stale = n % 10 == 0  # every tenth edit and cancel carries a stale version: it must be refused
        version = job.version - 1 if stale else job.version
        what = "cancel" if cancel else ("prompt" if rng.random() < 0.2 else "timing")
        spec = text = None
        if cancel:
            res = await self.sched.cancel(job.job_id, expected_version=version, actor="soak", source="soak")
        elif what == "prompt":
            res = await self.sched.reschedule(job.job_id, prompt=f"{entry['key']} edited at {_iso(now)}",
                                              expected_version=version, actor="soak", source="soak",
                                              zone=self.zone, zone_name=self.zone_name)
        else:
            spec, text, _form = self._series(rng, now)
            res = await self.sched.reschedule(job.job_id, timing=text, expected_version=version, actor="soak",
                                              source="soak", zone=self.zone, zone_name=self.zone_name)
        status = res.get("status")
        self.edits[f"{what}:{status}"] += 1
        if stale:
            if status != "conflict":
                self._violate(f"{entry['key']} ({job.job_id}): a {what} with a stale version answered {status!r}")
            return
        if cancel:
            if status not in ("cancelled", "cancel_pending"):
                self._violate(f"{entry['key']} ({job.job_id}): cancel answered {status!r} for a {job.status.value} job")
                return
            entry["cancelled_at"] = _iso(now)
            entry["segments"][-1]["end"] = _iso(now)
            if job.status.value == "pending" and job.next_run and _dt(job.next_run) <= now:
                entry["cancel_ok"].append(_iso(_dt(job.next_run)))
            if job.status.value == "delivering":
                for row in await self.store.deliveries_for(job.job_id):
                    self._delivery(int(row["id"]))["state"] = row["state"]
            return
        if status not in ("rescheduled", "edit_pending"):
            self._violate(f"{entry['key']} ({job.job_id}): an edit answered {status!r} for a {job.status.value} job "
                          f"({res.get('error', '')})")
            return
        if what == "timing" and spec is not None:
            first = first_after(spec, now)
            if first is None or _iso(first) != res.get("next_run"):
                self._violate(f"{entry['key']} ({job.job_id}) edited to {text!r}: the scheduler's next fire "
                              f"{res.get('next_run')} is not the harness's {None if first is None else _iso(first)}")
            self._second_opinion(entry["key"], spec, str(text), first)
            entry["segments"][-1]["end"] = _iso(now)
            entry["segments"].append({"spec": spec, "text": text, "first": res.get("next_run"), "end": None})

    async def _db_lock(self, *, mode: str) -> None:
        """Hold cron.db's write lock from another connection until a write of
        the scheduler has failed on it (past the store's busy timeout), or
        ``--db-lock-max-seconds`` of wall time. ``poll``: any write (a poll
        pass meets it first); ``mid_turn``: the fire whose turn is in flight
        must fail its own finish."""
        acquired, release = threading.Event(), threading.Event()
        cap = float(self.args.db_lock_max_seconds)

        def hold() -> None:
            conn = sqlite3.connect(str(self.db_path), timeout=60, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                acquired.set()
                release.wait(cap + 5)
                conn.execute("COMMIT")
            finally:
                conn.close()

        thread = threading.Thread(target=hold, name="soak-db-lock", daemon=True)
        thread.start()
        self.lock_threads.append(thread)
        await asyncio.to_thread(acquired.wait, 60)
        self.injected[f"db_locks_{mode}"] += 1
        self.lock_held = {"mode": mode, "release": release, "until": time.monotonic() + cap,
                          "locked": self.tally.locked, "fires": self.tally.locked_fires}

    def _release_lock_when_hit(self) -> bool:
        """Release a held lock once a write failed on it. True while it holds."""
        held = self.lock_held
        if held is None:
            return False
        hit = (self.tally.locked_fires > held["fires"] if held["mode"] == "mid_turn"
               else self.tally.locked > held["locked"])
        if hit or time.monotonic() > held["until"]:
            held["release"].set()
            self.lock_held = None
            if not hit:
                self.injected[f"db_lock_{held['mode']}_without_a_failed_write"] += 1
            return False
        return True

    async def _dead_loop(self) -> None:
        """Cancel the poll task (``_running`` stays set): readiness must say
        degraded within one interval. Then a restart."""
        from kazma_core.cron.scheduler import get_cron_scheduler, set_cron_scheduler
        from kazma_ui.health import check_cron

        sched = self.sched
        previous = get_cron_scheduler()
        set_cron_scheduler(sched)
        try:
            before = check_cron()
            sched._task.cancel()
            try:
                await sched._task
            except asyncio.CancelledError:
                pass
            t_dead = self.clock.time()
            self._end_up()
            detected: float | None = None
            seen: list[str] = []
            while True:
                ready = check_cron()
                seen.append(f"{self.clock.time() - t_dead:.0f}s:{ready.get('status')}/{ready.get('state')}")
                if detected is None and ready.get("status") == "degraded":
                    detected = self.clock.time() - t_dead
                if self.clock.time() >= t_dead + self.poll:
                    break
                await self._settle()
                nxt = self.clock.next_deadline()
                self.clock.advance_to(min(nxt if nxt is not None else math.inf, t_dead + self.poll))
        finally:
            set_cron_scheduler(previous)
        self.dead_loop = {"at": _iso(datetime.fromtimestamp(t_dead, UTC)), "before": before.get("status"),
                          "detected_after_s": detected, "bound_s": self.poll, "seen": seen[:6]}
        if detected is None or detected > self.poll:
            self._violate(f"readiness did not say degraded within one poll interval of a dead loop: {seen[:6]}")
        await self._restart("after_dead_loop", 30)

    # ── the plan and the run ─────────────────────────────────────────────

    def _plan(self, n_initial: int) -> list[tuple[float, int, str, dict[str, Any]]]:
        rng = random.Random(f"{self.seed}:plan")
        a = self.args
        span = self.run_end - self.t0
        seq = itertools.count()
        events: list[tuple[float, int, str, dict[str, Any]]] = []

        def add(at: float, kind: str, data: dict[str, Any]) -> None:
            events.append((float(int(at)), next(seq), kind, data))

        gap_at, dead_at = self.t0 + 0.4 * span, self.t0 + 0.65 * span
        add(gap_at, "gap", {"minutes": self.gap_minutes})
        add(dead_at, "dead_loop", {})
        kinds = ("graceful", "abrupt_turn", "graceful", "abrupt_send")
        t, k = self.t0, 0
        step = float(a.restart_hours) * 3600
        while step > 0:
            t += step * rng.uniform(0.9, 1.1)
            if t >= self.run_end - 1800:
                break
            if abs(t - gap_at) < 1800 or abs(t - dead_at) < 1800:
                continue
            kind = kinds[k % len(kinds)]
            k += 1
            down = rng.randint(10, 120) if kind == "graceful" else rng.randint(30, 180)
            add(t, "restart", {"kind": kind, "downtime": down})
        # Half the holds start at once (a poll pass meets the lock), half when
        # a turn is in flight (that fire's own finish meets it).
        for n in range(round(a.days * a.db_locks_per_day)):
            add(self.t0 + rng.uniform(0.05, 0.95) * span, "db_lock", {"mode": "mid_turn" if n % 2 else "poll"})
        for _ in range(max(1, round(a.days * a.outages_per_day)) if a.outages_per_day > 0 else 0):
            start = self.t0 + rng.uniform(0.05, 0.9) * span
            add(start, "outage", {"on": True})
            add(min(start + a.outage_minutes * 60, self.run_end - 60), "outage", {"on": False})
        for i in range(n_initial, int(a.jobs)):
            add(self.t0 + rng.uniform(0.02, 0.92) * span, "book", {"i": i})
        for n in range(round(a.jobs * a.edit_share)):
            add(self.t0 + rng.uniform(0.02, 0.95) * span, "edit", {"n": n})
        for n in range(round(a.jobs * a.cancel_share)):
            add(self.t0 + rng.uniform(0.02, 0.95) * span, "cancel", {"n": n})
        heapq.heapify(events)
        return events

    async def _apply(self, kind: str, data: dict[str, Any]) -> None:
        now = self.clock.time()
        if kind == "restart":
            if data["kind"] == "graceful":
                await self._restart("graceful", data["downtime"])
            elif data["kind"] == "abrupt_turn":
                self.arm_turn_until, self.arm_downtime = now + 1800, data["downtime"]
            else:
                self.arm_send_until, self.arm_downtime = now + 1800, data["downtime"]
        elif kind == "gap":
            await self._restart("gap", data["minutes"] * 60)
        elif kind == "dead_loop":
            await self._dead_loop()
        elif kind == "db_lock":
            if data.get("mode") == "mid_turn":
                self.arm_lock = {"until": now + 1800}
            else:
                await self._db_lock(mode="poll")
        elif kind == "outage":
            self.outage += 1 if data["on"] else -1
            self.injected["outage_windows"] += int(bool(data["on"]))
        elif kind == "book":
            await self._book(int(data["i"]), mid_run=True)
        elif kind in ("edit", "cancel"):
            await self._edit(int(data["n"]), cancel=kind == "cancel")

    async def _maybe_kill(self) -> bool:
        """An armed abrupt restart: mid-send, mid-turn, or plain when none came."""
        now = self.clock.time()
        kind = None
        if self.hung_sends:
            kind = "abrupt_mid_send"
        elif self.arm_turn_until is not None and self.turns_in_flight:
            kind = "abrupt_mid_turn"
        elif (self.arm_turn_until is not None and now >= self.arm_turn_until) or (
                self.arm_send_until is not None and now >= self.arm_send_until):
            kind = "abrupt"
        if kind is None:
            return False
        self.arm_turn_until = self.arm_send_until = None
        self.hung_sends = 0
        await self._restart(kind, self.arm_downtime)
        return True

    async def run(self) -> None:
        from kazma_core.cron.scheduler import resolve_cron_zone
        from kazma_core.cron.settings import cron_settings
        from kazma_core.cron.store import FireResult

        self.zone, self.zone_name = await asyncio.to_thread(resolve_cron_zone)
        if self.zone_name != OPERATOR_ZONE:
            raise RuntimeError(f"the operator zone is {self.zone_name!r}, not {OPERATOR_ZONE}: cron.timezone is "
                               "set in this settings store (the soak books in America/New_York)")
        self.default_window = (await asyncio.to_thread(cron_settings)).catchup_seconds
        self.budget = FireResult(success=False, outcome="").failure_budget
        self.gap_minutes = (float(self.args.gap_minutes) if self.args.gap_minutes
                            else self.default_window / 60 + 45)
        await self._open()
        n_initial = int(int(self.args.jobs) * (1 - float(self.args.mid_run_share)))
        for i in range(n_initial):
            await self._book(i, mid_run=False)
        plan = self._plan(n_initial)
        next_report = self.t0 + 3600
        next_check = self.t0 + 6 * 3600
        wall_report = time.monotonic() + _PROGRESS_WALL_S
        while not self.violations:
            if not await self._settle():
                break
            await self._observe()
            if self.violations:
                break
            # While a lock holds, the scheduler alone meets it: no restart,
            # booking or edit until a write of the scheduler failed on it.
            locked = self._release_lock_when_hit()
            if not locked:
                if await self._maybe_kill():
                    continue
                if self.arm_lock is not None and (self.turns_in_flight or self.clock.time() >= self.arm_lock["until"]):
                    self.arm_lock = None
                    await self._db_lock(mode="mid_turn" if self.turns_in_flight else "poll")
                    continue
            now = self.clock.time()
            if not locked and plan and plan[0][0] <= now:
                _, _, kind, data = heapq.heappop(plan)
                await self._apply(kind, data)
                continue
            if now >= next_report or time.monotonic() >= wall_report:
                self.samples.append({"t": _iso(self.clock.now()), **process_tree_sample()})
                _write_report(self.args.report, self.summary(status="running"))
                next_report = max(next_report, now) + 3600
                wall_report = time.monotonic() + _PROGRESS_WALL_S
            if now >= next_check:
                horizon = now - (max(self.default_window, 4 * 3600) + 3 * 3600)
                problems, _ = analyse(self.ledger(), horizon=horizon)
                for p in problems:
                    self._violate(p)
                next_check = now + 6 * 3600
                if self.violations:
                    break
            if now >= self.drain_end and not locked:
                break
            candidates = [self.clock.next_deadline(), plan[0][0] if plan and not locked else None,
                          self.drain_end, next_report]
            self.clock.advance_to(min((c for c in candidates if c is not None and c > now), default=now + self.poll))
        if self.lock_held is not None:
            self.lock_held["release"].set()
            self.lock_held = None
        if self.sched is not None:
            await self._stop_graceful()
        self._final_deliveries()
        for thread in self.lock_threads:
            thread.join(timeout=60)

    def _final_deliveries(self) -> None:
        """The outbox's own word at the end, for every message it still holds."""
        if self.observer is None:
            return
        for did, job_id, purpose, due, state, attempts, error in self.observer.execute(
                "SELECT id, job_id, purpose, due_at, state, attempts, last_error FROM cron_deliveries"):
            if job_id not in self.jobs:
                continue
            info = self._delivery(int(did), job=job_id, purpose=purpose, due=due)
            info["state"] = state
            info["attempts"] = int(attempts or 0)
            if error:
                info["error"] = str(error)[:200]
        self.observer.close()
        self.observer = None

    # ── the record ───────────────────────────────────────────────────────

    def ledger(self) -> dict[str, Any]:
        return {
            "version": 1, "seed": self.seed, "t0": self.t0, "run_end": self.run_end, "drain_end": self.drain_end,
            "poll_s": self.poll, "turn_s": self.turn_s, "default_window_s": self.default_window,
            "budget": self.budget, "operator_zone": OPERATOR_ZONE, "change_at": self.change_at,
            "up": [list(u) for u in self.up], "jobs": self.jobs, "fires": self.fires, "missed": self.missed,
            "deliveries": {str(k): v for k, v in self.deliveries.items()}, "alerts": list(self.alerts),
            "visibility": self.visibility, "dead_loop": self.dead_loop,
            "dead_loop_planned": True,
        }

    def summary(self, *, status: str, analysed: dict[str, Any] | None = None) -> dict[str, Any]:
        jobs = list(self.jobs.values())
        out: dict[str, Any] = {
            "status": status,
            "updated_at": utc_stamp(),
            "seed": self.seed,
            "fake_window": {"start": _iso(datetime.fromtimestamp(self.t0, UTC)),
                            "end": _iso(datetime.fromtimestamp(self.run_end, UTC)),
                            "drain_end": _iso(datetime.fromtimestamp(self.drain_end, UTC)),
                            "days": float(self.args.days), "now": _iso(self.clock.now())},
            "operator_zone": OPERATOR_ZONE,
            "poll_interval_s": self.poll,
            "turn_seconds": self.turn_s,
            "catchup_default_s": self.default_window,
            "jobs": {
                "total": len(jobs),
                "by_kind": dict(Counter(j["kind"] for j in jobs)),
                "by_form": dict(Counter(j["form"] for j in jobs)),
                "by_timing": dict(Counter(j["segments"][0]["spec"]["type"] for j in jobs)),
                "by_zone": dict(Counter(j["zone"] for j in jobs)),
                "by_target": dict(Counter(j["target_mode"] for j in jobs)),
                "own_catchup": sum(1 for j in jobs if j["catchup_s"]),
                "broken": sum(1 for j in jobs if j["broken"]),
                "booked_mid_run": sum(1 for j in jobs if j["mid_run"]),
                "edits": dict(self.edits),
                "booking_refused": self.booking_refused[:20],
            },
            "fires": {"total": len(self.fires), "by_outcome": dict(Counter(f["outcome"] for f in self.fires)),
                      "claims": self.counts["event_fire_claimed"],
                      "run_again_after_interruption": self.counts["event_fire_interrupted_retry"]},
            "missed": len(self.missed),
            "deliveries": {
                "total": len(self.deliveries),
                "by_state": dict(Counter(d.get("state") for d in self.deliveries.values())),
                "by_purpose": dict(Counter(d.get("purpose") for d in self.deliveries.values())),
                "send_attempts": self.counts["send_attempts"],
                "accepted": sum(int(d.get("accepted") or 0) for d in self.deliveries.values()),
            },
            "alerts": {"total": len(self.alerts), "by_key": dict(Counter(a["key"] for a in self.alerts))},
            "restarts": {"total": sum(self.restarts.values()), "by_kind": dict(self.restarts),
                         "gap_minutes": getattr(self, "gap_minutes", None),
                         "downtime_s_total": round(self.downtime_s, 1)},
            "dead_loop": self.dead_loop,
            "injected": dict(self.injected) | {"db_locked_writes": self.tally.locked,
                                               "db_locked_fire_finishes": self.tally.locked_fires},
            "clock_change": {"zone": OPERATOR_ZONE, "at": self.change_at},
            "second_opinions": self.counts["second_opinions"],
            "visibility_checks": len(self.visibility),
            "product_warnings": dict(self.tally.counts.most_common(25)),
            "violations": self.violations[:50],
            "violation_count": len(self.violations),
            "wall_seconds": round(time.monotonic() - self._wall_start, 1),
            "resources": {
                "max_rss_mib": max((s["rss_mib"] for s in self.samples), default=None),
                "max_handles": max((s["handles"] for s in self.samples), default=None),
                "samples": self.samples[:: max(1, len(self.samples) // 50)],
            },
            "data_dir": str(self.data_dir),
        }
        if analysed:
            out.update(analysed)
        return out


# ── the check: pure, over a recorded ledger ──────────────────────────────


def _up_through(up: list[list[float | None]], start: float, end: float) -> bool:
    """Whether one scheduler instance was up for the whole of [start, end]."""
    return any(a is not None and a <= start and (b is None or b >= end) for a, b in up)


#: A message created this close to the end may still be retrying (its six
#: attempts span 15.5 minutes): it is reported in flight, not unsettled.
_IN_FLIGHT_GRACE_S = 1200.0


def analyse(ledger: dict[str, Any], *, horizon: float | None = None) -> tuple[list[str], dict[str, Any]]:
    """Check a ledger against the harness's own ground truth.

    Returns ``(violations, summary)``. With *horizon* (a partial check while
    the run goes on, or after it stopped early) only occurrences due before
    it are judged, for losses, double ends and fires at a time not due; the
    end of a whole run judges everything.
    """
    final = horizon is None
    run_end, drain_end = float(ledger["run_end"]), float(ledger["drain_end"])
    horizon = run_end if horizon is None else min(float(horizon), run_end)
    default_window, budget = int(ledger["default_window_s"]), int(ledger["budget"])
    up = ledger["up"]
    zone_name = ledger["operator_zone"]
    zone = ZoneInfo(zone_name)
    change = ledger.get("change_at")
    v: list[str] = []
    fires_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in ledger["fires"]:
        fires_of[f["job"]].append(f)
    missed_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in ledger["missed"]:
        missed_of[m["job"]].append(m)
    deliveries = {int(k): d for k, d in ledger["deliveries"].items()}
    outcome: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    expected_alerts: Counter[tuple[str, str]] = Counter()
    crossed = 0
    upto_drain = datetime.fromtimestamp(drain_end, UTC)
    for job_id, job in ledger["jobs"].items():
        name = f"{job['key']} ({job_id})"
        window = int(job["catchup_s"] or default_window)
        job_fires = sorted(fires_of.get(job_id, []), key=lambda f: f["seq"])
        ends: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for f in job_fires:
            ends[f["due"]].append(("fire", f))
        for m in missed_of.get(job_id, []):
            ends[m["due"]].append(("missed", m))
        # The harness's own list of when this job was due, segment by segment.
        seg_of: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        for seg in job["segments"]:
            if not seg.get("first"):
                continue
            first = _dt(seg["first"])
            end = _dt(seg["end"]) + timedelta(seconds=1) if seg.get("end") else upto_drain
            for at in occurrences(seg["spec"], first, min(end, upto_drain)):
                due = _iso(at)
                seg_of[due] = seg
                if at.timestamp() < horizon:
                    required.append(due)
        cancel_ok = set(job.get("cancel_ok") or [])
        for due, recs in ends.items():
            if due not in seg_of and due not in cancel_ok:
                v.append(f"{name} fired at {due}, a time it was not due ({recs[0][0]})")
        stop_after = next((f["due"] for f in job_fires if f["status"] == "failed"
                           and seg_of.get(f["due"], {}).get("spec", {}).get("type") != "once"), None)
        for due in sorted(set(required)):
            if stop_after and due > stop_after:
                outcome["ended_by_failure_budget"] += 1
                continue
            recs = ends.get(due, [])
            if not recs:
                if due in cancel_ok:
                    outcome["cancelled"] += 1
                    by_kind[f"{job['kind']}:cancelled"] += 1
                    continue
                v.append(f"lost: {name} was due {due} and never fired, and was not recorded missed")
                continue
            if len(recs) > 1:
                v.append(f"{name} due {due} ended {len(recs)} times ({', '.join(r[0] for r in recs)})")
                continue
            what, rec = recs[0]
            cls = _classify(rec if what == "fire" else None, rec if what == "missed" else None, deliveries)
            outcome[cls] += 1
            by_kind[f"{job['kind']}:{cls}"] += 1
            if what == "missed":
                due_ts = _dt(due).timestamp()
                if rec.get("delivery") is None:
                    v.append(f"{name} due {due} was missed with no notice")
                if rec["at"] - due_ts <= window:
                    v.append(f"{name} due {due} was recorded missed {rec['at'] - due_ts:.0f} s late, inside "
                             f"its {window} s window")
                if _up_through(up, due_ts, due_ts + window):
                    v.append(f"{name} due {due} was missed while the scheduler ran through its whole window")
        if not final:
            continue
        # Failure alerts: one when a run of failures starts, one when the
        # budget runs out (the scheduler's rule, counted from what fired).
        streak = 0
        for f in job_fires:
            success = f["outcome"] == SUCCESS
            streak = 0 if success else streak + 1
            if f.get("failures") is not None and int(f["failures"]) != streak:
                v.append(f"{name} due {f['due']}: {f['failures']} failures recorded, {streak} in a row observed")
            if not success:
                recurring = seg_of.get(f["due"], {}).get("spec", {}).get("type") != "once"
                if f["status"] == "failed" and recurring and streak >= budget:
                    expected_alerts[(job_id, "cron.job_failed")] += 1
                elif streak == 1 or f["status"] == "failed":
                    expected_alerts[(job_id, "cron.job_failure")] += 1
        # The clock change: once per local day, at the intended wall time.
        local_series = [s["spec"] for s in job["segments"]
                        if s["spec"]["type"] != "once" and s["spec"]["zone"] == zone_name]
        if local_series:
            dues = sorted(d for d in ends if seg_of.get(d, {}).get("spec") in local_series)
            for day, n in Counter(_dt(d).astimezone(zone).date() for d in dues).items():
                if n > 1:
                    v.append(f"{name} fired {n} times on local day {day}")
            for d in dues:
                spec = seg_of[d]["spec"]
                local = _dt(d).astimezone(zone)
                if (local.hour, local.minute) not in {(spec["h"], spec["m"]), ((spec["h"] + 1) % 24, spec["m"])}:
                    v.append(f"{name} fired at {local.isoformat()}, not at {spec['h']:02d}:{spec['m']:02d} local")
            if change and any(d < change for d in dues) and any(d > change for d in dues):
                crossed += 1
    summary: dict[str, Any] = {"occurrences": {"required": sum(outcome.values()), "by_outcome": dict(outcome),
                                               "by_kind_outcome": dict(by_kind)}}
    if not final:
        return v, summary
    in_flight = 0
    for did, d in deliveries.items():
        accepted, state = int(d.get("accepted") or 0), d.get("state")
        if accepted > 1:
            v.append(f"delivery {did} of {d.get('job')} was delivered {accepted} times")
        if state not in TERMINAL_DELIVERY:
            if float(d.get("created") or 0.0) >= drain_end - _IN_FLIGHT_GRACE_S:
                in_flight += 1
            else:
                v.append(f"delivery {did} of {d.get('job')} never settled (still {state})")
        elif state == "delivered" and accepted != 1:
            v.append(f"delivery {did} of {d.get('job')} is recorded delivered, the platform took it {accepted} times")
        elif state in ("undeliverable", "cancelled") and accepted:
            v.append(f"delivery {did} of {d.get('job')} is recorded {state}, but the platform took it")
        if state == "undeliverable":
            expected_alerts[(str(d.get("job")), "cron.delivery_failed")] += 1
        elif state == "unknown":
            expected_alerts[(str(d.get("job")), "cron.delivery_unknown")] += 1
    observed = Counter((a["job"], a["key"]) for a in ledger["alerts"] if str(a["key"]).startswith("cron."))
    for pair in sorted(set(expected_alerts) | set(observed)):
        if expected_alerts[pair] != observed[pair]:
            v.append(f"{pair[0]}: {observed[pair]} {pair[1]} alert(s), {expected_alerts[pair]} expected")
    for e in ledger["visibility"]:
        if not e["ok"]:
            v.append(f"{e['job']} {e['what']}: the reason is not on the job ({e['detail']})")
    # Lateness: from the fire's own claim (fake time) to when it was due. The
    # scheduler's figure is checked against it, never trusted for the gate.
    eligible, catch_up = [], []
    for f in ledger["fires"]:
        due_ts = _dt(f["due"]).timestamp()
        late = f["claimed_at"] - due_ts if f.get("claimed_at") is not None else None
        if late is None:
            v.append(f"{f['job']} due {f['due']}: a fire with no claim before it")
            continue
        if abs(late - float(f["lateness"])) > 1.5:
            v.append(f"{f['job']} due {f['due']}: the scheduler recorded {f['lateness']} s late, it started "
                     f"{late:.0f} s after it was due")
        (eligible if _up_through(up, due_ts, due_ts + late) else catch_up).append(late)
    bound = float(ledger["poll_s"]) + float(ledger["turn_s"])
    p95 = percentile(eligible, 0.95)
    if p95 is not None and p95 > bound:
        v.append(f"p95 fire lateness {p95} s is above one poll interval plus the turn time ({bound} s)")
    dead = ledger.get("dead_loop") or {}
    if ledger.get("dead_loop_planned") and not dead:
        v.append("the dead poll loop was never exercised")
    elif dead and (dead.get("detected_after_s") is None or dead["detected_after_s"] > float(ledger["poll_s"])):
        v.append(f"readiness was not degraded within one poll interval of the dead loop: {dead.get('seen')}")
    summary.update({
        "lateness_s": {"while_up": latency_summary(eligible), "catch_up": latency_summary(catch_up),
                       "bound_s": bound, "p95_within_bound": p95 is not None and p95 <= bound},
        "clock_change": {"zone": zone_name, "at": change, "series_across_change": crossed},
        "alerts_expected": sum(expected_alerts.values()),
        "deliveries_in_flight_at_end": in_flight,
    })
    return v, summary


def _classify(fire: dict[str, Any] | None, miss: dict[str, Any] | None, deliveries: dict[int, dict[str, Any]]) -> str:
    """How one due occurrence ended."""
    if miss is not None:
        notice = deliveries.get(int(miss["delivery"])) if miss.get("delivery") is not None else None
        return "missed" if notice is None else f"missed (notice {notice.get('state')})"
    if fire is None:
        return "none"
    if fire["outcome"] != SUCCESS:
        return f"failed ({fire['outcome']})"
    if fire.get("delivery") is None:
        return "cancelled"
    state = (deliveries.get(int(fire["delivery"])) or {}).get("state")
    return {"delivered": "delivered", "undeliverable": "undeliverable", "unknown": "delivery_unknown",
            "cancelled": "cancelled"}.get(str(state), f"unsettled ({state})")


# ── the command ───────────────────────────────────────────────────────────


async def _soak(args: argparse.Namespace, data_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    import importlib

    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    soak = Soak(args, data_dir)
    real_alert = ops.alert
    had_backend = "soak" in sm._message_backends
    product_log = logging.getLogger("kazma_core")
    propagate = product_log.propagate
    product_log.addHandler(soak.tally)
    product_log.propagate = False
    session_mod = sys.modules.get("kazma_ui.session_manager")
    had_sessions = session_mod is not None and getattr(session_mod, "_session_manager", None) is not None
    sm._message_backends["soak"] = soak._send
    ops.alert = soak._alert
    try:
        await soak.run()
    except Exception as exc:  # noqa: BLE001 — a soak reports its own crash as a violation
        logging.getLogger(__name__).exception("scheduled soak crashed")
        soak._violate(f"the harness crashed: {type(exc).__name__}: {exc}")
        if soak.sched is not None:
            try:
                await soak._kill()
            except Exception:  # noqa: BLE001 — already failing; the report says why
                logging.getLogger(__name__).exception("scheduled soak: the kill after a crash failed")
    finally:
        ops.alert = real_alert
        if not had_backend:
            sm._message_backends.pop("soak", None)
        product_log.removeHandler(soak.tally)
        product_log.propagate = propagate
        if not had_sessions:
            _drop_session_manager()
    ledger = soak.ledger()
    if soak.violations:
        # Stopped early: judge only what was due well before the stop, or
        # everything after it reads as lost.
        horizon = soak.clock.time() - (max(soak.default_window, 4 * 3600) + 3 * 3600)
        problems, analysed = analyse(ledger, horizon=horizon)
    else:
        problems, analysed = analyse(ledger)
    soak.violations += [p for p in problems if p not in soak.violations]
    return soak.summary(status="running", analysed=analysed), ledger


def _drop_session_manager() -> None:
    """The chat store a job with no address made the scheduler open (its
    last-resort address lookup): closed, so the process is left as found."""
    mod = sys.modules.get("kazma_ui.session_manager")
    manager = getattr(mod, "_session_manager", None) if mod is not None else None
    if manager is None:
        return
    for name in ("_conn", "_spool"):
        handle = getattr(manager, name, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 — best effort: the process is ending its soak
                logging.getLogger(__name__).debug("closing the session manager's %s failed", name, exc_info=True)
    mod._session_manager = None


_ENV_KEYS = ("KAZMA_DATA_DIR", "KAZMA_DB_BACKEND", "KAZMA_DATABASE_URL", "KAZMA_TZ", "KAZMA_CRON_STALE_HOURS",
             "KAZMA_USER_HOME", "KAZMA_SKILLS_HOME", "KAZMA_PROCESS_PRIORITY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--days", type=float, default=7.0, help="simulated days of load")
    parser.add_argument("--jobs", type=int, default=2000, help="jobs in all (a fifth booked during the run)")
    parser.add_argument("--start", default=DEFAULT_START, help="fake start (the New York fall-back is 18 h in)")
    parser.add_argument("--poll-seconds", type=float, default=30.0, help="the scheduler's poll interval")
    parser.add_argument("--turn-seconds", type=float, default=20.0, help="fake time a task's turn takes")
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--restart-hours", type=float, default=3.0, help="simulated hours between restarts")
    parser.add_argument("--gap-minutes", type=float, default=None,
                        help="the long downtime (default: the catch-up window + 45 minutes)")
    parser.add_argument("--drain-minutes", type=float, default=45.0, help="fake time after the last due occurrence")
    parser.add_argument("--task-share", type=float, default=0.5, help="share of jobs that are agent tasks")
    parser.add_argument("--oneoff-share", type=float, default=0.4, help="share of jobs that are one-offs")
    parser.add_argument("--mid-run-share", type=float, default=0.2, help="share of jobs booked during the run")
    parser.add_argument("--edit-share", type=float, default=0.03, help="edits, as a share of the jobs")
    parser.add_argument("--cancel-share", type=float, default=0.03, help="cancels, as a share of the jobs")
    parser.add_argument("--fail-rate", type=float, default=0.05, help="a task's turn fails (raise/fail/approval)")
    parser.add_argument("--broken-rate", type=float, default=0.02, help="share of tasks that always fail")
    parser.add_argument("--outages-per-day", type=float, default=0.5, help="provider outage windows per day")
    parser.add_argument("--outage-minutes", type=float, default=45.0)
    parser.add_argument("--refuse-rate", type=float, default=0.05, help="a send the platform refuses")
    parser.add_argument("--raise-rate", type=float, default=0.02, help="a send that raises")
    parser.add_argument("--dead-target-rate", type=float, default=0.01, help="jobs whose chat always refuses")
    parser.add_argument("--no-target-rate", type=float, default=0.005, help="jobs with no address")
    parser.add_argument("--heal-target-rate", type=float, default=0.005,
                        help="jobs with no address that may adopt a sibling's")
    parser.add_argument("--db-locks-per-day", type=float, default=1.0, help="cron.db write-lock holds per day")
    parser.add_argument("--db-lock-max-seconds", type=float, default=30.0,
                        help="a hold lasts until a write failed on it (the busy timeout is 5 s), at most this")
    parser.add_argument("--settle-timeout", type=float, default=120.0, help="wall seconds a step may take")
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None)
    parser.add_argument("--ledger", default=None, help="default: beside the report, as .ledger.json")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="kazma-scheduled-soak-"))
    data_dir = Path(args.data_dir) if args.data_dir else work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    # Never an install's stores: a private data dir, SQLite, no database URL.
    os.environ.update({
        "KAZMA_DATA_DIR": str(data_dir), "KAZMA_DB_BACKEND": "sqlite", "KAZMA_TZ": OPERATOR_ZONE,
        "KAZMA_USER_HOME": str(data_dir / "home"), "KAZMA_SKILLS_HOME": str(data_dir / "home"),
        "KAZMA_PROCESS_PRIORITY": "keep",
    })
    os.environ.pop("KAZMA_DATABASE_URL", None)
    os.environ.pop("KAZMA_CRON_STALE_HOURS", None)
    try:
        report, ledger = asyncio.run(_soak(args, data_dir))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    healthy = report["violation_count"] == 0
    ledger_path = args.ledger or (str(Path(args.report).with_suffix("")) + ".ledger.json" if args.report else None)
    if ledger_path:
        Path(ledger_path).write_text(json.dumps(ledger, ensure_ascii=False, default=str), encoding="utf-8")
        report["ledger"] = ledger_path
    finished(report, healthy)
    _write_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
