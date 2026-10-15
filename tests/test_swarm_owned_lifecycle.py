"""One owned lifecycle for a swarm task (Swarm audit 2026-10-13, stage 2).

Each test inverts a witness in
``session-artifacts/audits-2026-10-13/test_swarm_engine_witnesses.py`` or
``test_swarm_surfaces_probes_2026_10_13.py``:

* SW-E02 -- the watchdog recorded TIMEOUT while the work kept running, and a
  cancel without a registered handle only relabelled the task. Now the engine
  creates and registers its own run of every task before the first await;
  cancel and the watchdog stop that run and the ending is recorded once the
  work has stopped. A timeout of 0 or less means no deadline.
* SW-E03 -- the store upserted anything over anything: a reaped pipeline came
  back PAUSED, the same id executed twice. Now one transition table, enforced
  by a guarded write on SQLite and Postgres, a generation per admission, and
  idempotent admission; a superseded outcome is kept as evidence only.
* SW-E06 / SW-S06 -- a broadcast bypassed admission, the RUNNING row, tenant
  stamping and cancellation. Now every product dispatch goes through
  ``SwarmEngine.dispatch``; a full swarm answers HTTP 429.
* SW-E05 -- an approved pipeline resumed outside admission and the watchdog,
  still labelled PAUSED. Now a resume is a RUNNING transition through the same
  admission, with its own start time.
* SW-E09 -- a restored paused pipeline evicted from the history LRU could not
  be cancelled and its reject was never saved.

Runs on SQLite here and on Postgres in the CI Postgres job (every id unique).
"""

from __future__ import annotations

import ast
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import TERMINAL_STATUSES, SwarmTask, TaskResult, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore

pytestmark = pytest.mark.postgres

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _inert(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _no_self_improvement(task, worker_results, status):
        return None

    async def _no_memory_index(**_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _no_self_improvement)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _no_memory_index)
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


@pytest.fixture
def store(tmp_path: Path):
    s = TaskStore(db_path=str(tmp_path / "tasks.db"))
    yield s
    s.close()


def _engine(store: TaskStore | None, *names: str) -> SwarmEngine:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in names:
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    return engine


