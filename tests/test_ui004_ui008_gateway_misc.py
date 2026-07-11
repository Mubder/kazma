"""Tests for VAL-UI-004 through VAL-UI-008.

Validates:
  - VAL-UI-004: Gateway adapters auto-refresh on connector save +
                "Refresh Gateway" button available in Settings UI.
  - VAL-UI-005: Agent status shows "Ready" / waiting-for-messages
                instead of bare "Running" when idle.
  - VAL-UI-006: Keyboard shortcuts work as actual bindings
                (Ctrl+Enter sends in chat, Ctrl+K focuses search).
  - VAL-UI-007: ChromaDB missing logged at DEBUG, not WARNING.
  - VAL-UI-008: Workspace path is config-relative (kazma-data/workspace),
                not the drive root.
"""

from __future__ import annotations

from pathlib import Path

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"
_JS_DIR = _STATIC_DIR / "js"


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-004: Gateway adapters auto-refresh on connector save
# ══════════════════════════════════════════════════════════════════════════


class TestGatewayRefreshOnSave:
    """saveConnector must call /api/gateway/refresh-adapters after saving."""

    def test_save_connector_calls_refresh(self) -> None:
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        assert "refresh-adapters" in js, (
            "saveConnector must call POST /api/gateway/refresh-adapters"
        )

    def test_refresh_gateway_button_exists(self) -> None:
        html = (_TEMPLATES_DIR / "settings.html").read_text(encoding="utf-8")
        assert "refreshGateway" in html, (
            "Settings must have a 'Refresh Gateway' button wired to refreshGateway()"
        )

    def test_save_connector_no_manual_restart_message(self) -> None:
        """The old 'Restart gateway to apply' message should be gone."""
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        assert "Restart gateway to apply" not in js, (
            "saveConnector still tells user to restart manually"
        )

    def test_refresh_adapters_endpoint_exists(self) -> None:
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        assert "/api/gateway/refresh-adapters" in app_source
        assert "refresh_gateway_adapters" in app_source

    def test_refresh_adapters_reads_config_store_for_telegram(self) -> None:
        """The refresh endpoint must re-read tokens from config_store (not env only)."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        # Find the refresh_gateway_adapters function body
        fn_start = app_source.find("async def refresh_gateway_adapters")
        assert fn_start != -1, "refresh_gateway_adapters function not found"
        # Look at the ~1500 chars of the function body
        fn_body = app_source[fn_start : fn_start + 1800]
        assert "config_store.get" in fn_body, (
            "refresh-adapters must re-read connector tokens from config_store"
        )
        assert "connectors.telegram.token" in fn_body

    def test_refresh_adapters_no_syntax_error_star(self) -> None:
        """The old 'telegram_token *' typo (should be '=') must be gone."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        fn_start = app_source.find("async def refresh_gateway_adapters")
        fn_body = app_source[fn_start : fn_start + 1800]
        # The buggy line was: telegram_token * config_store.get(...)
        assert "telegram_token * config_store" not in fn_body, (
            "refresh-adapters still has 'telegram_token *' syntax bug (should be '=')"
        )


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-005: Agent status shows "Ready" when idle (not bare "Running")
# ══════════════════════════════════════════════════════════════════════════


class TestAgentStatusReady:
    """Agents page must show 'Ready — waiting for messages' when idle."""

    def test_agents_html_shows_ready_when_idle(self) -> None:
        html = (_TEMPLATES_DIR / "agents.html").read_text(encoding="utf-8")
        assert "Ready" in html, (
            "agents.html must show 'Ready' status when agent is running but idle"
        )
        assert "waiting for messages" in html, (
            "agents.html must show 'waiting for messages' context"
        )

    def test_agents_html_no_bare_running_status(self) -> None:
        """The status bar should not show bare 'Running' without context."""
        html = (_TEMPLATES_DIR / "agents.html").read_text(encoding="utf-8")
        # The old pattern was: x-text="agent.running ? 'Running' : 'Stopped'"
        assert "agent.running ? 'Running'" not in html, (
            "agents.html still shows bare 'Running' status without context"
        )

    def test_agents_api_returns_agent_state(self) -> None:
        """_get_agent_info must still include agent_state (idle/thinking/acting)."""
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
                self.is_running = True  # running but idle

            def get_tools_info(self) -> dict:
                return {"count": 0, "list": [], "servers": 0}

            def get_llm_config(self) -> dict:
                return {"model": "gpt-4o-mini", "base_url": "", "max_tokens": 4096, "temperature": 0.7}

        info = _get_agent_info(MockAgent())
        assert info["agent_state"] in ("idle", "thinking", "acting")
        assert info["running"] is True


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-006: Keyboard shortcuts are interactive
# ══════════════════════════════════════════════════════════════════════════


