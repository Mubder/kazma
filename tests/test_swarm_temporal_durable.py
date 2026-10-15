"""The durable swarm path, qualified on Temporal's time-skipping test server.

Swarm tracker SQ03 and SW-E16 (2026-10-15). ``kazma_core.swarm.durable`` runs
an admitted swarm task through one Temporal workflow per run; its activity
runs the engine's own body of the run in the process that admitted it (the
rules are in that module's docstring). These run a real ``SwarmEngine`` with
scripted workers over a private SQLite task store, against the real test
server ``WorkflowEnvironment.start_time_skipping()`` starts (downloaded on
first use), and a real child process for the kill case:

* a task and a pipeline run once through their workflow, which records the
  limits the engine's budget gives (and a summary, no prompt);
* a worker past its deadline ends as the engine records it in process
  (``timeout`` / ``deadline``, ``execution_outcome`` from the effect
  tracker), and Temporal's own limit, when it is the one that fires, ends
  the task with the same record;
* a cancel (Kazma's, and one made through Temporal) stops the work and
  records the cause;
* an approved pipeline resumes through a workflow of its own;
* a lost start or result acknowledgement never runs anything twice; an
  activity no run here is waiting for is refused;
* a worker process killed mid-step: Temporal calls it lost at its heartbeat
  timeout, the next start holds the task as an unknown outcome naming the
  step, the committed step is not run again and nothing runs in the next
  process.

Time is not skipped while an activity is scheduled or running: the test
server locks time skipping for an activity task until it ends (measured
2026-10-15: a heartbeating activity with a 600 s start-to-close limit and a
workflow timer of 600 s did not end in 60 s of real time; ``env.sleep``
past them timed out; a scheduled activity's 120 s schedule-to-start limit
did not end in 60 s). The activity's limits here are therefore short real
ones (seconds), set through the module's constants.

Negative controls, each the code before 2026-10-15 (or a guard taken out):
the activity body as it was (``_dispatch_inner`` with no span, no deadline);
no heartbeat (Temporal's limit and cancel never reach the work); a killed
worker with no heartbeat timeout and no step journal.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

#: Set by the CI job that qualifies this path: there a missing SDK or a test
#: server that cannot start fails the run instead of skipping it.
_REQUIRED = os.environ.get("KAZMA_TEST_TEMPORAL_REQUIRED") == "1"
if not _REQUIRED:
    temporalio = pytest.importorskip("temporalio", reason="the durable extra (temporalio) is not installed")

from kazma_core.swarm import durable  # noqa: E402
from kazma_core.swarm.config import SwarmConfig, WorkerConfig  # noqa: E402
from kazma_core.swarm.engine import SwarmEngine  # noqa: E402
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType  # noqa: E402
from kazma_core.swarm.task_store import TaskStore  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).resolve().parent / "_swarm_durable_child.py"

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300)]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def server():
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # no network for the first download, no binary for this platform
        if _REQUIRED:
            raise
        pytest.skip(f"Temporal's time-skipping test server could not start (it is downloaded on "
                    f"first use): {type(exc).__name__}: {exc}")
    try:
        yield env
    finally:
        await env.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def temporal(server, monkeypatch, tmp_path):
    """This process's workers on a queue of the test's own, against the test
    server, with short real limits."""
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _none)
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", server.client.service_client.config.target_host)
    monkeypatch.setenv("KAZMA_TEMPORAL_QUEUE", f"kazma-test-{uuid.uuid4().hex[:10]}")
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")  # no silent fallback in a qualification
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    for name in ("KAZMA_SHARED_BREAKERS", "KAZMA_GATE_REGISTRY", "KAZMA_SELF_IMPROVEMENT"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(durable, "HEARTBEAT_EVERY_S", 0.2)
    monkeypatch.setattr(durable, "HEARTBEAT_TIMEOUT_S", 3.0)
    monkeypatch.setattr(durable, "SCHEDULE_TO_START_S", 5.0)
    await durable.start_temporal_worker()
    assert durable.worker_running(), "the Temporal workers did not start"
    try:
        yield server
    finally:
        await durable.stop_temporal_worker()


def _engine(tmp_path: Path, *names: str) -> tuple[SwarmEngine, TaskStore]:
    store = TaskStore(db_path=str(tmp_path / f"tasks-{uuid.uuid4().hex[:6]}.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in names:
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    return engine, store


def _ok(worker: str, output: str = "ok") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _row(store: TaskStore, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    meta = dict(getattr(task.result, "metadata", None) or {})
    return {"status": task.status, "result_status": getattr(task.result, "status", None),
            "error": getattr(task.result, "error", "") or "", "interrupted_by": meta.get("interrupted_by"),
            "execution_outcome": meta.get("execution_outcome"), "invoked_tools": meta.get("invoked_tools"),
            "metadata": meta, "task_metadata": dict(task.metadata or {})}


async def _history(env: Any, workflow_id: str) -> list[Any]:
    handle = env.client.get_workflow_handle(workflow_id)
    return [event async for event in handle.fetch_history_events()]


def _old_body(engine: Any, task: Any, started: float, task_span: Any, resume: Any) -> Any:
    """The activity's body before 2026-10-15: ``_dispatch_inner`` with no
    span and no deadline (``run_activity_payload`` passed ``None``)."""
    return engine._dispatch_inner(task, started, None)


# ── a run, once ──────────────────────────────────────────────────────────


async def test_a_task_runs_once_through_its_workflow(temporal, tmp_path) -> None:
    from temporalio.api.enums.v1 import EventType

    engine, store = _engine(tmp_path, "w")
    runs: list[str] = []

    async def w(prompt: str, context=None) -> dict:
        runs.append(prompt)
        return _ok("w", "the answer")

    engine.get_worker("w").dispatch = w
    task = SwarmTask(prompt="question", workers=["w"], timeout=30)
    try:
        result = await engine.dispatch(task)
        assert result.status == "success" and runs == ["question"], (result.status, result.error)
        assert result.metadata["durable"] == "temporal"
        workflow_id = result.metadata["workflow_id"]
        assert workflow_id == f"kazma-swarm-{task.id}"
        row = _row(store, task.id)
        assert row["status"] == TaskStatus.COMPLETED and row["task_metadata"]["workflow_id"] == workflow_id
        assert engine.list_active_tasks() == []
        events = await _history(temporal, workflow_id)
        scheduled = next(e.activity_task_scheduled_event_attributes for e in events
                         if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED)
        limits = durable.activity_timeouts(task)
        assert scheduled.activity_type.name == "kazma_swarm_run"
        assert scheduled.task_queue.name == durable.activity_queue()
        assert scheduled.start_to_close_timeout.ToTimedelta().total_seconds() == limits["start_to_close"]
        assert scheduled.heartbeat_timeout.ToTimedelta().total_seconds() == limits["heartbeat"]
        assert scheduled.retry_policy.maximum_attempts == 1
        # The engine's own end of the run (watchdog + grace) comes first.
        assert limits["start_to_close"] > 30 + 30 + 10
        completed = next(e for e in events if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED)
        summary = completed.workflow_execution_completed_event_attributes.result.payloads[0].data
        assert b'"success"' in summary and b"the answer" not in summary and b"question" not in summary
    finally:
        store.close()


async def test_negative_control_the_old_activity_body_never_ran_a_dispatch(temporal, tmp_path, monkeypatch) -> None:
    """``_dispatch_inner(task, started, None)``: the single-worker path reads
    the span's trace id, so every durable dispatch failed before its worker
    ran."""
    monkeypatch.setattr(durable, "_run_here", _old_body)
    engine, store = _engine(tmp_path, "w")
    runs: list[str] = []

    async def w(prompt: str, context=None) -> dict:
        runs.append(prompt)
        return _ok("w")

    engine.get_worker("w").dispatch = w
    try:
        result = await engine.dispatch(SwarmTask(prompt="question", workers=["w"], timeout=30))
        assert result.status != "success" and runs == []
    finally:
        store.close()


async def test_a_pipeline_runs_each_step_once_and_journals_them(temporal, tmp_path) -> None:
    engine, store = _engine(tmp_path, "a", "b", "c")
    runs: list[str] = []

    def step(name: str):
        async def run(prompt: str, context=None) -> dict:
            runs.append(name)
            return _ok(name, f"from {name}")
        return run

    for name in ("a", "b", "c"):
        engine.get_worker(name).dispatch = step(name)
    task = SwarmTask(prompt="p", type=TaskType.PIPELINE, workers=["a", "b", "c"], timeout=30)
    try:
        result = await engine.dispatch(task)
        assert result.status == "success" and runs == ["a", "b", "c"], result.error
        row = _row(store, task.id)
        assert row["status"] == TaskStatus.COMPLETED
        assert row["task_metadata"]["step_journal"]["step"] == 3
        assert row["task_metadata"]["step_journal"]["state"] == "committed"
        # Three steps and the Refiner, past the watchdog and its grace.
        assert durable.activity_timeouts(task)["start_to_close"] == 30 * 4 + 30 + 10 + durable.BACKSTOP_AFTER_S
    finally:
        store.close()


# ── one deadline owner ───────────────────────────────────────────────────


def _blocking_writer(started: asyncio.Event | None = None):
    async def worker(prompt: str, context=None) -> dict:
        from kazma_core.agent.dispatch_effects import note_effect_invocation

        note_effect_invocation("file_write")
        if started is not None:
            started.set()
        await asyncio.sleep(3600)
        return _ok("w")
    return worker


async def _deadline_record(tmp_path: Path, *, durable_on: bool, monkeypatch) -> dict[str, Any]:
    if not durable_on:
        monkeypatch.setenv("KAZMA_TEMPORAL", "0")
        monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    engine, store = _engine(tmp_path, "w")
    engine.get_worker("w").dispatch = _blocking_writer()
    task = SwarmTask(prompt="p", workers=["w"], timeout=1.0)
    try:
        t0 = time.monotonic()
        result = await asyncio.wait_for(engine.dispatch(task), 30)
        row = _row(store, task.id)
        row["seconds"] = time.monotonic() - t0
        row["told"] = result.status
        return row
    finally:
        store.close()


async def test_a_worker_past_its_deadline_ends_as_in_process(temporal, tmp_path, monkeypatch) -> None:
    durable_row = await _deadline_record(tmp_path, durable_on=True, monkeypatch=monkeypatch)
    in_process = await _deadline_record(tmp_path, durable_on=False, monkeypatch=monkeypatch)
    for row in (durable_row, in_process):
        assert row["told"] == "timeout" and row["status"] == TaskStatus.TIMEOUT, row
        assert row["interrupted_by"] == "deadline" and row["execution_outcome"] == "unknown", row
        assert row["invoked_tools"] == ["file_write"], row
        assert row["seconds"] < 10, row  # the engine's 1 s deadline, not Temporal's limit
    assert durable_row["error"] == in_process["error"]
    assert durable_row["task_metadata"]["durable"] == "temporal"


async def test_negative_control_work_outside_the_runs_context_loses_its_evidence(
        temporal, tmp_path, monkeypatch) -> None:
    """The activity used to run the work in the worker's own context: the
    run's effect tracker heard nothing, so a run past its deadline that had
    written a file was recorded as having changed nothing."""
    import contextvars

    monkeypatch.setattr(durable, "_run_context", contextvars.Context)
    row = await _deadline_record(tmp_path, durable_on=True, monkeypatch=monkeypatch)
    assert row["status"] == TaskStatus.TIMEOUT
    assert row["execution_outcome"] == "none_invoked" and not row["invoked_tools"], row


@pytest.mark.parametrize("kind, workers", [
    (TaskType.DISPATCH, ["w"]), (TaskType.PIPELINE, ["a", "b", "c", "d"]),
    (TaskType.FAN_OUT, ["a", "b"]), (TaskType.CONDITIONAL, ["router", "a"]),
])
async def test_temporals_limit_sits_after_the_engines_end_of_the_run(kind, workers) -> None:
    """Temporal's start-to-close limit is the engine's budget (the
    watchdog's) plus the watchdog's margin and stop grace and a backstop: the
    engine ends a run first. Negative control: the old limit
    (``max(timeout, 60) + 120`` whatever the steps) cut a four-step pipeline
    of 100 s steps short, at 220 s of a 500 s budget."""
    from kazma_core.swarm.engine import _STOP_GRACE_S, WATCHDOG_MARGIN_S, task_budget

    task = SwarmTask(prompt="p", type=kind, workers=workers, timeout=100)
    timeout, steps = task_budget(task)
    engine_ends_by = timeout * steps + WATCHDOG_MARGIN_S + _STOP_GRACE_S
    assert durable.activity_timeouts(task)["start_to_close"] > engine_ends_by
    old = max(float(task.timeout), 60.0) + 120.0
    if kind == TaskType.PIPELINE:
        assert old < timeout * steps, "the old limit would not have cut the pipeline short"
    no_deadline = SwarmTask(prompt="p", type=kind, workers=workers, timeout=0)
    assert durable.activity_timeouts(no_deadline)["start_to_close"] == durable.NO_DEADLINE_LIMIT_S


async def test_temporals_own_limit_ends_the_task_with_the_engines_record(temporal, tmp_path, monkeypatch) -> None:
    """When Temporal's limit is the one that fires (the engine's own end of
    the run did not come), the work stops and the ending is the engine's
    deadline record."""
    limits = durable.activity_timeouts

    def short(task):
        return {**limits(task), "start_to_close": 2.0, "schedule_to_close": 7.0}

    monkeypatch.setattr(durable, "activity_timeouts", short)
    engine, store = _engine(tmp_path, "w")
    engine.get_worker("w").dispatch = _blocking_writer()
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)  # the engine would wait 10 minutes
    try:
        result = await asyncio.wait_for(engine.dispatch(task), 30)
        row = _row(store, task.id)
        assert result.status == "timeout" and row["status"] == TaskStatus.TIMEOUT, (result.status, row)
        assert row["interrupted_by"] == "deadline" and row["execution_outcome"] == "unknown", row
        assert row["invoked_tools"] == ["file_write"] and "Temporal's time limit" in row["error"], row
    finally:
        store.close()


async def _old_settle(entry: Any, lost: Any) -> Any:
    """The durable path's ending before 2026-10-15: a workflow that failed
    was answered "outcome unknown", unfinalized, with the work left running."""
    from kazma_core.swarm.task import TaskResult

    if lost is None:
        return entry.result
    return TaskResult(task_id=entry.task.id, status="failed", error=f"Temporal outcome unknown for {entry.workflow_id}.",
                      metadata={"durable": "temporal", "execution_outcome": "unknown"})


@pytest.mark.parametrize("stop", ["limit", "cancel"])
async def test_negative_control_the_old_ending_left_the_work_running(temporal, tmp_path, monkeypatch, stop) -> None:
    """With the old ending, Temporal's limit (or a cancel made through it)
    closed the workflow and the caller was told "unknown" while the work went
    on, its row RUNNING and its slot held."""
    limits = durable.activity_timeouts
    monkeypatch.setattr(durable, "_settle", _old_settle)
    if stop == "limit":
        monkeypatch.setattr(durable, "activity_timeouts",
                            lambda task: {**limits(task), "start_to_close": 2.0, "schedule_to_close": 7.0})
    engine, store = _engine(tmp_path, "w")
    started = asyncio.Event()
    bodies: list[asyncio.Task] = []

    async def worker(prompt: str, context=None) -> dict:
        bodies.append(asyncio.current_task())
        started.set()
        await asyncio.sleep(3600)
        return _ok("w")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)
    run = asyncio.ensure_future(engine.dispatch(task))
    try:
        await asyncio.wait_for(started.wait(), 10)
        if stop == "cancel":
            await temporal.client.get_workflow_handle(durable._workflow_id_for(task)).cancel()
        result = await asyncio.wait_for(run, 20)
        assert result.metadata["execution_outcome"] == "unknown" and result.status == "failed"
        assert not bodies[0].done(), "the work stopped"
        assert store.get_task(task.id).status == TaskStatus.RUNNING
        assert task.id in {t.id for t in engine.list_active_tasks()}
    finally:
        for body in bodies:
            body.cancel()
        await asyncio.sleep(0.5)
        store.close()


# ── stops ────────────────────────────────────────────────────────────────


async def test_a_cancel_stops_the_work_and_its_workflow(temporal, tmp_path) -> None:
    from temporalio.client import WorkflowExecutionStatus

    engine, store = _engine(tmp_path, "w")
    started = asyncio.Event()
    engine.get_worker("w").dispatch = _blocking_writer(started)
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)
    run = asyncio.ensure_future(engine.dispatch(task))
    try:
        await asyncio.wait_for(started.wait(), 10)
        assert await engine.cancel_task(task.id)
        result = await asyncio.wait_for(run, 15)
        row = _row(store, task.id)
        assert result.status == "cancelled" and row["status"] == TaskStatus.CANCELLED, row
        assert row["interrupted_by"] == "cancelled_by_user" and row["execution_outcome"] == "unknown", row
        assert row["invoked_tools"] == ["file_write"]
        handle = temporal.client.get_workflow_handle(durable._workflow_id_for(task))
        until = time.monotonic() + 15
        while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING and time.monotonic() < until:
            await asyncio.sleep(0.2)
        assert (await handle.describe()).status != WorkflowExecutionStatus.RUNNING
    finally:
        store.close()


async def test_a_shutdown_mid_run_records_the_shutdown_before_it_returns(temporal, tmp_path) -> None:
    engine, store = _engine(tmp_path, "w")
    started = asyncio.Event()
    engine.get_worker("w").dispatch = _blocking_writer(started)
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)
    run = asyncio.ensure_future(engine.dispatch(task))
    try:
        await asyncio.wait_for(started.wait(), 10)
        assert await engine.stop_all_runs() == 1
        row = _row(store, task.id)  # as a process exiting now leaves it
        assert row["status"] == TaskStatus.CANCELLED and row["interrupted_by"] == "shutdown", row
        assert row["execution_outcome"] == "unknown" and row["invoked_tools"] == ["file_write"], row
        assert (await asyncio.wait_for(run, 10)).status == "cancelled"
    finally:
        store.close()


async def test_a_cancel_made_through_temporal_reaches_the_work(temporal, tmp_path) -> None:
    engine, store = _engine(tmp_path, "w")
    started = asyncio.Event()
    engine.get_worker("w").dispatch = _blocking_writer(started)
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)
    run = asyncio.ensure_future(engine.dispatch(task))
    try:
        await asyncio.wait_for(started.wait(), 10)
        await temporal.client.get_workflow_handle(durable._workflow_id_for(task)).cancel()
        result = await asyncio.wait_for(run, 15)
        row = _row(store, task.id)
        assert result.status == "cancelled" and row["interrupted_by"] == "temporal_cancel", row
        assert row["execution_outcome"] == "unknown" and row["invoked_tools"] == ["file_write"], row
        assert engine.list_active_tasks() == []
    finally:
        if not run.done():
            await engine.cancel_task(task.id)
            await asyncio.wait({run}, timeout=15)
        store.close()


# ── an approved resume ───────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["durable", "negative-control-in-process"])
async def test_an_approved_pipeline_resumes_through_a_workflow_of_its_own(temporal, tmp_path, monkeypatch,
                                                                       path) -> None:
    """The control is the resume as it was: run in process, no workflow."""
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError

    if path != "durable":
        monkeypatch.setattr(SwarmEngine, "_resume_run",
                            lambda self, task, step, results, board: self._resume(task, step, results, board))
    engine, store = _engine(tmp_path, "a", "b")
    runs: list[str] = []

    def step(name: str):
        async def run(prompt: str, context=None) -> dict:
            runs.append(name)
            return _ok(name)
        return run

    for name in ("a", "b"):
        engine.get_worker(name).dispatch = step(name)
    task = SwarmTask(prompt="p", type=TaskType.PIPELINE, workers=["a", "b"], timeout=30,
                     metadata={"hitl_checkpoints": [1]})
    try:
        paused = await engine.dispatch(task)
        assert paused.status == "paused" and runs == ["a"]
        result = await engine.approve_checkpoint(task.id)
        assert result.status == "success" and runs == ["a", "b"], result.error
        assert store.get_task(task.id).status == TaskStatus.COMPLETED
        first = await temporal.client.get_workflow_handle(f"kazma-swarm-{task.id}").describe()
        assert first.status == WorkflowExecutionStatus.COMPLETED
        resumed = temporal.client.get_workflow_handle(f"kazma-swarm-{task.id}-g2")
        if path != "durable":
            with pytest.raises(RPCError):  # the resume never reached Temporal
                await resumed.describe()
            return
        assert result.metadata["workflow_id"] == f"kazma-swarm-{task.id}-g2"
        assert (await resumed.describe()).status == WorkflowExecutionStatus.COMPLETED
    finally:
        store.close()


async def test_temporal_required_and_down_keeps_the_checkpoint_open(tmp_path, monkeypatch) -> None:
    """No server needed: required, with no worker running here."""
    import kazma_core.swarm.patterns as patterns

    async def _none(*_a, **_kw):
        return None

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    for name in ("KAZMA_SHARED_BREAKERS", "KAZMA_GATE_REGISTRY", "KAZMA_SELF_IMPROVEMENT"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    engine, store = _engine(tmp_path, "a", "b")
    for name in ("a", "b"):
        engine.get_worker(name).dispatch = (lambda n: (lambda prompt, context=None: _async_ok(n)))(name)
    task = SwarmTask(prompt="p", type=TaskType.PIPELINE, workers=["a", "b"], timeout=30,
                     metadata={"hitl_checkpoints": [1]})
    try:
        assert (await engine.dispatch(task)).status == "paused"
        monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
        monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "127.0.0.1:1")
        monkeypatch.delenv("KAZMA_TEMPORAL")
        refused = await engine.approve_checkpoint(task.id)
        assert refused.metadata["code"] == "temporal_unavailable" and refused.metadata["checkpoint_open"]
        assert engine.get_checkpoint_info(task.id) is not None
        assert store.get_task(task.id).status == TaskStatus.PAUSED
    finally:
        store.close()


async def _async_ok(name: str) -> dict:
    return _ok(name)


# ── lost acknowledgements, foreign activities ────────────────────────────


class _LossyClient:
    """The real client, losing one acknowledgement after the server acted."""

    def __init__(self, client: Any, lose: str) -> None:
        self._client, self._lose = client, lose

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:
        handle = await self._client.start_workflow(*args, **kwargs)
        if self._lose == "start":
            raise ConnectionError("start accepted; acknowledgement lost")
        if self._lose == "result":
            class _Lost:
                async def result(self_inner) -> Any:
                    await handle.result()
                    raise ConnectionError("result recorded; acknowledgement lost")

                def __getattr__(self_inner, name: str) -> Any:
                    return getattr(handle, name)
            return _Lost()
        return handle


@pytest.mark.parametrize("lose", ["start", "result"])
async def test_a_lost_acknowledgement_never_runs_anything_twice(temporal, tmp_path, monkeypatch, lose) -> None:
    real = await durable._client()

    async def lossy() -> Any:
        return _LossyClient(real, lose)

    monkeypatch.setattr(durable, "_client", lossy)
    engine, store = _engine(tmp_path, "w")
    runs: list[str] = []

    async def w(prompt: str, context=None) -> dict:
        runs.append(prompt)
        return _ok("w")

    engine.get_worker("w").dispatch = w
    task = SwarmTask(prompt="once", workers=["w"], timeout=30)
    try:
        result = await engine.dispatch(task)
        assert result.status == "success" and runs == ["once"], (result.status, result.error)
        assert store.get_task(task.id).status == TaskStatus.COMPLETED
        again = await engine.dispatch(task)  # the same id: answered from its record
        assert again.status == "success" and runs == ["once"]
    finally:
        store.close()


async def test_an_activity_no_run_here_waits_for_is_refused(temporal) -> None:
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ActivityError, ApplicationError

    from kazma_core.swarm.durable_temporal import KazmaSwarmRun

    task = SwarmTask(prompt="p", workers=["w"], timeout=30)
    payload = {"task_id": task.id, "generation": 1, "type": "dispatch", "resume": False,
               "activity_queue": durable.activity_queue(), "timeouts": durable.activity_timeouts(task)}
    with pytest.raises(WorkflowFailureError) as err:
        await temporal.client.execute_workflow(KazmaSwarmRun.run, payload, id=f"foreign-{task.id}",
                                               task_queue=durable._workflow_queue())
    cause = err.value.cause
    assert isinstance(cause, ActivityError) and isinstance(cause.cause, ApplicationError), cause
    assert cause.cause.type == durable.NOT_RUN and cause.cause.non_retryable


@pytest.mark.parametrize("ending", ["settled", "negative-control-old-ending"])
async def test_temporal_going_away_mid_run_holds_no_ending_hostage(tmp_path, monkeypatch, ending) -> None:
    """The server goes away while the work runs: the work goes on here and
    its own ending is the record (the caller is told what the store holds).
    The old ending told "outcome unknown" over a stored success."""
    from temporalio.testing import WorkflowEnvironment

    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _none)
    for name in ("KAZMA_SHARED_BREAKERS", "KAZMA_GATE_REGISTRY", "KAZMA_SELF_IMPROVEMENT"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(durable, "HEARTBEAT_EVERY_S", 0.2)
    monkeypatch.setattr(durable, "WORKFLOW_CLOSE_WAIT_S", 2.0)
    if ending != "settled":
        async def _old_wait(handle: Any, entry: Any) -> Any:
            try:
                await handle.result()  # the workflow's result alone
            except Exception as exc:
                return exc
            return None

        monkeypatch.setattr(durable, "_await_workflow", _old_wait)
        monkeypatch.setattr(durable, "_settle", _old_settle)
    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:
        if _REQUIRED:
            raise
        pytest.skip(f"Temporal's time-skipping test server could not start: {type(exc).__name__}: {exc}")
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", env.client.service_client.config.target_host)
    monkeypatch.setenv("KAZMA_TEMPORAL_QUEUE", f"kazma-test-{uuid.uuid4().hex[:10]}")
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    engine, store = _engine(tmp_path, "w")
    started, release = asyncio.Event(), asyncio.Event()

    async def worker(prompt: str, context=None) -> dict:
        started.set()
        await release.wait()
        return _ok("w", "done")

    engine.get_worker("w").dispatch = worker
    task = SwarmTask(prompt="p", workers=["w"], timeout=600)
    try:
        await durable.start_temporal_worker()
        run = asyncio.ensure_future(engine.dispatch(task))
        await asyncio.wait_for(started.wait(), 10)
        await env.shutdown()  # Temporal goes away
        release.set()
        result = await asyncio.wait_for(run, 60)
        stored = store.get_task(task.id).status
        assert stored == TaskStatus.COMPLETED
        if ending == "settled":
            assert result.status == "success", (result.status, result.error)
        else:
            assert result.status == "failed" and result.metadata["execution_outcome"] == "unknown"
    finally:
        await durable.stop_temporal_worker(timeout=3.0)
        store.close()


# ── a worker process killed mid-step ─────────────────────────────────────


def _child(server: Any, tmp_path: Path, *extra: str) -> tuple[subprocess.Popen, str, str, Path, Path, str]:
    db, markers = tmp_path / "tasks.db", tmp_path / "markers.txt"
    task_id = f"killed-{uuid.uuid4().hex[:8]}"
    queue = f"kazma-test-{uuid.uuid4().hex[:10]}"
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-I", str(CHILD), server.client.service_client.config.target_host, queue, str(db),
         str(markers), task_id, *extra],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc, task_id, queue, db, markers, ""


async def _ready(proc: subprocess.Popen) -> tuple[str, str, int]:
    lines: list[str] = []

    def _read() -> tuple[str, str, int] | None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("READY "):
                _, workflow_id, queue, pid = line.split()
                return workflow_id, queue, int(pid)
        return None

    found = await asyncio.wait_for(asyncio.to_thread(_read), 90)
    assert found, "".join(lines)[-3000:]
    return found


def _kill(proc: subprocess.Popen, pid: int) -> None:
    """Kill the interpreter itself, at once (a venv's python.exe on Windows
    is a launcher: killing it alone leaves the child running)."""
    import signal

    os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)  # TerminateProcess on Windows
    proc.wait(30)


async def _until_closed(handle: Any, seconds: float) -> None:
    from temporalio.client import WorkflowExecutionStatus

    until = time.monotonic() + seconds
    while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
        assert time.monotonic() < until, "the workflow is still running"
        await asyncio.sleep(0.2)


def _markers(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


async def test_a_killed_worker_process_is_held_unknown_and_nothing_runs_again(server, tmp_path, monkeypatch) -> None:
    from temporalio.api.enums.v1 import TimeoutType
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ActivityError
    from temporalio.exceptions import TimeoutError as TemporalTimeout

    proc, task_id, _, db, markers, _ = _child(server, tmp_path)
    try:
        workflow_id, child_queue, pid = await _ready(proc)
        _kill(proc, pid)
        killed = time.monotonic()
    finally:
        if proc.poll() is None:
            proc.kill()

    # The next start: the row is held as interrupted, its outcome unknown,
    # naming the step it stopped in.
    store = TaskStore(db_path=str(db))
    try:
        recovered = store.requeue_orphaned_running()
        assert task_id in recovered["failed"]
        row = store.get_task(task_id)
        meta = row.result.metadata
        assert row.status == TaskStatus.FAILED and meta["execution_outcome"] == "unknown"
        assert meta["step_journal"]["step"] == 2 and meta["step_journal"]["state"] == "begun"
        assert "step 2 (begun)" in row.result.error
    finally:
        store.close()

    # The restarted Kazma's workers poll the same workflow queue: they run the
    # workflow's next task, which ends it on the lost worker's heartbeat
    # timeout. The activity is not run again: never retried, and its queue
    # was the dead process's own.
    async with _restarted(server, monkeypatch, child_queue):
        handle = server.client.get_workflow_handle(workflow_id)
        await _until_closed(handle, 60)
        lost_after = time.monotonic() - killed
        with pytest.raises(WorkflowFailureError) as err:
            await handle.result()
        cause = err.value.cause
        assert isinstance(cause, ActivityError) and isinstance(cause.cause, TemporalTimeout), cause
        assert cause.cause.type == TimeoutType.TIMEOUT_TYPE_HEARTBEAT, cause.cause
        assert lost_after < 30, lost_after  # the 2 s heartbeat timeout, not the run's limit
        await asyncio.sleep(2.0)
    # Step 1 (committed) and step 2 (begun, held unknown) ran once; step 3 never.
    assert _markers(markers) == ["p1", "p2-begun"], _markers(markers)


class _restarted:
    """This process as the restarted Kazma: its Temporal workers on the dead
    process's workflow queue (a new activity queue of its own)."""

    def __init__(self, server: Any, monkeypatch: Any, child_queue: str) -> None:
        monkeypatch.setenv("KAZMA_TEMPORAL_HOST", server.client.service_client.config.target_host)
        monkeypatch.setenv("KAZMA_TEMPORAL_QUEUE", child_queue.rsplit("-run-", 1)[0])
        monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)

    async def __aenter__(self) -> None:
        await durable.start_temporal_worker()
        assert durable.worker_running()

    async def __aexit__(self, *exc: Any) -> None:
        await durable.stop_temporal_worker()


async def test_negative_control_a_killed_worker_without_heartbeat_or_journal(server, tmp_path, monkeypatch) -> None:
    """The durable path before 2026-10-15: with a restarted Kazma polling,
    Temporal still believes the run alive long after its process died (no
    heartbeat timeout: only the run's limit, minutes away, ends it), and the
    next start cannot name the step it stopped in (no step journal on a first
    run)."""
    from temporalio.client import WorkflowExecutionStatus

    proc, task_id, _, db, _, _ = _child(server, tmp_path, "control")
    try:
        workflow_id, child_queue, pid = await _ready(proc)
        _kill(proc, pid)
    finally:
        if proc.poll() is None:
            proc.kill()
    async with _restarted(server, monkeypatch, child_queue):
        await asyncio.sleep(8.0)
        handle = server.client.get_workflow_handle(workflow_id)
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        await handle.terminate("test cleanup")
    store = TaskStore(db_path=str(db))
    try:
        store.requeue_orphaned_running()
        row = store.get_task(task_id)
        assert "step_journal" not in (row.result.metadata or {})
        assert "step 2" not in row.result.error
    finally:
        store.close()
