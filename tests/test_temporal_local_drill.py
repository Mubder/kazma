"""Real Temporal protocol with fixture effects in a disposable local server."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.slow


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", ["start", "result"])
async def test_lost_acknowledgement_never_replays_effects(monkeypatch, lost_ack):
    pytest.importorskip("temporalio")
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from kazma_core.swarm import durable
    from kazma_core.swarm.durable_temporal import KazmaSwarmTask, kazma_swarm_dispatch
    from kazma_core.swarm.task import SwarmTask, TaskResult

    effects = []

    async def fixture_activity(payload):
        effects.append(payload["task_id"])
        return TaskResult(task_id=payload["task_id"], status="success").to_dict()

    monkeypatch.setattr(durable, "run_activity_payload", fixture_activity)
    async with await WorkflowEnvironment.start_local(ui=False) as environment:
        client = environment.client
        queue = "kazma-drill-" + uuid4().hex
        monkeypatch.setenv("KAZMA_TEMPORAL_HOST", client.service_client.config.target_host)
        monkeypatch.setenv("KAZMA_TEMPORAL_QUEUE", queue)
        monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
        monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)

        async def start(*args, **kwargs):
            handle = await client.start_workflow(*args, **kwargs)
            if lost_ack == "start":
                await handle.result()
                raise ConnectionError("Start accepted; acknowledgement lost")

            async def lost_result():
                await handle.result()
                raise ConnectionError("Effects committed; result acknowledgement lost")

            return SimpleNamespace(result=lost_result)

        proxy = SimpleNamespace(start_workflow=start, get_workflow_handle=client.get_workflow_handle)
        monkeypatch.setattr(Client, "connect", AsyncMock(return_value=proxy))
        engine = SimpleNamespace(_dispatch_inner=AsyncMock())
        task = SwarmTask(prompt="fixture only", id=uuid4().hex)
        async with Worker(client, task_queue=queue, workflows=[KazmaSwarmTask], activities=[kazma_swarm_dispatch]):
            result = await durable.run_via_durable(engine, task, 0, None)
            if lost_ack == "start":
                assert result.status == "success"
            else:
                assert result.metadata["execution_outcome"] == "unknown"
            # The same closed workflow ID is recovered, never executed afresh.
            recovered = await durable.run_via_durable(engine, task, 0, None)
            assert recovered.status == "success"
        assert effects == [task.id]
        engine._dispatch_inner.assert_not_awaited()
