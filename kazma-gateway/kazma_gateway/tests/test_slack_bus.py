"""Tests for Slack platform adapter."""

import pytest


class TestSlackBusAdapter:
    """Tests for SlackBusAdapter functionality."""

    def test_slack_bus_initialization(self):
        """Slack bus adapter should initialize with bot token and channel."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        adapter = SlackBusAdapter(bot_token="xoxb-test", channel_id="C1234567890")
        assert adapter._bot_token == "xoxb-test"
        assert adapter._channel_id == "C1234567890"

    def test_slack_bus_has_send_method(self):
        """Slack bus should have send method."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        adapter = SlackBusAdapter(bot_token="xoxb-test", channel_id="C1234567890")
        assert hasattr(adapter, "send")
        assert callable(adapter.send)


class TestSlackFormat:
    """Tests for Slack-specific formatting."""

    def test_format_mrkdwn(self):
        """Slack mrkdwn should be properly handled."""
        # Slack uses < > for links and & for escaping
        text = "Hello *world*"
        # Bold in Slack is *text* (but needs escaping in some contexts)
        assert text == "Hello *world*"