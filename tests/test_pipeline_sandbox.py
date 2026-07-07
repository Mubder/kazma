"""Tests for the Visual Pipeline Sandbox and Observability Alerts direct routes.

Verifies:
1. PipelineNode and PipelineDAG schema constraints (Referential integrity, cycle detection).
2. GET /api/pipelines/scaffold blueprint schema returns.
3. POST /api/pipelines/validate graph structure and WorkerRegistry name matching.
4. GET /api/alerts/recent retrieval of AlertDispatcher recent ring buffer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kazma_core.agent.pipeline_schema import PipelineDAG, PipelineNode
from kazma_core.observability.alerts import AlertDispatcher
from kazma_core.swarm.registry import get_worker_registry, WorkerEntry


# ══════════════════════════════════════════════════════════════════════════
# Schema & cycle detection unit tests
# ══════════════════════════════════════════════════════════════════════════

def test_pipeline_dag_valid() -> None:
    """Test standard valid PipelineDAG construction and dependency tracking."""
    node_a = PipelineNode(id="node_a", worker_name="auto", task_description="Task A")
    node_b = PipelineNode(id="node_b", worker_name="auto", task_description="Task B", dependencies=["node_a"])

    dag = PipelineDAG(nodes=[node_a, node_b])
    assert len(dag.nodes) == 2
    assert dag.nodes[1].dependencies == ["node_a"]


def test_pipeline_dag_referential_integrity_error() -> None:
    """Test referential integrity errors when a node references an invalid dependency."""
    node_a = PipelineNode(id="node_a", worker_name="auto", task_description="Task A", dependencies=["node_invalid"])

    with pytest.raises(ValueError) as exc:
        PipelineDAG(nodes=[node_a])

    assert "references a non-existent dependency: 'node_invalid'" in str(exc.value)


def test_pipeline_dag_simple_cycle() -> None:
    """Test cycle detection on a simple self-cycle (A -> A)."""
    node_a = PipelineNode(id="node_a", worker_name="auto", task_description="Task A", dependencies=["node_a"])

    with pytest.raises(ValueError) as exc:
        PipelineDAG(nodes=[node_a])

    assert "Cycle detected" in str(exc.value)


def test_pipeline_dag_deep_cycle() -> None:
    """Test cycle detection on a multi-node cyclic loop (A -> B -> C -> A)."""
    node_a = PipelineNode(id="node_a", worker_name="auto", task_description="Task A", dependencies=["node_c"])
    node_b = PipelineNode(id="node_b", worker_name="auto", task_description="Task B", dependencies=["node_a"])
    node_c = PipelineNode(id="node_c", worker_name="auto", task_description="Task C", dependencies=["node_b"])

    with pytest.raises(ValueError) as exc:
        PipelineDAG(nodes=[node_a, node_b, node_c])

    assert "Cycle detected" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════════
# API route integration tests
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client() -> TestClient:
    from kazma_ui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_get_pipeline_scaffold_returns_blueprint(client: TestClient) -> None:
    """GET /api/pipelines/scaffold must return the correct scaffold DAG model."""
    resp = client.get("/api/pipelines/scaffold")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert len(data["nodes"]) == 2
    assert data["nodes"][0]["id"] == "node-researcher"
    assert data["nodes"][1]["id"] == "node-writer"
    assert data["nodes"][1]["dependencies"] == ["node-researcher"]


def test_post_pipeline_validate_success(client: TestClient) -> None:
    """POST /api/pipelines/validate must succeed with a valid registered DAG."""
    # Ensure "auto" worker or registered worker works
    registry = get_worker_registry()
    registry.register(WorkerEntry(name="valid-worker", system_prompt="Soul"))

    payload = {
        "nodes": [
            {"id": "node-1", "worker_name": "auto", "task_description": "Retrieve research"},
            {"id": "node-2", "worker_name": "valid-worker", "task_description": "Draft response", "dependencies": ["node-1"]},
        ]
    }
    resp = client.post("/api/pipelines/validate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["errors"] == []


def test_post_pipeline_validate_schema_error(client: TestClient) -> None:
    """POST /api/pipelines/validate must return 422 with a schema error on cyclic inputs."""
    payload = {
        "nodes": [
            {"id": "node-1", "worker_name": "auto", "task_description": "Task 1", "dependencies": ["node-2"]},
            {"id": "node-2", "worker_name": "auto", "task_description": "Task 2", "dependencies": ["node-1"]},
        ]
    }
    resp = client.post("/api/pipelines/validate", json=payload)
    assert resp.status_code == 422
    data = resp.json()
    assert data["valid"] is False
    assert any("Cycle detected" in err for err in data["errors"])


def test_post_pipeline_validate_unregistered_worker_error(client: TestClient) -> None:
    """POST /api/pipelines/validate must return 400 when assigning an unregistered worker name."""
    payload = {
        "nodes": [
            {"id": "node-1", "worker_name": "ghost-worker-999", "task_description": "Retrieve research"},
        ]
    }
    resp = client.post("/api/pipelines/validate", json=payload)
    assert resp.status_code == 400
    data = resp.json()
    assert data["valid"] is False
    assert any("assigned to unregistered worker" in err for err in data["errors"])


# ══════════════════════════════════════════════════════════════════════════
# Alerts integration tests
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_recent_alerts_endpoint(client: TestClient) -> None:
    """GET /api/alerts/recent must return the active alerts in memory."""
    AlertDispatcher.clear_alerts()

    # Trigger a mock alert
    await AlertDispatcher.trigger_system_alert(
        subsystem="VectorMemory",
        status="DEGRADED",
        message="Could not load sentence-transformers for vector memory."
    )

    resp = client.get("/api/alerts/recent")
    assert resp.status_code == 200
    alerts = resp.json()
    assert isinstance(alerts, list)
    assert len(alerts) > 0
    assert alerts[0]["subsystem"] == "VectorMemory"
    assert alerts[0]["status"] == "DEGRADED"
    assert "sentence-transformers" in alerts[0]["reason"]
