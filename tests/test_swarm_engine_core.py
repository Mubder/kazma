"""Tests for the SwarmEngine core orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from kazma_core.swarm import (
    SwarmConfig,
    SwarmTask,
    TaskResult,
    TaskType,
    WorkerConfig,
)
from kazma_core.swarm.manager import SwarmManager
from kazma_ui.app import create_app
from kazma_ui.swarm_panel import _reset_swarm_state


@pytest.fixture
def empty_config() -> SwarmConfig:
    return SwarmConfig(enabled=True, workers=[])


@pytest.fixture
def in_process_config() -> SwarmConfig:
    return SwarmConfig(
        enabled=True,
        workers=[
            WorkerConfig(
                name="brain",
                type="in_process",
                model="gpt-4o-mini",
                provider="openai",
                role="reasoning",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_engine_dispatch_returns_task_result_for_single_worker(in_process_config):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(in_process_config)
    worker = engine.get_worker("brain")
    assert worker is not None
    worker.dispatch = AsyncMock(return_value={
        "worker": "brain",
        "task_id": "task-brain",
        "status": "success",
        "output": "dispatch complete",
        "error": None,
    })

    result = await engine.dispatch(
        SwarmTask(prompt="Analyze the codebase", workers=["brain"])
    )

    assert isinstance(result, TaskResult)
    assert result.status == "success"
    assert result.aggregated_output == "dispatch complete"
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "brain"


@pytest.mark.asyncio
async def test_engine_dispatch_missing_worker_returns_failed_result(empty_config):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)

    result = await engine.dispatch(SwarmTask(prompt="Do work", workers=["ghost"]))

    assert result.status == "failed"
    assert result.aggregated_output is None
    assert "Worker 'ghost' not found." in (result.error or "")


@pytest.mark.asyncio
async def test_engine_broadcast_runs_workers_concurrently_and_surfaces_partial_failure(
    empty_config,
):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))
    engine.add_worker(WorkerConfig(name="beta", type="in_process"))

    started: set[str] = set()
    ready = asyncio.Event()

    async def alpha_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        started.add("alpha")
        if len(started) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=0.1)
        return {
            "worker": "alpha",
            "task_id": "task-alpha",
            "status": "success",
            "output": f"alpha:{task}:{context}",
            "error": None,
        }

    async def beta_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        started.add("beta")
        if len(started) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=0.1)
        return {
            "worker": "beta",
            "task_id": "task-beta",
            "status": "error",
            "output": "",
            "error": "beta failed",
        }

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment,union-attr]

    result = await asyncio.wait_for(
        engine.broadcast(
            SwarmTask(
                prompt="Broadcast task",
                context="ctx",
                type=TaskType.BROADCAST,
            )
        ),
        timeout=0.2,
    )

    assert started == {"alpha", "beta"}
    assert result.status == "partial"
    assert len(result.worker_results) == 2
    by_worker = {item.worker: item for item in result.worker_results}
    assert by_worker["alpha"].status == "success"
    assert by_worker["beta"].status == "error"


@pytest.mark.asyncio
async def test_engine_broadcast_with_zero_workers_returns_empty_success(empty_config):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)

    result = await engine.broadcast(
        SwarmTask(prompt="Nothing to do", type=TaskType.BROADCAST)
    )

    assert result.status == "success"
    assert result.worker_results == []
    assert result.aggregated_output is None


@pytest.mark.asyncio
async def test_engine_broadcast_shares_blackboard_and_captures_snapshot(empty_config):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))
    engine.add_worker(WorkerConfig(name="beta", type="in_process"))

    seen_blackboards: list[int] = []

    async def alpha_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        assert hasattr(context, "blackboard")
        seen_blackboards.append(id(context.blackboard))
        await context.blackboard.update(
            "workers",
            lambda current: [*(current or []), "alpha"],
        )
        return {
            "worker": "alpha",
            "task_id": "task-alpha",
            "status": "success",
            "output": f"alpha:{context}",
            "error": None,
        }

    async def beta_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        assert hasattr(context, "blackboard")
        seen_blackboards.append(id(context.blackboard))
        await context.blackboard.update(
            "workers",
            lambda current: [*(current or []), "beta"],
        )
        return {
            "worker": "beta",
            "task_id": "task-beta",
            "status": "success",
            "output": f"beta:{context}",
            "error": None,
        }

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.broadcast(
        SwarmTask(
            prompt="Broadcast task",
            context="ctx",
            workers=["alpha", "beta"],
            type=TaskType.BROADCAST,
        )
    )

    assert len(set(seen_blackboards)) == 1
    assert set(result.metadata["blackboard"]["workers"]) == {"alpha", "beta"}
    assert [item.output for item in result.worker_results] == ["alpha:ctx", "beta:ctx"]


@pytest.mark.asyncio
async def test_engine_broadcast_creates_fresh_blackboard_per_task_group(empty_config):
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))

    seen_previous_values: list[str | None] = []

    async def alpha_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        seen_previous_values.append(await context.blackboard.get("shared"))
        await context.blackboard.set("shared", task)
        return {
            "worker": "alpha",
            "task_id": f"task-{task}",
            "status": "success",
            "output": task,
            "error": None,
        }

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment,union-attr]

    first = await engine.broadcast(
        SwarmTask(
            prompt="first",
            context="ctx",
            workers=["alpha"],
            type=TaskType.BROADCAST,
        )
    )
    second = await engine.broadcast(
        SwarmTask(
            prompt="second",
            context="ctx",
            workers=["alpha"],
            type=TaskType.BROADCAST,
        )
    )

    assert seen_previous_values == [None, None]
    assert first.metadata["blackboard"]["shared"] == "first"
    assert second.metadata["blackboard"]["shared"] == "second"


def test_swarm_engine_singleton_accessors(empty_config):
    from kazma_core.swarm.engine import (
        SwarmEngine,
        get_swarm_engine,
        set_swarm_engine,
    )

    engine = SwarmEngine(empty_config)
    set_swarm_engine(engine)
    assert get_swarm_engine() is engine
    set_swarm_engine(None)
    assert get_swarm_engine() is None


@pytest.mark.asyncio
async def test_swarm_manager_dispatch_wrapper_delegates_to_engine(in_process_config):
    manager = SwarmManager(in_process_config)
    expected = TaskResult(
        task_id="task-1",
        status="success",
        aggregated_output="wrapped output",
        worker_results=[
            {
                "worker": "brain",
                "task_id": "task-1",
                "status": "success",
                "output": "wrapped output",
                "error": None,
            }
        ],
    )
    manager.engine.dispatch = AsyncMock(return_value=expected)

    result = await manager.dispatch("brain", "Wrap this", context="ctx")

    manager.engine.dispatch.assert_awaited_once()
    dispatched_task = manager.engine.dispatch.await_args.args[0]
    assert dispatched_task.prompt == "Wrap this"
    assert dispatched_task.context == "ctx"
    assert dispatched_task.workers == ["brain"]
    assert result == {
        "worker": "brain",
        "task_id": "task-1",
        "status": "success",
        "output": "wrapped output",
        "error": None,
        "tokens_used": 0,
        "cost": 0.0,
        "duration_seconds": 0.0,
        "handoffs": [],
    }


@pytest.mark.asyncio
async def test_swarm_manager_broadcast_wrapper_delegates_to_engine(empty_config):
    manager = SwarmManager(empty_config)
    expected = TaskResult(
        task_id="task-2",
        status="partial",
        worker_results=[
            {
                "worker": "alpha",
                "task_id": "task-2",
                "status": "success",
                "output": "alpha output",
                "error": None,
            },
            {
                "worker": "beta",
                "task_id": "task-2",
                "status": "error",
                "output": "",
                "error": "beta failed",
            },
        ],
    )
    manager.engine.broadcast = AsyncMock(return_value=expected)

    result = await manager.broadcast("Broadcast this", context="ctx")

    manager.engine.broadcast.assert_awaited_once()
    dispatched_task = manager.engine.broadcast.await_args.args[0]
    assert dispatched_task.prompt == "Broadcast this"
    assert dispatched_task.context == "ctx"
    assert dispatched_task.type == TaskType.BROADCAST
    assert [item["worker"] for item in result] == ["alpha", "beta"]
    assert result[1]["status"] == "error"


def test_create_app_keeps_swarm_available_when_gateway_init_fails():
    _reset_swarm_state()

    async def fake_dispatch(
        self,
        task: str,
        context: str = "",
    ) -> dict[str, str | None]:
        return {
            "worker": self.name,
            "task_id": "sub-test",
            "status": "success",
            "output": f"handled:{task}:{context}",
            "error": None,
        }

    with (
        patch("kazma_gateway.GatewayManager", side_effect=RuntimeError("gateway boom")),
        patch("kazma_core.swarm.worker.InProcessWorker.dispatch", new=fake_dispatch),
    ):
        app = create_app(config_path=str(Path("G:/GitHubRepos/kazma/kazma.yaml")))
        with TestClient(app) as client:
            status = client.get("/api/swarm/status")
            assert status.status_code == 200

            created = client.post(
                "/api/swarm/workers",
                json={"name": "solo", "type": "in-process"},
            )
            assert created.status_code == 201

            dispatched = client.post(
                "/api/swarm/dispatch",
                json={"workers": ["solo"], "task": "Gateway independent"},
            )
            assert dispatched.status_code == 200
            payload = dispatched.json()
            assert payload["results"][0]["status"] == "success"
            assert payload["results"][0]["output"] == "handled:Gateway independent:"

            app_status = client.get("/api/status")
            assert app_status.status_code == 200
            assert any(
                item["subsystem"] == "gateway"
                for item in app_status.json()["init_errors"]
            )


@pytest.mark.asyncio
async def test_engine_public_facades_for_ui_and_sse(empty_config):
    """Test new public methods added during audit remediation (list_workers, task handles, sse_bus)."""
    from kazma_core.swarm.engine import SwarmEngine

    engine = SwarmEngine(empty_config)

    # list_workers
    workers = engine.list_workers()
    assert isinstance(workers, list)

    # task handle registration
    mock_handle = object()
    engine.register_task_handle("task-123", mock_handle)
    assert engine.get_task_handle("task-123") is mock_handle
    engine.unregister_task_handle("task-123")
    assert engine.get_task_handle("task-123") is None

    # active task
    assert engine.get_active_task("nonexistent") is None

    # sse bus
    mock_bus = object()
    engine.set_sse_bus(mock_bus)
    # no crash, and internal set
    assert getattr(engine, "_sse_bus", None) is mock_bus


def test_config_store_pragma_helper():
    """Basic smoke for centralized pragma helper."""
    from kazma_core.config_store import apply_sqlite_pragmas
    import sqlite3
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        conn = sqlite3.connect(db)
        try:
            apply_sqlite_pragmas(conn)
            # Should not raise, and pragmas applied
            cur = conn.execute("PRAGMA journal_mode")
            mode = cur.fetchone()[0].upper()
            assert mode in ("WAL", "MEMORY")  # WAL preferred
        finally:
            conn.close()
