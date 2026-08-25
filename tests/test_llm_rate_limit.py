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
                with patch("kazma_core.llm_provider.asyncio.sleep", new_callable=AsyncMock):
                    response = await provider.chat([{"role": "user", "content": "test"}])

        assert mock_http.post.call_count == 2
        assert response is not None

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

            with patch("kazma_core.llm_provider.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMError, match="rate-limited") as ei:
                    await provider.chat([{"role": "user", "content": "test"}])
            assert ei.value.transient is True
            assert ei.value.kind == "rate_limit_exhausted"

    def test_retry_after_floor(self):
        from kazma_core.llm_provider import retry_after_seconds

        assert retry_after_seconds({"retry-after": "0.01"}) == 1.0
        assert retry_after_seconds({"retry-after": "12"}) == 12.0
        assert retry_after_seconds({}) == 30.0

    @pytest.mark.asyncio
    async def test_anthropic_429_then_200(self):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from kazma_core.anthropic_llm import AnthropicProvider
        from kazma_core.llm_provider import LLMConfig, LLMError, LLMResponse

        provider = AnthropicProvider(LLMConfig(api_key="test-key", model="claude-sonnet-4"))
        mock_429 = MagicMock(spec=httpx.Response)
        mock_429.status_code = 429
        mock_429.headers = {"retry-after": "0.01"}
        mock_429.text = "rate"
        mock_429.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=mock_429)
        )

        mock_ok = MagicMock(spec=httpx.Response)
        mock_ok.status_code = 200
        mock_ok.headers = {}
        mock_ok.raise_for_status = MagicMock()
        mock_ok.json = MagicMock(
            return_value={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

        with patch.object(provider, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[mock_429, mock_ok])
            mock_client.return_value = mock_http
            with patch.object(
                provider,
                "_parse_response",
                return_value=LLMResponse(content="ok"),
            ):
                with patch("kazma_core.anthropic_llm.asyncio.sleep", new_callable=AsyncMock):
                    # First post raises via raise_for_status on the 429 path:
                    mock_http.post = AsyncMock(
                        side_effect=[
                            mock_429,
                            mock_ok,
                        ]
                    )
                    mock_429.raise_for_status.side_effect = httpx.HTTPStatusError(
                        "429", request=MagicMock(), response=mock_429
                    )
                    out = await provider.chat([{"role": "user", "content": "hi"}])
        assert out.content == "ok"
        assert mock_http.post.call_count == 2

    @pytest.mark.asyncio
    async def test_anthropic_429_exhausted_preserves_transient(self):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from kazma_core.anthropic_llm import AnthropicProvider
        from kazma_core.llm_provider import LLMConfig, LLMError

        provider = AnthropicProvider(LLMConfig(api_key="test-key", model="claude-sonnet-4"))
        mock_429 = MagicMock(spec=httpx.Response)
        mock_429.status_code = 429
        mock_429.headers = {"retry-after": "0.01"}
        mock_429.text = "rate"
        mock_429.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=mock_429)
        )
        with patch.object(provider, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_429)
            mock_client.return_value = mock_http
            with patch("kazma_core.anthropic_llm.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMError, match="rate-limited") as ei:
                    await provider.chat([{"role": "user", "content": "hi"}])
        assert ei.value.transient is True
        assert ei.value.kind == "rate_limit_exhausted"