#!/usr/bin/env python3
"""Concurrency soak of the swarm engine: admission, breakers and the autoscaler.

The Swarm hardening tracker's row SQ01 (``docs/plans/
SWARM_INDUSTRIAL_HARDENING_GOAL.md``) asks for a measured run under hundreds
of mixed tasks; this is the harness for it, not the result. It drives a real
``SwarmEngine`` -- its ``TaskStore`` (a private SQLite file), its
``ReliabilityRegistry`` and admission limit, and an ``AutoScaler`` loaded from
a templates file -- in a PRIVATE data directory (a temporary one unless
``--data-dir`` names one; never an install's, never a database server, no
network), on the event loop the server uses, for ``--duration`` seconds:

* tasks at ``--rate`` a second, and ``--burst-size`` at once every
  ``--burst-every`` seconds (so the admission limit is reached at the
  product's default limit), mixed by ``--mix``: work that succeeds (some of
  it noting a tool that changes things, as ``file_write`` would); a worker that
  fails during scripted outages, so its breaker trips, refuses, and recovers
  through the half-open probe; work past the engine's deadline; work the
  watchdog reaps (its start is moved back past its deadline, as the area's
  tests do, so the reap comes within a short run); work cancelled mid-run, and
  work that ignores the cancel (its late result must stay evidence, never the
  task's result); ``auto`` tasks the autoscaler serves from templates (it
  spawns up to a template's bound and reaps idle instances); fan-outs;
  pipelines; and re-submissions of earlier ids, which must not run twice;
* the workers are scripted: no model, no network, no tool. No danger tool and
  no bus approval is involved (that is not SQ01). Semantic routing is off (no
  embedding model in a soak): ``auto`` routes by keywords, as on an install
  without the model. A worker's result is not written to memory (the memory
  soak covers memory writes) and the pipeline Refiner makes no model call;
* every ``--restart-every`` seconds the engine stops as Kazma's shutdown stops
  it (``stop_all_runs``, then ``stop_all``) and a new engine starts on the
  same task store file as Kazma's boot starts one (paused tasks restored,
  orphan recovery). A restart that is due waits, still submitting, up to five
  seconds for a running task, so there is something to interrupt. A new
  autoscaler boot tag is drawn, as a new process draws one;
* a loop-lag probe (the drift of a 100 ms sleep) and the product's loop-stall
  watchdog (``observability/loop_stall.py``, dumping into the private data
  directory after ``--stall-threshold`` seconds) watch the event loop. The
  harness's own store reads, report writes and process samples run in
  threads, so they do not add to the lag they measure.

Checked from the task store (the ground truth) every ``--cycle-seconds``,
after every restart and at the end; the run stops at the first violation:

* every submitted task reaches exactly one status of ``TERMINAL_STATUSES``
  and never leaves it, or was refused at admission and has no row. A trigger
  the harness adds to its private task store logs every change of a row's
  status or generation in the write's own transaction; the checks read that
  whole history (no change after a terminal status, no write of an older
  generation applied), and compare each row with the status its task
  settled at. The result its caller got is the stored one; no row was never
  submitted; nothing ran more often than its workers allow;
* an interrupted task records ``interrupted_by`` and ``execution_outcome``
  (``unknown``, naming ``file_write``, when its worker had noted that write);
* the in-flight count never exceeds the admission limit: sampled from the
  engine every 100 ms and at every worker start, and computed from the stored
  start and end of every task (the watchdog tasks' real starts are kept);
* no breaker outcome from an older generation is applied, and no refresh
  from the shared breaker store puts back an older state (a breaker subclass
  observes every outcome; the registry's ``stale_outcomes`` are counted);
* the autoscaler never exceeds a template's bound and never reaps an
  instance with a dispatch in flight (an autoscaler subclass observes);
* after each restart's drain and after the final drain nothing is running:
  no row, no run, no admitted task, no worker with a dispatch in flight.

The report (``--report``, JSON) is rewritten whole every cycle
(``"status": "running"``), then once more with ``passed`` or ``failed``. It
holds the tasks submitted and finished per status and per kind, throughput,
caller latency, admission refusals and the highest in-flight count against
the limit, breaker trips, half-open probes, recoveries, reopenings and stale
outcomes, autoscaler spawns, reaps and the most instances at once, restarts
and what they interrupted, loop lag (p50/p95/p99/max) and stall dumps, peak
memory, handles and threads, the swarm failure counters, the warnings logged,
and every violation. Exit 0 when healthy, else 1. At the end the settled
statuses are written to ``swarm_soak_ledger.json`` in the data directory, so
:func:`check_store` can check the stores again afterwards.

Usage:
  python scripts/swarm_soak.py --duration 28800 --report swarm-soak.json
  python scripts/swarm_soak.py --duration 180 --rate 10 --restart-every 45 --report soak.json
"""

from __future__ import annotations

import argparse
import array
import asyncio
import collections
import contextlib
import json
import logging
import logging.handlers
import os
import random
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
# The shared report helpers sit beside this file; a runner that does not put
# the script's folder on the path (runpy, a priority wrapper) still finds them.
sys.path.insert(0, str(_HERE))

from soak_report import RUNNING, finished, latency_summary, process_tree_sample, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
for _pkg in ("kazma-cli", "kazma-ui", "kazma-tui", "kazma-skills", "kazma-gateway", "kazma-core"):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)  # this checkout's packages, whatever is installed

#: Written into the data directory at the end, for :func:`check_store`.
LEDGER_FILE = "swarm_soak_ledger.json"
TASK_DB = "swarm_tasks.db"

_KINDS = ("ok", "writer", "flaky", "slow", "stale", "cancel", "stubborn", "auto", "fanout", "pipeline")
_DEFAULT_MIX = "ok=40,writer=6,flaky=15,slow=6,stale=3,cancel=6,stubborn=1,auto=15,fanout=5,pipeline=3"
_STEADY = ("steady-1", "steady-2", "steady-3")
_WORKERS = (*_STEADY, "flaky", "longrun")
#: Words an ``auto`` task asks with: two templates' words, and words no
#: template has (the catch-all template takes those).
_AUTO_WORDS = (
    "please debug this python function",
    "draft a short essay outline",
    "what will the weather be tomorrow",
)
_DIRECTIVE = re.compile(r"soak:([a-z]+):(\S+)")
#: Result metadata that says the engine did not run the task.
_REFUSALS = ("admission_denied", "input_refused", "duplicate_admission")
_ENGINE_GRACE_S = 10.0  # SwarmEngine's _STOP_GRACE_S: how long a stop waits for the work


def _templates(pool_max: int) -> list[dict[str, Any]]:
    return [
        {"name": "soak-coder", "capabilities": {"expertise": ["python", "debug"]}, "max_instances": 2},
        {"name": "soak-writer", "capabilities": {"expertise": ["essay", "draft"]}, "max_instances": 2},
        {"name": "soak-general", "capabilities": {"expertise": ["explain"]}, "catch_all": True,
         "max_instances": pool_max},
    ]


# ── the checks that read only the stores (used after the run too) ─────────


def _terminal() -> frozenset[str]:
    from kazma_core.swarm.task import TERMINAL_STATUSES

    return frozenset(s.value for s in TERMINAL_STATUSES)


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA query_only = 1")
    return conn


def _status_rows(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None, str | None]]:
    """id -> (status, started_at, completed_at) of every task row."""
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT id, status, started_at, completed_at FROM swarm_tasks")}


def _full_rows(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The rows of *ids*, with their result and metadata read."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT id, status, generation, started_at, completed_at, result, metadata "  # noqa: S608
                f"FROM swarm_tasks WHERE id IN ({marks})", chunk):
            out[row[0]] = {
                "status": row[1], "generation": row[2], "started_at": row[3], "completed_at": row[4],
                "result": json.loads(row[5]) if row[5] else None,
                "metadata": json.loads(row[6]) if row[6] else {},
            }
    return out


