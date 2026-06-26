"""Tests for Swarm API endpoints and panel rendering."""

from __future__ import annotations

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------


def _build_client():
    """Build a FastAPI TestClient with the swarm router for testing."""
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates

    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")

    from kazma_ui.swarm_panel import create_swarm_router

    router = create_swarm_router(templates)
    app.include_router(router)

    return TestClient(app)


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


class TestSwarmStatus:
    """Test GET /api/swarm/status endpoint."""

    def test_swarm_status_endpoint(self):
        """Returns workers list with expected structure."""
        client = _build_client()
        response = client.get("/api/swarm/status")
        assert response.status_code == 200
        data = response.json()
        assert "workers" in data
        assert "count" in data
        assert "started" in data
        assert "has_swarm_core" in data
        assert isinstance(data["workers"], list)

    def test_swarm_status_shows_setup_instructions(self):
        """Has setup_instructions field in the response (None when core present)."""
        client = _build_client()
        response = client.get("/api/swarm/status")
        data = response.json()
        assert "setup_instructions" in data
        assert "has_swarm_core" in data


# ---------------------------------------------------------------------------
# Add / Remove worker
# ---------------------------------------------------------------------------


class TestSwarmAddWorker:
    """Test POST /api/swarm/workers endpoint."""

    def test_swarm_add_worker_endpoint(self):
        """Adds a worker and returns 201 with worker data."""
        client = _build_client()
        payload = {
            "name": "test-worker-1",
            "model": "deepseek-chat",
            "provider": "deepseek",
            "type": "in-process",
        }
        response = client.post("/api/swarm/workers", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "ok"
        assert data["worker"]["name"] == "test-worker-1"
        assert data["worker"]["model"] == "deepseek-chat"
        assert data["worker"]["provider"] == "deepseek"

    def test_add_worker_requires_name(self):
        """Adding a worker with no name returns 400."""
        client = _build_client()
        response = client.post("/api/swarm/workers", json={"name": ""})
        assert response.status_code == 400
        assert response.json()["status"] == "error"

    def test_add_worker_rejects_duplicate(self):
        """Adding a worker with an existing name returns 409."""
        client = _build_client()
        payload = {"name": "dup-worker", "model": "gpt-4o", "provider": "openai"}
        # First add
        r1 = client.post("/api/swarm/workers", json=payload)
        assert r1.status_code == 201
        # Second add — duplicate
        r2 = client.post("/api/swarm/workers", json=payload)
        assert r2.status_code == 409
        assert "already exists" in r2.json()["message"]

    def test_add_worker_defaults_model_and_provider(self):
        """Worker with minimal payload gets defaults."""
        client = _build_client()
        response = client.post("/api/swarm/workers", json={"name": "minimal"})
        assert response.status_code == 201
        worker = response.json()["worker"]
        assert worker["model"] == "deepseek-chat"
        assert worker["provider"] == "deepseek"


class TestSwarmRemoveWorker:
    """Test DELETE /api/swarm/workers/{name} endpoint."""

    def test_swarm_remove_worker_endpoint(self):
        """Removes an existing worker."""
        client = _build_client()
        # Add first
        client.post(
            "/api/swarm/workers",
            json={"name": "to-remove", "model": "claude-haiku-3.5", "provider": "anthropic"},
        )
        # Remove
        response = client.delete("/api/swarm/workers/to-remove")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_remove_nonexistent_worker(self):
        """Removing a worker that doesn't exist returns 404."""
        client = _build_client()
        response = client.delete("/api/swarm/workers/nonexistent")
        assert response.status_code == 404
        assert response.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestSwarmDispatch:
    """Test POST /api/swarm/dispatch endpoint."""

    def test_swarm_dispatch_endpoint(self):
        """Accepts dispatch request and returns dispatched list."""
        client = _build_client()
        # Add a worker first
        client.post("/api/swarm/workers", json={"name": "w1"})
        payload = {"workers": ["w1"], "task": "Run integration tests"}
        response = client.post("/api/swarm/dispatch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ok", "warning")
        assert "w1" in data.get("dispatched", [])

    def test_dispatch_requires_workers(self):
        """Dispatch with empty workers list returns 400."""
        client = _build_client()
        response = client.post("/api/swarm/dispatch", json={"workers": [], "task": "do stuff"})
        assert response.status_code == 400

    def test_dispatch_requires_task(self):
        """Dispatch with empty task returns 400."""
        client = _build_client()
        response = client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": ""})
        assert response.status_code == 400

    def test_dispatch_marks_missing_workers(self):
        """Workers not in registry appear in 'missing' list."""
        client = _build_client()
        payload = {"workers": ["ghost"], "task": "invisible work"}
        response = client.post("/api/swarm/dispatch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "ghost" in data.get("missing", [])


# ---------------------------------------------------------------------------
# Start / Stop lifecycle
# ---------------------------------------------------------------------------


class TestSwarmStartStop:
    """Test POST /api/swarm/start and /api/swarm/stop endpoints."""

    def test_swarm_start_stop_endpoints(self):
        """Full lifecycle: add worker → start → stop."""
        client = _build_client()
        # Add a worker
        client.post("/api/swarm/workers", json={"name": "lifecycle-w"})

        # Status should show started=False before start
        status = client.get("/api/swarm/status").json()
        assert status["started"] is False

        # Start — with swarm core missing, this returns warning
        resp_start = client.post("/api/swarm/start")
        assert resp_start.status_code in (200, 400)

        # Stop
        resp_stop = client.post("/api/swarm/stop")
        assert resp_stop.status_code == 200
        assert resp_stop.json()["status"] == "ok"

    def test_start_requires_workers(self):
        """Starting with no workers returns 400."""
        client = _build_client()
        # Reset shared state to ensure clean slate
        from kazma_ui.swarm_panel import _reset_swarm_state
        _reset_swarm_state()
        response = client.post("/api/swarm/start")
        assert response.status_code == 400
        assert "No workers registered" in response.json()["message"]

    def test_stop_when_already_stopped(self):
        """Stopping when already stopped is idempotent."""
        client = _build_client()
        from kazma_ui.swarm_panel import _reset_swarm_state
        _reset_swarm_state()
        response = client.post("/api/swarm/stop")
        assert response.status_code == 200
        assert "already stopped" in response.json()["message"]


# ---------------------------------------------------------------------------
# Panel rendering
# ---------------------------------------------------------------------------


class TestSwarmPanel:
    """Test /swarm page renders."""

    def test_swarm_panel_renders(self):
        """Page loads and returns HTML."""
        client = _build_client()
        response = client.get("/swarm")
        assert response.status_code == 200
        # Should return HTML content
        content = response.text.lower()
        assert "swarm" in content or "swarm panel" in content or "html" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Models endpoint
# ---------------------------------------------------------------------------


class TestSwarmModels:
    """Test GET /api/swarm/models."""

    def test_models_returns_lists(self):
        """Returns models and providers for dropdowns."""
        client = _build_client()
        response = client.get("/api/swarm/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert "providers" in data
        assert isinstance(data["models"], list)
        assert isinstance(data["providers"], list)
        assert len(data["models"]) > 0
        assert len(data["providers"]) > 0


# ---------------------------------------------------------------------------
# README & CHANGELOG verification
# ---------------------------------------------------------------------------


class TestDocsHaveSwarm:
    """Verify that swarm-related documentation is present."""

    def test_readme_has_swarm_section(self):
        """README.md contains the Swarm Orchestration section."""
        from pathlib import Path

        readme = Path(__file__).resolve().parent.parent / "README.md"
        content = readme.read_text()
        assert "Swarm Orchestration" in content
        assert "Swarm Panel" in content
        assert "swarm:" in content  # YAML config block

    def test_changelog_has_swarm(self):
        """CHANGELOG.md contains the Swarm entry."""
        from pathlib import Path

        changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
        content = changelog.read_text()
        assert "gw-067" in content
        assert "Swarm" in content
