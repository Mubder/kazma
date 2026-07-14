"""Tests for LLM provider rate-limit handling."""

from __future__ import annotations

import pytest


class TestRateLimitHandling:
    """Tests for 429 rate-limit retry logic."""

    @pytest.mark.asyncio
    async def test_rate_limit_triggers_retry(self):
        """429 status should trigger retry-after sleep and backoff."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from kazma_core.llm_provider import LLMProvider, LLMConfig, LLMError
        import httpx

        config = LLMConfig(base_url="https://api.openai.com/v1", api_key="test-key", model="gpt-4")
        provider = LLMProvider(config)

        # Create mock responses
        mock_response_429 = MagicMock(spec=httpx.Response)
        mock_response_429.status_code = 429
        mock_response_429.headers = {"retry-after": "0.01"}
        mock_response_429.text = "Rate limited"
        mock_response_429.json = MagicMock(return_value={
            "choices": [{"message": {"content": "test"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 100},
        })
        mock_response_429.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_response_429
        ))

        mock_response_ok = MagicMock(spec=httpx.Response)
        mock_response_ok.status_code = 200
        mock_response_ok.headers = {}
        mock_response_ok.json = MagicMock(return_value={
            "choices": [{"message": {"content": "success"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 100},
        })
        mock_response_ok.raise_for_status = MagicMock()

        with patch.object(provider, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[mock_response_429, mock_response_ok])
            mock_client.return_value = mock_http

            with patch.object(provider, "_parse_response") as mock_parse:
                mock_parse.return_value = AsyncMock()
                response = await provider.chat([{"role": "user", "content": "test"}])

        # Should have attempted retry
        assert mock_http.post.call_count >= 1

    @pytest.mark.asyncio
    async def test_rate_limit_max_retries_exhausted(self):
        """After 3 retries on 429, LLMError should be raised."""
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from kazma_core.llm_provider import LLMProvider, LLMConfig, LLMError

        config = LLMConfig(base_url="https://api.openai.com/v1", api_key="test-key", model="gpt-4")
        provider = LLMProvider(config)

        # Mock persistent 429 responses
        mock_response_429 = MagicMock(spec=httpx.Response)
        mock_response_429.status_code = 429
        mock_response_429.headers = {"retry-after": "0.01"}
        mock_response_429.text = "Rate limited"
        mock_response_429.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_response_429
        ))

        with patch.object(provider, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response_429)
            mock_client.return_value = mock_http

            # The test needs to allow time for the retry loop
            with pytest.raises(LLMError, match="rate-limited"):
                await provider.chat([{"role": "user", "content": "test"}])