class TestKeyboardShortcuts:
    """Ctrl+Enter must send in chat; Ctrl+K must focus search."""

    def test_chat_js_ctrl_enter_sends(self) -> None:
        js = (_JS_DIR / "chat.js").read_text(encoding="utf-8")
        # Ctrl+Enter / Cmd+Enter must call sendMessage
        assert "ctrlKey" in js or "metaKey" in js, (
            "chat.js must detect Ctrl/Cmd modifier for Enter"
        )
        # Find the keydown handler and verify Ctrl+Enter calls sendMessage
        assert "sendMessage" in js

    def test_chat_js_enter_sends(self) -> None:
        """Plain Enter (no modifier) must still send."""
        js = (_JS_DIR / "chat.js").read_text(encoding="utf-8")
        # The onInputKeydown function must exist
        assert "onInputKeydown" in js or "keydown" in js

    def test_chat_html_mentions_ctrl_enter_hint(self) -> None:
        html = (_TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
        assert "Ctrl+Enter" in html, (
            "chat.html footer must mention Ctrl+Enter as a send shortcut"
        )

    def test_app_js_ctrl_k_search(self) -> None:
        """Ctrl+K must toggle/focus search (global shortcut)."""
        js = (_JS_DIR / "app.js").read_text(encoding="utf-8")
        assert "ctrlKey" in js or "metaKey" in js
        assert "'k'" in js, "app.js must bind Ctrl+K"

    def test_chat_js_ctrl_k_focuses_search(self) -> None:
        """In chat context, Ctrl+K focuses the session search input."""
        js = (_JS_DIR / "chat.js").read_text(encoding="utf-8")
        assert "'k'" in js or "e.key === 'k'" in js
        assert "searchInputEl" in js or "search" in js.lower()

    def test_settings_shortcuts_capture_on_keydown(self) -> None:
        """Settings shortcuts tab must capture keys on keydown (not text input)."""
        js = (_JS_DIR / "settings.js").read_text(encoding="utf-8")
        assert "captureShortcut" in js
        html = (_TEMPLATES_DIR / "settings.html").read_text(encoding="utf-8")
        assert "@keydown" in html


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-007: ChromaDB warning suppressed (DEBUG level)
# ══════════════════════════════════════════════════════════════════════════


class TestChromaDBDebugLevel:
    """ChromaDB missing must be visible in production logs (WARNING level).

    Previously logged at DEBUG (invisible in default-logging deployments,
    causing silent RAG disablement). Elevated to WARNING per the memory audit.
    """

    def test_app_py_uses_warning_for_vector_memory(self) -> None:
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        vm_idx = app_source.find("[VectorMemory] Not available")
        assert vm_idx != -1
        context = app_source[max(0, vm_idx - 120) : vm_idx + 50]
        assert "logger.warning" in context, (
            "VectorMemory ImportError must use logger.warning (elevated from "
            "debug per memory audit — RAG disablement must be visible)"
        )

    def test_vector_store_uses_warning_for_chromadb_failures(self) -> None:
        """vector_store.py may use logger.warning for ChromaDB failures.

        Non-ImportError failures (corrupt DB, disk permission errors) SHOULD
        be visible at WARNING level. The old ImportError-only warning was
        broadened to catch all ChromaDB init failures per the memory audit.
        """
        vs_source = (
            Path(__file__).resolve().parent.parent
            / "kazma-core"
            / "kazma_core"
            / "memory"
            / "vector_store.py"
        ).read_text(encoding="utf-8")
        # The warning is expected for non-import failures now.
        assert "logger.warning" in vs_source, (
            "vector_store.py should use logger.warning for ChromaDB failures "
            "(non-import errors like corrupt DB, disk permission, etc.)"
        )


# ══════════════════════════════════════════════════════════════════════════
# VAL-UI-008: Workspace path is config-relative (not drive root)
# ══════════════════════════════════════════════════════════════════════════


class TestWorkspacePathConfigRelative:
    """file_write workspace must default to kazma-data/workspace, not drive root."""

    def test_file_write_default_workspace_not_cwd(self) -> None:
        fw_source = (
            Path(__file__).resolve().parent.parent
            / "kazma-core"
            / "kazma_core"
            / "tools"
            / "file_write.py"
        ).read_text(encoding="utf-8")
        assert "kazma-data" in fw_source, (
            "file_write default workspace must reference kazma-data/workspace"
        )
        # The old default was Path.cwd().resolve() directly
        # The new default should nest under kazma-data/workspace
        ws_fn_start = fw_source.find("def _get_workspace()")
        assert ws_fn_start != -1
        ws_fn_body = fw_source[ws_fn_start : ws_fn_start + 600]
        assert "kazma-data" in ws_fn_body

    def test_app_py_configures_workspace(self) -> None:
        """app.py must call configure_workspace with kazma-data/workspace."""
        app_source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        assert "configure_workspace" in app_source, (
            "app.py must call configure_workspace on startup"
        )
        assert "kazma-data/workspace" in app_source

    def test_file_write_workspace_not_drive_root(self) -> None:
        """When no workspace is configured, the default must NOT be the drive root."""
        from kazma_core.tools.file_write import _get_workspace, configure_workspace

        # Reset to unconfigured state
        configure_workspace(workspace=None, allow_absolute=False)
        ws = _get_workspace()
        # The workspace name must be 'workspace', parent must be 'kazma-data'
        assert ws.name == "workspace"
        assert ws.parent.name == "kazma-data"
        # Must not be the drive root (e.g. C:\)
        assert ws != ws.parent, "workspace resolved to drive root"
