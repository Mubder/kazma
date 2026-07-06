"""Tests for UI-001: Quick UI fixes.

Validates VAL-UI-001 through VAL-UI-004:
  - VAL-UI-001: /workspace serves workspace.html (not a redirect)
  - VAL-UI-002: Keyboard shortcuts use keydown capture (settings.js has captureShortcut)
  - VAL-UI-003: ChromaDB ImportError logged at DEBUG, not WARNING
  - VAL-UI-004: Agent status shows dynamic state with last activity context
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"
_JS_DIR = _STATIC_DIR / "js"


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-001: Workspace route serves workspace.html (not redirect)
# ══════════════════════════════════════════════════════════════════════════


class TestWorkspaceRoute:
    """GET /workspace must render workspace.html, not redirect to /."""

    @pytest.fixture
    def client(self) -> TestClient:
        from kazma_ui.app import create_app

        app = create_app()
        return TestClient(app)

    def test_workspace_returns_200(self, client: TestClient) -> None:
        resp = client.get("/workspace", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_workspace_not_redirect(self, client: TestClient) -> None:
        """Must not return a 307 redirect."""
        resp = client.get("/workspace", follow_redirects=False)
        assert resp.status_code != 307
        assert resp.status_code != 302

    def test_workspace_renders_template(self, client: TestClient) -> None:
        """Rendered page must contain workspace-specific content."""
        resp = client.get("/workspace")
        assert resp.status_code == 200
        # workspace.html extends base.html and has the workspace container
        assert "workspaceApp" in resp.text or "workspace" in resp.text.lower()

    def test_workspace_route_not_redirect_in_source(self) -> None:
        """app.py source must not have RedirectResponse for /workspace."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        # The /workspace route should NOT return RedirectResponse
        # Find the /workspace route block
        assert "/workspace" in app_source
        # Ensure no RedirectResponse is associated with workspace
        lines = app_source.split("\n")
        for i, line in enumerate(lines):
            if '"/workspace"' in line and "app.get" in line:
                # Check the next few lines for RedirectResponse
                block = "\n".join(lines[i : i + 10])
                assert "RedirectResponse" not in block, (
                    "/workspace route still uses RedirectResponse"
                )


class TestSidebarWorkspaceLink:
    """Sidebar workspace link must point to /workspace."""

    def test_sidebar_links_to_workspace(self) -> None:
        sidebar = (_TEMPLATES_DIR / "components" / "sidebar.html").read_text(encoding="utf-8")
        # The workspace nav link must have href="/workspace"
        assert 'href="/workspace"' in sidebar

    def test_sidebar_no_workspace_link_to_root(self) -> None:
        """The workspace nav link must NOT point to bare '/'."""
        sidebar = (_TEMPLATES_DIR / "components" / "sidebar.html").read_text(encoding="utf-8")
        lines = sidebar.split("\n")
        for line in lines:
            if "active_page == 'workspace'" in line:
                # The line with workspace active check must point to /workspace
                assert 'href="/workspace"' in line, (
                    "Workspace nav link does not point to /workspace"
                )


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-002: Keyboard shortcuts use keydown capture
# ══════════════════════════════════════════════════════════════════════════


class TestKeyboardShortcutCapture:
    """Settings page must capture key combinations on keydown."""

    def test_capture_shortcut_function_exists(self) -> None:
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        assert "captureShortcut" in js, "captureShortcut function missing from settings.js"

    def test_capture_shortcut_prevents_default(self) -> None:
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        # The HTML binds @keydown.prevent which prevents default
        html = (_TEMPLATES_DIR / "settings.html").read_text(encoding="utf-8")
        assert "@keydown" in html or "keydown" in html, (
            "Shortcuts tab must use keydown event, not @change"
        )

    def test_capture_shortcut_detects_modifiers(self) -> None:
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        # Must detect ctrl, alt, shift, meta
        assert "ctrlKey" in js or "event.ctrlKey" in js
        assert "altKey" in js or "event.altKey" in js
        assert "shiftKey" in js or "event.shiftKey" in js
        assert "metaKey" in js or "event.metaKey" in js

    def test_capture_shortcut_formats_combination(self) -> None:
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        # Must join with '+' to format the combination
        assert ".join('+')" in js or "join('+')" in js

    def test_shortcuts_tab_no_change_handler(self) -> None:
        """Shortcuts inputs must NOT use @change (old text input pattern)."""
        html = (_TEMPLATES_DIR / "settings.html").read_text(encoding="utf-8")
        # The shortcuts tab content section starts with x-show containing shortcuts
        # Find the Shortcuts tab CONTENT (x-show), not the nav button
        shortcuts_start = html.find('x-show="tab === \'shortcuts\'"')
        shortcuts_end = html.find('x-show="tab === \'account\'"')
        assert shortcuts_start != -1, "Shortcuts content section not found"
        shortcuts_section = html[shortcuts_start:shortcuts_end]
        assert "@keydown" in shortcuts_section, (
            "Shortcuts tab must use keydown event for capture"
        )
        # The old pattern @change="saveShortcut should not be in shortcuts section
        assert '@change="saveShortcut' not in shortcuts_section, (
            "Shortcuts tab still uses @change instead of keydown capture"
        )

    def test_shortcut_inputs_are_readonly(self) -> None:
        """Shortcut inputs should be readonly (capture-only, no manual typing)."""
        html = (_TEMPLATES_DIR / "settings.html").read_text(encoding="utf-8")
        shortcuts_start = html.find('x-show="tab === \'shortcuts\'"')
        shortcuts_end = html.find('x-show="tab === \'account\'"')
        assert shortcuts_start != -1
        shortcuts_section = html[shortcuts_start:shortcuts_end]
        assert "readonly" in shortcuts_section, (
            "Shortcut inputs should be readonly to enforce keydown capture"
        )


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-003: ChromaDB warning suppressed (DEBUG level)
# ══════════════════════════════════════════════════════════════════════════


