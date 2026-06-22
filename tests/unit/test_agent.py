"""Tests for kazma_core.agent module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from kazma_core.agent import AgentConfig, KazmaAgent, load_config
from kazma_core.llm_provider import LLMResponse


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self) -> None:
        config = AgentConfig()
        assert config.name == "kazma"
        assert config.version == "0.1.0"
        assert config.language == "ar"
        assert config.rtl is True
        assert config.vector_dim == 1536

    def test_custom_values(self) -> None:
        config = AgentConfig(name="custom", version="2.0", language="en")
        assert config.name == "custom"
        assert config.version == "2.0"
        assert config.language == "en"


class TestLoadConfig:
    """Tests for YAML config loading."""

    def test_missing_config_returns_defaults(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.name == "kazma"

    def test_loads_from_yaml(self, tmp_path, monkeypatch) -> None:
        config_file = tmp_path / "kazma.yaml"
        config_file.write_text(
            'agent:\n  name: test-agent\n  version: "1.0"\n'
        )
        monkeypatch.chdir(tmp_path)
        config = load_config(config_file)
        assert config.name == "test-agent"
        assert config.version == "1.0"


class TestKazmaAgent:
    """Tests for KazmaAgent class."""

    @pytest.mark.asyncio
    async def test_run_returns_response(self, agent: KazmaAgent) -> None:
        """Agent should return a response when LLM is mocked."""
        mock_response = LLMResponse(
            content="أهلاً وسهلاً! أنا كاظمه، كيف أقدر أساعدك؟",
            tool_calls=[],
            finish_reason="stop",
            model="test-model",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            cost_usd=0.001,
        )
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.run("شلونك")
            assert "كاظمه" in result or "أساعدك" in result

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self, agent: KazmaAgent) -> None:
        """Agent should execute tool calls and return final response."""
        from kazma_core.llm_provider import ToolCall

        # First LLM call returns a tool call
        tool_call_response = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="call_1", name="test_tool", arguments={"q": "hello"})],
            finish_reason="tool_calls",
            model="test-model",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        # Second LLM call returns final text
        final_response = LLMResponse(
            content="Tool result processed successfully.",
            tool_calls=[],
            finish_reason="stop",
            model="test-model",
            usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        )

        mock_tool_result = {"content": "tool output data", "is_error": False}

        with (
            patch.object(agent.llm, "chat", new_callable=AsyncMock, side_effect=[tool_call_response, final_response]),
            patch.object(agent.tools, "execute", new_callable=AsyncMock, return_value=mock_tool_result),
            patch.object(agent.tools, "get_tool_definitions", return_value=[{"type": "function", "function": {"name": "test_tool"}}]),
        ):
            result = await agent.run("search for something")
            assert "processed" in result.lower() or "tool" in result.lower()

    @pytest.mark.asyncio
    async def test_shutdown(self, agent: KazmaAgent) -> None:
        agent._running = True
        await agent.shutdown()
        assert agent._running is False
