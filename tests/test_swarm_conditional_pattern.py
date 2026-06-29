"""Tests for the swarm conditional orchestration pattern."""

from __future__ import annotations

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


def _worker_result(
    worker: str,
    *,
    status: str = "success",
    output: str = "",
    error: str | None = None,
) -> dict[str, str | None]:
    return {
        "worker": worker,
        "task_id": f"{worker}-task",
        "status": status,
        "output": output,
        "error": error,
    }


def _add_workers(engine: SwarmEngine, *names: str) -> None:
    for name in names:
        engine.add_worker(WorkerConfig(name=name, type="in_process"))


# --- VAL-ORCH-031: Conditional routes to correct worker based on router output ---


@pytest.mark.asyncio
async def test_conditional_routes_to_correct_worker_based_on_router_output(empty_config):
    """Router outputs a decision; engine dispatches to the mapped worker."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder", "researcher")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="code")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("coder").dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Here is the code fix")
    )  # type: ignore[assignment,union-attr]
    researcher_dispatch = AsyncMock(
        return_value=_worker_result("researcher", output="Research results")
    )
    engine.get_worker("researcher").dispatch = researcher_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Fix the authentication bug",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={
                "routes": {
                    "code": "coder",
                    "research": "researcher",
                },
            },
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "Here is the code fix"
    assert result.metadata["route_taken"] == "code"
    assert len(result.worker_results) == 2
    assert result.worker_results[0].worker == "router"
    assert result.worker_results[0].status == "success"
    assert result.worker_results[1].worker == "coder"
    assert result.worker_results[1].status == "success"
    researcher_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_conditional_records_route_taken_in_metadata(empty_config):
    """Route decision is recorded in metadata.route_taken."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "writer")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="write")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("writer").dispatch = AsyncMock(
        return_value=_worker_result("writer", output="Documentation written")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Write documentation for the API",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={
                "routes": {
                    "write": "writer",
                    "code": "coder",
                },
            },
        )
    )

    assert result.status == "success"
    assert result.metadata["route_taken"] == "write"


@pytest.mark.asyncio
async def test_conditional_router_output_whitespace_stripped(empty_config):
    """Router output is stripped of whitespace before route lookup."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="  code  \n")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("coder").dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Fix the bug",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={"routes": {"code": "coder"}},
        )
    )

    assert result.status == "success"
    assert result.metadata["route_taken"] == "code"


@pytest.mark.asyncio
async def test_conditional_router_output_case_insensitive(empty_config):
    """Router output matching is case-insensitive."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="CODE")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("coder").dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Fix the bug",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={"routes": {"code": "coder"}},
        )
    )

    assert result.status == "success"
    assert result.metadata["route_taken"] == "code"


# --- VAL-ORCH-032: Conditional with no matching route falls back gracefully ---


