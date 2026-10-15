#!/usr/bin/env python3
"""Swarm chaos runs: one fault at a time, and the ending each one leaves.

The Swarm hardening tracker's row SQ04 (``docs/plans/
SWARM_INDUSTRIAL_HARDENING_GOAL.md``) asks for fault-injection runs with the
recorded outcome per case: a task store database locked or down, Temporal
unreachable, a shutdown in the middle of a pattern. Each case here drives a
real ``SwarmEngine`` over private stores in a temporary directory (``--data-dir``
keeps them), with scripted workers -- no model, no network but a local port
nothing listens on (Temporal), Temporal's time-skipping test server started
on this machine (``temporal_lost_mid_run``, which shuts it down mid-run; the
SDK downloads it on first use) and, with ``--postgres-dsn``, a DISPOSABLE
Postgres server: a database of its own is created there and dropped after, and
the case takes it down by refusing its connections and ending its sessions.
Never point it at a server that holds anything.

What each case records:

* what the caller was told (status, error, metadata);
* what the task store holds, read through a fresh store as the next start
  reads it, and what that start's recovery (``requeue_orphaned_running``)
  makes of it;
* the ``kazma_swarm_failures_total`` sites the run counted;
* whether the ending is honest. Never a success the store does not hold;
  never a row left RUNNING once the store is back and the next start has run;
  an interrupted task's cause and effect evidence written before the shutdown
  returns; every lost write counted; plus each case's own expectation.

``tests/test_swarm_chaos.py`` runs the same cases (shorter waits) and holds
each ending, with negative controls; this script is how the outcome table in
the tracker was recorded, at the product's own timeouts.

Usage (repo root):
  python scripts/swarm_chaos.py                      # every case that needs nothing else
  python scripts/swarm_chaos.py --case sqlite_locked_finalize
  python scripts/swarm_chaos.py --postgres-dsn postgresql://postgres:x@127.0.0.1:55433/postgres
  python scripts/swarm_chaos.py --report chaos.json --markdown chaos.md
Exit 0 when every case ran and ended honestly, else 1.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from soak_report import finished, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
for _pkg in ("kazma-cli", "kazma-ui", "kazma-tui", "kazma-skills", "kazma-gateway", "kazma-core"):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)  # this checkout's packages, whatever is installed

logger = logging.getLogger("swarm_chaos")

TASK_DB = "swarm_tasks.db"


@dataclass
class Timing:
    """The waits a run uses. The script keeps the product's own; the tests
    shorten them (the store's busy timeout, the engine's write retries)."""

    busy_ms: int | None = None          # None: the store's own (5 s)
    write_waits: tuple[float, ...] | None = None  # None: the engine's own
    pg_pool_timeout: float = 2.0        # KAZMA_PG_POOL_TIMEOUT for the Postgres cases


@dataclass
class Outcome:
    case: str
    fault: str
    expected: str
    returned: dict[str, Any] = field(default_factory=dict)
    stored: dict[str, Any] = field(default_factory=dict)
    recovered: dict[str, Any] = field(default_factory=dict)
    counted: dict[str, float] | None = None
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0
    skipped: str = ""

    @property
    def honest(self) -> bool:
        return not self.problems and not self.skipped


# ── the environment of a case ──────────────────────────────────────────────


@contextlib.contextmanager
def _env(**values: str | None) -> Iterator[None]:
    """Set (or, with None, remove) environment variables for the block."""
    before = {k: os.environ.get(k) for k in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def _inert(timing: Timing) -> Iterator[None]:
    """No model call, no memory write, no gate registry, no Temporal unless a
    case turns it on; the engine's write retries as *timing* says."""
    from unittest import mock

    import kazma_core.swarm.engine as engine_module
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _refined(task: Any, worker_results: Any, status: Any, total_ms: Any) -> str:
        return "refined-stub"

    async def _none(*_a: Any, **_kw: Any) -> None:
        return None

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(patterns, "_synthesize_refined_output", _refined))
        stack.enter_context(mock.patch.object(patterns, "_run_self_improvement", _none))
        stack.enter_context(mock.patch.object(wd, "_index_worker_l4_memory", _none))
        if timing.write_waits is not None:
            stack.enter_context(mock.patch.object(engine_module, "_WRITE_RETRY_WAITS", timing.write_waits))
        stack.enter_context(_env(
            KAZMA_SHARED_BREAKERS="0", KAZMA_TEMPORAL="0", KAZMA_TEMPORAL_REQUIRED=None,
            KAZMA_TEMPORAL_HOST=None, TEMPORAL_ADDRESS=None, KAZMA_GATE_REGISTRY="0",
            KAZMA_SELF_IMPROVEMENT="0", KAZMA_SWARM_MAX_ACTIVE=None,
        ))
        yield


def _failures() -> dict[str, float] | None:
    """``kazma_swarm_failures_total`` by site (None without prometheus-client)."""
    from kazma_core import metrics

    counter = getattr(metrics, "SWARM_FAILURES_TOTAL", None)
    if counter is None:
        return None
    out: dict[str, float] = {}
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total"):
                out[sample.labels.get("site", "?")] = sample.value
    return out


def _delta(before: dict[str, float] | None, after: dict[str, float] | None) -> dict[str, float] | None:
    if before is None or after is None:
        return None
    return {k: v - before.get(k, 0.0) for k, v in after.items() if v - before.get(k, 0.0)}


