"""Tests for the swarm consult orchestration pattern."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskType, WorkerCapabilities, WorkerConfig
from kazma_core.swarm.blackboard import SwarmDispatchContext
from kazma_core.swarm.engine import SwarmEngine, get_swarm_engine
from kazma_core.swarm.worker import InProcessWorker
from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router


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


def _add_worker(
    engine: SwarmEngine,
    name: str,
    *,
    role: str,
    expertise: list[str],
    model_specialty: str = "",
) -> None:
    engine.add_worker(
        WorkerConfig(
            name=name,
            type="in_process",
            role=role,
            capabilities=WorkerCapabilities(
                role=role,
                expertise=expertise,
                model_specialty=model_specialty,
            ),
        )
    )


def _build_client() -> TestClient:
    _reset_swarm_state()
    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
    app.include_router(create_swarm_router(templates))
    return TestClient(app)


@pytest.mark.asyncio
async def test_engine_consult_builds_role_aware_independent_prompts() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    _add_worker(
        engine,
        "architect",
        role="backend_architect",
        expertise=["python", "api design"],
        model_specialty="reasoning",
    )
    _add_worker(
        engine,
        "reviewer",
        role="code_reviewer",
        expertise=["testing", "reliability"],
        model_specialty="analysis",
    )

    captured_contexts: dict[str, SwarmDispatchContext] = {}

    async def architect_dispatch(
        task: str,
        context: str | SwarmDispatchContext = "",
    ) -> dict[str, str | None]:
        assert isinstance(context, SwarmDispatchContext)
        captured_contexts["architect"] = context
        return _worker_result("architect", output="Prefer a queue-backed design.")

    async def reviewer_dispatch(
        task: str,
        context: str | SwarmDispatchContext = "",
    ) -> dict[str, str | None]:
        assert isinstance(context, SwarmDispatchContext)
        captured_contexts["reviewer"] = context
        return _worker_result("reviewer", output="Add validation around fan-out failures.")

    engine.get_worker("architect").dispatch = architect_dispatch  # type: ignore[assignment,union-attr]
    engine.get_worker("reviewer").dispatch = reviewer_dispatch  # type: ignore[assignment,union-attr]

    result = await engine.dispatch(
        SwarmTask(
            prompt="How should we orchestrate consult mode?",
            context="Optimize for reliability and clear worker specialization.",
            workers=["architect", "reviewer"],
            type=TaskType.CONSULT,
        )
    )

    assert result.status == "success"
    assert [opinion.worker for opinion in result.individual_opinions] == [
        "architect",
        "reviewer",
    ]

    architect_context = captured_contexts["architect"]
    reviewer_context = captured_contexts["reviewer"]

    assert "backend_architect" in architect_context.system_prompt
    assert "python" in architect_context.system_prompt
    assert "api design" in architect_context.system_prompt
    assert "code_reviewer" in reviewer_context.system_prompt
    assert "testing" in reviewer_context.system_prompt
    assert "reliability" in reviewer_context.system_prompt

    assert architect_context.text == (
        "Optimize for reliability and clear worker specialization."
    )
    assert reviewer_context.text == architect_context.text
    assert "Add validation around fan-out failures." not in architect_context.text
    assert "Prefer a queue-backed design." not in reviewer_context.text


@pytest.mark.asyncio
async def test_in_process_worker_dispatch_includes_system_prompt_in_spawn_context() -> None:
    mock_manager = MagicMock()
    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.summary = "Done"
    mock_result.error = None
    mock_manager.spawn = AsyncMock(return_value=mock_result)

    worker = InProcessWorker(name="architect", role="backend_architect", manager=mock_manager)
    await worker.start()

    await worker.dispatch(
        "Explain the consult tradeoffs",
        context=SwarmDispatchContext(
            "Use the current swarm engine primitives.",
            system_prompt="You are the backend architect for consult mode.",
        ),
    )

    call_kwargs = mock_manager.spawn.call_args.kwargs
    assert call_kwargs["goal"] == "Explain the consult tradeoffs"
    assert "You are the backend architect for consult mode." in call_kwargs["context"]
    assert "Use the current swarm engine primitives." in call_kwargs["context"]


@pytest.mark.asyncio
async def test_engine_consult_synthesizes_with_worker_attribution() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    _add_worker(engine, "alpha", role="architect", expertise=["architecture"])
    _add_worker(engine, "beta", role="reviewer", expertise=["testing"])

    engine.get_worker("alpha").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("alpha", output="Use queues for backpressure.")
    )
    engine.get_worker("beta").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("beta", output="Add tests for partial failures.")
    )

    with patch("kazma_core.swarm.aggregator._get_llm_provider", return_value=None):
        result = await engine.dispatch(
            SwarmTask(
                prompt="Synthesize the consult opinions",
                workers=["alpha", "beta"],
                type=TaskType.CONSULT,
            )
        )

    assert result.status == "success"
    assert result.aggregated_output == result.synthesized_output
    assert result.synthesized_output is not None
    assert "alpha" in result.synthesized_output
    assert "beta" in result.synthesized_output
    assert "Use queues for backpressure." in result.synthesized_output
    assert "Add tests for partial failures." in result.synthesized_output


@pytest.mark.asyncio
async def test_engine_consult_single_worker_returns_passthrough_synthesis() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    _add_worker(engine, "solo", role="architect", expertise=["architecture"])

    engine.get_worker("solo").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("solo", output="Keep the implementation simple.")
    )

    result = await engine.dispatch(
        SwarmTask(
            prompt="Handle the consult alone",
            workers=["solo"],
            type=TaskType.CONSULT,
        )
    )

    assert result.status == "success"
    assert [opinion.worker for opinion in result.individual_opinions] == ["solo"]
    assert result.synthesized_output == "Keep the implementation simple."
    assert result.aggregated_output == "Keep the implementation simple."


@pytest.mark.asyncio
async def test_engine_consult_partial_failure_synthesizes_available_opinions() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    _add_worker(engine, "alpha", role="architect", expertise=["architecture"])
    _add_worker(engine, "beta", role="reviewer", expertise=["testing"])
    _add_worker(engine, "gamma", role="ops", expertise=["operations"])

    engine.get_worker("alpha").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("alpha", output="Prefer deterministic fallbacks.")
    )
    engine.get_worker("beta").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("beta", status="error", error="beta failed")
    )
    engine.get_worker("gamma").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("gamma", output="Record history for later review.")
    )

    with patch("kazma_core.swarm.aggregator._get_llm_provider", return_value=None):
        result = await engine.dispatch(
            SwarmTask(
                prompt="Degrade gracefully on worker failures",
                workers=["alpha", "beta", "gamma"],
                type=TaskType.CONSULT,
            )
        )

    assert result.status == "partial"
    assert [opinion.worker for opinion in result.individual_opinions] == ["alpha", "gamma"]
    assert result.synthesized_output is not None
    assert "alpha" in result.synthesized_output
    assert "gamma" in result.synthesized_output
    assert result.error == "beta failed"
    assert len(result.worker_results) == 3


@pytest.mark.asyncio
async def test_engine_consult_all_fail_returns_no_synthesis() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    _add_worker(engine, "alpha", role="architect", expertise=["architecture"])
    _add_worker(engine, "beta", role="reviewer", expertise=["testing"])

    engine.get_worker("alpha").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("alpha", status="error", error="alpha failed")
    )
    engine.get_worker("beta").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("beta", status="timeout", error="beta timed out")
    )

    result = await engine.dispatch(
        SwarmTask(
            prompt="Handle complete failure",
            workers=["alpha", "beta"],
            type=TaskType.CONSULT,
        )
    )

    assert result.status == "failed"
    assert result.individual_opinions == []
    assert result.synthesized_output is None
    assert result.aggregated_output is None
    assert "alpha failed" in (result.error or "")
    assert "beta timed out" in (result.error or "")


def test_api_consult_requires_workers_and_returns_consult_history() -> None:
    client = _build_client()

    response = client.post(
        "/api/swarm/dispatch",
        json={"pattern": "consult", "workers": [], "task": "Need a decision"},
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Consult requires at least one worker."

    client.post("/api/swarm/workers", json={"name": "alpha", "role": "architect"})
    client.post("/api/swarm/workers", json={"name": "beta", "role": "reviewer"})

    engine = get_swarm_engine()
    assert engine is not None
    engine.get_worker("alpha").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("alpha", output="Prefer explicit attribution.")
    )
    engine.get_worker("beta").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
        return_value=_worker_result("beta", output="Keep each opinion isolated.")
    )

    with patch("kazma_core.swarm.aggregator._get_llm_provider", return_value=None):
        dispatch_response = client.post(
            "/api/swarm/dispatch",
            json={
                "pattern": "consult",
                "workers": ["alpha", "beta"],
                "task": "How should consult mode behave?",
                "context": "Answer for the swarm engine.",
            },
        )

    assert dispatch_response.status_code == 200
    dispatch_payload = dispatch_response.json()
    assert len(dispatch_payload["individual_opinions"]) == 2
    assert "alpha" in dispatch_payload["synthesized_output"]
    assert "beta" in dispatch_payload["synthesized_output"]

    history_response = client.get("/api/swarm/tasks", params={"type": "consult"})
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert history_payload["count"] == 1
    assert history_payload["tasks"][0]["type"] == "consult"
    assert len(history_payload["tasks"][0]["result"]["individual_opinions"]) == 2
    assert history_payload["tasks"][0]["result"]["synthesized_output"] == dispatch_payload[
        "synthesized_output"
    ]
