"""Tests for Human-in-the-Loop (HITL) checkpoints in pipeline execution.

Validation contract assertions covered:
    VAL-HITL-001: Pipeline pauses at defined checkpoint step
    VAL-HITL-003: POST approve resumes pipeline
    VAL-HITL-004: POST reject aborts pipeline
    VAL-HITL-005: Checkpoint timeout auto-rejects
    VAL-HITL-006: Multiple checkpoints work sequentially
    VAL-HITL-007: Checkpoint state persists across refresh/restart
    VAL-ORCH-010: Pipeline pauses at HITL checkpoint and resumes on approval
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskStatus, TaskType, WorkerConfig
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


def _make_worker_result(worker: str, output: str, task_id: str = "test-task") -> dict:
    """Helper to build a worker result dict."""
    return {
        "worker": worker,
        "task_id": task_id,
        "status": "success",
        "output": output,
        "error": None,
    }


# ---------------------------------------------------------------------------
# VAL-HITL-001: Pipeline pauses at defined checkpoint step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_pauses_at_checkpoint_step(empty_config):
    """VAL-HITL-001: Pipeline with checkpoint at step 2 pauses after step 2 completes.

    Status should be 'paused' and only the first 2 workers should have executed.
    """
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    async def gamma_dispatch(task: str, context=None):
        call_log.append("gamma")
        return _make_worker_result("gamma", "gamma output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process the request",
        context="base context",
        workers=["alpha", "beta", "gamma"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2]},
    )

    result = await engine.dispatch(task)

    # Pipeline should have paused after step 2
    assert result.status == "paused"
    assert len(result.worker_results) == 2
    assert [r.worker for r in result.worker_results] == ["alpha", "beta"]
    assert call_log == ["alpha", "beta"]  # gamma not called
    assert result.metadata.get("checkpoint") is not None
    assert result.metadata["checkpoint"]["step"] == 2
    assert result.metadata["checkpoint"]["needs_approval"] is True
    assert "beta output" in result.metadata["checkpoint"]["output_preview"]


@pytest.mark.asyncio
async def test_pipeline_checkpoint_at_step_1(empty_config):
    """Pipeline pauses after the very first step."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [1]},
    )

    result = await engine.dispatch(task)

    assert result.status == "paused"
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "alpha"
    assert call_log == ["alpha"]
    assert result.metadata["checkpoint"]["step"] == 1


# ---------------------------------------------------------------------------
# VAL-HITL-003: POST approve resumes pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_resumes_pipeline_after_checkpoint(empty_config):
    """VAL-HITL-003: Approving a checkpointed pipeline resumes remaining steps."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    async def gamma_dispatch(task: str, context=None):
        call_log.append("gamma")
        return _make_worker_result("gamma", "gamma output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        context="base",
        workers=["alpha", "beta", "gamma"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2]},
    )

    # Dispatch - should pause at step 2
    pause_result = await engine.dispatch(task)
    assert pause_result.status == "paused"
    task_id = pause_result.task_id

    # Approve - should resume and complete
    approve_result = await engine.approve_checkpoint(task_id)
    assert approve_result is not None
    assert approve_result.status == "success"
    assert len(approve_result.worker_results) == 3
    assert [r.worker for r in approve_result.worker_results] == ["alpha", "beta", "gamma"]
    assert approve_result.aggregated_output == "gamma output"
    assert call_log == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_approve_on_non_paused_task_returns_none(empty_config):
    """Approving a non-paused task returns None."""
    engine = SwarmEngine(empty_config)
    result = await engine.approve_checkpoint("nonexistent-task-id")
    assert result is None


# ---------------------------------------------------------------------------
# VAL-HITL-004: POST reject aborts pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_aborts_pipeline(empty_config):
    """VAL-HITL-004: Rejecting a checkpointed pipeline aborts with status=failed."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    gamma_dispatch = AsyncMock(return_value=_make_worker_result("gamma", "gamma output"))

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta", "gamma"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2]},
    )

    # Dispatch - should pause at step 2
    pause_result = await engine.dispatch(task)
    assert pause_result.status == "paused"
    task_id = pause_result.task_id

    # Reject - should abort
    reject_result = await engine.reject_checkpoint(task_id)
    assert reject_result is not None
    assert reject_result.status == "failed"
    assert len(reject_result.worker_results) == 2  # Only the 2 completed steps
    assert reject_result.error is not None
    assert "rejected" in reject_result.error.lower()

    # Gamma should never have been called
    gamma_dispatch.assert_not_awaited()
    assert call_log == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_reject_on_non_paused_task_returns_none(empty_config):
    """Rejecting a non-paused task returns None."""
    engine = SwarmEngine(empty_config)
    result = await engine.reject_checkpoint("nonexistent-task-id")
    assert result is None


