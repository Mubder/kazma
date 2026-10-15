"""A task stopped while its RUNNING row is being written still gets its ending.

Found by the SQ01 swarm soak (24-hour run on ec6b8512, failed after 6,366 s
at its 53rd restart): ``_dispatch`` puts a task in ``_active_tasks`` and then
awaits ``_admit_to_store`` (the RUNNING row, written in a thread) OUTSIDE the
``try`` whose ``CancelledError`` handler records an interrupted ending. A
stop that landed during that write -- the shutdown's ``stop_all_runs``,
``cancel_task``, a caller giving up -- raised straight out of the run: the
thread still wrote the RUNNING row, nothing finalized the task, it kept its
admission slot, and the next boot's orphan recovery recorded five tasks that
never started as "failed, interrupted by process exit, outcome unknown".

Now the stop is recorded as the run's interruption once the write has
landed (cancelled, its cause, ``execution_outcome: none_invoked``), the
ending is written after the RUNNING row, and an admission the store refused
(another run holds the id) is not this run's to record. Negative controls:
the stop raised out of the run, as before, leaves the row running and the
slot taken; and the gate finds an admission with no handler.

An approval stopped the same way (the approving request gone, a shutdown)
has resumed nothing: the checkpoint is offered again, its auto-reject armed
again, whatever the write left -- the approval's RUNNING row written back
to PAUSED, a failed write's PAUSED row kept, another run's row left to it.
Negative control: the stop raised with nothing reopened leaves the
checkpoint claimed for good and the row RUNNING.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "kazma-core" / "kazma_core" / "swarm" / "engine.py"


@pytest.fixture(autouse=True)
def _inert(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _none(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _none)
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


class _HeldAdmission(TaskStore):
    """A task store whose RUNNING-row write waits, in its thread, until let
    through: a stop then lands while the write is under way."""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path=db_path)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.holding = True
        self.refuse_with: Exception | None = None

    def admit(self, task: SwarmTask) -> bool:
        if not self.holding:
            return super().admit(task)
        self.entered.set()
        self.release.wait(10)
        if self.refuse_with is not None:
            raise self.refuse_with
        return super().admit(task)


@pytest.fixture
def store(tmp_path: Path):
    s = _HeldAdmission(str(tmp_path / "tasks.db"))
    yield s
    s.release.set()
    s.close()


def _engine(store: TaskStore) -> tuple[SwarmEngine, list[str]]:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    ran: list[str] = []

    async def worker(task: str, context: Any = None) -> dict:
        ran.append(task)
        return {"worker": "w", "task_id": "", "status": "success", "output": "ok", "error": None}

    engine.get_worker("w").dispatch = worker
    return engine, ran


def _task() -> SwarmTask:
    return SwarmTask(prompt="p", id=f"held-{uuid.uuid4().hex[:10]}", workers=["w"], timeout=60)


async def _stop_during_the_write(engine: SwarmEngine, store: _HeldAdmission, task: SwarmTask,
                                 cause: str) -> TaskResult | None:
    """Dispatch *task*, stop it while its RUNNING row is being written, then
    let the write finish. Returns what the dispatch's caller got."""
    caller = asyncio.create_task(engine.dispatch(task))
    assert await asyncio.to_thread(store.entered.wait, 5), "the admission write never started"
    # The write lands after the stop has been delivered.
    asyncio.get_running_loop().call_later(0.3, store.release.set)
    if cause == "shutdown":
        assert await engine.stop_all_runs() == 1
        return await asyncio.wait_for(caller, 15)
    if cause == "cancelled_by_user":
        assert await engine.cancel_task(task.id) is True
        return await asyncio.wait_for(caller, 15)
    caller.cancel()  # the caller stops waiting
    with pytest.raises(asyncio.CancelledError):
        await caller  # its own cancellation goes on (SW-E04)
    return None


@pytest.mark.parametrize("cause", ["shutdown", "cancelled_by_user", "caller_stopped"])
async def test_a_stop_during_the_admission_write_is_recorded(store: _HeldAdmission, cause: str) -> None:
    engine, ran = _engine(store)
    task = _task()
    result = await _stop_during_the_write(engine, store, task, cause)

    # Read at once: the stop's own waits cover the ending's write.
    stored = store.get_task(task.id)
    assert stored is not None and stored.status == TaskStatus.CANCELLED, stored and stored.status
    meta = stored.result.metadata
    assert meta["interrupted_by"] == cause and meta["execution_outcome"] == "none_invoked", meta
    if result is not None:
        assert result.status == "cancelled" and result.metadata["interrupted_by"] == cause
    assert engine.list_active_tasks() == [], "the stopped task still holds an admission slot"
    assert ran == [], "the work started although it was stopped first"