def _sqlite_store(workdir: Path, timing: Timing) -> Any:
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(db_path=str(workdir / TASK_DB))
    if timing.busy_ms is not None:
        with store._lock:
            store._get_conn().execute(f"PRAGMA busy_timeout={int(timing.busy_ms)}")
    return store


def _engine(store: Any, *names: str) -> Any:
    from kazma_core.swarm.config import SwarmConfig, WorkerConfig
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in names:
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    return engine


def _ok(worker: str, output: str = "ok") -> dict[str, Any]:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _task_id(case: str) -> str:
    return f"chaos-{case}-{uuid.uuid4().hex[:8]}"


class _SqliteLock:
    """Another connection holding the task store's write lock (``BEGIN
    IMMEDIATE``): every write waits out the busy timeout and fails."""

    def __init__(self, db: Path) -> None:
        self._conn = sqlite3.connect(str(db), isolation_level=None, check_same_thread=False)
        self._held = False
        self._closed = False
        self._guard = threading.Lock()

    def take(self) -> None:
        with self._guard:
            if not self._held and not self._closed:
                self._conn.execute("BEGIN IMMEDIATE")
                self._held = True

    def release(self) -> None:
        with self._guard:
            if self._held and not self._closed:
                self._conn.execute("ROLLBACK")
                self._held = False

    def close(self) -> None:
        self.release()
        with self._guard:
            self._closed = True
            self._conn.close()


@contextlib.contextmanager
def _engine_lines(fragment: str, on_line: Callable[[], None] | None = None) -> Iterator[list[str]]:
    """The engine's log lines holding *fragment* while the block runs (and
    *on_line* called at each, in the thread that logged it)."""
    seen: list[str] = []

    class _Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if fragment in message:
                seen.append(message)
                if on_line is not None:
                    on_line()

    engine_log = logging.getLogger("kazma_core.swarm.engine")
    handler, level = _Catch(level=logging.INFO), engine_log.level
    engine_log.addHandler(handler)
    if engine_log.getEffectiveLevel() > logging.INFO:
        engine_log.setLevel(logging.INFO)
    try:
        yield seen
    finally:
        engine_log.removeHandler(handler)
        engine_log.setLevel(level)


def _read(store: Any, task_id: str) -> dict[str, Any]:
    """A row as the next start reads it: status, the result it holds."""
    try:
        task = store.get_task(task_id)
    except Exception as exc:  # the store is the fault in some cases
        return {"status": "unreadable", "error": f"{type(exc).__name__}: {exc}"[:200]}
    if task is None:
        return {"status": "absent"}
    result = task.result
    meta = dict(getattr(result, "metadata", None) or {})
    return {
        "status": task.status.value,
        "result_status": getattr(result, "status", None),
        "error": (getattr(result, "error", None) or "")[:240],
        "interrupted_by": meta.get("interrupted_by"),
        "execution_outcome": meta.get("execution_outcome"),
        "invoked_tools": meta.get("invoked_tools"),
    }


async def _read_when_back(store: Any, task_id: str, seconds: float = 20.0) -> dict[str, Any]:
    """:func:`_read`, once a store that was down answers again (a pool
    reconnects with a backoff of its own)."""
    deadline = time.monotonic() + seconds
    while True:
        row = await asyncio.to_thread(_read, store, task_id)
        if row["status"] != "unreadable" or time.monotonic() > deadline:
            return row
        await asyncio.sleep(0.5)


def _returned(result: Any) -> dict[str, Any]:
    meta = dict(getattr(result, "metadata", None) or {})
    keep = ("unsaved", "result_status", "admission_unrecorded", "checkpoint_open", "code",
            "admission_denied", "durable", "execution_outcome", "interrupted_by")
    return {
        "status": getattr(result, "status", None),
        "error": (getattr(result, "error", None) or "")[:240],
        **{k: meta[k] for k in keep if k in meta},
    }


def _ending_problems(out: Outcome) -> list[str]:
    """The checks every case's ending must pass."""
    problems = []
    told = out.returned.get("status")
    held = out.stored.get("status")
    if told in ("success", "partial") and held != "completed":
        problems.append(f"the caller was told {told}, the store holds {held}")
    if told in ("success", "partial") and out.stored.get("result_status") not in (told, None):
        problems.append(f"the caller was told {told}, the stored result says {out.stored.get('result_status')}")
    if out.recovered and out.recovered.get("status") == "running":
        problems.append("a row is still RUNNING after the next start's recovery")
    return problems


def _expect(out: Outcome, ok: bool, problem: str) -> None:
    if not ok:
        out.problems.append(problem)


