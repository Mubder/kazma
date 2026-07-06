"""Tests for dynamic worker spawning.

Covers VAL-SPAWN-001, VAL-SPAWN-002, VAL-SPAWN-003, VAL-SPAWN-005,
VAL-SPAWN-006, and VAL-ORCH-052:

- POST /api/swarm/workers/spawn creates and registers InProcessWorker at runtime
- Spawned worker immediately visible in registry
- Spawned worker dispatchable by all patterns
- Capabilities used by CapabilityRouter
- Duplicate name rejected (409)
- DELETE /api/swarm/workers/{name} removes worker; subsequent dispatch returns 404
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from kazma_core.swarm import (
    SwarmConfig,
    SwarmTask,
    TaskType,
)
from kazma_core.swarm.engine import SwarmEngine
from kazma_ui.app import create_app
from kazma_ui.swarm_panel import _reset_swarm_state

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_config() -> SwarmConfig:
    return SwarmConfig(enabled=True, workers=[])


@pytest.fixture
def engine(empty_config: SwarmConfig) -> SwarmEngine:
    return SwarmEngine(empty_config)


@pytest.fixture
def app_with_engine(engine: SwarmEngine):
    """FastAPI app with the engine registered as singleton."""
    from kazma_core.swarm import set_swarm_engine
    set_swarm_engine(engine)
    app = create_app()
    _reset_swarm_state()
    # Re-set our engine after reset
    set_swarm_engine(engine)
    return app


@pytest.fixture
def client(app_with_engine) -> TestClient:
    return TestClient(app_with_engine)


# ---------------------------------------------------------------------------
# Engine-level spawn_worker tests
# ---------------------------------------------------------------------------


class TestEngineSpawnWorker:
    """SwarmEngine.spawn_worker() creates and registers workers."""

    @pytest.mark.asyncio
    async def test_spawn_creates_worker_in_registry(self, engine: SwarmEngine):
        """VAL-SPAWN-001: Spawn creates worker, immediately in registry."""
        worker = await engine.spawn_worker(
            name="spawned-1",
            role="researcher",
            capabilities={"role": "researcher", "expertise": ["web", "analysis"]},
        )
        assert worker.name == "spawned-1"
        assert worker.role == "researcher"
        assert engine.get_worker("spawned-1") is worker

    @pytest.mark.asyncio
    async def test_spawn_worker_has_capabilities(self, engine: SwarmEngine):
        """Spawned worker stores capabilities for CapabilityRouter."""
        worker = await engine.spawn_worker(
            name="cap-worker",
            role="coder",
            capabilities={
                "role": "coder",
                "expertise": ["python", "typescript"],
                "tools": ["editor", "terminal"],
                "model_specialty": "coding",
            },
        )
        assert worker.capabilities.role == "coder"
        assert "python" in worker.capabilities.expertise
        assert "typescript" in worker.capabilities.expertise
        assert "editor" in worker.capabilities.tools
        assert worker.capabilities.model_specialty == "coding"

    @pytest.mark.asyncio
    async def test_spawn_worker_default_capabilities(self, engine: SwarmEngine):
        """Spawn with minimal capabilities defaults role."""
        worker = await engine.spawn_worker(
            name="minimal",
            role="assistant",
            capabilities=None,
        )
        assert worker.capabilities.role == "assistant"

    @pytest.mark.asyncio
    async def test_spawn_worker_with_model_and_provider(self, engine: SwarmEngine):
        """Spawned worker stores model and provider."""
        worker = await engine.spawn_worker(
            name="model-worker",
            role="analyst",
            capabilities={"role": "analyst"},
            model="gpt-4o",
            provider="openai",
        )
        assert worker.model == "gpt-4o"
        assert worker.provider == "openai"

    @pytest.mark.asyncio
    async def test_spawn_worker_type_in_process(self, engine: SwarmEngine):
        """Spawned worker is an InProcessWorker."""
        from kazma_core.swarm.worker import InProcessWorker
        worker = await engine.spawn_worker(
            name="ip-worker",
            role="helper",
            capabilities={"role": "helper"},
            worker_type="in_process",
        )
        assert isinstance(worker, InProcessWorker)

    @pytest.mark.asyncio
    async def test_spawn_worker_type_normalization(self, engine: SwarmEngine):
        """Worker type 'in-process' normalizes to 'in_process'."""
        from kazma_core.swarm.worker import InProcessWorker
        worker = await engine.spawn_worker(
            name="norm-worker",
            role="helper",
            capabilities={"role": "helper"},
            worker_type="in-process",
        )
        assert isinstance(worker, InProcessWorker)

    @pytest.mark.asyncio
    async def test_spawn_duplicate_name_rejected(self, engine: SwarmEngine):
        """VAL-SPAWN-005: Duplicate name raises ValueError."""
        await engine.spawn_worker(
            name="unique",
            role="a",
            capabilities={"role": "a"},
        )
        with pytest.raises(ValueError, match="already registered"):
            await engine.spawn_worker(
                name="unique",
                role="b",
                capabilities={"role": "b"},
            )

    @pytest.mark.asyncio
    async def test_spawn_worker_immediately_dispatchable(self, engine: SwarmEngine):
        """VAL-SPAWN-003: Spawned worker is dispatchable."""
        worker = await engine.spawn_worker(
            name="dispatch-target",
            role="executor",
            capabilities={"role": "executor"},
        )
        worker.dispatch = AsyncMock(return_value={
            "worker": "dispatch-target",
            "task_id": "task-dt",
            "status": "success",
            "output": "executed",
            "error": None,
        })

        result = await engine.dispatch(
            SwarmTask(prompt="test task", workers=["dispatch-target"])
        )
        assert result.status == "success"
        assert result.worker_results[0].worker == "dispatch-target"

    @pytest.mark.asyncio
    async def test_spawn_worker_in_broadcast(self, engine: SwarmEngine):
        """Spawned worker participates in broadcast."""
        worker = await engine.spawn_worker(
            name="broadcast-target",
            role="listener",
            capabilities={"role": "listener"},
        )
        worker.dispatch = AsyncMock(return_value={
            "worker": "broadcast-target",
            "task_id": "task-bt",
            "status": "success",
            "output": "heard",
            "error": None,
        })

        task = SwarmTask(
            prompt="broadcast test",
            workers=["broadcast-target"],
            type=TaskType.BROADCAST,
        )
        result = await engine.broadcast(task)
        assert result.status == "success"
        assert len(result.worker_results) == 1

    @pytest.mark.asyncio
    async def test_spawn_worker_in_pipeline(self, engine: SwarmEngine):
        """Spawned worker works in pipeline pattern."""
        worker_a = await engine.spawn_worker(
            name="pipe-a",
            role="step1",
            capabilities={"role": "step1"},
        )
        worker_b = await engine.spawn_worker(
            name="pipe-b",
            role="step2",
            capabilities={"role": "step2"},
        )
        worker_a.dispatch = AsyncMock(return_value={
            "worker": "pipe-a",
            "task_id": "task-pa",
            "status": "success",
            "output": "step-a-done",
            "error": None,
        })
        worker_b.dispatch = AsyncMock(return_value={
            "worker": "pipe-b",
            "task_id": "task-pb",
            "status": "success",
            "output": "step-b-done",
            "error": None,
        })

        task = SwarmTask(
            prompt="pipeline test",
            workers=["pipe-a", "pipe-b"],
            type=TaskType.PIPELINE,
        )
        result = await engine.dispatch(task)
        assert result.status == "success"
        assert len(result.worker_results) == 2


class TestEngineRemoveSpawnedWorker:
    """Removing spawned workers cleans up correctly."""

    @pytest.mark.asyncio
    async def test_remove_spawned_worker(self, engine: SwarmEngine):
        """VAL-SPAWN-006: Remove cleans up worker from registry."""
        await engine.spawn_worker(
            name="temp-worker",
            role="temp",
            capabilities={"role": "temp"},
        )
        assert engine.get_worker("temp-worker") is not None

        engine.remove_worker("temp-worker")
        assert engine.get_worker("temp-worker") is None

    @pytest.mark.asyncio
    async def test_remove_then_dispatch_returns_not_found(self, engine: SwarmEngine):
        """VAL-SPAWN-006: Subsequent dispatch returns not-found."""
        await engine.spawn_worker(
            name="removed-worker",
            role="temp",
            capabilities={"role": "temp"},
        )
        engine.remove_worker("removed-worker")

        result = await engine.dispatch(
            SwarmTask(prompt="test", workers=["removed-worker"])
        )
        assert result.status == "failed"
        assert "not found" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_worker_raises(self, engine: SwarmEngine):
        """Removing nonexistent worker raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            engine.remove_worker("ghost")

    @pytest.mark.asyncio
    async def test_remove_cleans_circuit_breaker(self, engine: SwarmEngine):
        """Removing worker does not leave stale circuit breaker preventing re-add."""
        await engine.spawn_worker(
            name="cb-worker",
            role="test",
            capabilities={"role": "test"},
        )
        # Touch the circuit breaker
        engine.get_circuit_breaker("cb-worker")
        assert "cb-worker" in engine._reliability._circuit_breakers

        engine.remove_worker("cb-worker")
        # After removal, re-spawning with same name should work
        await engine.spawn_worker(
            name="cb-worker",
            role="test2",
            capabilities={"role": "test2"},
        )
        assert engine.get_worker("cb-worker") is not None


