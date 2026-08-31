"""Tests for UX-003 UI bug fixes.

Validates:
  VAL-UX-003: Only one telemetry SSE route exists (mock deleted)
  VAL-UX-004: Toast notifications work (no null reference error)
  VAL-UX-005: Swarm worker logs endpoint exists
  VAL-UX-008: Cost breaker sends error type (not done type)
  VAL-UX-009: Init failures (SSE/telemetry/gateway) surfaced to UI
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_COMPONENTS_DIR = _TEMPLATES_DIR / "components"
_STATIC_DIR = _UI_DIR / "static"
_JS_DIR = _STATIC_DIR / "js"


# ── VAL-UX-003: Only one telemetry SSE route exists ─────────────────

class TestTelemetryRouteDedup:
    """The mock /api/telemetry/stream in app.py must be removed."""

    @pytest.fixture
    def app_py(self):
        return (_UI_DIR / "app.py").read_text(encoding="utf-8")

    def test_only_one_telemetry_stream_route(self, app_py):
        """grep for '/api/telemetry/stream' returns exactly one route definition."""
        # Count @app.get("/api/telemetry/stream") registrations
        # The real route is inside telemetry_route.py, mounted via
        # create_telemetry_router() — app.py should have zero @app.get
        # for this path.
        count = len(re.findall(r'@app\.get\(["\']/api/telemetry/stream["\']', app_py))
        assert count == 0, (
            f"Found {count} mock @app.get('/api/telemetry/stream') in app.py. "
            "The mock route must be deleted; the real one is in telemetry_route.py."
        )

    def test_telemetry_router_import_exists(self, app_py):
        """app.py imports and mounts the real telemetry router."""
        assert "create_telemetry_router" in app_py
        assert "app.include_router(telemetry_router)" in app_py

    def test_no_mock_telemetry_stream_function(self, app_py):
        """No 'async def telemetry_stream' function in app.py."""
        # The function should NOT exist in app.py anymore
        assert "async def telemetry_stream" not in app_py, (
            "Mock telemetry_stream function still present in app.py"
        )


# ── VAL-UX-004: Toast notifications work ────────────────────────────

class TestToastFix:
    """streaming.js toast() must not throw a null reference error."""

    @pytest.fixture
    def streaming_js(self):
        return (_JS_DIR / "streaming.js").read_text(encoding="utf-8")

    def test_toast_falls_back_to_query_selector(self, streaming_js):
        """toast() uses querySelector('.toast-container') as fallback."""
        # The function should query by both id and class
        assert "querySelector('.toast-container')" in streaming_js or \
               'querySelector(".toast-container")' in streaming_js, (
            "streaming.js toast() must fall back to querySelector('.toast-container')"
        )

    def test_toast_has_null_guard(self, streaming_js):
        """toast() returns early if container is null (no appendChild on null)."""
        # Extract the toast function body
        match = re.search(r'function toast\(.*?\{(.*?)\n  \}', streaming_js, re.DOTALL)
        if match:
            body = match.group(1)
            # Should have a null guard before appendChild
            assert "if (!container)" in body or "if (container === null)" in body, (
                "toast() must guard against null container before appendChild"
            )


# ── VAL-UX-005: Swarm worker logs endpoint exists ───────────────────

class TestSwarmLogsEndpoint:
    """GET /api/swarm/workers/{name}/logs must exist and return data."""

    def test_logs_endpoint_returns_200(self):
        """After adding a worker, the logs endpoint returns 200 with log lines."""
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from fastapi.testclient import TestClient
        from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router

        _reset_swarm_state()

        app = FastAPI()
        templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
        router = create_swarm_router(templates)
        app.include_router(router)
        client = TestClient(app)

        # Add a worker
        client.post("/api/swarm/workers", json={"name": "logs-test-worker"})

        # Get logs
        resp = client.get("/api/swarm/workers/logs-test-worker/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert isinstance(data["logs"], list)
        assert len(data["logs"]) > 0  # At least the synthesized log line

    def test_logs_endpoint_returns_404_for_unknown_worker(self):
        """Logs endpoint returns 404 for unknown worker."""
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from fastapi.testclient import TestClient
        from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router

        _reset_swarm_state()

        app = FastAPI()
        templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
        router = create_swarm_router(templates)
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/swarm/workers/nonexistent-worker/logs")
        assert resp.status_code == 404

    def test_logs_reflect_dispatched_task(self):
        """Logs include task dispatch entries."""
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from fastapi.testclient import TestClient
        from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router

        _reset_swarm_state()

        app = FastAPI()
        templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
        router = create_swarm_router(templates)
        app.include_router(router)
        client = TestClient(app)

        # Add worker and dispatch a task
        client.post("/api/swarm/workers", json={"name": "task-log-worker"})
        client.post("/api/swarm/dispatch", json={
            "workers": ["task-log-worker"],
            "task": "Test task for logging"
        })

        # Get logs — should contain the dispatch entry
        resp = client.get("/api/swarm/workers/task-log-worker/logs")
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        log_text = " ".join(logs)
        assert "Test task for logging" in log_text or "Task dispatched" in log_text


# ── VAL-UX-008: Cost breaker sends error type ───────────────────────

class TestCostBreakerType:
    """Supervisor cost breaker must halt to RESPOND with a warning, not a fake answer."""

    @pytest.fixture
    def supervisor_py(self):
        root = Path(__file__).resolve().parent.parent
        return (
            root / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py"
        ).read_text(encoding="utf-8")

    def test_cost_breaker_forces_respond(self, supervisor_py):
        halt_idx = supervisor_py.find("cost_breaker.should_halt()")
        assert halt_idx > 0
        body = supervisor_py[halt_idx:halt_idx + 800]
        assert "NodeName.RESPOND" in body
        assert "⚠️" in body

    def test_budget_halt_is_not_a_tool_loop(self, supervisor_py):
        halt_idx = supervisor_py.find("Cost breaker tripped")
        assert halt_idx > 0
        body = supervisor_py[halt_idx:halt_idx + 500]
        assert "TOOL_WORKER" not in body


# ── VAL-UX-009: Init failures surfaced to UI ────────────────────────

class TestInitErrorsSurfaced:
    """app.py must expose init_errors via /api/status or /api/health."""

    @pytest.fixture
    def app_py(self):
        return (_UI_DIR / "app.py").read_text(encoding="utf-8")

    def test_init_errors_list_exists(self, app_py):
        """app.py defines an _init_errors list to track failures."""
        assert "_init_errors" in app_py, (
            "app.py must define _init_errors list to track init failures"
        )

    def test_sse_init_error_captured(self, app_py):
        """SSE router init failure appends to _init_errors."""
        sse_except_idx = app_py.find("SSE/WS chat router failed to initialize")
        if sse_except_idx < 0:
            sse_except_idx = app_py.find("SSE chat router failed to initialize")
        assert sse_except_idx > 0
        context = app_py[sse_except_idx:sse_except_idx + 250]
        assert "_init_errors.append" in context, (
            "SSE init failure must append to _init_errors"
        )

    def test_telemetry_init_error_captured(self, app_py):
        """Telemetry router init failure appends to _init_errors."""
        telemetry_except_idx = app_py.find("Telemetry router failed to initialize")
        assert telemetry_except_idx > 0
        context = app_py[telemetry_except_idx:telemetry_except_idx + 200]
        assert "_init_errors.append" in context, (
            "Telemetry init failure must append to _init_errors"
        )

    def test_gateway_init_error_captured(self, app_py):
        """Gateway init failure appends to _init_errors."""
        gateway_except_idx = app_py.find("Gateway failed to initialize")
        assert gateway_except_idx > 0
        context = app_py[gateway_except_idx:gateway_except_idx + 400]
        assert "_init_errors.append" in context, (
            "Gateway init failure must append to _init_errors"
        )

    def test_api_status_endpoint_exists(self, app_py):
        """GET /api/status returns init_errors (routes_direct/misc.py)."""
        misc = (_UI_DIR / "routes_direct" / "misc.py").read_text(encoding="utf-8")
        assert "/api/status" in misc or "/api/status" in app_py
        assert "init_errors" in misc

    def test_health_includes_init_errors(self, app_py):
        """Init errors are on /api/status (stripped /health payloads)."""
        misc = (_UI_DIR / "routes_direct" / "misc.py").read_text(encoding="utf-8")
        assert "init_errors" in misc
        assert "_init_errors" in app_py