@pytest.mark.asyncio
async def test_conditional_unmatched_route_falls_back_to_default(empty_config):
    """Unmatched route falls back to metadata.default worker if set."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder", "fallback")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="unknown_route")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("coder").dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("fallback").dispatch = AsyncMock(
        return_value=_worker_result("fallback", output="Handled by fallback")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Do something",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={
                "routes": {"code": "coder"},
                "default": "fallback",
            },
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "Handled by fallback"
    assert result.metadata["route_taken"] == "default"


@pytest.mark.asyncio
async def test_conditional_unmatched_route_no_default_returns_failed(empty_config):
    """Unmatched route with no default returns status=failed with clear error."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="unknown_route")
    )  # type: ignore[assignment,union-attr]
    coder_dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )
    engine.get_worker("coder").dispatch = coder_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Do something",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={"routes": {"code": "coder"}},
        )
    )

    assert result.status == "failed"
    assert "No route matched" in (result.error or "")
    assert result.metadata["route_taken"] is None
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "router"
    coder_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_conditional_default_worker_failure_propagated(empty_config):
    """When the default fallback worker itself fails, the error is surfaced."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "fallback")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="unmatched")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("fallback").dispatch = AsyncMock(
        return_value=_worker_result("fallback", status="error", error="Fallback crashed")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Do something",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={
                "routes": {"code": "coder"},
                "default": "fallback",
            },
        )
    )

    assert result.status == "failed"
    assert "Fallback crashed" in (result.error or "")
    assert result.metadata["route_taken"] == "default"


# --- VAL-ORCH-033: Conditional router failure surfaces clear error ---


@pytest.mark.asyncio
async def test_conditional_router_failure_no_downstream_execution(empty_config):
    """Router fails: status=failed, no downstream worker executes."""
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder")

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", status="error", error="Router crashed")
    )  # type: ignore[assignment,union-attr]
    coder_dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )
    engine.get_worker("coder").dispatch = coder_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Fix the bug",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            metadata={"routes": {"code": "coder"}},
        )
    )

    assert result.status == "failed"
    assert "Router crashed" in (result.error or "")
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "router"
    assert result.worker_results[0].status == "error"
    coder_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_conditional_router_timeout_no_downstream_execution(empty_config):
    """Router times out: status=timeout, no downstream worker executes."""
    import asyncio

    engine = SwarmEngine(empty_config)
    _add_workers(engine, "router", "coder")

    async def slow_router(task: str, context: str = "") -> dict[str, str | None]:
        await asyncio.sleep(1.0)
        return _worker_result("router", output="code")

    engine.get_worker("router").dispatch = slow_router  # type: ignore[assignment,union-attr]
    coder_dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code done")
    )
    engine.get_worker("coder").dispatch = coder_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Fix the bug",
            workers=["router"],
            type=TaskType.CONDITIONAL,
            timeout=0.05,
            metadata={"routes": {"code": "coder"}},
        )
    )

    assert result.status == "timeout"
    assert "timed out" in (result.error or "")
    assert len(result.worker_results) == 1
    assert result.worker_results[0].status == "timeout"
    coder_dispatch.assert_not_awaited()


# --- Edge cases ---


def test_conditional_dispatch_endpoint_rejects_empty_workers_with_clear_error():
    """Empty workers returns HTTP 400."""
    client = _build_client()

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "conditional",
            "workers": [],
            "task": "Route this",
            "metadata": {"routes": {"code": "coder"}},
        },
    )

    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "Conditional requires at least one worker" in response.json()["message"]


def test_conditional_dispatch_endpoint_rejects_missing_routes():
    """Missing routes metadata returns HTTP 400."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "router", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "coder", "type": "in-process"})

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "conditional",
            "workers": ["router"],
            "task": "Route this",
        },
    )

    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "routes" in response.json()["message"].lower()


def test_conditional_dispatch_endpoint_routes_correctly():
    """API dispatch routes correctly via the conditional pattern."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "router", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "coder", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="code")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("coder").dispatch = AsyncMock(
        return_value=_worker_result("coder", output="Code fix applied")
    )  # type: ignore[assignment,union-attr]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "conditional",
            "workers": ["router"],
            "task": "Fix the auth bug",
            "metadata": {
                "routes": {"code": "coder"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "success"
    assert payload["aggregated_output"] == "Code fix applied"
    assert payload["metadata"]["route_taken"] == "code"


def test_conditional_dispatch_endpoint_unmatched_route_returns_error():
    """API dispatch with unmatched route returns clear error."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "router", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "coder", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", output="unknown")
    )  # type: ignore[assignment,union-attr]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "conditional",
            "workers": ["router"],
            "task": "Do something",
            "metadata": {
                "routes": {"code": "coder"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "failed"
    assert "No route matched" in payload["error"]


def test_conditional_dispatch_endpoint_router_failure():
    """API dispatch with router failure returns clear error."""
    client = _build_client()
    client.post("/api/swarm/workers", json={"name": "router", "type": "in-process"})
    client.post("/api/swarm/workers", json={"name": "coder", "type": "in-process"})

    engine = get_swarm_engine()
    assert engine is not None

    engine.get_worker("router").dispatch = AsyncMock(
        return_value=_worker_result("router", status="error", error="Router exploded")
    )  # type: ignore[assignment,union-attr]

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "conditional",
            "workers": ["router"],
            "task": "Fix the bug",
            "metadata": {
                "routes": {"code": "coder"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "failed"
    assert "Router exploded" in payload["error"]
    assert len(payload["results"]) == 1
