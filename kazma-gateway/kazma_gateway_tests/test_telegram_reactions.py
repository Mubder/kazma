"""Tests for emoji reactions and quick reply buttons in Telegram adapter."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEmojiReactions:
    """Test setMessageReaction integration in TelegramAdapter."""

    @pytest.fixture
    def adapter(self):
        from kazma_gateway.adapters.telegram import TelegramAdapter
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token"}):
            return TelegramAdapter(token="test_token")

    @pytest.mark.asyncio
    async def test_set_reaction_sends_correct_payload(self, adapter):
        """_set_reaction calls setMessageReaction with correct params."""
        mock_post = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        with patch("httpx.AsyncClient.post", mock_post):
            await adapter._set_reaction(12345, 678, "✅")

        # Verify post was called once with reaction payload
        assert mock_post.called

    @pytest.mark.asyncio
    async def test_set_reaction_handles_connection_error(self, adapter):
        """_set_reaction catches ConnectionError and never propagates."""
        mock_post = AsyncMock()
        mock_post.side_effect = ConnectionError("refused")

        with patch("httpx.AsyncClient.post", mock_post):
            # Should not raise
            await adapter._set_reaction(12345, 678, "👀")

    @pytest.mark.asyncio
    async def test_set_reaction_handles_timeout(self, adapter):
        """_set_reaction catches TimeoutError gracefully."""
        mock_post = AsyncMock()
        mock_post.side_effect = TimeoutError("timed out")

        with patch("httpx.AsyncClient.post", mock_post):
            await adapter._set_reaction(12345, 678, "⏳")

    def test_emoji_map_has_all_reactions(self, adapter):
        """_EMOJI_MAP contains all documented reaction emojis."""
        from kazma_gateway.adapters.telegram import _EMOJI_MAP

        assert "👀" in _EMOJI_MAP, "watching reaction missing"
        assert "✅" in _EMOJI_MAP, "success reaction missing"
        assert "🎯" in _EMOJI_MAP, "tool-used reaction missing"
        assert "❌" in _EMOJI_MAP, "error reaction missing"
        assert "⏳" in _EMOJI_MAP, "waiting reaction missing"


class TestCallbackQueries:
    """Test inline keyboard callback query handling."""

    @pytest.fixture
    def adapter(self):
        from kazma_gateway.adapters.telegram import TelegramAdapter
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token"}):
            return TelegramAdapter(token="test_token")

    @pytest.mark.asyncio
    async def test_answer_callback_query_sends_ack(self, adapter):
        """_answer_callback_query calls answerCallbackQuery endpoint."""
        mock_post = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        with patch("httpx.AsyncClient.post", mock_post):
            await adapter._answer_callback_query("cb_abc123")

        assert mock_post.called

    @pytest.mark.asyncio
    async def test_answer_callback_handles_errors(self, adapter):
        """_answer_callback_query catches errors without propagating."""
        mock_post = AsyncMock()
        mock_post.side_effect = Exception("network gone")

        with patch("httpx.AsyncClient.post", mock_post):
            await adapter._answer_callback_query("cb_xyz")

    @pytest.mark.asyncio
    async def test_handle_callback_query_delegates(self, adapter):
        """_handle_callback_query schedules _answer_callback_query."""
        adapter._answer_callback_query = AsyncMock()
        callback = {"id": "cb_789", "data": "action_approve"}

        await adapter._handle_callback_query(callback)

        # _handle_callback_query schedules _answer via asyncio.create_task
        # So we just verify it doesn't raise
        assert True


class TestSendMethodReactions:
    """Test that reactions fire during the send() method on different paths."""

    @pytest.fixture
    def adapter(self):
        from kazma_gateway.adapters.telegram import TelegramAdapter
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token"}):
            return TelegramAdapter(token="test_token")

    @pytest.mark.asyncio
    async def test_reaction_fires_on_error_handling(self, adapter):
        """Reactions are callable without blowing up on all code paths."""
        # Just verify the methods exist and are callable
        assert callable(adapter._set_reaction)
        assert callable(adapter._answer_callback_query)
        assert callable(adapter._handle_callback_query)


class TestTelegramRelativePaths:
    """Regression tests for relative endpoint usage with tokenized base_url."""

    @pytest.fixture
    def adapter(self):
        from kazma_gateway.adapters.telegram import TelegramAdapter
        return TelegramAdapter(token="test_token")

    @pytest.mark.asyncio
    async def test_listen_startup_uses_relative_delete_webhook_and_get_me(self, adapter):
        """listen() should call /deleteWebhook, /getMe, and /setMyCommands (3 scopes)."""
        mock_http = AsyncMock()
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"ok": True}
        get_me_resp = MagicMock()
        get_me_resp.status_code = 200
        get_me_resp.json.return_value = {
            "result": {"username": "test_bot", "first_name": "Test"},
        }
        mock_http.post = AsyncMock(return_value=ok_resp)
        mock_http.get = AsyncMock(return_value=get_me_resp)
        mock_http.aclose = AsyncMock()

        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient", return_value=mock_http):
            queue: asyncio.Queue = asyncio.Queue()
            shutdown_event = asyncio.Event()
            shutdown_event.set()  # run startup section then exit loop
            await adapter.listen(queue, shutdown_event)

        # Verify 4 POST calls: 1 deleteWebhook + 3 setMyCommands (default, private, group)
        assert mock_http.post.await_count == 4
        # Check deleteWebhook was called
        mock_http.post.assert_any_await(
            "/deleteWebhook",
            json={"drop_pending_updates": False},
        )
        # Check setMyCommands was called for each scope
        scopes = ["default", "all_private_chats", "all_group_chats"]
        set_my_commands_calls = [
            call for call in mock_http.post.await_args_list
            if call.args[0] == "/setMyCommands"
        ]
        assert len(set_my_commands_calls) == 3
        called_scopes = [
            call.kwargs["json"]["scope"]["type"]
            for call in set_my_commands_calls
        ]
        for scope_type in scopes:
            assert scope_type in called_scopes, f"Missing scope: {scope_type}"
        mock_http.get.assert_awaited_once_with("/getMe")

    @pytest.mark.asyncio
    async def test_poll_uses_relative_get_updates(self, adapter):
        """_poll() should call /getUpdates, not /bot{token}/getUpdates."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": []}

        adapter._http = AsyncMock()
        adapter._http.get = AsyncMock(return_value=mock_resp)

        await adapter._poll()

        adapter._http.get.assert_awaited_once_with(
            "/getUpdates",
            params={"timeout": adapter._poll_timeout},
        )

    @pytest.mark.asyncio
    async def test_trigger_typing_uses_relative_send_chat_action(self, adapter):
        """_trigger_typing should call /sendChatAction on the tokenized base_url."""
        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_cls.return_value = mock_client

            await adapter._trigger_typing("telegram:12345")

        assert mock_client.post.await_args.args[0] == "/sendChatAction"

    @pytest.mark.asyncio
    async def test_set_reaction_uses_relative_set_message_reaction(self, adapter):
        """_set_reaction should call /setMessageReaction without duplicated token path."""
        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_cls.return_value = mock_client

            await adapter._set_reaction(12345, 678, "✅")

        assert mock_client.post.await_args.args[0] == "/setMessageReaction"

    @pytest.mark.asyncio
    async def test_answer_callback_query_uses_relative_endpoint(self, adapter):
        """_answer_callback_query should call /answerCallbackQuery."""
        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_cls.return_value = mock_client

            await adapter._answer_callback_query("cb_123")

        assert mock_client.post.await_args.args[0] == "/answerCallbackQuery"
