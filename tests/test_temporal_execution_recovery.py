"""A lost workflow acknowledgement must never replay host side effects."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_core.swarm import durable
from kazma_core.swarm.task import SwarmTask


@pytest.fixture
def temporal(monkeypatch):
    client = SimpleNamespace(start_workflow=AsyncMock(), get_workflow_handle=lambda _: None)
    sdk = ModuleType("temporalio.client")
    sdk.Client = SimpleNamespace(connect=AsyncMock(return_value=client))
    common = ModuleType("temporalio.common")
    common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
    workflow = ModuleType("kazma_core.swarm.durable_temporal")
    workflow.KazmaSwarmTask = SimpleNamespace(run="fixture-workflow")
    monkeypatch.setitem(sys.modules, "temporalio.client", sdk)
    monkeypatch.setitem(sys.modules, "temporalio.common", common)
    monkeypatch.setitem(sys.modules, "kazma_core.swarm.durable_temporal", workflow)
    monkeypatch.setattr(durable, "_sdk_available", lambda: True)
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "fixture.invalid:7233")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    return client, sdk.Client


@pytest.mark.asyncio
async def test_lost_result_does_not_dispatch_again(temporal):
    client, _ = temporal
    dispatch = AsyncMock(return_value={"task_id": "lost-result", "status": "success"})

    async def executed_then_lost():
        await dispatch()
        raise ConnectionError("lost result acknowledgement")

    client.start_workflow.return_value = SimpleNamespace(result=executed_then_lost)
    result = await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=dispatch), SwarmTask(prompt="fixture", id="lost-result"), 0, None
    )
    assert dispatch.await_count == 1
    assert result.metadata["execution_outcome"] == "unknown"
    assert result.metadata["workflow_id"] == "kazma-swarm-lost-result"


@pytest.mark.asyncio
async def test_lost_start_acknowledgement_recovers_existing_workflow(temporal):
    client, _ = temporal
    client.start_workflow.side_effect = ConnectionError("start accepted, acknowledgement lost")
    recovered = AsyncMock(return_value={"task_id": "recover", "status": "success"})
    client.get_workflow_handle = lambda wid: SimpleNamespace(result=recovered)
    dispatch = AsyncMock()
    result = await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=dispatch), SwarmTask(prompt="fixture", id="recover"), 0, None
    )
    assert result.status == "success"
    dispatch.assert_not_awaited()
    recovered.assert_awaited_once()


@pytest.mark.asyncio
async def test_unresolved_submission_is_held_without_local_execution(temporal):
    client, _ = temporal
    client.start_workflow.side_effect = ConnectionError("uncertain submission")
    client.get_workflow_handle = lambda _: SimpleNamespace(result=AsyncMock(side_effect=ConnectionError()))
    dispatch = AsyncMock()
    result = await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=dispatch), SwarmTask(prompt="fixture"), 0, None
    )
    assert result.status == "failed"
    assert result.metadata["execution_outcome"] == "unknown"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_connection_failure_before_submission_can_fall_back(temporal):
    _, factory = temporal
    factory.connect.side_effect = ConnectionError("not submitted")
    dispatch = AsyncMock(return_value="local-result")
    assert await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=dispatch), SwarmTask(prompt="fixture"), 0, None
    ) == "local-result"
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_closed_workflow_id_cannot_be_reused(temporal):
    client, _ = temporal
    client.start_workflow.return_value = SimpleNamespace(
        result=AsyncMock(return_value={"task_id": "once", "status": "success"})
    )
    await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=AsyncMock()), SwarmTask(prompt="fixture", id="once"), 0, None
    )
    assert client.start_workflow.call_args.kwargs["id_reuse_policy"] == "reject_duplicate"


@pytest.mark.asyncio
async def test_required_temporal_cannot_silently_disable(monkeypatch):
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    dispatch = AsyncMock()
    result = await durable.run_via_durable(
        SimpleNamespace(_dispatch_inner=dispatch), SwarmTask(prompt="fixture"), 0, None
    )
    assert result.status == "failed"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_activity_does_not_retry_arbitrary_effects(monkeypatch):
    pytest.importorskip("temporalio")
    from kazma_core.swarm import durable_temporal as definitions
    execute = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(definitions.workflow, "execute_activity", execute)
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
    client, _ = temporal
    engine = SwarmEngine()
    direct = AsyncMock()
    monkeypatch.setattr(engine, "broadcast", direct)
    client.start_workflow.return_value = SimpleNamespace(result=AsyncMock(return_value={"task_id": "broadcast", "status": "success"}))
    result = await engine.dispatch(SwarmTask(prompt="fixture", id="broadcast", type=TaskType.BROADCAST))
    assert result.status == "success"
    client.start_workflow.assert_awaited_once()
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_routes_broadcast_to_broadcast_executor(monkeypatch):
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import TaskType
    engine = SwarmEngine()
    direct = AsyncMock(return_value="broadcast-result")
    monkeypatch.setattr(engine, "broadcast", direct)
    task = SwarmTask(prompt="fixture", type=TaskType.BROADCAST)
    assert await engine._dispatch_inner(task, 0, None) == "broadcast-result"
    direct.assert_awaited_once_with(task)
