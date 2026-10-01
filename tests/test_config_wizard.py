"""Tests for /config interactive wizard slash command."""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

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
        assert "MCP servers" in result

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
        """`/config model <name>` switches the model AND its provider through
        switch_active_model, as the /model menu does."""
        from kazma_core.runtime.model_switch import SwitchResult

        ctx = _mock_context()
        done = SwitchResult(ok=True, model="claude-sonnet-4", provider="anthropic")
        with patch("kazma_core.runtime.model_switch.switch_active_model", return_value=done) as switch:
            result = resolve_slash_command("/config model claude-sonnet-4", ctx)
        assert switch.call_args.args == ("claude-sonnet-4",)
        assert result == "✅ Switched to **claude-sonnet-4** (provider: anthropic)"

    def test_config_model_failure_is_not_reported_as_a_switch(self):
        """It used to write llm.model alone and say "Switched" even when the
        write failed."""
        from kazma_core.runtime.model_switch import SwitchResult

        refused = SwitchResult(ok=False, error="Profile is locked by KAZMA_MODEL", error_code="env_locked")
        with patch("kazma_core.runtime.model_switch.switch_active_model", return_value=refused):
            result = resolve_slash_command("/config model claude-sonnet-4", _mock_context())
        assert result == "⚠️ Failed to switch model: Profile is locked by KAZMA_MODEL"

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
    def test_config_memory_toggle(self, tmp_path, monkeypatch):
        """`/config memory off` writes memory.enabled and nothing else. It
        used to save the whole merged configuration back, every setting."""
        from kazma_core.config_store import ConfigStore
        from kazma_gateway import slash_commands

        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text("llm:\n  model: gpt-4o-mini\n", encoding="utf-8")
        store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(yaml_path))
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store)
        try:
            before = {k for group in store.get_all().values() for k in group}
            result = resolve_slash_command("/config memory off", {})
            after = {k for group in store.get_all().values() for k in group}
            assert result == "💾 Memory **OFF**."
            assert store.get("memory.enabled") is False
            assert after - before == {"memory.enabled"}
        finally:
            store.close()

    def test_config_memory_failure_changes_nothing_and_says_so(self, monkeypatch):
        from kazma_gateway import slash_commands

        class _Refusing:
            def set(self, *_a):
                raise RuntimeError("database is locked")

        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: _Refusing())
        result = resolve_slash_command("/config memory off", {})
        assert result == "⚠️ Memory was not changed: database is locked"

    def test_config_memory_shows_current(self):
        """`/config memory` without arg shows current state."""
        with patch("kazma_gateway.slash_commands._load_config", return_value=copy.deepcopy(_MOCK_CONFIG)):
            result = resolve_slash_command("/config memory", {})
        assert "enabled" in result.lower()


# ══════════════════════════════════════════════════════════════════════
# /config tools
# ══════════════════════════════════════════════════════════════════════


class TestConfigTools:
    _SERVERS = [
        {"name": "filesystem", "transport": "stdio", "enabled": True},
        {"name": "github", "transport": "stdio", "enabled": False},
    ]

    def test_config_tools_list_shows_the_mcp_servers(self):
        """`/config tools list` lists the servers Settings -> MCP lists."""
        with patch("kazma_gateway.slash_commands._mcp_servers", return_value=self._SERVERS):
            result = resolve_slash_command("/config tools list", {})
        assert "• `filesystem`" in result
        assert "• `github` _(disabled)_" in result

    def test_config_tools_toggle_switches_the_server(self):
        """It used to write mcp.disabled_servers, which nothing reads, and
        report the server switched."""
        with (
            patch("kazma_gateway.slash_commands._mcp_servers", return_value=self._SERVERS),
            patch("kazma_core.mcp_servers_store.set_mcp_server_enabled") as toggle,
        ):
            result = resolve_slash_command("/config tools toggle GitHub", {})
        toggle.assert_called_once_with("github", True)
        assert result == "🔧 MCP server `github` **enabled**. It applies when Kazma next starts."

    def test_config_tools_toggle_unknown(self):
        """`/config tools toggle <unknown>` gives error."""
        with patch("kazma_gateway.slash_commands._mcp_servers", return_value=self._SERVERS):
            result = resolve_slash_command("/config tools toggle nonexistent", {})
        assert result.startswith("❌ Unknown MCP server: `nonexistent`")
        assert "filesystem, github" in result


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