async def _until(predicate: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("the scripted worker never got there")
        await asyncio.sleep(0.01)


async def _recover(store_factory: Callable[[], Any], task_id: str) -> dict[str, Any]:
    """What the next start makes of the row: a new store, its recovery."""
    store = await asyncio.to_thread(store_factory)
    try:
        await asyncio.to_thread(store.requeue_orphaned_running)
        return await asyncio.to_thread(_read, store, task_id)
    finally:
        await asyncio.to_thread(store.close)


# ── the cases ──────────────────────────────────────────────────────────────


async def case_sqlite_locked_admission(workdir: Path, timing: Timing) -> Outcome:
    """The store stays locked through every attempt of the admission write."""
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("sqlite_locked_admission", "another connection holds the task store's write lock "
                  "(BEGIN IMMEDIATE) from before the dispatch until after it has returned (past every "
                  "retry of the RUNNING row's write)",
                  "the task is refused, not run: task_store_unavailable (retryable), counted running_row; "
                  "no row, the admission slot free")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "w")
    ran: list[str] = []

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        ran.append("w")
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(out.case), workers=["w"], timeout=60)
    lock.take()
    with _engine_lines("not admitted yet") as retries:
        try:
            result = await engine.dispatch(task)
        finally:
            lock.close()
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    out.notes.append(f"the admission was tried again {len(retries)} time(s); worker runs: {len(ran)}; "
                     f"in flight after: {len(engine.list_active_tasks())}")
    _expect(out, out.returned["status"] == "failed" and out.returned.get("code") == "task_store_unavailable",
            f"expected a refusal (task_store_unavailable): {out.returned}")
    _expect(out, not ran, "the task ran without its RUNNING row")
    _expect(out, out.stored["status"] == "absent", f"expected no row: {out.stored}")
    _expect(out, not engine.list_active_tasks(), "the refused task holds a slot")
    _expect(out, len(retries) >= 1, "the admission was never tried again")
    return out


async def case_sqlite_locked_admission_briefly(workdir: Path, timing: Timing) -> Outcome:
    """The admission's first write meets the lock; it goes before the retries run out."""
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("sqlite_locked_admission_briefly", "the store is locked from before the dispatch until "
                  "the RUNNING row's first write has waited out its busy timeout and failed",
                  "the admission is tried again and lands: the task runs once; a stored success; nothing "
                  "counted")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "w")
    ran: list[str] = []

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        ran.append("w")
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(out.case), workers=["w"], timeout=60)
    lock.take()
    with _engine_lines("not admitted yet", on_line=lock.release) as retries:
        try:
            result = await engine.dispatch(task)
        finally:
            lock.close()
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    out.notes.append(f"the admission was tried again {len(retries)} time(s); worker runs: {len(ran)}")
    _expect(out, out.returned["status"] == "success" and out.stored["status"] == "completed",
            f"expected a stored success, got {out.returned} / {out.stored}")
    _expect(out, ran == ["w"], f"the task runs once: {ran}")
    _expect(out, len(retries) >= 1, "the admission was never tried again")
    return out


async def case_sqlite_locked_step_journal(workdir: Path, timing: Timing) -> Outcome:
    """A resumed pipeline's step journal cannot be written."""
    from kazma_core.swarm.task import SwarmTask, TaskType

    out = Outcome("sqlite_locked_step_journal", "a pipeline approved past its checkpoint: the store is "
                  "locked from the first resumed step's start until the next step starts",
                  "both lost journal writes counted (step_journal); the steps run; the ending is stored")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "a", "b", "c")
    ran: list[str] = []

    def stepper(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def run(task: str, context: Any = None) -> dict[str, Any]:
            ran.append(name)
            if name == "b":
                lock.take()
            elif name == "c":
                lock.release()
            return _ok(name, f"from {name}")
        return run

    for name in ("a", "b", "c"):
        engine.get_worker(name).dispatch = stepper(name)
    task = SwarmTask(prompt="p", id=_task_id(out.case), type=TaskType.PIPELINE, workers=["a", "b", "c"],
                     timeout=60, metadata={"hitl_checkpoints": [1]})
    try:
        paused = await engine.dispatch(task)
        _expect(out, paused.status == "paused", f"the pipeline did not pause: {paused.status}")
        result = await engine.approve_checkpoint(task.id)
    finally:
        lock.close()
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    out.notes.append(f"steps run: {ran}")
    _expect(out, ran == ["a", "b", "c"], f"each step runs once: {ran}")
    _expect(out, out.stored["status"] == "completed", f"expected a stored success: {out.stored}")
    return out


async def case_sqlite_locked_finalize_briefly(workdir: Path, timing: Timing) -> Outcome:
    """The ending's first write meets the lock; the lock goes before the retries run out."""
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("sqlite_locked_finalize_briefly", "the store is locked from the worker's start until the "
                  "ending's first write has waited out its busy timeout and failed",
                  "the ending's write is tried again and lands: a stored success (the worker metric's "
                  "write, lost to the same lock, counted worker_metric)")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "w")

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        lock.take()
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(out.case), workers=["w"], timeout=60)
    with _engine_lines("trying again", on_line=lock.release) as retries:
        try:
            result = await engine.dispatch(task)
        finally:
            lock.close()
    out.returned = _returned(result)
    out.notes.append(f"the ending's write was tried again {len(retries)} time(s)")
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    _expect(out, out.returned["status"] == "success" and out.stored["status"] == "completed",
            f"expected a stored success: {out.returned} / {out.stored}")
    _expect(out, len(retries) >= 1, "the ending's write was never tried again")
    return out


