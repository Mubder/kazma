"""Tests for UX-005: Build functional Agents page.

Validates VAL-UX-006:
  - Agents page is functional (not a 6-line placeholder)
  - GET /agents returns HTML with populated agent data (not placeholder string)
  - GET /api/agents returns JSON data (non-empty array)
  - Agent running status is visible
  - Page uses consistent styling (extends base.html, loads kazma.css)
  - New endpoints: traces, tool history, reasoning, start/stop controls
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"
_JS_DIR = _STATIC_DIR / "js"


# ══════════════════════════════════════════════════════════════════════════
# Mock agent for route testing
# ══════════════════════════════════════════════════════════════════════════


def _make_mock_agent(running: bool = False) -> Any:
    """Build a minimal mock agent object for the agents router."""
    config = SimpleNamespace(
        name="kazma-test",
        version="0.1.0",
        language="ar",
        rtl=True,
        default_model="gpt-4o-mini",
        system_prompt="You are a test agent.",
        raw={},
    )
    llm_config = SimpleNamespace(
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        max_tokens=4096,
        temperature=0.7,
    )

    class MockTools:
        def list_tools(self) -> list[dict[str, Any]]:
            return [
                {"name": "web_search", "description": "Search the web"},
                {"name": "code_exec", "description": "Execute Python code"},
            ]

        def get_tool_definitions(self) -> list[dict[str, Any]]:
            return self.list_tools()

        def list_servers(self) -> list[dict[str, Any]]:
            return []

        _servers: dict[str, Any] = {}

    mock_state: dict[str, Any] = {"running": running}

    class _Agent:
        """Mock agent with public facade methods."""

        def __init__(self) -> None:
            self.config = config
            self.llm_config = llm_config
            self.tools = MockTools()

        @property
        def is_running(self) -> bool:
            return mock_state["running"]

        def set_running(self, val: bool) -> None:
            mock_state["running"] = val

        def get_tools_info(self) -> dict[str, Any]:
            return {
                "count": len(self.tools.list_tools()),
                "list": [
                    {"name": t["name"], "description": t["description"]}
                    for t in self.tools.list_tools()[:20]
                ],
                "servers": len(self.tools.list_servers()),
            }

        def get_llm_config(self) -> dict[str, Any]:
            return {
                "model": self.llm_config.model,
                "base_url": self.llm_config.base_url,
                "max_tokens": self.llm_config.max_tokens,
                "temperature": self.llm_config.temperature,
            }

    return _Agent()


@pytest.fixture
def mock_agent() -> Any:
    return _make_mock_agent(running=False)


@pytest.fixture
def agents_html() -> str:
    return (_TEMPLATES_DIR / "agents.html").read_text(encoding="utf-8")


@pytest.fixture
def agents_py() -> str:
    return (_UI_DIR / "agents.py").read_text(encoding="utf-8")


@pytest.fixture
def agents_js() -> str:
    return (_JS_DIR / "agents.js").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# 1. Template is no longer a placeholder
# ══════════════════════════════════════════════════════════════════════════


class TestAgentsTemplateNotPlaceholder:
    """agents.html must be a functional page, not the 6-line placeholder."""

    def test_placeholder_string_removed(self, agents_html: str):
        """The placeholder text must be gone."""
        assert "Agent management and monitoring will be available here." not in agents_html, (
            "Placeholder text still present in agents.html"
        )

    def test_extends_base_html(self, agents_html: str):
        """Must extend base.html for consistent styling."""
        assert '{% extends "base.html" %}' in agents_html

    def test_has_active_page_agents(self, agents_html: str):
        """Must set active_page to 'agents' for sidebar highlighting."""
        assert "active_page" in agents_html and "agents" in agents_html

    def test_has_alpine_component(self, agents_html: str):
        """Must use Alpine.js x-data pattern like other pages."""
        assert "x-data" in agents_html and "x-init" in agents_html

    def test_shows_running_status(self, agents_html: str):
        """Must show agent running status."""
        assert "running" in agents_html.lower()

    def test_shows_agent_state(self, agents_html: str):
        """Must show agent state (idle/thinking/acting)."""
        assert "agent_state" in agents_html

    def test_has_tool_execution_history(self, agents_html: str):
        """Must include a tool execution history section."""
        assert "Tool Execution History" in agents_html or "tool" in agents_html.lower()

    def test_has_reasoning_steps(self, agents_html: str):
        """Must include a reasoning steps section."""
        assert "Reasoning" in agents_html

    def test_has_start_stop_controls(self, agents_html: str):
        """Must have start/stop controls for the agent."""
        assert "Start" in agents_html and "Stop" in agents_html

    def test_loads_agents_js(self, agents_html: str):
        """Must include the agents.js script."""
        assert "/static/js/agents.js" in agents_html

    def test_loads_streaming_js(self, agents_html: str):
        """Must include streaming.js for toast notifications."""
        assert "/static/js/streaming.js" in agents_html


# ══════════════════════════════════════════════════════════════════════════
# 2. JavaScript file exists and is correct
# ══════════════════════════════════════════════════════════════════════════


class TestAgentsJs:
    """agents.js must implement the Alpine component correctly."""

    def test_agents_js_exists(self):
        assert (_JS_DIR / "agents.js").is_file()

    def test_defines_agents_page_function(self, agents_js: str):
        assert "function agentsPage()" in agents_js

    def test_polls_status(self, agents_js: str):
        """Must poll /api/agents/status for real-time updates."""
        assert "/api/agents/status" in agents_js

    def test_calls_tool_history_endpoint(self, agents_js: str):
        assert "/api/agents/tools" in agents_js

    def test_calls_reasoning_endpoint(self, agents_js: str):
        assert "/api/agents/reasoning" in agents_js

    def test_has_start_stop_actions(self, agents_js: str):
        """Must call /api/agents/start and /api/agents/stop."""
        assert "/api/agents/" in agents_js and "start" in agents_js and "stop" in agents_js


# ══════════════════════════════════════════════════════════════════════════
# 3. Backend endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestAgentsEndpoints:
    """HTTP-level tests for the agents API endpoints."""

    @pytest.fixture
    def client(self, mock_agent: Any):
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from fastapi.testclient import TestClient
        from kazma_ui.agents import create_agents_router

        app = FastAPI()
        templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
        router = create_agents_router(mock_agent, templates)
        app.include_router(router)
        return TestClient(app)

    def test_agents_page_returns_200(self, client):
        """GET /agents must return 200 HTML."""
        resp = client.get("/agents")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_agents_page_has_no_placeholder(self, client):
        """Rendered page must not contain the placeholder string."""
        resp = client.get("/agents")
        assert "will be available here" not in resp.text

    def test_agents_page_shows_status(self, client):
        """Rendered page must contain agent status info."""
        resp = client.get("/agents")
        assert "kazma-test" in resp.text or "Running" in resp.text or "Stopped" in resp.text

    def test_agents_status_endpoint(self, client):
        """GET /api/agents/status returns JSON with agent info."""
        resp = client.get("/api/agents/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "agent_state" in data
        assert data["agent_state"] in ("idle", "thinking", "acting")

    def test_agents_list_endpoint(self, client):
        """GET /api/agents returns a non-empty array (VAL-UX-006)."""
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)
        assert len(data["agents"]) > 0
        agent = data["agents"][0]
        assert "running" in agent
        assert "agent_state" in agent

    def test_agents_traces_endpoint(self, client):
        """GET /api/agents/traces returns trace data."""
        resp = client.get("/api/agents/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data
        assert isinstance(data["traces"], list)

    def test_agents_tools_endpoint(self, client):
        """GET /api/agents/tools returns tool history."""
        resp = client.get("/api/agents/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_agents_reasoning_endpoint(self, client):
        """GET /api/agents/reasoning returns reasoning steps."""
        resp = client.get("/api/agents/reasoning")
        assert resp.status_code == 200
        data = resp.json()
        assert "steps" in data
        assert isinstance(data["steps"], list)

    def test_agent_start_control(self, client, mock_agent: Any):
        """POST /api/agents/start starts the agent."""
        assert mock_agent.is_running is False
        resp = client.post("/api/agents/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["running"] is True
        assert mock_agent.is_running is True

    def test_agent_stop_control(self, client, mock_agent: Any):
        """POST /api/agents/stop stops the agent."""
        mock_agent.set_running(True)
        resp = client.post("/api/agents/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["running"] is False
        assert mock_agent.is_running is False

    def test_agent_invalid_action(self, client):
        """POST /api/agents/{invalid} returns 400."""
        resp = client.post("/api/agents/jump")
        assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════
# 4. Agent state derivation logic
# ══════════════════════════════════════════════════════════════════════════


class TestAgentStateDerivation:
    """_derive_agent_state must produce idle/thinking/acting correctly."""

    def test_not_running_is_idle(self):
        from kazma_ui.agents import _derive_agent_state

        assert _derive_agent_state(False, []) == "idle"

    def test_running_no_traces_is_idle(self):
        from kazma_ui.agents import _derive_agent_state

        assert _derive_agent_state(True, []) == "idle"

    def test_running_latest_llm_is_thinking(self):
        from kazma_core.tracing import TraceEntry
        from kazma_ui.agents import _derive_agent_state

        traces = [
            TraceEntry(timestamp=1.0, trace_type="tool", label="web_search", status="success", duration_ms=100),
            TraceEntry(timestamp=2.0, trace_type="llm", label="gpt-4o", status="success", duration_ms=200),
        ]
        assert _derive_agent_state(True, traces) == "thinking"

    def test_running_latest_tool_is_acting(self):
        from kazma_core.tracing import TraceEntry
        from kazma_ui.agents import _derive_agent_state

        traces = [
            TraceEntry(timestamp=1.0, trace_type="llm", label="gpt-4o", status="success", duration_ms=200),
            TraceEntry(timestamp=2.0, trace_type="tool", label="web_search", status="success", duration_ms=100),
        ]
        assert _derive_agent_state(True, traces) == "acting"

    def test_running_latest_state_trace_is_idle(self):
        from kazma_core.tracing import TraceEntry
        from kazma_ui.agents import _derive_agent_state

        traces = [
            TraceEntry(timestamp=1.0, trace_type="state", label="idle → thinking", status="success", duration_ms=0),
        ]
        assert _derive_agent_state(True, traces) == "idle"


# ══════════════════════════════════════════════════════════════════════════
# 5. Sidebar includes Agents link
# ══════════════════════════════════════════════════════════════════════════


class TestSidebarAgentsLink:
    """The sidebar must include a link to the Agents page."""

    def test_sidebar_has_agents_link(self):
        sidebar = (_TEMPLATES_DIR / "components" / "sidebar.html").read_text(encoding="utf-8")
        assert 'href="/agents"' in sidebar
        assert "active_page == 'agents'" in sidebar
