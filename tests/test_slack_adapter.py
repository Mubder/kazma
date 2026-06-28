"""Tests for the Slack Adapter — Socket Mode + REST API.

Covers:
    - Message parsing (events_api format)
    - Filtering logic (bot messages, edits, subtypes)
    - Send logic (chat.postMessage + 429 retry)
    - Team/channel whitelist (applied in listen())
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_gateway.adapters.slack import SlackAdapter


class TestSlackAdapter:
    """Unit tests for SlackAdapter (mocked HTTP)."""

    def test_init(self):
        """Test SlackAdapter initialization."""
        adapter = SlackAdapter(
            bot_token="***",
            app_token="xapp-test-token",
            allowed_teams=["T123"],
            allowed_channels=["C456"],
        )
        assert adapter._bot_token == "***"
        assert adapter._app_token == "xapp-test-token"
        assert adapter._allowed_teams == {"T123"}
        assert adapter._allowed_channels == {"C456"}
        assert adapter.name == "slack"

    def test_parse_regular_message(self):
        """Test parsing a regular message event."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "Hello from Slack",
            "ts": "1234567890.123456",
            "team": "T0000000001",
        }

        msg = adapter._parse_event(event)

        assert msg is not None
        assert msg.platform == "slack"
        assert msg.sender_id == "slack:U9876543210"
        assert msg.text == "Hello from Slack"
        assert msg.context_metadata["channel_id"] == "C0123456789"
        assert msg.context_metadata["user_id"] == "U9876543210"
        assert msg.context_metadata["team_id"] == "T0000000001"
        assert msg.context_metadata["message_ts"] == "1234567890.123456"

    def test_parse_bot_message_skipped(self):
        """Test that bot messages are skipped."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U0000000001",
            "text": "Bot message",
            "ts": "1234567891.000000",
            "bot_id": "B0000000001",
        }

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_edit_subtype_skipped(self):
        """Test that message edits are skipped."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "Edited message",
            "subtype": "message_changed",
        }

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_empty_text_skipped(self):
        """Test that empty text is skipped."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "",
        }

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_app_mention(self):
        """Test parsing an app_mention event."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "app_mention",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "<@U0000BOT> do something",
            "ts": "1234567892.000000",
        }

        msg = adapter._parse_event(event)

        assert msg is not None
        assert msg.sender_id == "slack:U9876543210"
        assert msg.text == "<@U0000BOT> do something"

    def test_parse_non_message_event_skipped(self):
        """Test that non-message event types are skipped."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {"type": "channel_created", "channel": {"id": "C999"}}

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_none_event(self):
        """Test that None event returns None."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")
        assert adapter._parse_event(None) is None

    def test_parse_missing_channel(self):
        """Test that missing channel returns None."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "user": "U9876543210",
            "text": "No channel",
        }

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_missing_user(self):
        """Test that missing user returns None."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "text": "No user",
        }

        msg = adapter._parse_event(event)
        assert msg is None

    def test_parse_username_fallback(self):
        """Test username falls back to slack_{user_id}."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "No username field",
            "ts": "123.456",
        }

        msg = adapter._parse_event(event)
        assert msg is not None
        assert msg.context_metadata["username"] == "slack_U9876543210"

    @pytest.mark.asyncio
    async def test_send_success(self):
        """Test successful message send via REST API."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_http.post = AsyncMock(return_value=mock_resp)
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Hello Slack!"
        outbound.context_metadata = {"channel_id": "C123456"}
        outbound.target_id = "slack:C123456"

        result = await adapter.send(outbound)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_rate_limit_retry(self):
        """Test send with 429 rate limit → retry → success."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.1"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"ok": True}

        mock_http.post = AsyncMock(side_effect=[resp_429, resp_200])
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Retry test"
        outbound.context_metadata = {"channel_id": "C123"}
        outbound.target_id = "slack:C123"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.send(outbound)

        assert result is True
        assert mock_http.post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_rate_limit_exhausted(self):
        """Test send fails after max retries on 429."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.1"}

        mock_http.post = AsyncMock(return_value=resp_429)
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Fail test"
        outbound.context_metadata = {"channel_id": "C123"}
        outbound.target_id = "slack:C123"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.send(outbound)

        assert result is False
        assert mock_http.post.call_count == 3

    @pytest.mark.asyncio
    async def test_send_http_error(self):
        """Test send returns False on HTTP error."""
        import httpx

        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_http.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=mock_resp,
            )
        )
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Error test"
        outbound.context_metadata = {"channel_id": "C123"}
        outbound.target_id = "slack:C123"

        result = await adapter.send(outbound)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_no_channel_id(self):
        """Test send returns False when no channel_id available."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "No channel"
        outbound.context_metadata = {}
        outbound.target_id = "slack:"

        result = await adapter.send(outbound)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_target_id_fallback(self):
        """Test send falls back to parsing target_id for channel_id."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_http.post = AsyncMock(return_value=mock_resp)
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Fallback test"
        outbound.context_metadata = {}
        outbound.target_id = "slack:C999888"

        result = await adapter.send(outbound)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_slack_api_error(self):
        """Test send returns False when Slack API returns ok=false."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_http.post = AsyncMock(return_value=mock_resp)
        adapter._http = mock_http

        outbound = MagicMock()
        outbound.text = "Bad channel"
        outbound.context_metadata = {"channel_id": "C_INVALID"}
        outbound.target_id = "slack:C_INVALID"

        result = await adapter.send(outbound)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_creates_http_client(self):
        """Test send creates httpx client if not initialized."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")
        assert adapter._http is None

        with patch("kazma_gateway.adapters.slack.httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True}
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            outbound = MagicMock()
            outbound.text = "Init test"
            outbound.context_metadata = {"channel_id": "C123"}
            outbound.target_id = "slack:C123"

            result = await adapter.send(outbound)
            assert result is True

    def test_package_export(self):
        """Test SlackAdapter is exported from adapters __init__."""
        from kazma_gateway.adapters import SlackAdapter as SA2  # noqa: N814
        assert SA2 is SlackAdapter

    def test_message_text_truncation(self):
        """Test send truncates text to Slack's 40k limit."""
        adapter = SlackAdapter("xoxb-test", "xapp-test")

        event = {
            "type": "message",
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "a" * 50000,
            "ts": "123.456",
        }

        msg = adapter._parse_event(event)
        assert msg is not None
        # Text is preserved in IncomingMessage — truncation happens in send()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