async def case_sqlite_locked_finalize(workdir: Path, timing: Timing) -> Outcome:
    """The store stays locked past every attempt of the ending's write."""
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("sqlite_locked_finalize", "the store is locked from the worker's start until after the "
                  "dispatch has returned (past every retry of the ending's write)",
                  "the caller is told failed (unsaved, the result it would have stored kept); counted "
                  "task_write; the row stays RUNNING and the next start holds it as interrupted")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "w")

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        lock.take()
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(out.case), workers=["w"], timeout=60)
    try:
        result = await engine.dispatch(task)
    finally:
        lock.close()
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    history = engine.get_task(task.id)
    out.notes.append(f"engine history: {getattr(history, 'status', None)}")
    _expect(out, out.returned["status"] == "failed" and out.returned.get("unsaved") is True
            and out.returned.get("result_status") == "success",
            f"expected a failed, unsaved report of a success: {out.returned}")
    _expect(out, out.stored["status"] == "running", f"expected the admitted row: {out.stored}")
    _expect(out, out.recovered.get("execution_outcome") == "unknown",
            f"expected the next start to hold it as unknown: {out.recovered}")
    _expect(out, getattr(getattr(history, "result", None), "status", None) == "failed",
            "the engine's own record still says success")
    return out


async def case_sqlite_locked_approval(workdir: Path, timing: Timing) -> Outcome:
    """An approval the store cannot record resumes nothing."""
    from kazma_core.swarm.task import SwarmTask, TaskType

    out = Outcome("sqlite_locked_approval", "a paused pipeline is approved while the store is locked; "
                  "approved again once it is free",
                  "the first approval resumes nothing (admission_unrecorded, the checkpoint stays open, "
                  "counted running_row); the second runs the remaining steps once")
    store = _sqlite_store(workdir, timing)
    lock = _SqliteLock(workdir / TASK_DB)
    engine = _engine(store, "a", "b")
    ran: list[str] = []

    def stepper(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def run(task: str, context: Any = None) -> dict[str, Any]:
            ran.append(name)
            return _ok(name, f"from {name}")
        return run

    for name in ("a", "b"):
        engine.get_worker(name).dispatch = stepper(name)
    task = SwarmTask(prompt="p", id=_task_id(out.case), type=TaskType.PIPELINE, workers=["a", "b"],
                     timeout=60, metadata={"hitl_checkpoints": [1]})
    await engine.dispatch(task)
    lock.take()
    try:
        first = await engine.approve_checkpoint(task.id)
    finally:
        lock.close()
    still_open = engine.get_checkpoint_info(task.id) is not None
    after_first = list(ran)
    second = await engine.approve_checkpoint(task.id)
    out.returned = {"first": _returned(first), **_returned(second)}
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    out.notes.append(f"steps after the first approval: {after_first}; at the end: {ran}")
    _expect(out, (first.metadata or {}).get("admission_unrecorded") is True and still_open,
            f"the first approval should resume nothing and keep the checkpoint: {out.returned['first']}")
    _expect(out, after_first == ["a"], f"nothing ran on the unrecorded approval: {after_first}")
    _expect(out, ran == ["a", "b"] and out.stored["status"] == "completed",
            f"the second approval runs the rest once: {ran} / {out.stored}")
    return out


class _DownSettings:
    """A settings store that is not there (every call fails)."""

    def __getattr__(self, name: str) -> Any:
        def _down(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("settings store unreachable (chaos)")
        return _down


async def case_breaker_store_down(workdir: Path, timing: Timing) -> Outcome:
    """Shared breakers on, the settings store that shares them down."""
    from unittest import mock

    from kazma_core.swarm.reliability import RetryPolicy
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("breaker_store_down", "shared breakers on (KAZMA_SHARED_BREAKERS=1) and every call to "
                  "the settings store fails; admission limit 1",
                  "admission refuses at its limit; a failing worker's breaker trips, refuses and recovers "
                  "through its probe on this process's own state; the lost shares counted "
                  "(breaker_read, breaker_persist, breaker_lease)")
    store = _sqlite_store(workdir, timing)
    engine = _engine(store, "flaky", "slow")
    engine.set_circuit_breaker_config("flaky", failure_threshold=2, cooldown_seconds=0.3)
    engine.set_retry_policy("flaky", RetryPolicy(max_retries=0))
    outage = {"on": True}
    slow_started = asyncio.Event()
    slow_release = asyncio.Event()

    async def flaky(task: str, context: Any = None) -> dict[str, Any]:
        if outage["on"]:
            return {"worker": "flaky", "status": "error", "output": "", "error": "upstream down",
                    "retryable": False}
        return _ok("flaky", "back")

    async def slow(task: str, context: Any = None) -> dict[str, Any]:
        slow_started.set()
        await slow_release.wait()
        return _ok("slow", "done")

    engine.get_worker("flaky").dispatch = flaky
    engine.get_worker("slow").dispatch = slow
    endings: list[tuple[str, str, str]] = []
    with mock.patch("kazma_core.config_store.get_config_store", lambda: _DownSettings()), \
            _env(KAZMA_SHARED_BREAKERS="1", KAZMA_SWARM_MAX_ACTIVE="1"):
        held = asyncio.create_task(engine.dispatch(SwarmTask(prompt="p", id=_task_id("slow"),
                                                             workers=["slow"], timeout=60)))
        await asyncio.wait_for(slow_started.wait(), 10)
        refused = await engine.dispatch(SwarmTask(prompt="p", id=_task_id("refused"), workers=["flaky"],
                                                  timeout=60))
        endings.append(("at capacity", refused.status, (refused.error or "")[:60]))
        slow_release.set()
        await held
        tasks = []
        for label in ("fail 1", "fail 2 (trips)", "refused (open)"):
            t = SwarmTask(prompt="p", id=_task_id("flaky"), workers=["flaky"], timeout=60)
            r = await engine.dispatch(t)
            tasks.append((label, t, r))
        outage["on"] = False
        await asyncio.sleep(0.4)  # past the cooldown: half-open
        t = SwarmTask(prompt="p", id=_task_id("flaky"), workers=["flaky"], timeout=60)
        tasks.append(("probe (recovers)", t, await engine.dispatch(t)))
        t = SwarmTask(prompt="p", id=_task_id("flaky"), workers=["flaky"], timeout=60)
        tasks.append(("after recovery", t, await engine.dispatch(t)))
    for label, t, r in tasks:
        row = await asyncio.to_thread(_read, store, t.id)
        endings.append((label, r.status, row["status"]))
        if r.status in ("success", "partial") and row["status"] != "completed":
            out.problems.append(f"{label}: told {r.status}, stored {row['status']}")
    store.close()
    out.notes.append("endings: " + "; ".join(f"{a}: {b} / {c}" for a, b, c in endings))
    out.returned = {"status": tasks[-1][2].status}
    out.stored = await asyncio.to_thread(_read, _sqlite_store(workdir, timing), tasks[-1][1].id)
    statuses = [e[1] for e in endings]
    _expect(out, statuses == ["failed", "failed", "failed", "failed", "success", "success"],
            f"expected refused, fail, fail, refused, probe ok, ok: {endings}")
    _expect(out, "capacity" in endings[0][2].lower(), f"the first refusal is admission: {endings[0]}")
    _expect(out, "circuit" in (tasks[2][2].error or "").lower(),
            f"the third flaky task is refused by its open breaker: {tasks[2][2].error}")
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _temporal_case(workdir: Path, timing: Timing, *, required: bool) -> Outcome:
    from kazma_core.swarm.task import SwarmTask

    name = "temporal_unreachable_required" if required else "temporal_unreachable"
    expected = ("the task is refused with the reason and finalized: failed in the store (not RUNNING), "
                "the worker never ran, the admission slot free" if required else
                "it falls back in process (a WARNING) and its ending is stored")
    out = Outcome(name, "KAZMA_TEMPORAL=1 with KAZMA_TEMPORAL_HOST on a local port nothing listens on"
                  + (" and KAZMA_TEMPORAL_REQUIRED=1" if required else ""), expected)
    try:
        import temporalio  # noqa: F401
    except ImportError:
        out.skipped = "temporalio is not installed (the durable extra)"
        return out
    store = _sqlite_store(workdir, timing)
    engine = _engine(store, "w")
    ran: list[str] = []

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        ran.append("w")
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(name), workers=["w"], timeout=60)
    with _env(KAZMA_TEMPORAL="1", KAZMA_TEMPORAL_HOST=f"127.0.0.1:{_free_port()}",
              KAZMA_TEMPORAL_REQUIRED="1" if required else None):
        result = await engine.dispatch(task)
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    out.notes.append(f"worker runs: {len(ran)}; in flight after: {len(engine.list_active_tasks())}")
    if required:
        _expect(out, out.returned["status"] == "failed" and "Temporal" in out.returned["error"],
                f"expected a refusal naming Temporal: {out.returned}")
        _expect(out, out.stored["status"] == "failed", f"expected a stored failure: {out.stored}")
        _expect(out, not ran and not engine.list_active_tasks(), "the worker ran, or the task holds a slot")
    else:
        _expect(out, out.returned["status"] == "success" and out.stored["status"] == "completed"
                and ran == ["w"], f"expected an in-process success: {out.returned} / {out.stored}")
    return out


async def case_temporal_unreachable(workdir: Path, timing: Timing) -> Outcome:
    """Temporal configured, nothing there: the in-process fallback."""
    return await _temporal_case(workdir, timing, required=False)


async def case_temporal_unreachable_required(workdir: Path, timing: Timing) -> Outcome:
    """Temporal required, nothing there: a refusal, finalized."""
    return await _temporal_case(workdir, timing, required=True)


async def case_temporal_lost_mid_run(workdir: Path, timing: Timing) -> Outcome:
    """Temporal accepts the workflow, then goes away while the work runs."""
    from kazma_core.swarm import durable
    from kazma_core.swarm.task import SwarmTask

    out = Outcome("temporal_lost_mid_run", "KAZMA_TEMPORAL_REQUIRED=1 against Temporal's time-skipping test "
                  "server, shut down while the task's worker runs; the worker then finishes",
                  "the work goes on in Kazma and its own ending is the record: told success, stored completed")
    try:
        from temporalio.testing import WorkflowEnvironment
    except ImportError:
        out.skipped = "temporalio is not installed (the durable extra)"
        return out
    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # the test server is downloaded on first use
        out.skipped = f"Temporal's time-skipping test server could not start: {type(exc).__name__}: {exc}"
        return out
    store = _sqlite_store(workdir, timing)
    engine = _engine(store, "w")
    started, release = asyncio.Event(), asyncio.Event()

    async def worker(task: str, context: Any = None) -> dict[str, Any]:
        started.set()
        await release.wait()
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_task_id(out.case), workers=["w"], timeout=600)
    t0 = time.monotonic()
    with _env(KAZMA_TEMPORAL="1", KAZMA_TEMPORAL_HOST=env.client.service_client.config.target_host,
              KAZMA_TEMPORAL_REQUIRED="1", KAZMA_TEMPORAL_QUEUE=f"kazma-chaos-{uuid.uuid4().hex[:8]}"):
        try:
            await durable.start_temporal_worker()
            if not durable.worker_running():
                out.skipped = "the Temporal workers did not start"
                return out
            run = asyncio.ensure_future(engine.dispatch(task))
            await asyncio.wait_for(started.wait(), 30)
            await env.shutdown()  # Temporal goes away
            release.set()
            result = await asyncio.wait_for(run, 120)
        finally:
            release.set()
            await durable.stop_temporal_worker(timeout=5.0)
    out.notes.append(f"settled {time.monotonic() - t0:.1f}s after the dispatch")
    out.returned = _returned(result)
    out.stored = await asyncio.to_thread(_read, store, task.id)
    store.close()
    out.recovered = await _recover(lambda: _sqlite_store(workdir, timing), task.id)
    _expect(out, out.returned["status"] == "success" and out.stored["status"] == "completed",
            f"expected a stored success: {out.returned} / {out.stored}")
    return out


