"""Tests for the swarm fan-out orchestration pattern."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskType, WorkerConfig
from kazma_core.swarm.aggregator import ResultAggregator
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


@pytest.mark.asyncio
async def test_engine_fan_out_runs_workers_concurrently(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta", "gamma")

    started: set[str] = set()
    ready = asyncio.Event()

    async def make_dispatch(name: str) -> dict[str, str | None]:
        started.add(name)
        if len(started) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=0.1)
        return _worker_result(name, output=f"{name} output")

    engine.get_worker("alpha").dispatch = lambda task, context="": make_dispatch("alpha")  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = lambda task, context="": make_dispatch("beta")  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = lambda task, context="": make_dispatch("gamma")  # type: ignore[assignment,union-attr]

    result = await asyncio.wait_for(
        engine.dispatch(
            SwarmTask(
                prompt="Analyze it",
                context="ctx",
                workers=["alpha", "beta", "gamma"],
                type=TaskType.FAN_OUT,
                aggregation="collect",
            )
        ),
        timeout=0.2,
    )

    assert started == {"alpha", "beta", "gamma"}
    assert result.status == "success"
    assert len(result.worker_results) == 3


@pytest.mark.asyncio
async def test_engine_fan_out_merge_all_combines_outputs(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta", "gamma")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="alpha output")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", output="beta output")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = AsyncMock(
        return_value=_worker_result("gamma", output="gamma output")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Merge the responses",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.FAN_OUT,
            aggregation="merge_all",
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == (
        "[alpha] alpha output\n\n[beta] beta output\n\n[gamma] gamma output"
    )
    assert [item.output for item in result.worker_results] == [
        "alpha output",
        "beta output",
        "gamma output",
    ]
    assert result.metadata["aggregation_strategy"] == "merge_all"


@pytest.mark.asyncio
async def test_engine_fan_out_vote_returns_majority_with_tally(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta", "gamma")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="same answer")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", output="same answer")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = AsyncMock(
        return_value=_worker_result("gamma", output="different answer")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Vote on the answer",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.FAN_OUT,
            aggregation="vote",
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "same answer"
    assert result.metadata["vote_tally"] == {
        "same answer": 2,
        "different answer": 1,
    }


@pytest.mark.asyncio
async def test_engine_fan_out_synthesize_uses_configured_synthesizer(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta")

    captured: dict[str, object] = {}

    async def fake_synthesizer(
        task: SwarmTask,
        worker_results: list,
    ) -> str:
        captured["prompt"] = task.prompt
        captured["workers"] = [result.worker for result in worker_results]
        return "combined synthesis"

    engine._result_aggregator = ResultAggregator(  # type: ignore[attr-defined]
        synthesizer=fake_synthesizer
    )
    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="alpha opinion")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", output="beta opinion")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Synthesize the opinions",
            workers=["alpha", "beta"],
            type=TaskType.FAN_OUT,
            aggregation="synthesize",
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "combined synthesis"
    assert result.synthesized_output == "combined synthesis"
    assert result.metadata["synthesized"] is True
    assert captured == {
        "prompt": "Synthesize the opinions",
        "workers": ["alpha", "beta"],
    }


@pytest.mark.asyncio
async def test_engine_fan_out_first_valid_skips_errors(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta", "gamma")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", status="error", error="alpha failed")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", output="beta output")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = AsyncMock(
        return_value=_worker_result("gamma", output="gamma output")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Pick the first valid answer",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.FAN_OUT,
            aggregation="first_valid",
        )
    )

    assert result.status == "partial"
    assert result.aggregated_output == "beta output"
    assert result.metadata["selected_worker"] == "beta"
    assert len(result.worker_results) == 3


@pytest.mark.asyncio
async def test_engine_fan_out_collect_returns_all_results_without_aggregation(
    empty_config,
):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="alpha output")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", output="beta output")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Collect the responses",
            workers=["alpha", "beta"],
            type=TaskType.FAN_OUT,
            aggregation="collect",
        )
    )

    assert result.status == "success"
    assert result.aggregated_output is None
    assert [item.worker for item in result.worker_results] == ["alpha", "beta"]
    assert result.metadata["aggregation_strategy"] == "collect"


@pytest.mark.asyncio
async def test_engine_fan_out_partial_failure_surfaces_all_results(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta", "gamma")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="alpha output")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", status="error", error="beta failed")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("gamma").dispatch = AsyncMock(
        return_value=_worker_result("gamma", output="gamma output")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Allow partial success",
            workers=["alpha", "beta", "gamma"],
            type=TaskType.FAN_OUT,
            aggregation="merge_all",
        )
    )

    assert result.status == "partial"
    assert len(result.worker_results) == 3
    assert result.worker_results[1].status == "error"
    assert result.error == "beta failed"
    assert result.aggregated_output == "[alpha] alpha output\n\n[gamma] gamma output"


@pytest.mark.asyncio
async def test_engine_fan_out_all_fail_marks_task_failed(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha", "beta")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", status="error", error="alpha failed")
    )  # type: ignore[assignment,union-attr]
    engine.get_worker("beta").dispatch = AsyncMock(
        return_value=_worker_result("beta", status="timeout", error="beta timed out")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Everyone fails",
            workers=["alpha", "beta"],
            type=TaskType.FAN_OUT,
            aggregation="merge_all",
        )
    )

    assert result.status == "failed"
    assert result.aggregated_output is None
    assert len(result.worker_results) == 2
    assert "alpha failed" in (result.error or "")
    assert "beta timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_engine_fan_out_single_worker_matches_dispatch_behavior(empty_config):
    engine = SwarmEngine(empty_config)
    _add_workers(engine, "alpha")

    engine.get_worker("alpha").dispatch = AsyncMock(
        return_value=_worker_result("alpha", output="single output")
    )  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="Handle it alone",
            workers=["alpha"],
            type=TaskType.FAN_OUT,
            aggregation="synthesize",
        )
    )

    assert result.status == "success"
    assert result.aggregated_output == "single output"
    assert len(result.worker_results) == 1
    assert result.worker_results[0].worker == "alpha"


@pytest.mark.asyncio
async def test_engine_fan_out_uses_default_max_concurrent_of_five():
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    worker_names = [f"worker-{index}" for index in range(8)]
    _add_workers(engine, *worker_names)

    concurrent = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def tracked_dispatch(name: str) -> dict[str, str | None]:
        nonlocal concurrent, max_observed
        async with lock:
            concurrent += 1
            max_observed = max(max_observed, concurrent)
        await asyncio.sleep(0.02)
        async with lock:
            concurrent -= 1
        return _worker_result(name, output=f"{name} output")

    for name in worker_names:
        engine.get_worker(name).dispatch = (  # type: ignore[assignment,union-attr]
            lambda task, context="", worker_name=name: tracked_dispatch(worker_name)
        )

    result = await engine.dispatch(
        SwarmTask(
            prompt="Use the default concurrency",
            workers=worker_names,
            type=TaskType.FAN_OUT,
            aggregation="collect",
        )
    )

    assert result.status == "success"
    assert max_observed == 5


@pytest.mark.asyncio
async def test_engine_fan_out_honors_task_max_concurrent_override(empty_config):
    engine = SwarmEngine(empty_config)
    worker_names = [f"worker-{index}" for index in range(5)]
    _add_workers(engine, *worker_names)

    concurrent = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def tracked_dispatch(name: str) -> dict[str, str | None]:
        nonlocal concurrent, max_observed
        async with lock:
            concurrent += 1
            max_observed = max(max_observed, concurrent)
        await asyncio.sleep(0.02)
        async with lock:
            concurrent -= 1
        return _worker_result(name, output=f"{name} output")

    for name in worker_names:
        engine.get_worker(name).dispatch = (  # type: ignore[assignment,union-attr]
            lambda task, context="", worker_name=name: tracked_dispatch(worker_name)
        )

    result = await engine.dispatch(
        SwarmTask(
            prompt="Respect the override",
            workers=worker_names,
            type=TaskType.FAN_OUT,
            aggregation="collect",
            metadata={"max_concurrent": 2},
        )
    )

    assert result.status == "success"
    assert max_observed == 2
    assert result.metadata["max_concurrent"] == 2


def test_fan_out_dispatch_endpoint_supports_vote_aggregation():
    client = _build_client()
    for name in ("alpha", "beta", "gamma"):
        created = client.post("/api/swarm/workers", json={"name": name, "type": "in-process"})
        assert created.status_code == 201

    engine = get_swarm_engine()
    assert engine is not None

    engine.get_worker("alpha").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("alpha", output="same answer")
    )
    engine.get_worker("beta").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("beta", output="same answer")
    )
    engine.get_worker("gamma").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("gamma", output="different answer")
    )

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "fan-out",
            "workers": ["alpha", "beta", "gamma"],
            "task": "Choose the best answer",
            "aggregation": "vote",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "success"
    assert payload["aggregated_output"] == "same answer"
    assert payload["metadata"]["vote_tally"] == {
        "same answer": 2,
        "different answer": 1,
    }


def test_fan_out_dispatch_endpoint_passes_max_concurrent_to_engine():
    client = _build_client()
    worker_names = [f"worker-{index}" for index in range(5)]
    for name in worker_names:
        created = client.post("/api/swarm/workers", json={"name": name, "type": "in-process"})
        assert created.status_code == 201

    engine = get_swarm_engine()
    assert engine is not None

    concurrent = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def tracked_dispatch(name: str) -> dict[str, str | None]:
        nonlocal concurrent, max_observed
        async with lock:
            concurrent += 1
            max_observed = max(max_observed, concurrent)
        await asyncio.sleep(0.02)
        async with lock:
            concurrent -= 1
        return _worker_result(name, output=f"{name} output")

    for name in worker_names:
        engine.get_worker(name).dispatch = (  # type: ignore[assignment,union-attr]
            lambda task, context="", worker_name=name: tracked_dispatch(worker_name)
        )

    response = client.post(
        "/api/swarm/dispatch",
        json={
            "pattern": "fan_out",
            "workers": worker_names,
            "task": "Limit concurrency",
            "aggregation": "collect",
            "max_concurrent": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_status"] == "success"
    assert payload["aggregated_output"] is None
    assert payload["metadata"]["max_concurrent"] == 2
    assert max_observed == 2
