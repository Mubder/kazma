"""Tests for VAL-SIDEBAR-001: Sidebar shows currently selected model.

Validates that the sidebar fetches the active model from
``/api/provider/active`` on page load instead of displaying a hardcoded
``gpt-4o-mini`` string.

Checks performed:
  - ``sidebarComponent()`` in ``app.js`` calls ``/api/provider/active`` on init
  - ``sidebar.html`` uses ``x-text`` for reactive model display (no hardcoded
    bare Jinja-only rendering of the model name)
  - ``/api/provider/active`` returns a ``model`` key
  - The SSE chat router (which mounts ``/api/provider/active``) is registered
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"
_JS_DIR = _STATIC_DIR / "js"


class TestStartupLlmHydration:
    """Startup runtime LLM settings should seed active provider profile."""

    def _make_agent(self, *, base_url: str, model: str, api_key: str) -> SimpleNamespace:
        llm = MagicMock()
        llm.config = SimpleNamespace(base_url=base_url, model=model, api_key=api_key)
        llm.reconfigure = MagicMock()
        return SimpleNamespace(llm=llm)

    def test_runtime_settings_hydrate_profile(self) -> None:
        from kazma_ui.app import _hydrate_startup_llm_profile

        agent = self._make_agent(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="yaml-key",
        )
        config_store = MagicMock()
        config_store.get_all.return_value = {
            "model": {
                "llm.base_url": "https://api.deepseek.com/v1",
                "llm.model": "deepseek-chat",
                "llm.api_key": "test-api-key-placeholder",
            }
        }

        profile, source = _hydrate_startup_llm_profile(agent, config_store)

        agent.llm.reconfigure.assert_called_once_with(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            api_key="test-api-key-placeholder",
        )
        assert source == "runtime store"
        assert profile["base_url"] == "https://api.deepseek.com/v1"
        assert profile["model"] == "deepseek-chat"
        assert profile["provider"] == "deepseek"

    def test_yaml_defaults_used_when_no_runtime_values(self) -> None:
        from kazma_ui.app import _hydrate_startup_llm_profile

        agent = self._make_agent(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="yaml-key",
        )
        config_store = MagicMock()
        config_store.get_all.return_value = {}

        profile, source = _hydrate_startup_llm_profile(agent, config_store)

        agent.llm.reconfigure.assert_not_called()
        assert source == "yaml defaults"
        assert profile["base_url"] == "https://api.openai.com/v1"
        assert profile["model"] == "gpt-4o-mini"
        assert profile["provider"] == "openai"


# ══════════════════════════════════════════════════════════════════════════
# Source-level checks: sidebar.html and app.js
# ══════════════════════════════════════════════════════════════════════════


class TestSidebarSourceHasDynamicModel:
    """sidebar.html and app.js must fetch + display the active model dynamically."""

    def test_sidebar_html_uses_x_text_for_model(self) -> None:
        """sidebar.html must use Alpine x-text bound to activeModel."""
        sidebar = (_TEMPLATES_DIR / "components" / "sidebar.html").read_text()
        assert "x-text" in sidebar, "sidebar.html must use x-text for reactive model display"
        assert "activeModel" in sidebar, (
            "sidebar.html must reference activeModel from sidebarComponent()"
        )

    def test_sidebar_html_model_badge_not_bare_hardcoded(self) -> None:
        """The model-badge span must not contain a bare hardcoded model string.

        The model text must be wrapped in an x-text binding so Alpine.js
        replaces it with the fetched value. We verify the model-badge
        div contains x-text (not just a Jinja expression alone).
        """
        sidebar = (_TEMPLATES_DIR / "components" / "sidebar.html").read_text()
        # Find the model-badge section
        badge_idx = sidebar.find("model-badge")
        assert badge_idx != -1, "model-badge section not found in sidebar.html"
        badge_section = sidebar[badge_idx:]
        # The badge section should contain x-text with activeModel
        assert "x-text" in badge_section, (
            "model-badge must use x-text for reactive model display"
        )

    def test_app_js_sidebar_component_has_fetch(self) -> None:
        """sidebarComponent() in app.js must fetch /api/provider/active."""
        js = (_JS_DIR / "app.js").read_text()
        # Find the sidebarComponent function
        idx = js.find("function sidebarComponent()")
        assert idx != -1, "sidebarComponent() not found in app.js"
        section = js[idx:]
        assert "/api/provider/active" in section, (
            "sidebarComponent() must fetch /api/provider/active"
        )

    def test_app_js_sidebar_component_has_init(self) -> None:
        """sidebarComponent() must have an init() that triggers the fetch."""
        js = (_JS_DIR / "app.js").read_text()
        idx = js.find("function sidebarComponent()")
        section = js[idx:]
        assert "init()" in section, "sidebarComponent() must define init()"
        assert "fetchActiveModel" in section, (
            "sidebarComponent() must call fetchActiveModel()"
        )

    def test_app_js_sidebar_component_has_active_model_state(self) -> None:
        """sidebarComponent() must have activeModel state property."""
        js = (_JS_DIR / "app.js").read_text()
        idx = js.find("function sidebarComponent()")
        section = js[idx:]
        assert "activeModel" in section, (
            "sidebarComponent() must expose activeModel reactive property"
        )


# ══════════════════════════════════════════════════════════════════════════
# API-level check: /api/provider/active returns a model field
# ══════════════════════════════════════════════════════════════════════════


class TestProviderActiveEndpoint:
    """/api/provider/active must return a dict with a 'model' key."""

    @pytest.fixture
    def client(self) -> TestClient:
        from kazma_ui.app import create_app

        app = create_app()
        return TestClient(app)

    def test_provider_active_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/provider/active")
        assert resp.status_code == 200

    def test_provider_active_returns_model_key(self, client: TestClient) -> None:
        resp = client.get("/api/provider/active")
        data = resp.json()
        assert "model" in data, "/api/provider/active must return a 'model' key"


# ══════════════════════════════════════════════════════════════════════════
# Integration check: pages that include the sidebar return 200
# ══════════════════════════════════════════════════════════════════════════


class TestSidebarRendersOnPages:
    """Pages that include the sidebar must render successfully (200)."""

    @pytest.fixture
    def client(self) -> TestClient:
        from kazma_ui.app import create_app

        app = create_app()
        return TestClient(app)

    @pytest.mark.parametrize("route", ["/", "/chat", "/settings", "/agents"])
    def test_page_returns_200_with_sidebar(self, client: TestClient, route: str) -> None:
        resp = client.get(route)
        assert resp.status_code == 200
        # The sidebar component script must be loaded
        assert "sidebarComponent" in resp.text or "sidebar" in resp.text.lower()