async def case_shutdown_mid_patterns(workdir: Path, timing: Timing) -> Outcome:
    """Kazma stops with a pipeline, a fan-out, a consult and a dispatch running."""
    from kazma_core.agent.dispatch_effects import note_effect_invocation
    from kazma_core.swarm.reliability import RetryPolicy
    from kazma_core.swarm.task import SwarmTask, TaskType

    out = Outcome("shutdown_mid_patterns", "stop_all_runs() while a pipeline is in its second step, a "
                  "fan-out has one worker done and two running, a consult has two experts running and "
                  "a dispatch that changed nothing is running",
                  "every row CANCELLED with interrupted_by=shutdown, written before stop_all_runs returns; "
                  "execution_outcome unknown naming file_write where a worker had noted it, none_invoked "
                  "for the dispatch that changed nothing; nothing left for the next start to recover")
    store = _sqlite_store(workdir, timing)
    names = ("p1", "p2", "p3", "f1", "f2", "f3", "c1", "c2", "idle")
    engine = _engine(store, *names)
    for name in names:
        engine.set_retry_policy(name, RetryPolicy(max_retries=0))
    blocked = {n: asyncio.Event() for n in ("p2", "f2", "f3", "c1", "c2", "idle")}

    def worker(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def run(task: str, context: Any = None) -> dict[str, Any]:
            if name in ("p1", "p2", "f2", "c1"):
                note_effect_invocation("file_write")
            if name in blocked:
                blocked[name].set()
                await asyncio.sleep(60)
            return _ok(name, f"from {name}")
        return run

    for name in names:
        engine.get_worker(name).dispatch = worker(name)
    tasks = {
        "pipeline": SwarmTask(prompt="p", id=_task_id("pipeline"), type=TaskType.PIPELINE,
                              workers=["p1", "p2", "p3"], timeout=60),
        "fan_out": SwarmTask(prompt="p", id=_task_id("fanout"), type=TaskType.FAN_OUT,
                             workers=["f1", "f2", "f3"], timeout=60),
        "consult": SwarmTask(prompt="p", id=_task_id("consult"), type=TaskType.CONSULT,
                             workers=["c1", "c2"], timeout=60),
        "dispatch": SwarmTask(prompt="p", id=_task_id("dispatch"), workers=["idle"], timeout=60),
    }
    callers = [asyncio.create_task(engine.dispatch(t)) for t in tasks.values()]
    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in blocked.values())), 10)
    stopped = await engine.stop_all_runs()
    # The rows as they stand the moment the shutdown returns (a process that
    # exits now leaves exactly these).
    at_return = {kind: await asyncio.to_thread(_read, store, t.id) for kind, t in tasks.items()}
    results = await asyncio.wait_for(asyncio.gather(*callers), 15)
    store.close()
    nxt = _sqlite_store(workdir, timing)
    recovered = await asyncio.to_thread(nxt.requeue_orphaned_running)
    nxt.close()
    told = {kind: _returned(r)["status"] for kind, r in zip(tasks, results, strict=True)}
    out.returned = {"status": "cancelled" if set(told.values()) == {"cancelled"} else "mixed", **told}
    out.stored = {"status": "cancelled" if all(r["status"] == "cancelled" for r in at_return.values())
                  else "mixed", **{kind: {k: row[k] for k in ("status", "interrupted_by", "execution_outcome",
                                                               "invoked_tools")}
                                   for kind, row in at_return.items()}}
    out.recovered = {"recovered_rows": len(recovered.get("failed", []))}
    out.notes.append(f"stop_all_runs stopped {stopped}")
    _expect(out, stopped == len(tasks), f"stopped {stopped} of {len(tasks)}")
    _expect(out, out.returned["status"] == "cancelled", f"every caller is told cancelled: {told}")
    for kind, row in at_return.items():
        _expect(out, row["status"] == "cancelled" and row["interrupted_by"] == "shutdown",
                f"{kind}: not written as a shutdown before stop_all_runs returned: {row}")
        changed = kind != "dispatch"
        _expect(out, row["execution_outcome"] == ("unknown" if changed else "none_invoked"),
                f"{kind}: execution_outcome {row['execution_outcome']}")
        if changed:
            _expect(out, "file_write" in (row["invoked_tools"] or []), f"{kind}: invoked_tools {row}")
    _expect(out, not recovered.get("failed"), f"the next start found rows still running: {recovered}")
    return out