async def test_an_admission_the_store_refused_is_not_recorded_as_this_run(store: _HeldAdmission) -> None:
    """The id is held by another run (its row is finished): a stop during the
    refused write ends this dispatch without touching that row."""
    engine, ran = _engine(store)
    task = _task()
    done = SwarmTask(prompt="p", id=task.id, workers=["w"], status=TaskStatus.COMPLETED, generation=1,
                     result=TaskResult(task_id=task.id, status="success"))
    assert store.persist_task(done)
    result = await _stop_during_the_write(engine, store, task, "shutdown")

    assert result.metadata.get("duplicate_admission") is True, result
    stored = store.get_task(task.id)
    assert stored.status == TaskStatus.COMPLETED and stored.result.status == "success"
    assert not (stored.metadata or {}).get("orphan_results"), "this run's stop was written onto the other's row"
    assert engine.list_active_tasks() == [] and ran == []


async def test_negative_control_a_stop_raised_out_of_the_run_strands_the_task(
        store: _HeldAdmission, monkeypatch: pytest.MonkeyPatch) -> None:
    """The code before the fix: the stop raised out of the run. The test sees
    what the soak saw -- the row left running and the slot still taken."""

    async def _raise_it(self: SwarmEngine, task: SwarmTask, started: float) -> TaskResult:
        raise asyncio.CancelledError

    monkeypatch.setattr(SwarmEngine, "_stopped_at_admission", _raise_it)
    engine, _ = _engine(store)
    task = _task()
    caller = asyncio.create_task(engine.dispatch(task))
    assert await asyncio.to_thread(store.entered.wait, 5)
    asyncio.get_running_loop().call_later(0.3, store.release.set)
    assert await engine.stop_all_runs() == 1
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, 15)
    assert store.get_task(task.id).status == TaskStatus.RUNNING
    assert [t.id for t in engine.list_active_tasks()] == [task.id]



# ── an approval stopped during its write ───────────────────────────────────


