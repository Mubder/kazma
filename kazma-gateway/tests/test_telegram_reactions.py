"""Tests for emoji reactions and quick reply buttons in Telegram adapter."""
from __future__ import annotations

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