# ---------------------------------------------------------------------------
# Capability Router integration with spawned workers
# ---------------------------------------------------------------------------


class TestSpawnedWorkerCapabilityRouting:
    """Spawned workers' capabilities are used by CapabilityRouter."""

    @pytest.mark.asyncio
    async def test_spawned_worker_selected_by_router(self, engine: SwarmEngine):
        """VAL-SPAWN-004: Capability router considers spawned workers."""
        # Add a worker with python expertise
        await engine.spawn_worker(
            name="python-expert",
            role="backend",
            capabilities={
                "role": "backend",
                "expertise": ["python", "django", "fastapi"],
                "model_specialty": "coding",
            },
        )
        # Add a worker with frontend expertise
        await engine.spawn_worker(
            name="react-expert",
            role="frontend",
            capabilities={
                "role": "frontend",
                "expertise": ["react", "typescript", "css"],
                "model_specialty": "creative",
            },
        )

        # Create a task that needs Python work
        task = SwarmTask(
            prompt="Fix the Python API endpoint",
            workers=["auto"],
        )
        available = engine._build_available_workers_list()
        routed = await engine._routing_engine.route(task, available)

        assert "python-expert" in routed
        # React expert may or may not match "api" depending on token overlap

    @pytest.mark.asyncio
    async def test_spawned_worker_unique_capability_selected(self, engine: SwarmEngine):
        """Router selects spawned worker with unique capability match."""
        await engine.spawn_worker(
            name="data-scientist",
            role="data",
            capabilities={
                "role": "data_scientist",
                "expertise": ["pandas", "numpy", "machine_learning"],
            },
        )
        await engine.spawn_worker(
            name="devops",
            role="infra",
            capabilities={
                "role": "devops",
                "expertise": ["docker", "kubernetes", "terraform"],
            },
        )

        task = SwarmTask(
            prompt="Train a machine learning model with pandas",
            workers=["auto"],
        )
        available = engine._build_available_workers_list()
        routed = await engine._routing_engine.route(task, available)

        assert "data-scientist" in routed


