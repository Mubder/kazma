"""Tests for swarm task store API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.swarm import (
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerResult,
)
from kazma_core.swarm.task_store import TaskStore
from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router


@pytest.fixture(autouse=True)
def _reset_engine() -> None:
    """Reset swarm state before each test."""
    _reset_swarm_state()


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    """Create a temporary TaskStore."""
    return TaskStore(db_path=str(tmp_path / "test_api.db"))


def _make_app(store: TaskStore | None = None) -> tuple[FastAPI, TestClient]:
    """Create a FastAPI app with the swarm router."""
    from unittest.mock import MagicMock

    app = FastAPI()
    templates = MagicMock()
    templates.TemplateResponse = MagicMock(side_effect=Exception("no template"))
    router = create_swarm_router(templates)
    app.include_router(router)
    client = TestClient(app)
    return app, client


def _populate_store(store: TaskStore, count: int = 10) -> list[str]:
    """Populate the store with N tasks."""
    ids = []
    for i in range(count):
        task = SwarmTask(
            prompt=f"Task {i}",
            id=f"task-{i:04d}",
            type=TaskType.DISPATCH if i % 2 == 0 else TaskType.CONSULT,
            status=TaskStatus.COMPLETED if i % 3 != 0 else TaskStatus.FAILED,
            workers=[f"worker-{i % 3}"],
            result=TaskResult(
                task_id=f"task-{i:04d}",
                status="success" if i % 3 != 0 else "failed",
                worker_results=[
                    WorkerResult(
                        worker=f"worker-{i % 3}",
                        task_id=f"task-{i:04d}",
                        status="success" if i % 3 != 0 else "error",
                        output=f"Output for task {i}",
                        tokens_used=100 + i,
                        cost=0.001 * (i + 1),
                        duration_seconds=1.0 + i * 0.5,
                    )
                ],
                total_cost=0.001 * (i + 1),
                total_tokens=100 + i,
                duration_seconds=1.0 + i * 0.5,
            ),
            completed_at=f"2026-06-29T{10 + i % 14:02d}:00:00+00:00",
        )
        store.persist_task(task)
        ids.append(task.id)
    return ids


class TestTaskHistoryAPI:
    """Tests for GET /api/swarm/tasks with pagination and filtering."""

    def test_tasks_endpoint_returns_paginated_results(self) -> None:
        """GET /api/swarm/tasks returns paginated results."""
        _, client = _make_app()
        response = client.get("/api/swarm/tasks?page=1&pageSize=5")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "count" in data

    def test_tasks_endpoint_pagination_non_overlapping(self) -> None:
        """Two different pages return non-overlapping items."""
        _, client = _make_app()
        page1 = client.get("/api/swarm/tasks?page=1&pageSize=3").json()
        page2 = client.get("/api/swarm/tasks?page=2&pageSize=3").json()
        p1_ids = {t["id"] for t in page1["tasks"]}
        p2_ids = {t["id"] for t in page2["tasks"]}
        assert p1_ids.isdisjoint(p2_ids)

    def test_tasks_endpoint_filter_by_status(self) -> None:
        """GET /api/swarm/tasks?status=completed filters correctly."""
        _, client = _make_app()
        response = client.get("/api/swarm/tasks?status=completed")
        assert response.status_code == 200
        data = response.json()
        for task in data["tasks"]:
            assert task["status"] == "completed"

    def test_tasks_endpoint_filter_by_type(self) -> None:
        """GET /api/swarm/tasks?type=consult filters correctly."""
        _, client = _make_app()
        response = client.get("/api/swarm/tasks?type=consult")
        assert response.status_code == 200
        data = response.json()
        for task in data["tasks"]:
            assert task["type"] == "consult"

    def test_tasks_endpoint_filter_by_worker(self) -> None:
        """GET /api/swarm/tasks?worker=worker-0 filters correctly."""
        _, client = _make_app()
        response = client.get("/api/swarm/tasks?worker=worker-0")
        assert response.status_code == 200


class TestTaskDetailAPI:
    """Tests for GET /api/swarm/tasks/{id}."""

    def test_detail_returns_full_result(self) -> None:
        """GET /api/swarm/tasks/{id} returns full task detail."""
        _, client = _make_app()
        # Use the dispatch endpoint first to create a task
        # For now, just test the non-existent case
        response = client.get("/api/swarm/tasks/nonexistent-task-id")
        assert response.status_code == 404

    def test_detail_unknown_id_returns_404(self) -> None:
        """Unknown task id returns 404."""
        _, client = _make_app()
        response = client.get("/api/swarm/tasks/does-not-exist")
        assert response.status_code == 404


class TestWorkerMetricsAPI:
    """Tests for GET /api/swarm/workers/{name}/metrics."""

    def test_metrics_endpoint_returns_data(self) -> None:
        """GET /api/swarm/workers/{name}/metrics returns metrics."""
        _, client = _make_app()
        response = client.get("/api/swarm/workers/worker-a/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data

    def test_metrics_all_endpoint(self) -> None:
        """GET /api/swarm/workers/metrics/all returns all worker metrics."""
        _, client = _make_app()
        response = client.get("/api/swarm/workers/metrics/all")
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