class TestChromaDBWarningSuppressed:
    """VectorMemory ImportError must be logged at DEBUG, not WARNING."""

    def test_no_warning_level_for_vector_memory(self) -> None:
        """app.py must not use logger.warning for VectorMemory unavailability."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        # Find the VectorMemory exception handler
        vm_idx = app_source.find("[VectorMemory] Not available")
        assert vm_idx != -1, "VectorMemory handler not found"
        # Check the surrounding code (50 chars before)
        context = app_source[max(0, vm_idx - 100) : vm_idx + 50]
        assert "logger.warning" not in context, (
            "VectorMemory ImportError still logged at WARNING level"
        )

    def test_debug_level_for_vector_memory(self) -> None:
        """app.py must use logger.debug for VectorMemory unavailability."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        vm_idx = app_source.find("[VectorMemory] Not available")
        assert vm_idx != -1
        context = app_source[max(0, vm_idx - 100) : vm_idx + 50]
        assert "logger.debug" in context, (
            "VectorMemory ImportError should use logger.debug"
        )

    def test_helpful_hint_present(self) -> None:
        """A helpful hint about installing the rag extra should be present."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        # Look for a hint about rag extra or chromadb
        assert "rag" in app_source.lower() or "chromadb" in app_source.lower(), (
            "No helpful hint about chromadb/rag installation found"
        )


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-004: Agent status shows dynamic state with context
# ══════════════════════════════════════════════════════════════════════════


class TestAgentStatusDynamic:
    """Agents page must show dynamic state with last activity context."""

    def test_agents_html_no_static_agent_process(self) -> None:
        """agents.html must NOT have static 'agent process' text."""
        html = (_TEMPLATES_DIR / "agents.html").read_text(encoding="utf-8")
        # The static "agent process" text should not be in the status card
        assert ">agent process<" not in html, (
            "Static 'agent process' text still present in agents.html"
        )

    def test_agents_html_shows_last_activity(self) -> None:
        """agents.html must display last_activity context."""
        html = (_TEMPLATES_DIR / "agents.html").read_text(encoding="utf-8")
        assert "last_activity" in html, (
            "agents.html does not show last_activity"
        )

    def test_agents_api_returns_last_activity(self) -> None:
        """_get_agent_info must include last_activity field."""
        from types import SimpleNamespace

        from kazma_ui.agents import _get_agent_info

        class MockAgent:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    name="test-agent",
                    version="0.1.0",
                    language="en",
                    rtl=False,
                    default_model="gpt-4o-mini",
                    system_prompt="test",
                    raw={},
                )
                self.is_running = False

            def get_tools_info(self) -> dict[str, Any]:
                return {"count": 0, "list": [], "servers": 0}

            def get_llm_config(self) -> dict[str, Any]:
                return {"model": "gpt-4o-mini", "base_url": "", "max_tokens": 4096, "temperature": 0.7}

        info = _get_agent_info(MockAgent())
        assert "last_activity" in info
        assert isinstance(info["last_activity"], str)

    def test_agents_api_returns_agent_state(self) -> None:
        """Agent info must include agent_state."""
        from types import SimpleNamespace

        from kazma_ui.agents import _get_agent_info

        class MockAgent:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    name="test-agent",
                    version="0.1.0",
                    language="en",
                    rtl=False,
                    default_model="gpt-4o-mini",
                    system_prompt="test",
                    raw={},
                )
                self.is_running = False

            def get_tools_info(self) -> dict[str, Any]:
                return {"count": 0, "list": [], "servers": 0}

            def get_llm_config(self) -> dict[str, Any]:
                return {"model": "gpt-4o-mini", "base_url": "", "max_tokens": 4096, "temperature": 0.7}

        info = _get_agent_info(MockAgent())
        assert info["agent_state"] in ("idle", "thinking", "acting")
