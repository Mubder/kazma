"""Leftovers-except-G: TUI mouths, permissions gate, memory scale seams."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHAT = _ROOT / "kazma-tui" / "kazma_tui" / "chat.py"
_SETTINGS = _ROOT / "kazma-tui" / "kazma_tui" / "settings_panel.py"
_TRACES = _ROOT / "kazma-tui" / "kazma_tui" / "traces.py"
_DASH = _ROOT / "kazma-tui" / "kazma_tui" / "dashboard.py"
_REGISTRY = _ROOT / "kazma-core" / "kazma_core" / "agent" / "tool_registry.py"


def test_tui_leftover_mouths_use_live_api():
    chat = _CHAT.read_text(encoding="utf-8")
    assert "handle_personality_command" not in chat
    assert "get_current_personality" not in chat
    assert "estimate_tokens" not in chat
    assert 'GET", "/api/settings/agent"' in chat or "/api/settings/agent" in chat
    assert "/api/settings/agent/personalities" in chat
    assert "/api/settings/agent/context" in chat
    assert "/api/settings/single" in chat
    assert "/api/chat/sessions/" in chat
    assert "async def _cmd_personality" in chat
    assert "async def _cmd_config" in chat
    assert "async def _cmd_export" in chat
    assert "async def _cmd_context" in chat


def test_tui_settings_and_traces_are_honest():
    settings = _SETTINGS.read_text(encoding="utf-8")
    traces = _TRACES.read_text(encoding="utf-8")
    dash = _DASH.read_text(encoding="utf-8")
    assert "/api/settings/single" in settings
    assert "this TUI" in settings
    assert "server traces" in traces.lower()
    assert "This TUI · CPU/Mem" in dash


def test_permission_manager_called_from_execute():
    text = _REGISTRY.read_text(encoding="utf-8")
    assert "is_allowed" in text
    assert "should_enforce_permissions" in text
    assert "KAZMA_PERMISSIONS_ENFORCE" in text


def test_twin_prune_is_wired():
    directory = (
        _ROOT / "kazma-core" / "kazma_core" / "sessions" / "directory.py"
    ).read_text(encoding="utf-8")
    manager = (
        _ROOT / "kazma-ui" / "kazma_ui" / "session_manager.py"
    ).read_text(encoding="utf-8")
    assert "def prune_twin_sessions" in directory
    assert "prune_twin_sessions(apply=True)" in manager