# ---------------------------------------------------------------------------
# API endpoint tests: POST /api/swarm/workers/spawn
# ---------------------------------------------------------------------------


class TestSpawnWorkerAPI:
    """POST /api/swarm/workers/spawn endpoint tests."""

    def test_spawn_worker_returns_201(self, client: TestClient):
        """VAL-SPAWN-001: POST spawn creates worker and returns 201."""
        resp = client.post("/api/swarm/workers/spawn", json={
            "name": "api-spawned",
            "role": "researcher",
            "capabilities": {
                "role": "researcher",
                "expertise": ["web", "analysis"],
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ok"
        assert data["worker"]["name"] == "api-spawned"

    def test_spawn_worker_appears_in_registry(self, client: TestClient):
        """VAL-SPAWN-002: Spawned worker appears in worker list."""
        client.post("/api/swarm/workers/spawn", json={
            "name": "registry-check",
            "role": "tester",
            "capabilities": {"role": "tester"},
        })
        resp = client.get("/api/swarm/status")
        names = [w["name"] for w in resp.json()["workers"]]
        assert "registry-check" in names

    def test_spawn_duplicate_returns_409(self, client: TestClient):
        """VAL-SPAWN-005: Duplicate spawn name returns 409 Conflict."""
        client.post("/api/swarm/workers/spawn", json={
            "name": "dup-worker",
            "role": "a",
            "capabilities": {"role": "a"},
        })
        resp = client.post("/api/swarm/workers/spawn", json={
            "name": "dup-worker",
            "role": "b",
            "capabilities": {"role": "b"},
        })
        assert resp.status_code == 409
        assert "already exists" in resp.json()["message"]

    def test_spawn_missing_name_returns_400(self, client: TestClient):
        """Spawn without name returns 400."""
        resp = client.post("/api/swarm/workers/spawn", json={
            "role": "test",
            "capabilities": {"role": "test"},
        })
        assert resp.status_code == 400

    def test_spawn_with_model_and_provider(self, client: TestClient):
        """Spawn accepts model and provider fields."""
        resp = client.post("/api/swarm/workers/spawn", json={
            "name": "model-spawn",
            "role": "analyst",
            "capabilities": {"role": "analyst", "expertise": ["data"]},
            "model": "gpt-4o",
            "provider": "openai",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["worker"]["model"] == "gpt-4o"
        assert data["worker"]["provider"] == "openai"

    def test_spawn_with_capabilities_serialized(self, client: TestClient):
        """Spawned worker's capabilities are serialized in response."""
        resp = client.post("/api/swarm/workers/spawn", json={
            "name": "cap-spawn",
            "role": "backend",
            "capabilities": {
                "role": "backend",
                "expertise": ["python", "fastapi"],
                "tools": ["editor"],
                "model_specialty": "coding",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "capabilities" in data["worker"]
        caps = data["worker"]["capabilities"]
        assert "python" in caps["expertise"]


# ---------------------------------------------------------------------------
# API endpoint tests: DELETE /api/swarm/workers/{name}
# ---------------------------------------------------------------------------


class TestDeleteSpawnedWorkerAPI:
    """DELETE /api/swarm/workers/{name} endpoint tests."""

    def test_delete_spawned_worker_returns_200(self, client: TestClient):
        """VAL-SPAWN-006: DELETE removes worker."""
        client.post("/api/swarm/workers/spawn", json={
            "name": "to-delete",
            "role": "temp",
            "capabilities": {"role": "temp"},
        })
        resp = client.delete("/api/swarm/workers/to-delete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_delete_removes_from_registry(self, client: TestClient):
        """Deleted worker no longer in registry."""
        client.post("/api/swarm/workers/spawn", json={
            "name": "vanish",
            "role": "temp",
            "capabilities": {"role": "temp"},
        })
        client.delete("/api/swarm/workers/vanish")

        resp = client.get("/api/swarm/status")
        names = [w["name"] for w in resp.json()["workers"]]
        assert "vanish" not in names

    def test_delete_nonexistent_returns_404(self, client: TestClient):
        """Deleting nonexistent worker returns 404."""
        resp = client.delete("/api/swarm/workers/nonexistent")
        assert resp.status_code == 404

    def test_dispatch_after_delete_returns_error(self, client: TestClient):
        """VAL-SPAWN-006: Dispatch after delete returns not-found error."""
        # Spawn and mock the worker's dispatch
        spawn_resp = client.post("/api/swarm/workers/spawn", json={
            "name": "dispatch-after-delete",
            "role": "temp",
            "capabilities": {"role": "temp"},
        })
        assert spawn_resp.status_code == 201

        # Delete
        client.delete("/api/swarm/workers/dispatch-after-delete")

        # Attempt dispatch
        resp = client.post("/api/swarm/dispatch", json={
            "workers": ["dispatch-after-delete"],
            "task": "should fail",
        })
        data = resp.json()
        # The dispatch should report missing worker
        assert "dispatch-after-delete" in data.get("missing", []) or \
               data.get("result_status") == "failed"


# ---------------------------------------------------------------------------
# API endpoint: spawn then dispatch end-to-end
# ---------------------------------------------------------------------------


class TestSpawnThenDispatchAPI:
    """VAL-ORCH-052: Spawned worker immediately usable via dispatch API."""

    def test_spawn_then_dispatch_success(self, client: TestClient):
        """Spawn + dispatch in sequence works."""
        spawn_resp = client.post("/api/swarm/workers/spawn", json={
            "name": "e2e-worker",
            "role": "executor",
            "capabilities": {"role": "executor"},
        })
        assert spawn_resp.status_code == 201

        # The spawned worker will use SubAgentManager which is not initialized,
        # so we expect an error from the dispatch (not a missing worker error).
        # This verifies the worker IS in the registry and IS dispatchable.
        dispatch_resp = client.post("/api/swarm/dispatch", json={
            "workers": ["e2e-worker"],
            "task": "test task",
        })
        data = dispatch_resp.json()
        # Worker should be in dispatched, not missing
        assert "e2e-worker" in data.get("dispatched", [])
        assert "e2e-worker" not in data.get("missing", [])
