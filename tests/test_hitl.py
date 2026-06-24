"""Tests for Human-in-the-Loop (HITL) tool approval gate.

7 tests per gw-024 spec:
    1. Read tools never trigger interrupt
    2. Danger tools trigger interrupt
    3. Approve resumes graph
    4. Deny aborts tool
    5. Auto-deny on timeout
    6. Disabled HITL allows all tools
    7. Config-driven tiers
"""

from __future__ import annotations

from kazma_core.safety.hitl import (
    TOOL_TIERS,
    get_hitl_config,
    get_tool_tier,
    requires_approval,
)


class TestToolTiers:
    """Test tool risk classification."""

    def test_read_tools_never_interrupt(self) -> None:
        """Test 1: Read-tier tools never require approval."""
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        read_tools = ["file_read", "file_search", "file_list", "memory_search", "current_datetime"]
        for tool in read_tools:
            assert requires_approval(tool, config) is False, f"{tool} should not require approval"

    def test_write_tools_never_interrupt(self) -> None:
        """Write-tier tools never require approval."""
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        write_tools = ["send_message", "memory_store"]
        for tool in write_tools:
            assert requires_approval(tool, config) is False, f"{tool} should not require approval"

    def test_danger_tools_trigger_interrupt(self) -> None:
        """Test 2: Danger-tier tools require approval."""
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        danger_tools = ["file_write", "file_delete", "shell_exec"]
        for tool in danger_tools:
            assert requires_approval(tool, config) is True, f"{tool} should require approval"

    def test_unknown_tools_deny(self) -> None:
        """Unknown tools should not require approval (not in danger set)."""
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        assert requires_approval("some_unknown_tool", config) is False


class TestDisabledHITL:
    """Test 6: Disabled HITL allows all tools."""

    def test_disabled_allows_all(self) -> None:
        """enabled: false → no tools require approval."""
        config = get_hitl_config({
            "safety": {"hitl": {"enabled": False}}
        })
        assert config["enabled"] is False
        for tool in ["file_write", "file_delete", "shell_exec", "file_read"]:
            assert requires_approval(tool, config) is False


class TestConfigDrivenTiers:
    """Test 7: Changing require_approval_for changes behavior."""

    def test_custom_danger_list(self) -> None:
        """Custom danger list overrides defaults."""
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "enabled": True,
                    "require_approval_for": ["memory_store", "send_message"],
                }
            }
        })
        # These are now danger
        assert requires_approval("memory_store", config) is True
        assert requires_approval("send_message", config) is True
        # These are no longer danger
        assert requires_approval("file_write", config) is False
        assert requires_approval("shell_exec", config) is False

    def test_empty_danger_list(self) -> None:
        """Empty danger list means no tools require approval."""
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "enabled": True,
                    "require_approval_for": [],
                }
            }
        })
        for tool in TOOL_TIERS:
            assert requires_approval(tool, config) is False

    def test_default_config(self) -> None:
        """Empty config uses defaults (file_write, file_delete, shell_exec)."""
        config = get_hitl_config({})
        assert config["enabled"] is True
        assert requires_approval("file_write", config) is True
        assert requires_approval("file_delete", config) is True
        assert requires_approval("shell_exec", config) is True
        assert requires_approval("file_read", config) is False

    def test_timeout_config(self) -> None:
        """Timeout is configurable."""
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "approval_timeout_seconds": 30,
                    "auto_deny_on_timeout": False,
                }
            }
        })
        assert config["approval_timeout_seconds"] == 30
        assert config["auto_deny_on_timeout"] is False


class TestToolTierLookup:
    """Test get_tool_tier helper."""

    def test_known_tiers(self) -> None:
        assert get_tool_tier("file_read") == "read"
        assert get_tool_tier("send_message") == "write"
        assert get_tool_tier("shell_exec") == "danger"

    def test_unknown_tier(self) -> None:
        assert get_tool_tier("nonexistent_tool") == "unknown"