def _pipeline_engine(store: TaskStore) -> tuple[SwarmEngine, list[str]]:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    ran: list[str] = []

    def stepper(name: str) -> Any:
        async def run(task: str, context: Any = None) -> dict:
            ran.append(name)
            return {"worker": name, "task_id": "", "status": "success", "output": f"from {name}", "error": None}
        return run

    for name in ("a", "b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
        engine.get_worker(name).dispatch = stepper(name)
    return engine, ran


async def _paused_pipeline(engine: SwarmEngine, store: _HeldAdmission) -> SwarmTask:
    store.holding = False
    task = SwarmTask(prompt="p", id=f"appr-{uuid.uuid4().hex[:10]}", type=TaskType.PIPELINE,
                     workers=["a", "b"], timeout=60,
                     metadata={"hitl_checkpoints": [1], "checkpoint_timeout": 3600})
    await engine.dispatch(task)
    assert engine.get_checkpoint_info(task.id) is not None, "the pipeline did not pause"
    store.holding = True
    return task


async def _stop_the_approval(engine: SwarmEngine, store: _HeldAdmission, task_id: str,
                             before_release: Any = None) -> None:
    approving = asyncio.create_task(engine.approve_checkpoint(task_id))
    assert await asyncio.to_thread(store.entered.wait, 5), "the approval's write never started"
    if before_release is not None:
        await asyncio.to_thread(before_release)
    asyncio.get_running_loop().call_later(0.3, store.release.set)
    approving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await approving


def _cancel_timeouts(engine: SwarmEngine) -> None:
    for entry in list(engine._checkpoint_handler._paused.values()):
        if entry.timeout_task is not None:
            entry.timeout_task.cancel()


@pytest.mark.parametrize("write", ["lands", "fails"])
async def test_an_approval_stopped_during_its_write_offers_the_checkpoint_again(
        store: _HeldAdmission, write: str) -> None:
    engine, ran = _pipeline_engine(store)
    task = await _paused_pipeline(engine, store)
    paused_generation = store.get_task(task.id).generation
    if write == "fails":
        store.refuse_with = sqlite3.OperationalError("database is locked")
    try:
        await _stop_the_approval(engine, store, task.id)

        stored = store.get_task(task.id)
        assert stored.status == TaskStatus.PAUSED, stored.status
        if write == "lands":
            assert stored.generation == paused_generation + 1  # written back after the admission
        else:
            assert stored.generation == paused_generation
        assert ran == ["a"], "a step ran on the stopped approval"
        entry = engine._checkpoint_handler._paused[task.id]
        assert entry.claim == "pending", entry.claim
        assert entry.timeout_task is not None and not entry.timeout_task.done(), "auto-reject not armed again"

        store.holding, store.refuse_with = False, None
        result = await engine.approve_checkpoint(task.id)
        assert result is not None and result.status == "success", result
        assert ran == ["a", "b"], "the approval given again did not run the rest exactly once"
        assert store.get_task(task.id).status == TaskStatus.COMPLETED
        assert engine.get_checkpoint_info(task.id) is None
    finally:
        _cancel_timeouts(engine)


async def test_an_approval_stopped_while_another_run_took_the_task_leaves_it_to_that_run(
        store: _HeldAdmission) -> None:
    engine, ran = _pipeline_engine(store)
    task = await _paused_pipeline(engine, store)
    paused = store.get_task(task.id)

    def another_run_finishes_it() -> None:
        other = SwarmTask.from_dict(paused.to_dict())
        other.status, other.generation, other.run_id = TaskStatus.COMPLETED, paused.generation + 1, "other-run"
        other.result = TaskResult(task_id=task.id, status="success")
        assert TaskStore.persist_task(store, other)

    try:
        await _stop_the_approval(engine, store, task.id, before_release=another_run_finishes_it)
        stored = store.get_task(task.id)
        assert stored.status == TaskStatus.COMPLETED and stored.run_id == "other-run"
        assert engine.get_checkpoint_info(task.id) is None, "the checkpoint stayed open over another run's task"
        assert ran == ["a"]
    finally:
        _cancel_timeouts(engine)


async def test_negative_control_an_approval_stopped_with_nothing_reopened_is_stranded(
        store: _HeldAdmission, monkeypatch: pytest.MonkeyPatch) -> None:
    """The code before the fix: the stop raised out of the approval."""

    async def _nothing(self: SwarmEngine, *_a: Any) -> None:
        return None

    monkeypatch.setattr(SwarmEngine, "_approval_stopped_at_admission", _nothing)
    engine, ran = _pipeline_engine(store)
    task = await _paused_pipeline(engine, store)
    try:
        await _stop_the_approval(engine, store, task.id)
        for _ in range(100):  # the write lands in its thread
            if store.get_task(task.id).status == TaskStatus.RUNNING:
                break
            await asyncio.sleep(0.05)
        assert store.get_task(task.id).status == TaskStatus.RUNNING
        store.holding = False
        assert await engine.approve_checkpoint(task.id) is None, "the claim was released"
        assert ran == ["a"]
    finally:
        _cancel_timeouts(engine)

# ── the class: every admission write has its stop handled ─────────────────

#: Functions that admit a task to the store without a CancelledError handler
#: around the write, and why.
_NOT_A_RUN: dict[str, str] = {}


def _unguarded_admissions(tree: ast.AST) -> set[str]:
    """Functions with an ``self._admit_to_store(...)`` call that no
    ``except ... CancelledError`` of the same function surrounds."""
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        guarded: set[int] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Try) and any(
                    "CancelledError" in ast.unparse(h.type or ast.Name("BaseException")) for h in node.handlers):
                guarded |= {id(n) for stmt in node.body for n in ast.walk(stmt)}
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_admit_to_store" and id(node) not in guarded):
                found.add(fn.name)
    return found


def test_every_admission_write_has_its_stop_handled() -> None:
    found = _unguarded_admissions(ast.parse(ENGINE.read_text(encoding="utf-8")))
    assert found == set(_NOT_A_RUN), (
        "a task is admitted to the store with no CancelledError handler around the write: "
        "a stop during it strands the task (handle it, or declare it in _NOT_A_RUN with why): "
        f"{sorted(found ^ set(_NOT_A_RUN))}")


def test_the_gate_finds_an_unguarded_admission() -> None:
    """Negative control: the shape of _dispatch before the fix."""
    planted = ast.parse(
        "async def _dispatch(self, task):\n"
        "    self._active_tasks[task.id] = task\n"
        "    held = await self._admit_to_store(task)\n"
        "    try:\n"
        "        return await self._dispatch_inner(task)\n"
        "    except asyncio.CancelledError:\n"
        "        return self._interrupted(task)\n")
    assert _unguarded_admissions(planted) == {"_dispatch"}