#: Every status a task row takes, logged by the store itself in the write's
#: own transaction: the history the checks read. Added to the soak's private
#: task store only (the engine's guarded upsert fires the update trigger).
_STATUS_LOG_DDL = """
CREATE TABLE IF NOT EXISTS soak_status_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    generation INTEGER,
    at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE TRIGGER IF NOT EXISTS soak_status_insert AFTER INSERT ON swarm_tasks BEGIN
    INSERT INTO soak_status_log (id, old_status, new_status, generation)
    VALUES (NEW.id, NULL, NEW.status, NEW.generation);
END;
CREATE TRIGGER IF NOT EXISTS soak_status_update AFTER UPDATE ON swarm_tasks
WHEN OLD.status IS NOT NEW.status OR OLD.generation IS NOT NEW.generation BEGIN
    INSERT INTO soak_status_log (id, old_status, new_status, generation)
    VALUES (NEW.id, OLD.status, NEW.status, NEW.generation);
END;
"""


def _install_status_log(db: Path) -> None:
    conn = sqlite3.connect(str(db), timeout=30.0)
    try:
        conn.executescript(_STATUS_LOG_DDL)
    finally:
        conn.close()


def _transitions(conn: sqlite3.Connection, after: int) -> list[tuple[int, str, str | None, str, int]]:
    """(seq, id, old status, new status, generation) logged after *after*."""
    return conn.execute(
        "SELECT seq, id, old_status, new_status, generation FROM soak_status_log WHERE seq > ? ORDER BY seq",
        (after,)).fetchall()


def history_problems(transitions: list[tuple[int, str, str | None, str, int]],
                     seen: dict[str, tuple[str | None, int]]) -> list[str]:
    """Each logged status change against what the task's history allows: no
    change once a status of ``TERMINAL_STATUSES`` is reached, and no write
    from an older generation than the row's. *seen* (id -> the terminal
    status reached, or None; the newest generation) carries the history from
    one call to the next."""
    terminal = _terminal()
    problems: list[str] = []
    for _, tid, old, new, generation in transitions:
        reached, newest = seen.get(tid, (None, 0))
        generation = int(generation or 0)
        if reached is not None:
            problems.append(f"{tid} left its terminal status {reached} for {new} (was {old})")
        if generation < newest:
            problems.append(f"{tid}: a write of generation {generation} applied over generation {newest}")
        seen[tid] = (reached or (new if new in terminal else None), max(newest, generation))
    return problems


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def peak_in_flight(intervals: list[tuple[str, str | None]], *, since: float | None = None,
                   now: float | None = None) -> int:
    """The most tasks running at once, from their stored starts and ends.

    An ending at the same instant as a start comes first: the engine frees
    the slot before it admits the next task. With *since*, only the moments
    from *since* on count (an interval begun earlier counts from there)."""
    now = time.time() if now is None else now
    events: list[tuple[float, int]] = []
    for started, completed in intervals:
        if not started:
            continue
        s = _epoch(started)
        c = max(s, _epoch(completed) if completed else now)
        if since is not None:
            if c < since:
                continue
            s = max(s, since)
        events.append((s, 1))
        events.append((c, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _, step in events:
        current += step
        peak = max(peak, current)
    return peak


def in_flight_problems(samples: list[int], limit: int) -> list[str]:
    """A sampled in-flight count above the admission limit, if any."""
    over = [n for n in samples if n > limit]
    return [f"in flight: {max(over)} tasks admitted at once, above the limit of {limit}"] if over else []


def settled_mismatches(rows: dict[str, str], settled: dict[str, str]) -> tuple[list[str], list[str]]:
    """Each settled task's row against the status it settled at.

    *rows*: id -> status now. *settled*: id -> the terminal status recorded
    when the task settled (``refused``: refused at admission, no row).
    Returns the problems, and the ids of rows no settled task names."""
    problems: list[str] = []
    unknown: list[str] = []
    for tid, status in rows.items():
        want = settled.get(tid)
        if want is None:
            unknown.append(tid)
        elif status != want:
            problems.append(f"{tid} left its recorded status {want} for {status}")
    for tid, want in settled.items():
        if want == "refused":
            if tid in rows:
                problems.append(f"{tid} was refused at admission and has a row ({rows[tid]})")
        elif tid not in rows:
            problems.append(f"{tid} ({want}) has no row any more")
    return problems, unknown


def compare_with_settled(rows: dict[str, str], settled: dict[str, str], *,
                         open_ids: frozenset[str] = frozenset()) -> list[str]:
    """:func:`settled_mismatches` after the run: a row no task settled at is
    a task still open (and then it must be finished) or never submitted."""
    terminal = _terminal()
    problems, unknown = settled_mismatches(rows, settled)
    for tid in unknown:
        if tid not in open_ids:
            problems.append(f"row {tid} ({rows[tid]}) was never submitted")
        elif rows[tid] not in terminal:
            problems.append(f"{tid} is still {rows[tid]} after the drain")
    return problems


def check_store(data_dir: str | os.PathLike[str]) -> list[str]:
    """Check a finished run's stores again: every task's whole status history,
    every row against the status its task settled at, every row terminal, and
    the in-flight count from the stored starts and ends against the limit.
    Returns the violations."""
    data = Path(data_dir)
    ledger = json.loads((data / LEDGER_FILE).read_text(encoding="utf-8"))
    conn = _connect_ro(data / TASK_DB)
    try:
        rows = _status_rows(conn)
        history = _transitions(conn, 0)
    finally:
        conn.close()
    problems = history_problems(history, {})
    problems += compare_with_settled({k: v[0] for k, v in rows.items()}, ledger["settled"],
                                     open_ids=frozenset(ledger.get("open") or ()))
    real = ledger.get("real_starts") or {}
    peak = peak_in_flight([(real.get(k) or v[1], v[2]) for k, v in rows.items()])
    if peak > int(ledger["limit"]):
        problems.append(f"in flight: {peak} tasks running at once in the store, above the limit "
                        f"of {ledger['limit']}")
    return problems


# ── the plan ──────────────────────────────────────────────────────────────


@dataclass
class _Spec:
    id: str
    kind: str
    task_type: str
    workers: list[str]
    prompt: str
    timeout: float
    fallback: list[str]
    work_s: float

    @property
    def max_exec(self) -> int:
        """Worker runs the task may make: one per worker, and a fallback."""
        return len(self.workers) + len(self.fallback)


class _Planner:
    """Deterministic for a seed: the same kinds, ids and work in the same order."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.rng = random.Random(args.seed)
        self.args = args
        self.mix = _parse_mix(args.mix)
        self.recent: collections.deque[_Spec] = collections.deque(maxlen=100)
        self.seq = 0

    def next(self, open_ids: Any) -> tuple[_Spec, bool]:
        rng = self.rng
        again = [s for s in self.recent if s.id in open_ids]
        if again and rng.random() < self.args.duplicate_rate:
            return rng.choice(again), True
        self.seq += 1
        kinds = [k for k, w in self.mix.items() if w > 0]
        kind = rng.choices(kinds, weights=[self.mix[k] for k in kinds])[0]
        tid = f"soak-{self.seq:07d}-{rng.getrandbits(32):08x}"
        work = rng.uniform(self.args.work_min_ms, self.args.work_max_ms) / 1000.0
        task_type, workers, fallback, timeout, words = "dispatch", [], [], 30.0, "steady work"
        if kind in ("ok", "writer"):
            workers = [rng.choice(_STEADY)]
        elif kind == "flaky":
            workers = ["flaky"]
            if rng.random() < 0.3:
                fallback = ["steady-1"]
        elif kind in ("slow", "stale", "cancel", "stubborn"):
            workers = ["longrun"]
            timeout = self.args.slow_timeout if kind == "slow" else 60.0
        elif kind == "auto":
            workers = ["auto"]
            words = rng.choice(_AUTO_WORDS)
        elif kind == "fanout":
            task_type, workers = "fan_out", list(_STEADY)
        elif kind == "pipeline":
            task_type, workers = "pipeline", ["steady-1", "steady-2"]
        spec = _Spec(id=tid, kind=kind, task_type=task_type, workers=workers,
                     prompt=f"soak:{kind}:{tid} {words}", timeout=timeout, fallback=fallback, work_s=work)
        self.recent.append(spec)
        return spec, False


def _parse_mix(text: str) -> dict[str, float]:
    mix = dict.fromkeys(_KINDS, 0.0)
    for part in (text or "").split(","):
        if not part.strip():
            continue
        name, _, weight = part.partition("=")
        name = name.strip()
        if name not in mix:
            raise SystemExit(f"--mix: unknown kind {name!r} (kinds: {', '.join(_KINDS)})")
        mix[name] = float(weight)
    if not any(mix.values()):
        raise SystemExit("--mix: every weight is 0")
    return mix


class _Rec:
    """One submitted task id, until it settles."""

    __slots__ = ("spec", "submissions", "pending", "outcomes", "body_entries", "effect",
                 "real_started_at", "started", "stale_checks")

    def __init__(self, spec: _Spec) -> None:
        self.spec = spec
        self.submissions = 0
        self.pending = 0
        self.outcomes: list[str] = []
        self.body_entries = 0
        self.effect = False
        self.real_started_at: str | None = None
        self.started = False
        self.stale_checks = 0


# ── logging: to a private file, and counted ───────────────────────────────


class _CountingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.by_level: collections.Counter[str] = collections.Counter()
        self.by_message: collections.Counter[str] = collections.Counter()

    def emit(self, record: logging.LogRecord) -> None:
        self.by_level[record.levelname] += 1
        self.by_message[f"{record.levelname} {record.name}: {str(record.msg)[:110]}"] += 1


def _setup_logging(data_dir: Path) -> _CountingHandler:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    file_handler = logging.handlers.RotatingFileHandler(
        data_dir / "swarm_soak.log", maxBytes=10 * 2**20, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    counter = _CountingHandler()
    root.addHandler(file_handler)
    root.addHandler(counter)
    return counter


def _private_environment(data_dir: Path, *, shared_breakers: bool) -> None:
    """Nothing of an install: no KAZMA_* setting survives, the stores are the
    private data directory's, SQLite only, no model download."""
    for name in [n for n in os.environ if n.startswith("KAZMA_")] + ["DATABASE_URL"]:
        os.environ.pop(name, None)
    (data_dir / "home").mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "KAZMA_DATA_DIR": str(data_dir),
        "KAZMA_USER_HOME": str(data_dir / "home"),  # stall dumps land here
        "KAZMA_SKILLS_HOME": str(data_dir / "home"),
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_PROCESS_PRIORITY": "keep",
        "KAZMA_TEMPORAL": "0",
        "KAZMA_GATE_REGISTRY": "0",
        "KAZMA_SELF_IMPROVEMENT": "0",
        # On by default: an install in production or with platform users
        # shares its breakers through the settings store.
        "KAZMA_SHARED_BREAKERS": "1" if shared_breakers else "0",
        "KAZMA_LOOP_STALL_WATCHDOG": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONIOENCODING": "utf-8",
    })


