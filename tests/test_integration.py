"""Integration tests for the Kazma Web UI using FastAPI TestClient."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from kazma_ui.app import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestDashboardRoutes:
    def test_dashboard_page(self, client: TestClient) -> None:
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "Observability Dashboard" in resp.text

    def test_dashboard_api_status(self, client: TestClient) -> None:
        resp = client.get("/api/dashboard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data
        assert "metrics" in data
        assert "cost" in data

    def test_dashboard_api_has_required_keys(self, client: TestClient) -> None:
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert "circuit_breaker" in data
        assert "tracing_backend" in data
        cost = data.get("cost", {})
        assert "current" in cost
        assert "max" in cost


class TestAgentRoutes:
    def test_agents_page(self, client: TestClient) -> None:
        resp = client.get("/agents")
        assert resp.status_code == 200
        assert "Agent Management" in resp.text

    def test_agents_api_status(self, client: TestClient) -> None:
        resp = client.get("/api/agents/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "config" in data
        assert "llm" in data
        assert "tools" in data

    def test_agents_hub_api(self, client: TestClient) -> None:
        resp = client.get("/api/agents/hub")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "count" in data

    def test_agents_stop_action(self, client: TestClient) -> None:
        resp = client.post("/api/agents/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["running"] is False

    def test_agents_start_action(self, client: TestClient) -> None:
        resp = client.post("/api/agents/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["running"] is True

    def test_agents_unknown_action(self, client: TestClient) -> None:
        resp = client.post("/api/agents/reboot")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data or resp.status_code == 400


class TestSettingsRoutes:
    def test_settings_page(self, client: TestClient) -> None:
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text

    def test_settings_api_all(self, client: TestClient) -> None:
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_settings_export_yaml(self, client: TestClient) -> None:
        resp = client.get("/api/settings/export")
        assert resp.status_code == 200
        assert "text/yaml" in resp.headers.get("content-type", "")


class TestSkillRoutes:
    def test_skills_page(self, client: TestClient) -> None:
        resp = client.get("/skills")
        assert resp.status_code == 200
        assert "Skills" in resp.text

    def test_skills_api_list(self, client: TestClient) -> None:
        resp = client.get("/api/skills")
        assert resp.status_code == 200

    def test_skills_hub_search(self, client: TestClient) -> None:
        resp = client.get("/api/skills/hub/search?q=test")
        assert resp.status_code == 200


class TestMCPRoutes:
    def test_mcp_page(self, client: TestClient) -> None:
        resp = client.get("/mcp")
        assert resp.status_code == 200
        assert "MCP" in resp.text


class TestRootRoutes:
    def test_root_redirects_to_chat(self, client: TestClient) -> None:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307 or resp.status_code == 303
        assert "/chat" in resp.headers.get("location", "")

    def test_chat_page(self, client: TestClient) -> None:
        resp = client.get("/chat")
        assert resp.status_code == 200
        assert "Chat" in resp.text


class TestErrorPages:
    def test_404_page(self, client: TestClient) -> None:
        resp = client.get("/nonexistent-route")
        assert resp.status_code == 404
        assert "not found" in resp.text.lower()

    def test_404_returns_html(self, client: TestClient) -> None:
        resp = client.get("/missing/page")
        assert resp.status_code == 404
        assert "text/html" in resp.headers.get("content-type", "")


class TestStaticFiles:
    def test_css_served(self, client: TestClient) -> None:
        resp = client.get("/static/css/kazma.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")

    def test_htmx_js_served(self, client: TestClient) -> None:
        resp = client.get("/static/js/htmx.min.js")
        assert resp.status_code == 200


class TestLanguageDirection:
    def test_page_has_rtl_dir(self, client: TestClient) -> None:
        resp = client.get("/chat")
        assert 'dir="rtl"' in resp.text

    def test_page_has_arabic_lang(self, client: TestClient) -> None:
        resp = client.get("/chat")
        assert 'lang="ar"' in resp.text