# ── Postgres ───────────────────────────────────────────────────────────────


class _PgDatabase:
    """A database of the case's own on a disposable server; *down* refuses
    its connections and ends its sessions, *up* lets them back."""

    def __init__(self, admin_dsn: str) -> None:
        import psycopg

        self._psycopg = psycopg
        self.admin_dsn = admin_dsn
        self.name = f"kazma_sq04_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.name}"')
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        params = conninfo_to_dict(admin_dsn)
        params["dbname"] = self.name
        self.dsn = make_conninfo(**params)

    def _admin(self, *statements: tuple[str, tuple[Any, ...]]) -> None:
        with self._psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            for sql, params in statements:
                conn.execute(sql, params)

    def down(self) -> None:
        self._admin(
            (f'ALTER DATABASE "{self.name}" ALLOW_CONNECTIONS false', ()),
            ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
             "WHERE datname = %s AND pid <> pg_backend_pid()", (self.name,)),
        )

    def up(self) -> None:
        self._admin((f'ALTER DATABASE "{self.name}" ALLOW_CONNECTIONS true', ()))

    def drop(self) -> None:
        with contextlib.suppress(Exception):
            self.up()
        self._admin(
            ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
             "WHERE datname = %s AND pid <> pg_backend_pid()", (self.name,)),
            (f'DROP DATABASE IF EXISTS "{self.name}"', ()),
        )


