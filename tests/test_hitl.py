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
    ALWAYS_HITL_TOOLS,
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
        """Write-tier tools never require approval.

        `send_message` moved to the danger tier in audit F-04 (it dispatches
        to Telegram/Discord/Slack), so it is no longer an example here.
        """
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        write_tools = ["memory_store", "update_scratchpad", "document_index"]
        for tool in write_tools:
            assert requires_approval(tool, config) is False, f"{tool} should not require approval"

    def test_danger_tools_trigger_interrupt(self) -> None:
        """Test 2: Danger-tier tools require approval."""
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        danger_tools = ["file_write", "file_delete", "shell_exec"]
        for tool in danger_tools:
            assert requires_approval(tool, config) is True, f"{tool} should require approval"

    def test_unknown_tools_require_approval(self) -> None:
        """An unclassified tool is gated, not exempt (audit F-04).

        This asserted the opposite until the audit: a tool nobody remembered
        to classify ran with no approval, which is how 125 of 153 registered
        tools ended up ungated. Approval is now the default.
        """
        config = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
        assert requires_approval("some_unknown_tool", config) is True


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

    def test_custom_danger_list_extends_the_tier_floor(self) -> None:
        """A custom list ADDS to the tier classification; it cannot un-gate.

        Behaviour change from audit F-04. The configured list used to be the
        entire policy, so narrowing it silently un-gated `file_write` and
        `shell_exec` — open-by-omission. Danger-tier tools are now gated
        regardless of the list; the list only adds tools on top.
        """
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "enabled": True,
                    "require_approval_for": ["memory_store", "send_message"],
                }
            }
        })
        # Explicitly listed -> gated (memory_store is otherwise tier 'write').
        assert requires_approval("memory_store", config) is True
        assert requires_approval("send_message", config) is True
        # Danger-tier tools stay gated even though the list omits them.
        assert requires_approval("file_write", config) is True
        assert requires_approval("shell_exec", config) is True
        # Read-tier tools stay ungated.
        assert requires_approval("file_read", config) is False

    def test_empty_danger_list_still_gates_by_tier(self) -> None:
        """An empty list cannot open the gate (audit F-04).

        Previously an empty `require_approval_for` meant *nothing* needed
        approval except ALWAYS_HITL_TOOLS. Approval is now the default:
        read/write-tier tools run freely, danger-tier tools do not, and an
        unknown tool is gated rather than exempt.
        """
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "enabled": True,
                    "require_approval_for": [],
                }
            }
        })
        for tool, tier in TOOL_TIERS.items():
            expected = tool in ALWAYS_HITL_TOOLS or tier not in ("read", "write", "safe")
            assert requires_approval(tool, config) is expected, (
                f"{tool} (tier={tier}) approval expected={expected}"
            )
        # An unclassified tool defaults to requiring approval.
        assert requires_approval("some_tool_added_next_week", config) is True

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


    def test_configstore_require_approval_for_overrides_yaml(self, monkeypatch) -> None:
        """Settings UI list must reach get_hitl_config via ConfigStore."""

        class _FakeCS:
            def get(self, key, default=None):
                data = {
                    "safety.require_approval_for": ["memory_store"],
                    "safety.hitl_enabled": True,
                }
                return data.get(key, default)

        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store",
            lambda: _FakeCS(),
        )
        config = get_hitl_config({
            "safety": {
                "hitl": {
                    "enabled": True,
                    "require_approval_for": ["file_write", "shell_exec"],
                }
            }
        })
        assert requires_approval("memory_store", config) is True
        # Danger-tier tools remain gated whatever the configured list says
        # (audit F-04) — the list adds to the floor, it does not replace it.
        assert requires_approval("file_write", config) is True
        assert requires_approval("shell_exec", config) is True


class TestToolTierLookup:
    """Test get_tool_tier helper."""

    def test_known_tiers(self) -> None:
        assert get_tool_tier("file_read") == "read"
        # send_message dispatches to Telegram/Discord/Slack — outbound side
        # effect, reclassified from "write" to "danger" by audit F-04.
        assert get_tool_tier("send_message") == "danger"
        assert get_tool_tier("shell_exec") == "danger"

    def test_unknown_tier(self) -> None:
        assert get_tool_tier("nonexistent_tool") == "unknown"
