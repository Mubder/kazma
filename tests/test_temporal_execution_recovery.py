"""The durable path's decisions that need no Temporal server.

A lost acknowledgement must never replay host side effects, and a run that
never started in this process never starts later. These run with a fake
Temporal SDK (so on every OS and Python, with or without the durable extra);
the same paths against Temporal's real test server are
``tests/test_swarm_temporal_durable.py``.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_core.swarm import durable
from kazma_core.swarm.task import SwarmTask


class _WorkflowFailureError(Exception):
    def __init__(self, cause=None) -> None:
        super().__init__("workflow failed")
        self.cause = cause


class _WorkflowAlreadyStartedError(Exception):
    pass


class _ApplicationError(Exception):
    def __init__(self, message, *, type=None, non_retryable=False) -> None:  # noqa: A002 (the SDK's name)
        super().__init__(message)
        self.type, self.non_retryable = type, non_retryable


@pytest.fixture
def temporal(monkeypatch):
    """A fake SDK and a fake client; this process's workers count as running."""
    client = SimpleNamespace(start_workflow=AsyncMock(), get_workflow_handle=lambda _: None)
    package = ModuleType("temporalio")
    package.__path__ = []  # a package, for "from temporalio import activity"
    sdk = ModuleType("temporalio.client")
    sdk.Client = SimpleNamespace(connect=AsyncMock(return_value=client))
    sdk.WorkflowFailureError = _WorkflowFailureError
    common = ModuleType("temporalio.common")
    common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
    exceptions = ModuleType("temporalio.exceptions")
    exceptions.WorkflowAlreadyStartedError = _WorkflowAlreadyStartedError
    exceptions.ApplicationError = _ApplicationError
    for name in ("ActivityError", "TerminatedError", "CancelledError", "TimeoutError"):
        setattr(exceptions, name, type(name, (Exception,), {}))
    exceptions.TimeoutType = SimpleNamespace(HEARTBEAT=4)
    activity = ModuleType("temporalio.activity")
    activity.info = lambda: SimpleNamespace(workflow_id="")
    package.activity = activity
    service = ModuleType("temporalio.service")
    service.RPCError = type("RPCError", (Exception,), {})
    workflow = ModuleType("kazma_core.swarm.durable_temporal")
    workflow.KazmaSwarmRun = SimpleNamespace(run="fixture-workflow")
    for name, module in (("temporalio", package), ("temporalio.client", sdk), ("temporalio.common", common),
                         ("temporalio.exceptions", exceptions), ("temporalio.activity", activity),
                         ("temporalio.service", service),
                         ("kazma_core.swarm.durable_temporal", workflow)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(durable, "_sdk_available", lambda: True)
    monkeypatch.setattr(durable, "worker_running", lambda: True)
    monkeypatch.setattr(durable, "_CLIENTS", {})
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "fixture.invalid:7233")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    return client, sdk.Client, activity


def _engine(**methods) -> SimpleNamespace:
    return SimpleNamespace(_active_tasks={}, _effects={}, _interrupts={}, **methods)


@pytest.mark.asyncio
async def test_a_submission_never_acknowledged_never_runs_here(temporal):
    """Start and result both lost: the run never started here, so it is
    settled as not run -- and refused if its activity arrives later."""
    client, _, activity = temporal
    client.start_workflow.side_effect = ConnectionError("uncertain submission")
    client.get_workflow_handle = lambda _: SimpleNamespace(result=AsyncMock(side_effect=ConnectionError()))
    run = AsyncMock()
    task = SwarmTask(prompt="fixture", id="uncertain")
    result = await durable.run_via_durable(_engine(_run_admitted=run), task, 0, None)
    assert result.status == "failed" and result.metadata["execution_outcome"] == "none_invoked"
    assert "will not run" in result.error
    run.assert_not_awaited()
    activity.info = lambda: SimpleNamespace(workflow_id=durable._workflow_id_for(task))
    with pytest.raises(_ApplicationError) as refused:
        await durable.run_activity_payload({"task_id": task.id})
    assert refused.value.type == durable.NOT_RUN and refused.value.non_retryable
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_connection_failure_before_submission_can_fall_back(temporal):
    _, factory, _ = temporal
    factory.connect.side_effect = ConnectionError("not submitted")
    run = AsyncMock(return_value="local-result")
    assert await durable.run_via_durable(_engine(_run_admitted=run), SwarmTask(prompt="fixture"), 0, None) \
        == "local-result"
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_failure_when_required_is_not_run(temporal, monkeypatch):
    _, factory, _ = temporal
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    factory.connect.side_effect = ConnectionError("not submitted")
    run = AsyncMock()
    result = await durable.run_via_durable(_engine(_run_admitted=run), SwarmTask(prompt="fixture"), 0, None)
    assert result.status == "failed" and "Temporal" in result.error
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_worker_here_falls_back_or_refuses(temporal, monkeypatch):
    """The activity runs only on this process's own queue: with no worker
    polling it, nothing is submitted (a workflow would wait for nobody)."""
    client, _, _ = temporal
    monkeypatch.setattr(durable, "worker_running", lambda: False)
    run = AsyncMock(return_value="local-result")
    assert await durable.run_via_durable(_engine(_run_admitted=run), SwarmTask(prompt="fixture"), 0, None) \
        == "local-result"
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    refused = await durable.run_via_durable(_engine(_run_admitted=run), SwarmTask(prompt="fixture"), 0, None)
    assert refused.status == "failed" and "worker is not running" in refused.error
    assert run.await_count == 1
    client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_workflow_per_run_never_reused_with_no_prompt(temporal):
    client, _, _ = temporal
    client.start_workflow.return_value = SimpleNamespace(result=AsyncMock(return_value={"status": "success"}))
    task = SwarmTask(prompt="the secret prompt", id="once")
    await durable.run_via_durable(_engine(_run_admitted=AsyncMock()), task, 0, None)
    kwargs = client.start_workflow.call_args.kwargs
    payload = client.start_workflow.call_args.args[1]
    assert kwargs["id"] == "kazma-swarm-once" and kwargs["id_reuse_policy"] == "reject_duplicate"
    assert kwargs["task_queue"] == durable._workflow_queue()
    assert payload["activity_queue"] == durable.activity_queue() != durable._workflow_queue()
    assert "the secret prompt" not in repr(payload)
    task.generation = 2  # an approved resume: a workflow of its own
    assert durable._workflow_id_for(task) == "kazma-swarm-once-g2"