# ---------------------------------------------------------------------------
# VAL-HITL-005: Checkpoint timeout auto-rejects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_timeout_auto_rejects(empty_config):
    """VAL-HITL-005: Configurable timeout auto-rejects the checkpoint."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    gamma_dispatch = AsyncMock(return_value=_make_worker_result("gamma", "gamma output"))

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta", "gamma"],
        type=TaskType.PIPELINE,
        metadata={
            "hitl_checkpoints": [2],
            "checkpoint_timeout": 0.1,  # 100ms timeout
        },
    )

    # Dispatch - should pause at step 2
    pause_result = await engine.dispatch(task)
    assert pause_result.status == "paused"

    # Wait for timeout to fire
    await asyncio.sleep(0.3)

    # Task should have been auto-rejected
    task_obj = engine.get_task(pause_result.task_id)
    assert task_obj is not None
    assert task_obj.status in (TaskStatus.FAILED, TaskStatus.COMPLETED)
    if task_obj.result:
        assert task_obj.result.status == "failed"
        assert task_obj.result.error is not None
        assert "timed out" in task_obj.result.error.lower()

    gamma_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# VAL-HITL-006: Multiple checkpoints work sequentially
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_checkpoints_work_sequentially(empty_config):
    """VAL-HITL-006: Pipeline with checkpoints at steps 2 and 4 pauses at each."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def make_dispatch(name: str):
        async def dispatch(task: str, context=None):
            call_log.append(name)
            return _make_worker_result(name, f"{name} output")
        return dispatch

    for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
        engine.get_worker(name).dispatch = await make_dispatch(name)  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta", "gamma", "delta", "epsilon"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2, 4]},
    )

    # First dispatch - should pause at step 2
    result1 = await engine.dispatch(task)
    assert result1.status == "paused"
    assert result1.metadata["checkpoint"]["step"] == 2
    assert call_log == ["alpha", "beta"]

    # Approve first checkpoint - should continue and pause at step 4
    result2 = await engine.approve_checkpoint(result1.task_id)
    assert result2 is not None
    assert result2.status == "paused"
    assert result2.metadata["checkpoint"]["step"] == 4
    assert call_log == ["alpha", "beta", "gamma", "delta"]

    # Approve second checkpoint - should complete
    result3 = await engine.approve_checkpoint(result2.task_id)
    assert result3 is not None
    assert result3.status == "success"
    assert len(result3.worker_results) == 5
    assert call_log == ["alpha", "beta", "gamma", "delta", "epsilon"]
    assert result3.aggregated_output == "epsilon output"


@pytest.mark.asyncio
async def test_reject_at_second_checkpoint(empty_config):
    """Rejecting at the second checkpoint aborts with partial results."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma", "delta"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    call_log: list[str] = []

    async def make_dispatch(name: str):
        async def dispatch(task: str, context=None):
            call_log.append(name)
            return _make_worker_result(name, f"{name} output")
        return dispatch

    for name in ("alpha", "beta", "gamma", "delta"):
        engine.get_worker(name).dispatch = await make_dispatch(name)  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta", "gamma", "delta"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2, 3]},
    )

    # First dispatch - pause at step 2
    result1 = await engine.dispatch(task)
    assert result1.status == "paused"

    # Approve first checkpoint - pause at step 3
    result2 = await engine.approve_checkpoint(result1.task_id)
    assert result2 is not None
    assert result2.status == "paused"

    # Reject second checkpoint
    result3 = await engine.reject_checkpoint(result2.task_id)
    assert result3 is not None
    assert result3.status == "failed"
    assert len(result3.worker_results) == 3  # alpha, beta, gamma
    assert call_log == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# VAL-HITL-007: Checkpoint state persists across refresh/restart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_state_persists_in_engine(empty_config):
    """VAL-HITL-007: Paused checkpoint state is queryable and persists in the engine."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        return _make_worker_result("beta", "beta output")

    async def gamma_dispatch(task: str, context=None):
        return _make_worker_result("gamma", "gamma output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha", "beta", "gamma"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [2]},
    )

    result = await engine.dispatch(task)
    assert result.status == "paused"
    task_id = result.task_id

    # Task should be findable in history and show as paused
    stored_task = engine.get_task(task_id)
    assert stored_task is not None
    assert stored_task.status == TaskStatus.PAUSED

    # Active checkpoint should be queryable
    checkpoint_info = engine.get_checkpoint_info(task_id)
    assert checkpoint_info is not None
    assert checkpoint_info.step == 2
    assert checkpoint_info.needs_approval is True

    # Can still approve after querying
    approve_result = await engine.approve_checkpoint(task_id)
    assert approve_result is not None
    assert approve_result.status == "success"


