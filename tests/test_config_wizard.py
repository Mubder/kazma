"""Tests for /config interactive wizard slash command."""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from kazma_gateway.slash_commands import resolve_slash_command


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_context(**overrides: dict) -> dict:
    return {
        "started": True,
        "adapters": "telegram",
        "queue_depth": 3,
        "active_threads": 2,
        "model": "gpt-4o-mini",
        "memory_count": 12,
        "total_tokens": 4520,
        "total_cost": 0.0231,
        **overrides,
    }


_MOCK_CONFIG = {
    "agent": {"name": "kazma", "version": "0.1.0", "personality": "default"},
    "models": {"default": "gpt-4o-mini", "fallback": "gpt-4o-mini"},
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "max_tokens": 4096,
        "temperature": 0.7,
        "timeout": 60.0,
    },
    "mcp": {
        "servers": [
            {"name": "filesystem", "transport": "stdio", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
        ],
    },
    "memory": {"enabled": True},
    "gateway": {"rate_limits": {"telegram": 30}},
}


# ══════════════════════════════════════════════════════════════════════
# /config show
# ══════════════════════════════════════════════════════════════════════


class TestConfigShow:
    def test_config_show_returns_table(self):
        """`/config show` returns a config table."""
        ctx = _mock_context()
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config show", ctx)
        assert result is not None
        assert "Current Configuration" in result
        assert "Model" in result
        assert "Personality" in result
        assert "Memory" in result
        assert "Tools" in result

    def test_config_show_contains_model_info(self):
        """`/config show` table contains model/provider info."""
        ctx = _mock_context(model="gpt-4o-mini")
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config show", ctx)
        assert "gpt-4o-mini" in result
        assert "enabled" in result  # memory enabled

    def test_config_defaults_to_show(self):
        """Plain `/config` without sub-command defaults to show."""
        ctx = _mock_context()
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config", ctx)
        assert result is not None
        assert "Current Configuration" in result


# ══════════════════════════════════════════════════════════════════════
# /config model
# ══════════════════════════════════════════════════════════════════════


class TestConfigModel:
    def test_config_model_switch_confirms(self):
        """`/config model <name>` switches and confirms."""
        ctx = _mock_context()
        with patch("kazma_gateway.slash_commands._load_config", return_value=copy.deepcopy(_MOCK_CONFIG)), \
             patch("kazma_gateway.slash_commands._save_config"):
            result = resolve_slash_command("/config model claude-sonnet-4", ctx)
        assert result is not None
        assert "claude-sonnet-4" in result
        assert "Switched" in result or "✅" in result

    def test_config_model_invalid_gives_error(self):
        """`/config model` with empty name gives usage error."""
        ctx = _mock_context()
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config model", ctx)
        assert result is not None
        assert "Current model" in result or "Usage" in result

    def test_config_model_shows_current(self):
        """`/config model` without name shows current model."""
        ctx = _mock_context(model="gpt-4o-mini")
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config model", ctx)
        assert "gpt-4o-mini" in result


# ══════════════════════════════════════════════════════════════════════
# /config personality
# ══════════════════════════════════════════════════════════════════════


class TestConfigPersonality:
    def test_config_personality_show_current(self):
        """`/config personality` shows current personality."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG), \
             patch("kazma_core.tools.personality_cmd.handle_personality_command", return_value="🎭 Current personality: **default** 🤖"):
            result = resolve_slash_command("/config personality", {})
        assert result is not None
        assert "🎭" in result
        assert "default" in result

    def test_config_personality_delegates(self):
        """/config personality delegates to /personality handler."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG), \
             patch("kazma_core.tools.personality_cmd.handle_personality_command", return_value="✅ Switched to **concise**"):
            result = resolve_slash_command("/config personality concise", {})
        assert "Switched" in result or "concise" in result


# ══════════════════════════════════════════════════════════════════════
# /config memory
# ══════════════════════════════════════════════════════════════════════


class TestConfigMemory:
    def test_config_memory_toggle(self):
        """`/config memory on|off` toggles and confirms."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=copy.deepcopy(_MOCK_CONFIG)), \
             patch("kazma_gateway.slash_commands._save_config"):
            result = resolve_slash_command("/config memory off", {})
        assert "OFF" in result or "off" in result.upper()

    def test_config_memory_shows_current(self):
        """`/config memory` without arg shows current state."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=copy.deepcopy(_MOCK_CONFIG)):
            result = resolve_slash_command("/config memory", {})
        assert "enabled" in result.lower()


# ══════════════════════════════════════════════════════════════════════
# /config tools
# ══════════════════════════════════════════════════════════════════════


class TestConfigTools:
    def test_config_tools_list_shows_names(self):
        """`/config tools list` shows tool names."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config tools list", {})
        assert result is not None
        assert "filesystem" in result

    def test_config_tools_toggle_enables_disables(self):
        """`/config tools toggle <name>` enables/disables a tool."""
        test_config = {
            **{k: v for k, v in _MOCK_CONFIG.items()},
            "mcp": {
                "servers": [{"name": "filesystem", "transport": "stdio", "command": ["npx"]}],
                "disabled_servers": ["filesystem"],
            },
        }
        with patch("kazma_gateway.slash_commands._load_config", return_value=test_config), \
             patch("kazma_gateway.slash_commands._save_config"):
            result = resolve_slash_command("/config tools toggle filesystem", {})
        assert "filesystem" in result
        assert "enabled" in result.lower()

    def test_config_tools_toggle_unknown(self):
        """`/config tools toggle <unknown>` gives error."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config tools toggle nonexistent", {})
        assert "Unknown" in result or "❌" in result


# ══════════════════════════════════════════════════════════════════════
# /config export
# ══════════════════════════════════════════════════════════════════════


class TestConfigExport:
    def test_config_export_produces_valid_json(self):
        """`/config export` produces valid JSON."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config export", {})
        assert result is not None
        # Extract the JSON from inside the markdown code fence
        assert "```json" in result
        # Strip the markdown wrapping
        inner = result.split("```json\n")[1].split("\n```")[0]
        parsed = json.loads(inner)
        assert isinstance(parsed, dict)
        assert "agent" in parsed or "models" in parsed

    def test_config_export_redacts_sensitive(self):
        """`/config export` redacts api_key."""
        cfg_with_key = {
            **_MOCK_CONFIG,
            "llm": {**_MOCK_CONFIG["llm"], "api_key": "sk-secret-123"},
        }
        with patch("kazma_gateway.slash_commands._load_config", return_value=cfg_with_key):
            result = resolve_slash_command("/config export", {})
        assert "sk-secret-123" not in result
        assert "REDACTED" in result


# ══════════════════════════════════════════════════════════════════════
# /config usage / unknown sub-command
# ══════════════════════════════════════════════════════════════════════


class TestConfigEdgeCases:
    def test_config_unknown_subcommand_shows_usage(self):
        """Unknown /config sub-command shows usage help."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=_MOCK_CONFIG):
            result = resolve_slash_command("/config bogus", {})
        assert result is not None
        assert "sub-commands" in result.lower() or "show" in result.lower()

    def test_config_help_listing(self):
        """`/help` includes /config commands."""
        result = resolve_slash_command("/help", {})
        assert "/config" in result