@contextlib.contextmanager
def _postgres_backend(db: _PgDatabase, timing: Timing) -> Iterator[None]:
    from kazma_core.db.postgres_pool import reset_postgres_pool

    reset_postgres_pool()
    try:
        with _env(KAZMA_DB_BACKEND="postgres", KAZMA_DATABASE_URL=db.dsn, DATABASE_URL=None,
                  KAZMA_PG_POOL_TIMEOUT=str(timing.pg_pool_timeout), KAZMA_PG_POOL_RETRIES="1"):
            yield
    finally:
        reset_postgres_pool()


async def _postgres_case(workdir: Path, timing: Timing, dsn: str | None, *, at: str) -> Outcome:
    from kazma_core.swarm.task import SwarmTask
    from kazma_core.swarm.task_store import TaskStore

    name = f"postgres_down_at_{at}"
    if at == "admission":
        fault = ("the Postgres task store's database refuses connections (its sessions ended) from "
                 "before the dispatch until the RUNNING row's first write has failed")
        expected = ("the admission is tried again and lands once the database is back: the task runs "
                    "once; a stored success")
    else:
        fault = ("the Postgres task store's database refuses connections from the worker's start until "
                 "after the dispatch has returned")
        expected = ("the caller is told failed (unsaved); counted task_write; the row stays RUNNING and the "
                    "next start holds it as interrupted")
    out = Outcome(name, fault, expected)
    if not dsn:
        out.skipped = "no disposable Postgres server given (--postgres-dsn)"
        return out
    db = await asyncio.to_thread(_PgDatabase, dsn)
    try:
        with _postgres_backend(db, timing):
            store = await asyncio.to_thread(TaskStore)
            if not store._pg:
                out.problems.append("the task store did not take the Postgres backend")
                return out
            engine = _engine(store, "w")

            ran: list[str] = []

            async def worker(task: str, context: Any = None) -> dict[str, Any]:
                ran.append("w")
                if at != "admission":
                    await asyncio.to_thread(db.down)
                return _ok("w", "done")

            engine.get_worker("w").dispatch = worker
            task = SwarmTask(prompt="p", id=_task_id(name), workers=["w"], timeout=120)
            if at == "admission":
                await asyncio.to_thread(db.down)
            # At admission the database comes back when the first attempt has
            # failed (the retry's log line, in the writing thread).
            with _engine_lines("not admitted yet", on_line=db.up if at == "admission" else None) as retries:
                try:
                    result = await engine.dispatch(task)
                finally:
                    await asyncio.to_thread(db.up)
            out.notes.append(f"the admission was tried again {len(retries)} time(s); worker runs: {len(ran)}")
            out.returned = _returned(result)
            out.stored = await _read_when_back(store, task.id)
            await asyncio.to_thread(store.requeue_orphaned_running)
            out.recovered = await _read_when_back(store, task.id)
    finally:
        await asyncio.to_thread(db.drop)
    if at == "admission":
        _expect(out, out.returned["status"] == "success" and out.stored["status"] == "completed",
                f"expected a stored success: {out.returned} / {out.stored}")
        _expect(out, ran == ["w"] and len(retries) >= 1, f"one run after a retried admission: {ran}, {retries}")
    else:
        _expect(out, out.returned["status"] == "failed" and out.returned.get("unsaved") is True,
                f"expected a failed, unsaved report: {out.returned}")
        _expect(out, out.stored["status"] == "running", f"expected the admitted row: {out.stored}")
        _expect(out, out.recovered.get("execution_outcome") == "unknown",
                f"expected the next start to hold it as unknown: {out.recovered}")
    return out