@pytest.mark.asyncio
async def test_list_paused_tasks(empty_config):
    """Paused tasks appear in the task list with paused status."""
    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

    task = SwarmTask(
        prompt="Process",
        workers=["alpha"],
        type=TaskType.PIPELINE,
        metadata={"hitl_checkpoints": [1]},
    )

    result = await engine.dispatch(task)
    assert result.status == "paused"

    # List tasks should include the paused task
    tasks = engine.list_tasks()
    paused_tasks = [t for t in tasks if t.status == TaskStatus.PAUSED]
    assert len(paused_tasks) >= 1
    assert any(t.id == result.task_id for t in paused_tasks)


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

def test_approve_endpoint_resumes_pipeline():
    """POST /api/swarm/tasks/{id}/approve resumes a paused pipeline."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "beta", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "gamma", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    call_log: list[str] = []

    async def alpha_dispatch(task: str, context=None):
        call_log.append("alpha")
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        call_log.append("beta")
        return _make_worker_result("beta", "beta output")

    async def gamma_dispatch(task: str, context=None):
        call_log.append("gamma")
        return _make_worker_result("gamma", "gamma output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
    engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

    # Dispatch with checkpoint at step 2
    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha", "beta", "gamma"],
            "task": "Process it",
            "metadata": {"hitl_checkpoints": [2]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "paused"
    task_id = payload["task_id"]

    # Approve via API
    approve_response = client.post(f"/api/swarm/tasks/{task_id}/approve")
    assert approve_response.status_code == 200
    approve_payload = approve_response.json()
    assert approve_payload["status"] == "success"
    assert len(approve_payload["worker_results"]) == 3
    assert call_log == ["alpha", "beta", "gamma"]


def test_reject_endpoint_aborts_pipeline():
    """POST /api/swarm/tasks/{id}/reject aborts a paused pipeline."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "beta", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    async def beta_dispatch(task: str, context=None):
        return _make_worker_result("beta", "beta output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
    engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

    # Dispatch with checkpoint at step 1
    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha", "beta"],
            "task": "Process it",
            "metadata": {"hitl_checkpoints": [1]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "paused"
    task_id = payload["task_id"]

    # Reject via API
    reject_response = client.post(f"/api/swarm/tasks/{task_id}/reject")
    assert reject_response.status_code == 200
    reject_payload = reject_response.json()
    assert reject_payload["status"] == "failed"
    assert len(reject_payload["worker_results"]) == 1


def test_approve_nonexistent_task_returns_404():
    """Approving a nonexistent task returns 404."""
    client = _build_client()
    response = client.post("/api/swarm/tasks/nonexistent-task-id/approve")
    assert response.status_code == 404


def test_reject_nonexistent_task_returns_404():
    """Rejecting a nonexistent task returns 404."""
    client = _build_client()
    response = client.post("/api/swarm/tasks/nonexistent-task-id/reject")
    assert response.status_code == 404


def test_approve_non_paused_task_returns_409():
    """Approving a task that isn't paused returns 409 Conflict."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

    # Dispatch without checkpoint (completes normally)
    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha"],
            "task": "Process it",
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    # Try to approve completed task
    approve_response = client.post(f"/api/swarm/tasks/{task_id}/approve")
    assert approve_response.status_code == 409


def test_checkpoint_info_in_api_response():
    """Checkpoint info is included in the API dispatch response."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha"],
            "task": "Process it",
            "metadata": {"hitl_checkpoints": [1]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "paused"
    assert payload.get("checkpoint") is not None
    assert payload["checkpoint"]["step"] == 1
    assert payload["checkpoint"]["needs_approval"] is True
    assert "task_id" in payload["checkpoint"]


def test_checkpoint_timeout_in_metadata():
    """Checkpoint timeout is configurable via metadata."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "alpha", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    async def alpha_dispatch(task: str, context=None):
        return _make_worker_result("alpha", "alpha output")

    engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "pipeline",
            "workers": ["alpha"],
            "task": "Process it",
            "metadata": {"hitl_checkpoints": [1], "checkpoint_timeout": 60},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "paused"
