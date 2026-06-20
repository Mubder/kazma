"""Tests for kazma_core.llm_provider module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider, LLMResponse, LLMError, ToolCall


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""

    def test_default_values(self) -> None:
        config = LLMConfig()
        assert config.model == "gpt-4o-mini"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.max_tokens == 4096
        assert config.temperature == 0.7

    def test_from_dict(self) -> None:
        config = LLMConfig.from_dict({
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key": "test-key",
            "max_tokens": 2048,
        })
        assert config.base_url == "http://localhost:1234/v1"
        assert config.model == "local-model"
        assert config.api_key == "test-key"
        assert config.max_tokens == 2048

    def test_from_dict_defaults(self) -> None:
        config = LLMConfig.from_dict({})
        assert config.model == "gpt-4o-mini"


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""

    def test_defaults(self) -> None:
        resp = LLMResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.finish_reason == ""

    def test_with_content(self) -> None:
        resp = LLMResponse(content="Hello!", model="gpt-4o", usage={"total_tokens": 100})
        assert resp.content == "Hello!"
        assert resp.model == "gpt-4o"


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_creation(self) -> None:
        tc = ToolCall(id="call_123", name="web_search", arguments={"query": "test"})
        assert tc.id == "call_123"
        assert tc.name == "web_search"
        assert tc.arguments == {"query": "test"}


class TestLLMProvider:
    """Tests for LLMProvider."""

    def test_resolve_api_key_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        provider = LLMProvider(LLMConfig(api_key=""))
        assert provider.config.api_key == "sk-test-key"

    def test_resolve_api_key_fallback(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KAZMA_API_KEY", raising=False)
        provider = LLMProvider(LLMConfig(api_key=""))
        assert provider.config.api_key == "not-needed"

    def test_parse_response_text(self) -> None:
        provider = LLMProvider()
        data = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        resp = provider._parse_response(data, duration_ms=100.0)
        assert resp.content == "Hello!"
        assert resp.finish_reason == "stop"
        assert resp.tool_calls == []
        assert resp.usage["total_tokens"] == 15

    def test_parse_response_tool_calls(self) -> None:
        provider = LLMProvider()
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps({"query": "test"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        resp = provider._parse_response(data, duration_ms=50.0)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "web_search"
        assert resp.tool_calls[0].arguments == {"query": "test"}
        assert resp.finish_reason == "tool_calls"

    def test_parse_response_cost_calculation(self) -> None:
        provider = LLMProvider(LLMConfig(input_cost_per_1m=1.0, output_cost_per_1m=2.0))
        data = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "model": "test",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        }
        resp = provider._parse_response(data, 100.0)
        # 1000 * 1.0/1M + 500 * 2.0/1M = 0.001 + 0.001 = 0.002
        assert abs(resp.cost_usd - 0.002) < 0.0001

    def test_parse_response_malformed_tool_args(self) -> None:
        provider = LLMProvider()
        data = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "bad_tool",
                            "arguments": "not-json",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "model": "test",
            "usage": {},
        }
        resp = provider._parse_response(data, 50.0)
        assert resp.tool_calls[0].arguments == {"raw": "not-json"}

    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="test"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "model": "test",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        provider._http = mock_client
        result = await provider.chat([{"role": "user", "content": "hi"}])
        assert result.content == "Hello!"

    @pytest.mark.asyncio
    async def test_chat_connection_error(self) -> None:
        import httpx
        provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="test"))

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.is_closed = False

        provider._http = mock_client
        with pytest.raises(LLMError, match="Cannot connect"):
            await provider.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = LLMProvider()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        provider._http = mock_client
        await provider.close()
        mock_client.aclose.assert_awaited()