async def case_postgres_down_at_admission(workdir: Path, timing: Timing, dsn: str | None = None) -> Outcome:
    """The Postgres task store is down when the task is admitted."""
    return await _postgres_case(workdir, timing, dsn, at="admission")


async def case_postgres_down_at_finalize(workdir: Path, timing: Timing, dsn: str | None = None) -> Outcome:
    """The Postgres task store goes down while the task runs and stays down."""
    return await _postgres_case(workdir, timing, dsn, at="finalize")


CASES: dict[str, Callable[..., Awaitable[Outcome]]] = {
    "sqlite_locked_admission": case_sqlite_locked_admission,
    "sqlite_locked_admission_briefly": case_sqlite_locked_admission_briefly,
    "sqlite_locked_step_journal": case_sqlite_locked_step_journal,
    "sqlite_locked_finalize_briefly": case_sqlite_locked_finalize_briefly,
    "sqlite_locked_finalize": case_sqlite_locked_finalize,
    "sqlite_locked_approval": case_sqlite_locked_approval,
    "breaker_store_down": case_breaker_store_down,
    "temporal_unreachable": case_temporal_unreachable,
    "temporal_unreachable_required": case_temporal_unreachable_required,
    "temporal_lost_mid_run": case_temporal_lost_mid_run,
    "shutdown_mid_patterns": case_shutdown_mid_patterns,
    "postgres_down_at_admission": case_postgres_down_at_admission,
    "postgres_down_at_finalize": case_postgres_down_at_finalize,
}
POSTGRES_CASES = frozenset({"postgres_down_at_admission", "postgres_down_at_finalize"})


async def run_case(name: str, workdir: Path, *, timing: Timing | None = None,
                   dsn: str | None = None) -> Outcome:
    """Run one case in *workdir* (its own subdirectory) and check its ending."""
    timing = timing or Timing()
    case_dir = Path(workdir) / name
    case_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    before = _failures()
    with _inert(timing):
        if name in POSTGRES_CASES:
            out = await CASES[name](case_dir, timing, dsn)
        else:
            out = await CASES[name](case_dir, timing)
    out.counted = _delta(before, _failures())
    out.seconds = round(time.monotonic() - started, 2)
    if not out.skipped:
        out.problems = _ending_problems(out) + out.problems
    return out


def markdown(outcomes: list[Outcome]) -> str:
    """The outcome table (the tracker's SQ04 row holds the same)."""
    lines = ["| Case | Fault | Caller told | Store holds (next start) | Counted | Honest |",
             "|---|---|---|---|---|---|"]
    for o in outcomes:
        if o.skipped:
            lines.append(f"| `{o.case}` | {o.fault} | skipped: {o.skipped} | | | |")
            continue
        told = o.returned.get("status")
        if o.returned.get("unsaved"):
            told = f"{told} (unsaved; ran {o.returned.get('result_status')})"
        held = o.stored.get("status")
        nxt = o.recovered.get("status") if isinstance(o.recovered, dict) else None
        counted = ", ".join(f"{k} {int(v)}" for k, v in sorted((o.counted or {}).items())) or "nothing"
        lines.append(f"| `{o.case}` | {o.fault} | {told} | {held} ({nxt or '-'}) | {counted} | "
                     f"{'yes' if o.honest else 'NO: ' + '; '.join(o.problems)} |")
    return "\n".join(lines) + "\n"


async def _main_async(args: argparse.Namespace, workdir: Path) -> list[Outcome]:
    names = [args.case] if args.case else list(CASES)
    outcomes = []
    for name in names:
        print(f"[chaos] {name} ...", flush=True)
        out = await run_case(name, workdir, dsn=args.postgres_dsn)
        state = "skipped" if out.skipped else ("honest" if out.honest else "NOT HONEST")
        print(f"[chaos] {name}: {state} in {out.seconds}s {'; '.join(out.problems)}", flush=True)
        outcomes.append(out)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--case", choices=sorted(CASES), default=None, help="one case only")
    parser.add_argument("--postgres-dsn", default=None,
                        help="a DISPOSABLE Postgres server (a database is created there and dropped)")
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None, help="write the outcomes as JSON")
    parser.add_argument("--markdown", default=None, help="write the outcome table")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="swarm-chaos-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    with _env(KAZMA_DATA_DIR=str(data_dir), KAZMA_USER_HOME=str(data_dir / "home"),
              KAZMA_SKILLS_HOME=str(data_dir / "home"), KAZMA_DB_BACKEND="sqlite",
              KAZMA_DATABASE_URL=None, DATABASE_URL=None, KAZMA_OPS_ALERTS="0",
              KAZMA_PROCESS_PRIORITY="keep", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"):
        outcomes = asyncio.run(_main_async(args, data_dir))
    table = markdown(outcomes)
    print(table)
    healthy = all(o.honest or o.skipped for o in outcomes)
    report = finished({
        "updated_at": utc_stamp(),
        "data_dir": str(data_dir),
        "outcomes": [{**asdict(o), "honest": o.honest} for o in outcomes],
    }, healthy)
    if args.report:
        _write_report(args.report, report)
    if args.markdown:
        Path(args.markdown).write_text(table, encoding="utf-8")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
