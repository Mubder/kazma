"""Tests for the swarm pipeline orchestration pattern."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskType, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine, get_swarm_engine
from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router


@pytest.fixture
def empty_config() -> SwarmConfig:
    return SwarmConfig(enabled=True, workers=[])


def _build_client() -> TestClient:
    _reset_swarm_state()
    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
    app.include_router(create_swarm_router(templates))
    return TestClient(app)


@pytest.mark.asyncio
async def test_engine_pipeline_chains_outputs_and_shares_blackboard(empty_config):
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    seen_contexts: dict[str, str] = {}
    seen_blackboards: list[int] = []

    async def alpha_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        seen_contexts["alpha"] = str(context)
        seen_blackboards.append(id(context.blackboard))
        await context.blackboard.set("alpha_note", "from alpha")
        return {
            "worker": "alpha",
            "task_id": "alpha-task",
            "status": "success",
            "output": "alpha output",
            "error": None,
        }

    async def beta_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        seen_contexts["beta"] = str(context)
        seen_blackboards.append(id(context.blackboard))
        assert await context.blackboard.get("alpha_note") == "from alpha"
        await context.blackboard.set("beta_note", "from beta")
        return {
            "worker": "beta",
            "task_id": "beta-task",
            "status": "success",
            "output": "beta output",
            "error": None,
        }

    async def gamma_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        seen_contexts["gamma"] = str(context)
        seen_blackboards.append(id(context.blackboard))
        assert await context.blackboard.get("alpha_note") == "from alpha"
        assert await context.blackboard.get("beta_note") == "from beta"
        return {
            "worker": "gamma",
            "task_id": "gamma-task",
            "status": "success",
            "output": "gamma output",
            "error": None,
        }

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Process the request",
            context="base context",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.PIPELINE,
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "gamma output"
    assert [item.worker for item in result.worker_results] == ["alpha", "beta", "gamma"]
    assert len(set(seen_blackboards)) == 1
    assert "alpha output" in seen_contexts["beta"]
    assert "beta output" in seen_contexts["gamma"]
    assert result.metadata["blackboard_snapshot"]["alpha_note"] == "from alpha"
    assert result.metadata["blackboard_snapshot"]["beta_note"] == "from beta"


@pytest.mark.asyncio
async def test_engine_pipeline_single_worker_matches_dispatch_behavior(empty_config):
    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))
    engine.get_worker("alpha").dispatch = AsyncMock(return_value={
        "worker": "alpha",
        "task_id": "alpha-task",
        "status": "success",
        "output": "single output",
        "error": None,
    })  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Do the work",
            context="ctx",
            workers=["alpha"],
            type=TaskType.PIPELINE,
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "single output"
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "alpha"


@pytest.mark.asyncio
async def test_engine_pipeline_halts_on_midstream_failure_and_preserves_prior_results(
    empty_config,
):
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    engine.get_worker("alpha").dispatch = AsyncMock(return_value={
        "worker": "alpha",
        "task_id": "alpha-task",
        "status": "success",
        "output": "alpha output",
        "error": None,
    })  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(return_value={
        "worker": "beta",
        "task_id": "beta-task",
        "status": "error",
        "output": "",
        "error": "beta failed",
    })  # type: ignore[assignment,union-attr]
    gamma_dispatch = AsyncMock(return_value={
        "worker": "gamma",
        "task_id": "gamma-task",
        "status": "success",
        "output": "gamma output",
        "error": None,
    })
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Process the request",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.PIPELINE,
        )
    )

    assert result.status == "partial"
    assert len(result.worker_results) == 2
    assert [item.worker for item in result.worker_results] == ["alpha", "beta"]
    assert result.worker_results[0].status == "success"
    assert result.worker_results[1].status == "error"
    assert result.error == "beta failed"
    gamma_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_pipeline_enforces_timeout_per_step(empty_config):
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    engine.get_worker("alpha").dispatch = AsyncMock(return_value={
        "worker": "alpha",
        "task_id": "alpha-task",
        "status": "success",
        "output": "alpha output",
        "error": None,
    })  # type: ignore[assignment,union-attr]

    async def slow_beta(task: str, context: str = "") -> dict[str, str | None]:
        await asyncio.sleep(0.05)
        return {
            "worker": "beta",
            "task_id": "beta-task",
            "status": "success",
            "output": "beta output",
            "error": None,
        }

    engine.get_worker("beta").dispatch = slow_beta  # type: ignore[assignment,union-attr]
    gamma_dispatch = AsyncMock(return_value={
        "worker": "gamma",
        "task_id": "gamma-task",
        "status": "success",
        "output": "gamma output",
        "error": None,
    })
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Process the request",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.PIPELINE,
            timeout=0.01,
        )
    )

    assert result.status == "timeout"
    assert len(result.worker_results) == 2
    assert result.worker_results[-1].worker == "beta"
    assert result.worker_results[-1].status == "timeout"
    assert "timed out" in (result.error or "")
    gamma_dispatch.assert_not_awaited()


def test_pipeline_dispatch_endpoint_rejects_empty_workers_with_clear_error():
    client = _build_client()

    response = client.post(
        "/api/swarm/dispatch",
        json={"pattern": "pipeline", "workers": [], "task": "Plan it"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "message": "Pipeline requires at least one worker.",
    }


def test_pipeline_dispatch_endpoint_uses_pipeline_orchestration():
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "beta", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    async def alpha_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        return {
            "worker": "alpha",
            "task_id": "alpha-task",
            "status": "success",
            "output": "alpha output",
            "error": None,
        }

    async def beta_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        return {
            "worker": "beta",
            "task_id": "beta-task",
            "status": "success",
            "output": str(context),
            "error": None,
        }

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment,union-attr]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha", "beta"],
            "task": "Process the request",
            "context": "base context",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "success"
    assert payload["aggregated_output"] == payload["results"][-1]["output"]
    assert "alpha output" in payload["results"][-1]["output"]
    snapshot = payload["metadata"]["blackboard_snapshot"]
    assert snapshot["pipeline_outputs"][0]["output"] == "alpha output"
    assert "alpha output" in snapshot["last_output"]
