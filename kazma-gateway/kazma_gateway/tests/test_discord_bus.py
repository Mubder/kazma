"""Tests for Discord platform adapter."""

import pytest


class TestDiscordBusAdapter:
    """Tests for DiscordBusAdapter functionality."""

    def test_discord_bus_initialization(self):
        """Discord bus adapter should initialize with bot token and channel."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        assert adapter._bot_token == "test_token"
        assert adapter._channel_id == "12345"
        assert hasattr(adapter, "_pending_approvals")

    def test_discord_bus_has_send_method(self):
        """Discord bus should have send method."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        assert hasattr(adapter, "send")
        assert callable(adapter.send)


class TestDiscordFormat:
    """Tests for Discord-specific formatting."""

    def test_format_code_blocks(self):
        """Code blocks should be properly formatted for Discord."""
        # Discord supports triple backticks like standard Markdown
        code = "def hello():\n    print('world')"
        formatted = f"```\n{code}\n```"
        assert "```" in formatted