@pytest.mark.asyncio
async def test_required_temporal_cannot_silently_disable(monkeypatch):
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    run = AsyncMock()
    result = await durable.run_via_durable(_engine(_run_admitted=run), SwarmTask(prompt="fixture"), 0, None)
    assert result.status == "failed"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_activity_is_never_retried_and_runs_on_its_processs_queue(monkeypatch):
    pytest.importorskip("temporalio")
    from kazma_core.swarm import durable_temporal as definitions

    execute = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(definitions.workflow, "execute_activity", execute)
    task = SwarmTask(prompt="p", timeout=30)
    payload = {"activity_queue": "q-run-tag", "timeouts": durable.activity_timeouts(task)}
    await definitions.KazmaSwarmRun().run(payload)
    kwargs = execute.call_args.kwargs
    assert kwargs["retry_policy"].maximum_attempts == 1
    assert kwargs["task_queue"] == "q-run-tag"
    assert kwargs["heartbeat_timeout"].total_seconds() == durable.HEARTBEAT_TIMEOUT_S
    assert kwargs["start_to_close_timeout"].total_seconds() == payload["timeouts"]["start_to_close"]
    # An older build's workflow replays as it was, never retried either.
    await definitions.KazmaSwarmTask().run({"task": {"timeout": 30}})
    assert execute.call_args.kwargs["retry_policy"].maximum_attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("broadcast", [False, True])
async def test_engine_cannot_bypass_required_temporal(monkeypatch, broadcast):
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import TaskType
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    engine = SwarmEngine()
    inner, direct = AsyncMock(), AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_inner", inner)
    monkeypatch.setattr(engine, "broadcast", direct)
    task = SwarmTask(prompt="fixture", type=TaskType.BROADCAST if broadcast else TaskType.DISPATCH)
    result = await engine.dispatch(task)
    assert result.status == "failed"
    assert "Required Temporal" in result.error
    assert not engine._active_tasks
    inner.assert_not_awaited()
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_uses_durable_workflow(temporal, monkeypatch):
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import TaskType
    client, _, _ = temporal
    engine = SwarmEngine()
    direct = AsyncMock()
    monkeypatch.setattr(engine, "_broadcast_task", direct)
    client.start_workflow.return_value = SimpleNamespace(result=AsyncMock(return_value={"status": "success"}))
    await engine.dispatch(SwarmTask(prompt="fixture", id="broadcast", type=TaskType.BROADCAST))
    client.start_workflow.assert_awaited_once()
    direct.assert_not_awaited()  # only the activity, in this process, runs it


@pytest.mark.asyncio
async def test_activity_routes_broadcast_to_broadcast_executor(monkeypatch):
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import TaskType
    engine = SwarmEngine()
    direct = AsyncMock(return_value="broadcast-result")
    # The broadcast executor (``broadcast`` is now a wrapper over dispatch,
    # Swarm audit SW-E06; the activity runs the executor inside it).
    monkeypatch.setattr(engine, "_broadcast_task", direct)
    task = SwarmTask(prompt="fixture", type=TaskType.BROADCAST)
    assert await engine._dispatch_inner(task, 0, None) == "broadcast-result"
    direct.assert_awaited_once_with(task)