def _swarm_failures() -> dict[str, float] | None:
    """``kazma_swarm_failures_total`` by site; None without prometheus-client
    (the counter does not exist then, which is not zero failures)."""
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


# ── the soak ──────────────────────────────────────────────────────────────


class _Soak:
    def __init__(self, args: argparse.Namespace, data_dir: Path, log_counter: _CountingHandler) -> None:
        self.args = args
        self.data_dir = data_dir
        self.db_path = data_dir / TASK_DB
        self.log_counter = log_counter
        self.limit = int(args.max_active)
        self.planner = _Planner(args)
        self.recs: dict[str, _Rec] = {}
        self.settled: dict[str, str] = {}
        self.real_starts: dict[str, str] = {}  # the watchdog tasks' starts, before they were moved
        self.violations: list[str] = []
        self.engine: Any = None
        self.store: Any = None
        self.scaler: Any = None
        self.epoch = 0
        self.stopping = False
        self.classes: dict[str, Any] = {}
        self._dispatches: dict[asyncio.Task[Any], int] = {}  # dispatch task -> its epoch
        self._helpers: set[asyncio.Task[Any]] = set()
        self._started_events: dict[str, asyncio.Event] = {}
        self._reader: sqlite3.Connection | None = None
        self._last_rowid = 0
        self._last_seq = 0
        # id -> (terminal status reached, newest generation): read only by the
        # check's thread, one check at a time.
        self._history: dict[str, tuple[str | None, int]] = {}
        self._sweep_since: float | None = None
        self._note_effect: Any = None  # set with the instrumented classes
        self._autoscaler_mod: Any = None
        self.started_wall = time.time()
        self.started = time.monotonic()
        # Measurements.
        self.lags = array.array("d")
        self.idle_lags = array.array("d")
        self.latencies = array.array("d")
        self.restart_seconds: list[float] = []
        self.peak_sampled = 0
        self.peak_store = 0
        self.resource_peak: dict[str, float] = {"rss_mib": 0.0, "handles": 0, "threads": 0}
        self.resource_samples: list[dict[str, Any]] = []
        self.counts: collections.Counter[str] = collections.Counter()
        self.bodies: collections.Counter[str] = collections.Counter()
        self.by_kind: dict[str, dict[str, Any]] = {
            k: {"submitted": 0, "settled": collections.Counter()} for k in _KINDS}
        self.endings: collections.Counter[str] = collections.Counter()
        self.execution_outcomes: collections.Counter[str] = collections.Counter()
        self.breakers: collections.Counter[str] = collections.Counter()
        self.breaker_by_worker: dict[str, collections.Counter[str]] = {}
        self.scaler_stats: collections.Counter[str] = collections.Counter()
        self.scaler_most: dict[str, int] = {}
        self.restart_stats: collections.Counter[str] = collections.Counter()
        self._breaker_lock = threading.Lock()  # outcomes are recorded in threads

    # ── instrumentation ──────────────────────────────────────────────────

    def violation(self, text: str) -> None:
        if len(self.violations) < 200:
            self.violations.append(text)

    def instrument(self, stack: contextlib.ExitStack) -> None:
        """Scripted workers and observing subclasses; the model-bound side
        paths made inert. Every patch is undone when *stack* closes."""
        from unittest import mock

        import kazma_core.memory.embedder as embedder
        import kazma_core.swarm.autoscaler as autoscaler_mod
        import kazma_core.swarm.patterns as patterns
        import kazma_core.swarm.reliability_registry as registry_mod
        import kazma_core.swarm.worker_dispatch as worker_dispatch
        from kazma_core.swarm.semantic_router import get_semantic_router

        self.classes = _instrumented_classes(self)

        async def _no_memory_index(**_kw: Any) -> None:
            return None

        async def _no_refiner(*_a: Any, **_kw: Any) -> str:
            return "refined (soak: no model)"

        async def _no_self_improvement(*_a: Any, **_kw: Any) -> None:
            return None

        stack.enter_context(mock.patch.object(worker_dispatch, "_index_worker_l4_memory", _no_memory_index))
        stack.enter_context(mock.patch.object(patterns, "_synthesize_refined_output", _no_refiner))
        stack.enter_context(mock.patch.object(patterns, "_run_self_improvement", _no_self_improvement))
        stack.enter_context(mock.patch.object(registry_mod, "CircuitBreaker", self.classes["breaker"]))
        stack.enter_context(mock.patch.object(embedder, "get_embedder", lambda: None))
        router = get_semantic_router()
        stack.enter_context(mock.patch.object(router, "_initialized", True))
        stack.enter_context(mock.patch.object(router, "_available", False))
        stack.enter_context(mock.patch.object(autoscaler_mod, "_BOOT_TAG", autoscaler_mod._BOOT_TAG))
        self._autoscaler_mod = autoscaler_mod

    def breaker_event(self, name: str | None, event: str) -> None:
        with self._breaker_lock:
            self.breakers[event] += 1
            key = (name or "?").split("-pool-")[0]
            self.breaker_by_worker.setdefault(key, collections.Counter())[event] += 1

    def scaler_spawned(self, template: Any, of_template: int, total: int) -> None:
        self.scaler_stats["spawns"] += 1
        name = template.name
        self.scaler_most[name] = max(self.scaler_most.get(name, 0), of_template)
        self.scaler_stats["most_instances"] = max(self.scaler_stats["most_instances"], total)
        if of_template > template.max_instances:
            self.violation(f"autoscaler: {of_template} instances of {name}, above its bound of "
                           f"{template.max_instances}")

    def scaler_reaping(self, name: str, worker: Any) -> None:
        self.scaler_stats["reaps"] += 1
        if worker is not None and int(getattr(worker, "in_flight", 0) or 0) > 0:
            self.violation(f"autoscaler: reaped {name} with {worker.in_flight} dispatch(es) in flight")

    # ── the scripted worker ──────────────────────────────────────────────

    async def body(self, worker: Any, prompt: str, context: Any) -> dict[str, Any]:
        """What a worker does for a soak task, by the task's kind."""
        match = _DIRECTIVE.search(prompt or "")
        kind, tid = (match.group(1), match.group(2)) if match else ("ok", "")
        rec = self.recs.get(tid)
        work = rec.spec.work_s if rec is not None else 0.05
        self.bodies[kind] += 1
        if rec is not None:
            rec.body_entries += 1
            if not rec.started:
                rec.started = True
                event = self._started_events.get(tid)
                if event is not None:
                    event.set()
        self._sample_in_flight()
        ok = {"worker": worker.name, "task_id": "", "status": "success", "output": f"done {tid}",
              "error": None}
        if kind == "flaky":
            await asyncio.sleep(work)
            if worker.name == "flaky" and self._outage():  # its fallback worker is healthy
                return {"worker": worker.name, "task_id": "", "status": "error", "output": "",
                        "error": "soak: scripted outage", "retryable": False}
            return ok
        if kind == "writer":
            self._note_effect("file_write")  # a tool that changes things ran
            if rec is not None:
                rec.effect = True
            await asyncio.sleep(work)
            return ok
        if kind == "slow":
            await asyncio.sleep(float(rec.spec.timeout if rec else 1.0) + 60.0)  # the deadline ends it
            return ok
        if kind == "stale":
            task = self.engine.get_active_task(tid) if self.engine is not None else None
            if task is not None and rec is not None and rec.real_started_at is None:
                rec.real_started_at = task.started_at
                self.real_starts[tid] = task.started_at
                # Past its deadline and the watchdog's 30 s margin: the next
                # sweep (a dispatch, or the maintenance loop) reaps it.
                task.started_at = (datetime.now(UTC) - timedelta(seconds=float(task.timeout) + 31.0)).isoformat()
            await asyncio.sleep(3600.0)
            return ok
        if kind == "cancel":
            await asyncio.sleep(3600.0)
            return ok
        if kind == "stubborn":
            try:
                await asyncio.sleep(3600.0)
            except asyncio.CancelledError:
                # Ignores the stop: the engine records the ending after its
                # grace, and this late result must stay evidence only.
                self.counts["cancels_ignored"] += 1
                await asyncio.sleep(self.args.stubborn_extra)
            return ok
        await asyncio.sleep(work)
        return ok

    def _outage(self) -> bool:
        every, length = float(self.args.outage_every), float(self.args.outage_seconds)
        if every <= 0 or length <= 0:
            return False
        return (time.monotonic() - self.started) % every >= every - length

    def _sample_in_flight(self) -> None:
        engine = self.engine
        if engine is None:
            return
        paused = self.classes["paused"]
        n = sum(1 for t in engine.list_active_tasks() if t.status != paused)
        if n > self.peak_sampled:
            self.peak_sampled = n
        for problem in in_flight_problems([n], self.limit):
            self.violation(problem)

    # ── submissions ──────────────────────────────────────────────────────

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._helpers.add(task)
        task.add_done_callback(self._helpers.discard)
        return task

    def submit_next(self) -> None:
        spec, duplicate = self.planner.next(self.recs)
        rec = self.recs.get(spec.id)
        if rec is None:
            rec = self.recs[spec.id] = _Rec(spec)
        rec.submissions += 1
        rec.pending += 1
        self.counts["submitted"] += 1
        self.counts["duplicates"] += duplicate
        self.by_kind[spec.kind]["submitted"] += 1
        engine, epoch = self.engine, self.epoch
        dispatch = asyncio.get_running_loop().create_task(
            self._dispatch(spec, rec, engine, duplicate), name=f"soak-dispatch:{spec.id}")
        self._dispatches[dispatch] = epoch
        dispatch.add_done_callback(lambda t: self._dispatches.pop(t, None))
        if spec.kind in ("cancel", "stubborn") and not duplicate:
            event = self._started_events.setdefault(spec.id, asyncio.Event())
            self._spawn(self._cancel_soon(spec.id, engine, event), f"soak-cancel:{spec.id}")

    async def _dispatch(self, spec: _Spec, rec: _Rec, engine: Any, duplicate: bool) -> None:
        if self.stopping or engine is not self.engine:
            # Never reached the engine (a restart began first): not a task.
            rec.submissions -= 1
            rec.pending -= 1
            self.counts["submitted"] -= 1
            self.counts["duplicates"] -= duplicate
            self.by_kind[spec.kind]["submitted"] -= 1
            if rec.submissions == 0:
                self.recs.pop(spec.id, None)
                event = self._started_events.pop(spec.id, None)
                if event is not None:
                    event.set()  # its canceller has nothing to wait for
            return
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(prompt=spec.prompt, id=spec.id, type=TaskType(spec.task_type),
                         workers=list(spec.workers), timeout=spec.timeout, fallback_chain=list(spec.fallback))
        t0 = time.perf_counter()
        outcome = "raised"
        try:
            result = await engine.dispatch(task)
        except Exception as exc:  # noqa: BLE001 - a soak records every failure
            self.violation(f"dispatch of {spec.id} raised {type(exc).__name__}: {exc}")
        else:
            meta = result.metadata or {}
            refused = next((flag for flag in _REFUSALS if meta.get(flag)), None)
            outcome = f"refused:{refused}" if refused else str(result.status)
            if refused:
                self.counts[f"refused_{refused}"] += 1
            error = result.error or ""
            if "Circuit breaker is open" in error:
                self.counts["breaker_open_refusals"] += 1
            if "No capable workers" in error:
                self.counts["auto_no_capacity"] += 1
        finally:
            rec.pending -= 1
            self.latencies.append(time.perf_counter() - t0)
            event = self._started_events.get(spec.id)
            if event is not None:
                event.set()  # a refused task's canceller has nothing to wait for
        rec.outcomes.append(outcome)

    async def _cancel_soon(self, tid: str, engine: Any, event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(event.wait(), timeout=60.0)
        except TimeoutError:
            pass
        rec = self.recs.get(tid)
        if rec is None or not rec.started or rec.pending == 0:
            self._started_events.pop(tid, None)
            return  # refused, or already ended: nothing to cancel
        await asyncio.sleep(self.args.cancel_after)
        self.counts["cancels_sent"] += 1
        try:
            if await engine.cancel_task(tid):
                self.counts["cancels_taken"] += 1
        except Exception as exc:  # noqa: BLE001
            self.violation(f"cancel of {tid} raised {type(exc).__name__}: {exc}")
        self._started_events.pop(tid, None)

    # ── the engine's life ────────────────────────────────────────────────

    def _new_engine(self, store: Any) -> Any:
        from kazma_core.swarm.config import SwarmConfig, WorkerConfig

        config = SwarmConfig(enabled=True, max_concurrent_tasks=self.limit,
                             workers=[WorkerConfig(name=n, type="in_process") for n in _WORKERS])
        engine = self.classes["engine"](config, task_store=store)
        # As Kazma's boot does, before the loop serves anything.
        engine.restore_paused_tasks()
        recovered = store.requeue_orphaned_running()
        self.restart_stats["orphan_rows_recovered"] += len(recovered.get("failed") or [])
        engine.redispatch_recovered_tasks()
        engine.set_circuit_breaker_config("flaky", failure_threshold=self.args.breaker_threshold,
                                          cooldown_seconds=self.args.breaker_cooldown)
        # The deadline, reap and cancel tasks' worker: those endings count as
        # breaker failures by design (SW-E11); a trip here would only refuse
        # them, so its threshold is out of reach.
        engine.set_circuit_breaker_config("longrun", failure_threshold=10**9, cooldown_seconds=1.0)
        scaler = self.classes["scaler"](engine, idle_ttl=self.args.idle_ttl)
        scaler.load_templates()
        engine._autoscaler = scaler
        return engine

    async def boot(self) -> None:
        from kazma_core.swarm.task_store import TaskStore

        self.epoch += 1
        self._autoscaler_mod._BOOT_TAG = uuid.uuid4().hex[:4]  # a new process draws a new tag
        store = await asyncio.to_thread(TaskStore, db_path=str(self.db_path))
        if self.epoch == 1:
            await asyncio.to_thread(_install_status_log, self.db_path)
        engine = await asyncio.to_thread(self._new_engine, store)
        engine.start_maintenance_loop(interval_seconds=15.0)
        await engine.start_all()
        self.engine, self.store, self.scaler = engine, store, engine._autoscaler
        self.stopping = False

    async def shutdown(self, *, final: bool) -> None:
        """Stop the engine as Kazma's shutdown does, then check nothing is left."""
        engine, store, epoch = self.engine, self.store, self.epoch
        self.stopping = True
        running_status = self.classes["running"]
        running = [t.id for t in engine.list_active_tasks() if t.status == running_status]
        self.restart_stats["running_at_stop"] += len(running)
        engine.stop_maintenance_loop()
        await engine.stop_all_runs()
        for handle in list(engine._task_handles.values()):
            if handle is not None and not handle.done():
                handle.cancel()
        await engine.stop_all()
        # stop_all_runs returns once the tasks it stopped are written (the
        # first trial measured 4 of 4 still pending, and the engine now waits
        # for them): one still unwritten here could be recorded by the next
        # boot as a failure of unknown outcome instead of the shutdown.
        writes = [w for tid, w in list(engine._writes.items()) if tid in running and not w.done()]
        self.restart_stats["writes_pending_after_stop"] += len(writes)
        if writes:
            self.violation(f"{len(writes)} task(s) stopped by the shutdown of epoch {epoch} not yet "
                           "written when stop_all_runs returned")
        mine = [t for t, e in list(self._dispatches.items()) if e == epoch]
        if mine:
            _, left = await asyncio.wait(mine, timeout=_ENGINE_GRACE_S + self.args.stubborn_extra + 15.0)
            if left:
                self.violation(f"{len(left)} task(s) still running {_ENGINE_GRACE_S:.0f}s after "
                               f"the engine stopped (epoch {epoch})")
        writes = [w for w in list(engine._writes.values()) if not w.done()]
        if writes:
            await asyncio.wait(writes, timeout=30.0)
        left = await asyncio.to_thread(self._unfinished_rows)
        if left:
            self.violation(f"after the {'final drain' if final else f'restart of epoch {epoch}'}: "
                           f"{len(left)} row(s) not finished: {left[:5]}")
        paused = self.classes["paused"]
        active = [t.id for t in engine.list_active_tasks() if t.status != paused]
        live_runs = [tid for tid, run in list(engine._runs.items()) if not run.done()]
        busy = [w.name for w in engine.list_workers() if int(getattr(w, "in_flight", 0) or 0) > 0]
        if active or live_runs or busy:
            self.violation(f"after the engine stopped (epoch {epoch}): admitted {active[:5]}, "
                           f"runs {live_runs[:5]}, workers with a dispatch in flight {busy[:5]}")
        self.scaler_stats["instances_at_stop"] += len(getattr(self.scaler, "_instances", {}) or {})
        await asyncio.to_thread(store.close)

    def anything_running(self) -> bool:
        running_status = self.classes["running"]
        return any(t.status == running_status for t in self.engine.list_active_tasks())

    async def restart(self) -> None:
        t0 = time.perf_counter()
        await self.shutdown(final=False)
        await self.boot()
        self.restart_seconds.append(time.perf_counter() - t0)
        self.restart_stats["restarts"] += 1

    def _unfinished_rows(self) -> list[str]:
        conn = _connect_ro(self.db_path)
        try:
            terminal = sorted(_terminal())
            marks = ",".join("?" * len(terminal))
            return [r[0] for r in conn.execute(
                f"SELECT id FROM swarm_tasks WHERE status NOT IN ({marks})", terminal)]  # noqa: S608
        finally:
            conn.close()

    # ── the checks ───────────────────────────────────────────────────────

    def _read_for_check(self, ids: list[str], full: bool, since_iso: str, real: dict[str, str],
                        settled: dict[str, str] | None) -> dict[str, Any]:
        """Everything a check reads, in one thread, on its own connection."""
        if self._reader is None:
            self._reader = _connect_ro(self.db_path)
        conn = self._reader
        changes = _transitions(conn, self._last_seq)
        out: dict[str, Any] = {"history": history_problems(changes, self._history), "changes": len(changes)}
        if changes:
            self._last_seq = changes[-1][0]
        out["rows"] = _full_rows(conn, ids)
        new = conn.execute("SELECT rowid, id FROM swarm_tasks WHERE rowid > ? ORDER BY rowid",
                           (self._last_rowid,)).fetchall()
        out["new_ids"] = [r[1] for r in new]
        out["max_rowid"] = new[-1][0] if new else self._last_rowid
        now = time.time()
        intervals = [(real.get(r[0]) or r[1], r[2]) for r in conn.execute(
            "SELECT id, started_at, completed_at FROM swarm_tasks "
            "WHERE completed_at IS NULL OR completed_at >= ?", (since_iso,))]
        out["peak"] = peak_in_flight(intervals, since=self._sweep_since, now=now)
        out["now"] = now
        if full and settled is not None:
            rows = {k: v[0] for k, v in _status_rows(conn).items()}
            out["full"], out["unknown"] = settled_mismatches(rows, settled)
        return out

    async def check(self, *, full: bool, final: bool) -> None:
        """Settle the finished tasks, then the full comparison when due."""
        ids = list(self.recs)
        since = self._sweep_since if self._sweep_since is not None else self.started_wall
        since_iso = datetime.fromtimestamp(since - 1.0, UTC).isoformat()
        real = dict(self.real_starts)
        settled = dict(self.settled) if full else None
        seen = await asyncio.to_thread(self._read_for_check, ids, full, since_iso, real, settled)
        self._sweep_since = seen["now"]
        self.counts["status_changes"] += seen["changes"]
        for problem in seen["history"]:
            self.violation(problem)
        self.peak_store = max(self.peak_store, seen["peak"])
        if seen["peak"] > self.limit:
            self.violation(f"in flight: {seen['peak']} tasks running at once in the store, above the "
                           f"limit of {self.limit}")
        self._last_rowid = seen["max_rowid"]
        for tid in seen["new_ids"]:
            if tid not in self.recs and tid not in self.settled:
                self.violation(f"row {tid} was never submitted")
        for tid in ids:
            rec = self.recs.get(tid)
            if rec is not None:
                self._settle(tid, rec, seen["rows"].get(tid), final=final)
        for problem in seen.get("full") or []:
            self.violation(problem)
        # Read now, after the store was: a task submitted meanwhile is open.
        for tid in seen.get("unknown") or []:
            if tid not in self.recs and tid not in self.settled:
                self.violation(f"row {tid} was never submitted")
        if full:
            self.counts["full_checks"] += 1

    def _settle(self, tid: str, rec: _Rec, row: dict[str, Any] | None, *, final: bool) -> None:
        if rec.pending:
            return
        terminal = _terminal()
        answered = [o for o in rec.outcomes if not o.startswith("refused:")]
        if row is None:
            if answered:
                rec.stale_checks += 1
                if final or rec.stale_checks >= 3:
                    self.violation(f"{tid}: its caller got {answered[-1]} and the store has no row")
                    self._close(tid, rec, "lost")
                return
            self._close(tid, rec, "refused")
            return
        status = row["status"]
        if status not in terminal:
            rec.stale_checks += 1
            if final or rec.stale_checks >= 3:
                self.violation(f"{tid}: its caller was answered and its row is still {status}")
                self._close(tid, rec, status)
            return
        result = row["result"] or {}
        stored = result.get("status")
        for outcome in answered:
            if outcome not in (stored, "raised"):
                self.violation(f"{tid}: its caller got {outcome}, the store holds {stored} ({status})")
        if rec.body_entries > rec.spec.max_exec:
            self.violation(f"{tid}: ran {rec.body_entries} time(s), at most {rec.spec.max_exec}")
        meta = result.get("metadata") or {}
        cause = meta.get("interrupted_by")
        evidence = meta.get("execution_outcome")
        if cause:
            if evidence not in ("none_invoked", "unknown"):
                self.violation(f"{tid}: interrupted ({cause}) without an execution outcome ({evidence!r})")
            if rec.effect and (evidence != "unknown" or "file_write" not in (meta.get("invoked_tools") or [])):
                self.violation(f"{tid}: interrupted ({cause}) after its write, recorded {evidence!r} "
                               f"with tools {meta.get('invoked_tools')}")
        if evidence:
            self.execution_outcomes[str(evidence)] += 1
        self.endings[f"{status}/{cause or '-'}"] += 1
        late = len((row.get("metadata") or {}).get("orphan_results") or [])
        self.counts["late_outcomes_kept_as_evidence"] += late
        self._close(tid, rec, status)

    def _close(self, tid: str, rec: _Rec, status: str) -> None:
        self.settled[tid] = status
        self.by_kind[rec.spec.kind]["settled"][status] += 1
        self.recs.pop(tid, None)
        self._started_events.pop(tid, None)

    # ── the monitors ─────────────────────────────────────────────────────

    async def _lag_probe(self, into: array.array | None = None) -> None:
        """The drift of a 100 ms sleep, every 100 ms. It includes the timer's
        own granularity (about 16 ms on Windows): the idle baseline, taken
        before the engine starts, says how much."""
        interval = 0.1
        out = self.lags if into is None else into
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(interval)
            out.append(max(0.0, time.perf_counter() - t0 - interval))
            if into is None:
                self._sample_in_flight()

    async def _baseline(self) -> None:
        if self.args.baseline_seconds <= 0:
            return
        probe = self._spawn(self._lag_probe(self.idle_lags), "soak-lag-baseline")
        await asyncio.sleep(self.args.baseline_seconds)
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)

    def _resource_sample(self) -> dict[str, Any]:
        sample = process_tree_sample()
        sample["threads"] = threading.active_count()
        sample["task_db_mib"] = round(sum(
            p.stat().st_size for p in self.data_dir.glob(TASK_DB + "*") if p.is_file()) / 2**20, 2)
        return sample

    async def _sampler(self) -> None:
        while True:
            sample = await asyncio.to_thread(self._resource_sample)
            sample["t"] = round(time.monotonic() - self.started, 1)
            for key in ("rss_mib", "handles", "threads"):
                self.resource_peak[key] = max(self.resource_peak[key], sample[key])
            self.resource_samples.append(sample)
            await asyncio.sleep(5.0)

    def _stall_dumps(self) -> list[str]:
        return sorted(p.name for p in (self.data_dir / "home").glob("stall-*.txt"))

    # ── the run ──────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        from kazma_core.observability.loop_stall import start_stall_watchdog

        args = self.args
        await self._baseline()
        self.started = time.monotonic()
        self.started_wall = time.time()
        await self.boot()
        heartbeat = start_stall_watchdog(threshold_s=args.stall_threshold,
                                         interval_s=max(0.05, args.stall_threshold / 4))
        monitors = [self._spawn(self._lag_probe(), "soak-lag"), self._spawn(self._sampler(), "soak-sampler")]
        interval = 1.0 / max(0.01, args.rate)
        deadline = self.started + args.duration
        next_submit = self.started
        next_restart = self.started + args.restart_every if args.restart_every > 0 else float("inf")
        next_burst = self.started + args.burst_every if args.burst_every > 0 else float("inf")
        next_cycle = self.started + args.cycle_seconds
        next_full = self.started + args.full_check_every
        try:
            while time.monotonic() < deadline and not self.violations:
                now = time.monotonic()
                if next_submit < now - 1.0:
                    next_submit = now  # a pause (a restart) is not made up with a burst
                while next_submit <= now:
                    self.submit_next()
                    next_submit += interval
                if now >= next_burst:
                    # Many at once, as a chat or a script sending a batch: the
                    # admission limit is reached, whatever the rate.
                    for _ in range(args.burst_size):
                        self.submit_next()
                    self.counts["bursts"] += 1
                    next_burst = now + args.burst_every
                # A restart is due; it waits (up to 5 s, submitting) for a
                # running task, so there is something to interrupt.
                if now >= next_restart and (self.anything_running() or now >= next_restart + 5.0):
                    await self.restart()
                    next_restart = time.monotonic() + args.restart_every
                    next_submit = time.monotonic()
                if now >= next_cycle:
                    full = now >= next_full
                    await self.check(full=full, final=False)
                    if full:
                        next_full = now + args.full_check_every
                    await self._write(RUNNING)
                    next_cycle = time.monotonic() + args.cycle_seconds
                await asyncio.sleep(min(interval, 0.05))
            await self._drain()
            await self.shutdown(final=True)
            for _ in range(3):  # a finished task settles once its row is read
                await self.check(full=False, final=False)
                if not self.recs:
                    break
            await self.check(full=True, final=True)
        finally:
            for task in monitors:
                task.cancel()
            if heartbeat is not None:
                heartbeat.cancel()
            await asyncio.gather(*monitors, *([heartbeat] if heartbeat is not None else []),
                                 return_exceptions=True)
            if self._reader is not None:
                await asyncio.to_thread(self._reader.close)
        await asyncio.to_thread(self._write_ledger)
        summaries = await asyncio.to_thread(self.summaries)
        if args.fail_on_stall and summaries["dumps"]:
            self.violation(f"the loop stalled past {args.stall_threshold:g}s: {summaries['dumps'][:5]}")
        report = finished(self.report(RUNNING, summaries), not self.violations)
        await asyncio.to_thread(_write_report, args.report, report)
        return report

    async def _drain(self) -> None:
        """Let everything submitted end; sweep the watchdog each second."""
        until = time.monotonic() + self.args.drain
        while self._dispatches and time.monotonic() < until:
            await self.engine.reap_stale_tasks_async()
            await asyncio.wait(list(self._dispatches), timeout=1.0)
        if self._dispatches:
            self.violation(f"{len(self._dispatches)} task(s) did not end within the "
                           f"{self.args.drain:.0f}s drain")

    async def _write(self, status: str) -> None:
        summaries = await asyncio.to_thread(self.summaries, self.lags[:], self.latencies[:])
        report = self.report(status, summaries)
        if not await asyncio.to_thread(_write_report, self.args.report, report):
            self.counts["report_write_failures"] += 1
        print(f"[swarm-soak] {report['seconds']:>7.0f}s submitted={report['tasks']['submitted']} "
              f"settled={report['tasks']['settled']} restarts={report['restarts']['count']} "
              f"violations={len(self.violations)}", flush=True)

    def _write_ledger(self) -> None:
        (self.data_dir / LEDGER_FILE).write_text(json.dumps({
            "limit": self.limit, "settled": self.settled, "real_starts": self.real_starts,
            "open": sorted(self.recs),
        }), encoding="utf-8")

    def summaries(self, lags: Any = None, latencies: Any = None) -> dict[str, Any]:
        """The sorted measurements and the stall dumps. Run in a thread: over
        a long soak a sort of every sample would stall the loop it measures."""
        ordered = sorted(self.lags if lags is None else lags)

        def in_ms(values: list[float]) -> dict[str, Any]:
            return {k: (round(v * 1000, 2) if isinstance(v, float) else v)
                    for k, v in latency_summary(values).items()}

        return {
            "lag_ms": in_ms(ordered),
            "idle_lag_ms": in_ms(sorted(self.idle_lags)),
            "lag_over_250ms": sum(1 for x in ordered if x > 0.25),
            "latency": latency_summary(sorted(self.latencies if latencies is None else latencies)),
            "restarts": latency_summary(list(self.restart_seconds)),
            "dumps": self._stall_dumps(),
        }

    def report(self, status: str, summaries: dict[str, Any]) -> dict[str, Any]:
        elapsed = time.monotonic() - self.started
        statuses = collections.Counter(self.settled.values())
        finished_tasks = sum(n for s, n in statuses.items() if s not in ("refused", "lost"))
        samples = self.resource_samples
        dumps = summaries["dumps"]
        return {
            "soak": "swarm",
            "tracker_row": "SQ01",
            "status": status,
            "updated_at": utc_stamp(),
            "healthy": not self.violations,
            "seconds": round(elapsed, 1),
            "seed": self.args.seed,
            "config": {k: v for k, v in vars(self.args).items() if k != "report"},
            "tasks": {
                "submitted": self.counts["submitted"],
                "unique": len(self.settled) + len(self.recs),
                "duplicates": self.counts["duplicates"],
                "settled": len(self.settled),
                "open": len(self.recs),
                "final_status": dict(statuses),
                "throughput_per_minute": round(finished_tasks / max(elapsed, 1e-9) * 60, 1),
                "caller_latency_s": summaries["latency"],
                "by_kind": {k: {"submissions": v["submitted"], "worker_runs": self.bodies[k],
                                "settled": dict(v["settled"])}
                            for k, v in self.by_kind.items() if v["submitted"]},
                "endings": dict(self.endings),
                "execution_outcome": dict(self.execution_outcomes),
                "late_outcomes_kept_as_evidence": self.counts["late_outcomes_kept_as_evidence"],
            },
            "admission": {
                "limit": self.limit,
                "bursts": self.counts["bursts"],
                "refusals": self.counts["refused_admission_denied"],
                "peak_in_flight": {"sampled": self.peak_sampled, "store": self.peak_store},
            },
            "breakers": {
                "trips": self.breakers["trips"], "half_open_probes": self.breakers["probes"],
                "recoveries": self.breakers["recoveries"], "reopens": self.breakers["reopens"],
                "refused_admissions": self.breakers["refusals"],
                "open_refusals_seen_by_callers": self.counts["breaker_open_refusals"],
                "stale_outcomes": self.breakers["stale_outcomes"],
                "older_generation_outcomes_seen": self.breakers["older_outcomes"],
                "shared_refreshes_with_older_state": self.breakers["refresh_regressions"],
                "by_worker": {k: dict(v) for k, v in sorted(self.breaker_by_worker.items())},
            },
            "autoscaler": {
                "spawns": self.scaler_stats["spawns"], "reaps": self.scaler_stats["reaps"],
                "most_instances": self.scaler_stats["most_instances"],
                "most_by_template": dict(self.scaler_most),
                "bounds": {t["name"]: t["max_instances"] for t in _templates(self.args.pool_max)},
                "auto_tasks_without_capacity": self.counts["auto_no_capacity"],
                "instances_left_at_stops": self.scaler_stats["instances_at_stop"],
            },
            "cancels": {"sent": self.counts["cancels_sent"], "taken": self.counts["cancels_taken"],
                        "ignored_by_the_worker": self.counts["cancels_ignored"]},
            "restarts": {
                "count": self.restart_stats["restarts"],
                "seconds": summaries["restarts"],
                "running_at_stop": self.restart_stats["running_at_stop"],
                "interrupted": self.endings["cancelled/shutdown"],
                "writes_pending_after_stop_all_runs": self.restart_stats["writes_pending_after_stop"],
                "orphan_rows_recovered_at_boot": self.restart_stats["orphan_rows_recovered"],
            },
            "loop": {
                "lag_ms": summaries["lag_ms"],
                "idle_lag_ms_before_the_engine_started": summaries["idle_lag_ms"],
                "lag_over_250ms": summaries["lag_over_250ms"],
                "stall_threshold_s": self.args.stall_threshold,
                "stall_dumps": len(dumps),
                "stall_dump_files": dumps[:20],
            },
            "resources": {
                "max_rss_mib": self.resource_peak["rss_mib"],
                "max_handles": self.resource_peak["handles"],
                "max_threads": self.resource_peak["threads"],
                "task_db_mib": samples[-1]["task_db_mib"] if samples else None,
                "samples": samples[:: max(1, len(samples) // 100)],
            },
            "swarm_failures": _swarm_failures(),
            "log": {"by_level": dict(self.log_counter.by_level),
                    "top": self.log_counter.by_message.most_common(15)},
            "checks": {"full": self.counts["full_checks"],
                       "status_changes_read": self.counts["status_changes"],
                       "report_write_failures": self.counts["report_write_failures"]},
            "violations": list(self.violations),
            "data_dir": str(self.data_dir),
        }


def _instrumented_classes(soak: _Soak) -> dict[str, Any]:
    """The engine with scripted workers, and the observing breaker and
    autoscaler. Built after the private environment is in place."""
    from kazma_core.agent.dispatch_effects import note_effect_invocation
    from kazma_core.swarm.autoscaler import AutoScaler
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.reliability import CircuitBreaker, CircuitState
    from kazma_core.swarm.task import TaskStatus
    from kazma_core.swarm.worker import InProcessWorker

    soak._note_effect = note_effect_invocation

    class ScriptedWorker(InProcessWorker):
        async def dispatch(self, task: str, context: Any = "") -> dict[str, Any]:
            return await soak.body(self, task, context)

    class SoakEngine(SwarmEngine):
        def _create_worker(self, worker_config: Any) -> Any:
            base = super()._create_worker(worker_config)  # the product's type check
            return ScriptedWorker(name=base.name, role=base.role, model=base.model, provider=base.provider,
                                  capabilities=base.capabilities, system_prompt=base.system_prompt)

    class CountingBreaker(CircuitBreaker):
        """The product's breaker, observed: every outcome and refresh."""

        def admit(self, worker_name: str | None = None) -> Any:
            admission = super().admit(worker_name)
            if worker_name:
                self.__dict__["_soak_name"] = worker_name
            if admission is None:
                soak.breaker_event(worker_name, "refusals")
            elif admission.probe:
                soak.breaker_event(worker_name, "probes")
            return admission

        def record_success(self, admission: Any = None) -> None:
            self._observe(super().record_success, admission)

        def record_failure(self, admission: Any = None) -> None:
            self._observe(super().record_failure, admission)

        def _observe(self, apply: Any, admission: Any) -> None:
            name = self.__dict__.get("_soak_name")
            with self._lock:
                before = self.state  # reading moves open to half-open: not the outcome's doing
                generation, failures, stale = self.generation, self.consecutive_failures, self.stale_outcomes
                older = admission is not None and admission.generation < generation
                apply(admission)
                changed = (self.generation, self.consecutive_failures) != (generation, failures)
                after = self._state
                if self.stale_outcomes > stale:
                    soak.breaker_event(name, "stale_outcomes")
                if older:
                    soak.breaker_event(name, "older_outcomes")
                    if changed:
                        soak.violation(f"breaker {name}: an outcome admitted in generation "
                                       f"{admission.generation} changed generation {generation}")
                    return
                if changed and before == CircuitState.CLOSED and after == CircuitState.OPEN:
                    soak.breaker_event(name, "trips")
                elif before == CircuitState.HALF_OPEN and after == CircuitState.CLOSED:
                    soak.breaker_event(name, "recoveries")
                elif changed and before == CircuitState.HALF_OPEN and after == CircuitState.OPEN:
                    soak.breaker_event(name, "reopens")

        def refresh_from_shared(self, worker_name: str) -> None:
            with self._lock:
                before, generation = self.state, self.generation
                super().refresh_from_shared(worker_name)
                if self.state != before and self.generation <= generation:
                    # The shared copy changed the state without a newer
                    # generation: an older state put back over this one.
                    soak.breaker_event(worker_name, "refresh_regressions")
                    soak.violation(f"breaker {worker_name}: a refresh from the shared store moved "
                                   f"it from {before.value} to {self.state.value} at generation "
                                   f"{generation} (no newer generation)")

    class SoakScaler(AutoScaler):
        def _instantiate(self, template: Any) -> Any:
            worker = super()._instantiate(template)  # under the scaler's lock
            if worker is not None:
                # Counted here, not with the scaler's own helper: the check
                # must not share the code it checks.
                alive = sum(1 for entry in self._instances.values() if entry[0] == template.name)
                soak.scaler_spawned(template, alive, len(self._instances))
            return worker

        def _reap_instance(self, name: str) -> None:
            soak.scaler_reaping(name, self._engine.get_worker(name))
            super()._reap_instance(name)

    return {"engine": SoakEngine, "worker": ScriptedWorker, "breaker": CountingBreaker,
            "scaler": SoakScaler, "running": TaskStatus.RUNNING, "paused": TaskStatus.PAUSED}


def _loop_factory() -> Any:
    from kazma_core.eventloop import uvicorn_loop_factory

    return uvicorn_loop_factory()  # the server's loop: a SelectorEventLoop on Windows


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--duration", type=float, default=600.0, help="seconds of load")
    parser.add_argument("--rate", type=float, default=5.0, help="tasks submitted a second")
    parser.add_argument("--burst-every", type=float, default=30.0,
                        help="seconds between bursts of --burst-size tasks at once (0: none)")
    parser.add_argument("--burst-size", type=int, default=20)
    parser.add_argument("--mix", default=_DEFAULT_MIX, help=f"kind=weight,... of {', '.join(_KINDS)}")
    parser.add_argument("--max-active", type=int, default=10,
                        help="the admission limit (swarm.max_concurrent_tasks; the product's default)")
    parser.add_argument("--restart-every", type=float, default=120.0, help="seconds between restarts (0: none)")
    parser.add_argument("--cycle-seconds", type=float, default=60.0, help="seconds between checks and reports")
    parser.add_argument("--full-check-every", type=float, default=600.0,
                        help="seconds between comparisons of every row (the end always has one)")
    parser.add_argument("--drain", type=float, default=180.0, help="seconds to let submitted work end")
    parser.add_argument("--duplicate-rate", type=float, default=0.03, help="share of re-submitted ids")
    parser.add_argument("--work-min-ms", type=float, default=30.0)
    parser.add_argument("--work-max-ms", type=float, default=400.0)
    parser.add_argument("--slow-timeout", type=float, default=2.0, help="the deadline of a 'slow' task")
    parser.add_argument("--cancel-after", type=float, default=0.3, help="seconds into a run a cancel comes")
    parser.add_argument("--stubborn-extra", type=float, default=_ENGINE_GRACE_S + 2.0,
                        help="how long a worker that ignores a cancel runs on (past the engine's grace)")
    parser.add_argument("--breaker-threshold", type=int, default=3, help="the flaky worker's breaker")
    parser.add_argument("--breaker-cooldown", type=float, default=5.0)
    parser.add_argument("--outage-every", type=float, default=60.0, help="the flaky worker's outage cycle")
    parser.add_argument("--outage-seconds", type=float, default=20.0)
    parser.add_argument("--idle-ttl", type=float, default=10.0, help="the autoscaler's idle reap")
    parser.add_argument("--pool-max", type=int, default=3, help="the catch-all template's bound")
    parser.add_argument("--local-breakers", action="store_true",
                        help="breakers kept in memory only (default: shared through the settings store)")
    parser.add_argument("--stall-threshold", type=float, default=1.0,
                        help="seconds of an unresponsive loop before the watchdog dumps (the server: 15)")
    parser.add_argument("--fail-on-stall", action="store_true", help="a stall dump fails the run")
    parser.add_argument("--baseline-seconds", type=float, default=5.0,
                        help="idle loop-lag measurement before the engine starts")
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    _parse_mix(args.mix)
    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="kazma-swarm-soak-")) / "data"
    if (data_dir / TASK_DB).exists():
        # Its rows were submitted by another run: every check would fail on them.
        raise SystemExit(f"--data-dir {data_dir} holds an earlier run's task store; give a new folder")
    data_dir.mkdir(parents=True, exist_ok=True)
    _private_environment(data_dir, shared_breakers=not args.local_breakers)
    # The autoscaler reads the operator's templates from the data directory.
    (data_dir / "swarm_templates.json").write_text(json.dumps(_templates(args.pool_max), indent=2),
                                                   encoding="utf-8")
    counter = _setup_logging(data_dir)
    soak = _Soak(args, data_dir, counter)
    try:
        with contextlib.ExitStack() as stack:
            soak.instrument(stack)
            with asyncio.Runner(loop_factory=_loop_factory()) as runner:
                report = runner.run(soak.run())
    except KeyboardInterrupt:
        soak.violation("interrupted by the operator before the end")
        report = finished(soak.report(RUNNING, soak.summaries()), False)
        _write_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