def _ok(worker: str, output: str = "ok") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _old_iso(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class _Blocking:
    """A worker that waits to be released, recording what it did after."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.effects: list[str] = []

    async def __call__(self, task: Any, context: Any = None) -> dict:
        self.started.set()
        await self.release.wait()
        self.effects.append("wrote")
        return _ok(self.name, "late result")


# ── SW-E02: the watchdog and cancel stop the work ────────────────────────


async def test_the_watchdog_stops_the_work_before_recording_the_timeout(store: TaskStore) -> None:
    engine = _engine(store, "w")
    worker = _Blocking("w")
    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_id("reap"), workers=["w"], timeout=1.0)
    caller = asyncio.create_task(engine.dispatch(task))  # no handle registered by the caller
    await asyncio.wait_for(worker.started.wait(), 5)
    task.started_at = _old_iso(400)

    assert engine.reap_stale_tasks() == 1
    result = await asyncio.wait_for(caller, 5)
    worker.release.set()  # the run has ended: the worker cannot write now

    assert result.status == "timeout"
    assert worker.effects == [], "the work was stopped, not only relabelled"
    stored = store.get_task(task.id)
    assert stored.status == TaskStatus.TIMEOUT
    # It had run no tool that changes things (SW-E04's tracker says so).
    assert stored.result.metadata["execution_outcome"] == "none_invoked"
    assert stored.result.metadata["interrupted_by"] == "deadline"


async def test_finalize_without_stopping_lets_the_work_run_on(store: TaskStore) -> None:
    """Negative control: recording the ending without stopping the run (the
    old watchdog) leaves the work running -- why the stop comes first."""
    engine = _engine(store, "w")
    worker = _Blocking("w")
    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_id("ctrl"), workers=["w"], timeout=0)
    caller = asyncio.create_task(engine.dispatch(task))
    await asyncio.wait_for(worker.started.wait(), 5)
    engine._finalize_task(task, worker_results=[], status="timeout", duration_seconds=0)
    worker.release.set()
    await asyncio.wait_for(caller, 5)
    assert worker.effects == ["wrote"]


async def test_a_task_with_no_deadline_is_never_reaped(store: TaskStore) -> None:
    engine = _engine(store, "w")
    worker = _Blocking("w")
    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_id("nodeadline"), workers=["w"], timeout=0)
    caller = asyncio.create_task(engine.dispatch(task))
    await asyncio.wait_for(worker.started.wait(), 5)
    task.started_at = _old_iso(100_000)
    assert engine.reap_stale_tasks() == 0
    worker.release.set()
    assert (await asyncio.wait_for(caller, 5)).status == "success"


async def test_cancel_stops_work_whoever_dispatched_it(store: TaskStore) -> None:
    engine = _engine(store, "w")
    worker = _Blocking("w")
    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_id("cancel"), workers=["w"], timeout=0)
    caller = asyncio.create_task(engine.dispatch(task))  # never registered
    await asyncio.wait_for(worker.started.wait(), 5)

    assert await engine.cancel_task(task.id) is True
    assert (await asyncio.wait_for(caller, 5)).status == "cancelled"
    worker.release.set()  # cancel_task returned once the run had ended
    assert worker.effects == []
    assert store.get_task(task.id).status == TaskStatus.CANCELLED


async def test_a_caller_that_stops_waiting_stops_the_work(store: TaskStore) -> None:
    engine = _engine(store, "w")
    worker = _Blocking("w")
    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", id=_id("caller"), workers=["w"], timeout=0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(engine.dispatch(task), 0.2)
    worker.release.set()  # dispatch() raised only after the run had ended
    assert worker.effects == []
    assert store.get_task(task.id).status == TaskStatus.CANCELLED


#: The engine's private executors, called on an engine (``engine._x`` or, in
#: the engine, ``self._x``), and the swarm functions that run a task's work.
_ENGINE_EXECUTORS = {"_dispatch", "_dispatch_inner", "_broadcast_task", "_resume"}
_FUNCTION_EXECUTORS = {"broadcast_task", "resume_pipeline", "dispatch_inner", "_dispatch_inner_impl"}
#: (module, function) -> why it may call an executor directly.
_EXECUTOR_CALLERS = {
    ("swarm/engine.py", "SwarmEngine.dispatch"): "the owned run (via _run_owned)",
    ("swarm/engine.py", "SwarmEngine._dispatch"): "the run's body",
    ("swarm/engine.py", "SwarmEngine._dispatch_inner"): "inside the run",
    ("swarm/engine.py", "SwarmEngine._broadcast_task"): "inside the run",
    ("swarm/engine.py", "SwarmEngine.approve_checkpoint"): "the resumed run (via _run_owned)",
    ("swarm/engine.py", "SwarmEngine._resume"): "the resumed run's body",
    ("swarm/durable.py", "run_via_durable"): "inside the run: the durable path of _dispatch",
    ("swarm/durable.py", "run_activity_payload"): "the Temporal activity of a run",
}


def _executor_calls(tree: ast.AST, rel: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    class _V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _scope(self, node: Any) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            hit = isinstance(func, ast.Name) and func.id in _FUNCTION_EXECUTORS
            if isinstance(func, ast.Attribute):
                owner = ast.unparse(func.value).lower()
                hit = hit or (func.attr in _ENGINE_EXECUTORS
                              and ("engine" in owner or (owner == "self" and rel.endswith("swarm/engine.py"))))
                hit = hit or func.attr in _FUNCTION_EXECUTORS
            if hit:
                found.add((rel, ".".join(self.stack) or "<module>"))
            self.generic_visit(node)

    _V().visit(tree)
    return found


def test_every_entry_point_runs_through_the_owned_run() -> None:
    """Gateway /swarm, IDE send_to_swarm, SwarmManager, the panel (foreground
    and background), dispatch_swarm, retry: each calls ``engine.dispatch``,
    and an approve calls ``approve_checkpoint``; nothing else starts the
    executors (enumerated from the source, every package)."""
    planted = "async def go(engine, task):\n    return await engine._dispatch_inner(task, 0, None)\n"
    assert _executor_calls(ast.parse(planted), "x.py") == {("x.py", "go")}, "negative control"

    core = REPO / "kazma-core" / "kazma_core"
    found: set[tuple[str, str]] = set()
    for pkg in sorted(REPO.glob("kazma-*/kazma_*")):
        for path in pkg.rglob("*.py"):
            if "tests" in path.parts:
                continue
            rel = (path.relative_to(core) if core in path.parents else path.relative_to(REPO)).as_posix()
            found |= _executor_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
    undeclared = sorted(found - set(_EXECUTOR_CALLERS))
    assert not undeclared, f"swarm work started outside the engine's owned run: {undeclared}"
    stale = sorted(set(_EXECUTOR_CALLERS) - found)
    assert not stale, f"declared callers that no longer exist: {stale}"


# ── SW-E03: one transition table, guarded on every backend ───────────────

_ALL = [None, *TaskStatus]


def _allowed(row: TaskStatus | None, new: TaskStatus) -> bool:
    if row is None:
        return True
    if row == new:
        return True  # its own state, written again
    if row in TERMINAL_STATUSES:
        return False
    if new == TaskStatus.PENDING:
        return False
    return True


@pytest.mark.parametrize("row", _ALL, ids=lambda s: getattr(s, "value", "absent"))
def test_the_store_enforces_the_transition_table(store: TaskStore, row: TaskStatus | None) -> None:
    for new in TaskStatus:
        task = SwarmTask(prompt="p", id=_id("tt"), result=TaskResult(task_id="x", status="success"))
        if row is not None:
            task.status = row
            assert store.persist_task(task) is True
        task.status = new
        assert store.persist_task(task) is _allowed(row, new), (row, new)
        stored = store.get_task(task.id)
        assert stored.status == (new if _allowed(row, new) else row)


def test_an_older_generation_never_overwrites_a_newer_one(store: TaskStore) -> None:
    task = SwarmTask(prompt="p", id=_id("gen"), status=TaskStatus.RUNNING)
    task.generation = 2
    assert store.persist_task(task) is True
    task.generation = 1
    task.status = TaskStatus.PAUSED
    assert store.persist_task(task) is False
    assert store.get_task(task.id).status == TaskStatus.RUNNING


def test_admission_is_once_per_generation(store: TaskStore) -> None:
    task = SwarmTask(prompt="p", id=_id("admit"), status=TaskStatus.RUNNING)
    task.generation = 1
    assert store.admit(task) is True
    assert store.admit(task) is False, "a second admission of the same generation"
    task.status = TaskStatus.PAUSED
    assert store.persist_task(task) is True
    task.status = TaskStatus.RUNNING
    task.generation = 2
    assert store.admit(task) is True, "a resume takes the next generation"
    assert store.admit(task) is False, "a second approval of the same checkpoint"


def test_the_old_upsert_reopens_a_finished_task(tmp_path: Path) -> None:
    """Negative control: an unguarded upsert (the old INSERT OR REPLACE) puts a
    reaped task back to paused -- the SW-E02b witness."""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE swarm_tasks (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT OR REPLACE INTO swarm_tasks VALUES ('t', 'timeout')")
    conn.execute("INSERT OR REPLACE INTO swarm_tasks VALUES ('t', 'paused')")
    assert conn.execute("SELECT status FROM swarm_tasks").fetchone()[0] == "paused"
    conn.close()


async def test_a_duplicate_dispatch_executes_once(store: TaskStore) -> None:
    engine = _engine(store, "w")
    calls: list[int] = []

    async def w(task, context=None):
        calls.append(1)
        await asyncio.sleep(0.05)
        return _ok("w")

    engine.get_worker("w").dispatch = w
    task = SwarmTask(prompt="p", id=_id("dup"), workers=["w"], timeout=0)
    first, second = await asyncio.gather(engine.dispatch(task), engine.dispatch(task))
    assert calls == [1]
    assert first.status == second.status == "success"
    again = await engine.dispatch(task)
    assert calls == [1] and again.status == "success", "a finished id is not run again"


async def test_another_process_cannot_run_a_held_task(store: TaskStore, tmp_path: Path) -> None:
    """Two engines over one store (two processes): the second admission of
    the same id is refused by the store, and its worker never runs."""
    one, two = _engine(store, "w"), _engine(TaskStore(db_path=store._db_path), "w")
    worker = _Blocking("w")
    one.get_worker("w").dispatch = worker
    ran_two: list[int] = []

    async def w2(task, context=None):
        ran_two.append(1)
        return _ok("w")

    two.get_worker("w").dispatch = w2
    task = SwarmTask(prompt="p", id=_id("xproc"), workers=["w"], timeout=0)
    running = asyncio.create_task(one.dispatch(task))
    await asyncio.wait_for(worker.started.wait(), 5)
    copy = SwarmTask.from_dict(task.to_dict())
    copy.generation = 0  # what the other process holds: the task as submitted
    held = await two.dispatch(copy)
    assert ran_two == []
    assert held.status == "failed" and held.metadata.get("duplicate_admission")
    worker.release.set()
    assert (await asyncio.wait_for(running, 5)).status == "success"
    two._task_store.close()


async def test_a_late_outcome_is_kept_as_evidence_not_as_the_result(store: TaskStore) -> None:
    engine = _engine(store, "w")
    task = SwarmTask(prompt="p", id=_id("orphan"), workers=["w"], status=TaskStatus.RUNNING)
    task.generation = 1
    assert store.admit(task)
    ended = SwarmTask.from_dict(task.to_dict())
    ended.status = TaskStatus.TIMEOUT
    ended.result = TaskResult(task_id=task.id, status="timeout", error="reaped")
    assert store.persist_task(ended)
    engine._active_tasks[task.id] = task  # the superseded run, finishing late
    engine._finalize_task(task, worker_results=[], status="success", duration_seconds=1.0)
    await engine._flush_writes(task.id)  # the write runs off the loop (SW-E07)
    stored = store.get_task(task.id)
    assert stored.status == TaskStatus.TIMEOUT and stored.result.error == "reaped"
    assert stored.metadata["orphan_results"][-1]["status"] == "success"


_STARTS = {"RUNNING", "PAUSED", "PENDING"}
#: (file under kazma_core/swarm, function) -> how it goes through the table.
_STARTS_DECLARED = {
    ("engine.py", "SwarmEngine._dispatch"): "admission compare-and-set (TaskStore.admit) before any work",
    ("engine.py", "SwarmEngine.approve_checkpoint"): "PAUSED -> RUNNING by TaskStore.admit before the resume",
    ("engine.py", "SwarmEngine._approval_stopped_at_admission"):
        "back to PAUSED only when the store holds this approval's own RUNNING row (same run_id), "
        "written after the admission; nothing resumed",
    ("checkpoint_manager.py", "CheckpointManager.handle_pipeline_checkpoint"):
        "the pause; persist_task's guard refuses it over a finished row",
}


def _status_starts(source: str) -> set[str]:
    found: set[str] = set()

    class _V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _scope(self, node: Any) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if not (isinstance(target, ast.Attribute) and target.attr == "status"):
                    continue
                if isinstance(target.value, ast.Name) and target.value.id == "self":
                    continue
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                            and sub.value.id == "TaskStatus" and sub.attr in _STARTS):
                        found.add(".".join(self.stack) or "<module>")
            self.generic_visit(node)

    _V().visit(ast.parse(source))
    return found


def test_only_the_declared_places_start_or_pause_a_task() -> None:
    """The reverse of tests/test_swarm_paused_task_endings.py: a task is put
    RUNNING or PAUSED only where the transition table is asked."""
    assert _status_starts("def f(t):\n    t.status = TaskStatus.RUNNING\n") == {"f"}, "negative control"
    swarm = REPO / "kazma-core" / "kazma_core" / "swarm"
    found = {(p.name, fn) for p in sorted(swarm.glob("*.py")) for fn in _status_starts(p.read_text(encoding="utf-8"))}
    assert not found - set(_STARTS_DECLARED), sorted(found - set(_STARTS_DECLARED))
    assert not set(_STARTS_DECLARED) - found, sorted(set(_STARTS_DECLARED) - found)


# ── SW-E05: a resume is admitted and watched like a dispatch ─────────────


async def _paused_pipeline(engine: SwarmEngine, *, timeout: float = 0) -> SwarmTask:
    task = SwarmTask(prompt="p", id=_id("pipe"), workers=["a", "b"], type=TaskType.PIPELINE,
                     timeout=timeout, metadata={"hitl_checkpoints": [1], "checkpoint_timeout": 3600})
    assert (await engine.dispatch(task)).status == "paused"
    return task


async def test_a_resume_runs_as_running_and_counts_against_admission(store: TaskStore, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_SWARM_MAX_ACTIVE", "1")
    engine = _engine(store, "a", "b", "c")
    engine.get_worker("a").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("a"))
    b = _Blocking("b")
    engine.get_worker("b").dispatch = b
    ran_c: list[int] = []

    async def c(task, context=None):
        ran_c.append(1)
        return _ok("c")

    engine.get_worker("c").dispatch = c
    task = await _paused_pipeline(engine, timeout=1.0)
    approve = asyncio.create_task(engine.approve_checkpoint(task.id))
    await asyncio.wait_for(b.started.wait(), 5)
    assert task.status == TaskStatus.RUNNING
    assert store.get_task(task.id).status == TaskStatus.RUNNING
    assert task.generation == 2

    other = await engine.dispatch(SwarmTask(prompt="q", workers=["c"], timeout=0))
    assert other.metadata.get("admission_denied") and ran_c == []

    task.started_at = _old_iso(10_000)
    assert engine.reap_stale_tasks() == 1, "the watchdog watches a resumed run"
    result = await asyncio.wait_for(approve, 5)
    assert result.status == "timeout"
    b.release.set()  # the resumed run has ended
    assert b.effects == []


async def test_an_approve_at_capacity_leaves_the_checkpoint_open(store: TaskStore, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_SWARM_MAX_ACTIVE", "1")
    engine = _engine(store, "a", "b", "busy")
    engine.get_worker("a").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("a"))
    engine.get_worker("b").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("b"))
    busy = _Blocking("busy")
    engine.get_worker("busy").dispatch = busy
    task = await _paused_pipeline(engine)
    blocker = asyncio.create_task(engine.dispatch(SwarmTask(prompt="x", workers=["busy"], timeout=0)))
    await asyncio.wait_for(busy.started.wait(), 5)

    deferred = await engine.approve_checkpoint(task.id)
    assert deferred.metadata.get("admission_denied") and deferred.metadata.get("checkpoint_open")
    assert engine.get_checkpoint_info(task.id) is not None
    assert store.get_task(task.id).status == TaskStatus.PAUSED

    busy.release.set()
    await asyncio.wait_for(blocker, 5)
    assert (await engine.approve_checkpoint(task.id)).status == "success"


# ── SW-E09: a restored paused task is never lost to the history LRU ──────


async def test_a_restored_paused_task_cancels_and_rejects_after_eviction(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    s1 = TaskStore(db_path=str(db))
    e1 = _engine(s1, "a", "b")
    e1.get_worker("a").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("a"))
    rejected = await _paused_pipeline(e1)
    cancelled = await _paused_pipeline(e1)
    s1.close()

    s2 = TaskStore(db_path=str(db))
    e2 = _engine(s2, "a", "b", "q")
    e2._max_history = 1  # stands in for 500 newer tasks
    e2.restore_paused_tasks()
    e2.get_worker("q").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("q"))
    await e2.dispatch(SwarmTask(prompt="newer", workers=["q"], timeout=0))
    assert e2.get_task(rejected.id) is None, "evicted from history"
    assert await e2.cancel_task(cancelled.id) is True
    result = await e2.reject_checkpoint(rejected.id)
    assert result is not None and result.status == "failed"
    assert s2.get_task(rejected.id).status == TaskStatus.FAILED
    assert s2.get_task(cancelled.id).status == TaskStatus.CANCELLED
    s2.close()

    s3 = TaskStore(db_path=str(db))
    e3 = _engine(s3, "a", "b")
    restored = {t.id for t in e3.restore_paused_tasks()}
    assert rejected.id not in restored and cancelled.id not in restored
    s3.close()


# ── SW-E06 / SW-S06: one dispatch path; broadcasts are tasks ─────────────


def test_no_product_code_dispatches_around_the_engine() -> None:
    """``engine.broadcast`` / ``dispatch_by_name`` are thin wrappers over
    dispatch(); product code outside the engine calls dispatch() itself."""

    def offenders(tree: ast.AST) -> list[int]:
        return [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("broadcast", "dispatch_by_name")
            and "engine" in ast.unparse(n.func.value).lower()
        ]

    assert offenders(ast.parse("async def f(engine, t):\n    await engine.broadcast(t)\n")) == [2]
    bad = []
    for pkg in sorted(REPO.glob("kazma-*/kazma_*")):
        for path in pkg.rglob("*.py"):
            if "tests" in path.parts or path.name == "engine.py":
                continue
            bad += [f"{path.relative_to(REPO)}:{ln}" for ln in offenders(ast.parse(path.read_text(encoding="utf-8")))]
    assert not bad, bad


async def test_a_broadcast_is_admitted_recorded_and_stamped(store: TaskStore, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_SWARM_MAX_ACTIVE", "1")
    engine = _engine(store, "busy", "b1")
    busy = _Blocking("busy")
    engine.get_worker("busy").dispatch = busy
    engine.get_worker("b1").dispatch = lambda task, context=None: asyncio.sleep(0, _ok("b1"))
    first = asyncio.create_task(engine.dispatch(SwarmTask(prompt="x", workers=["busy"], timeout=0)))
    await asyncio.wait_for(busy.started.wait(), 5)

    refused = await engine.broadcast(SwarmTask(prompt="z", workers=["b1"], timeout=0))
    assert refused.metadata.get("admission_denied"), "a broadcast is admitted like any task"
    busy.release.set()
    await asyncio.wait_for(first, 5)

    btask = SwarmTask(prompt="z2", workers=["b1"], timeout=0)
    assert (await engine.broadcast(btask)).status == "success"
    stored = store.get_task(btask.id)
    assert stored.type == TaskType.BROADCAST and stored.status == TaskStatus.COMPLETED
    assert "tenant_id" in (stored.metadata or {})


def test_the_panel_answers_a_full_swarm_with_429(monkeypatch, tmp_path: Path) -> None:
    import kazma_gateway.agent_handler as ah
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.swarm_panel import routes_tasks

    store = TaskStore(str(tmp_path / "swarm_tasks.db"))
    engine = _engine(store, "probe")
    calls: list[str] = []

    async def probe(task, context=None):
        calls.append(str(task))
        return _ok("probe")

    engine.get_worker("probe").dispatch = probe

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return engine

        def has_swarm_core(self) -> bool:
            return True

        def register_task_handle(self, *a: Any) -> None: ...

        def unregister_task_handle(self, *a: Any) -> None: ...

    async def _no_send(*a: Any, **k: Any) -> bool:
        return False

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    monkeypatch.setattr(ah, "_maybe_send_to_output_target", _no_send)
    monkeypatch.setenv("KAZMA_SWARM_MAX_ACTIVE", "1")
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    blocker = SwarmTask(prompt="blocker", workers=["probe"], status=TaskStatus.RUNNING)
    blocker.started_at = datetime.now(UTC).isoformat()
    engine._active_tasks[blocker.id] = blocker
    for pattern, background in (("dispatch", False), ("broadcast", False), ("broadcast", True)):
        r = client.post("/api/swarm/dispatch", json={
            "workers": ["probe"], "task": "x", "pattern": pattern, "background": background})
        assert r.status_code == 429, (pattern, background, r.text)
        assert r.json()["code"] == "swarm_at_capacity" and "capacity" in r.json()["message"]
    assert calls == []

    engine._active_tasks.pop(blocker.id, None)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    b = client.post("/api/swarm/dispatch", json={"workers": ["probe"], "task": "two", "pattern": "broadcast"}).json()
    assert b["result_status"] == "success"
    prompts = [t["prompt"] for t in client.get("/api/swarm/tasks").json()["tasks"]]
    assert "two" in prompts, "the broadcast is listed in Task History in the production posture"
    store.close()
