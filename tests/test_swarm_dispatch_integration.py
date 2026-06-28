"""Tests for SwarmManager integration with the swarm panel dispatch endpoint.

Validates VAL-SWARM-001: dispatching a task to an in-process worker calls
SwarmManager.dispatch() and the result is returned from the endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router


def _build_client_with_manager(swarm_manager=None):
    """Build a FastAPI TestClient with the swarm router wired to a manager."""
    _reset_swarm_state()
    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")

    router = create_swarm_router(templates, swarm_manager=swarm_manager)
    app.include_router(router)

    return TestClient(app)


class TestDispatchCallsSwarmManager:
    """VAL-SWARM-001: dispatching a task calls SwarmManager.dispatch()."""

    def test_dispatch_calls_swarm_manager(self):
        """When a SwarmManager is wired, dispatch must invoke manager.dispatch."""
        mock_manager = MagicMock()
        mock_manager.dispatch = AsyncMock(return_value={
            "worker": "w1",
            "task_id": "task-123",
            "status": "success",
            "output": "Task completed",
            "error": None,
        })

        client = _build_client_with_manager(swarm_manager=mock_manager)

        # Add a worker so it exists in the local registry
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        payload = {"workers": ["w1"], "task": "Run tests"}
        response = client.post("/api/swarm/dispatch", json=payload)
        assert response.status_code == 200
        data = response.json()

        # SwarmManager.dispatch must have been called
        mock_manager.dispatch.assert_called_once()
        call_args = mock_manager.dispatch.call_args
        assert call_args.args[0] == "w1"
        assert call_args.args[1] == "Run tests"

    def test_dispatch_returns_result_from_manager(self):
        """Dispatch response includes results from SwarmManager.dispatch."""
        mock_manager = MagicMock()
        mock_manager.dispatch = AsyncMock(return_value={
            "worker": "w1",
            "task_id": "task-456",
            "status": "success",
            "output": "Computed result",
            "error": None,
        })

        client = _build_client_with_manager(swarm_manager=mock_manager)
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        payload = {"workers": ["w1"], "task": "Do work", "context": "ctx"}
        response = client.post("/api/swarm/dispatch", json=payload)
        data = response.json()

        assert "results" in data
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["worker"] == "w1"
        assert result["status"] == "success"
        assert result["output"] == "Computed result"

    def test_dispatch_worker_busy_flag_cleared_after_completion(self):
        """VAL-SWARM-001: worker busy flag set back to false after completion."""
        mock_manager = MagicMock()
        mock_manager.dispatch = AsyncMock(return_value={
            "worker": "w1",
            "task_id": "task-789",
            "status": "success",
            "output": "done",
            "error": None,
        })

        client = _build_client_with_manager(swarm_manager=mock_manager)
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": "x"})

        # Check status to verify busy flag cleared
        status = client.get("/api/swarm/status").json()
        w1 = [w for w in status["workers"] if w["name"] == "w1"][0]
        assert w1["status"] != "busy"

    def test_dispatch_includes_error_result_on_failure(self):
        """When dispatch returns an error, the result is surfaced."""
        mock_manager = MagicMock()
        mock_manager.dispatch = AsyncMock(return_value={
            "worker": "w1",
            "task_id": "task-err",
            "status": "error",
            "output": "",
            "error": "Manager not initialized",
        })

        client = _build_client_with_manager(swarm_manager=mock_manager)
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        response = client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": "fail"})
        data = response.json()
        assert data["results"][0]["status"] == "error"
        assert "Manager not initialized" in data["results"][0]["error"]

    def test_dispatch_handles_manager_exception_gracefully(self):
        """If SwarmManager.dispatch raises, endpoint returns error result."""
        mock_manager = MagicMock()
        mock_manager.dispatch = AsyncMock(side_effect=RuntimeError("boom"))

        client = _build_client_with_manager(swarm_manager=mock_manager)
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        response = client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": "x"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "error"
        assert "boom" in data["results"][0]["error"]


class TestDispatchNoManagerFallback:
    """Behavior when no SwarmManager is wired (graceful degradation)."""

    def test_dispatch_without_manager_returns_warning(self):
        """Without a manager, dispatch falls back to local-only mode."""
        client = _build_client_with_manager(swarm_manager=None)
        client.post("/api/swarm/workers", json={"name": "w1", "type": "in-process"})

        response = client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": "x"})
        assert response.status_code == 200
        data = response.json()
        # Should still succeed locally but indicate no real execution
        assert data["status"] in ("ok", "warning")
        assert "w1" in data.get("dispatched", [])


class TestUiAddedInProcessWorker:
    """UI-added in-process workers are dispatched via SwarmManager."""

    def test_ui_added_worker_creates_inprocess_worker_dynamically(self):
        """A UI-added in-process worker is registered with the SwarmManager."""
        mock_manager = MagicMock()
        mock_manager.add_worker = MagicMock()
        mock_manager.get_worker = MagicMock(return_value=None)
        mock_manager.dispatch = AsyncMock(return_value={
            "worker": "ui-w1",
            "task_id": "t1",
            "status": "success",
            "output": "ok",
            "error": None,
        })

        client = _build_client_with_manager(swarm_manager=mock_manager)

        # Add a worker via the UI endpoint
        client.post("/api/swarm/workers", json={
            "name": "ui-w1",
            "type": "in-process",
            "model": "deepseek-chat",
            "provider": "deepseek",
        })

        # Manager.add_worker should have been called to register dynamically
        mock_manager.add_worker.assert_called()
