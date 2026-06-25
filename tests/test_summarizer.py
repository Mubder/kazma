"""Tests for conversation summarization middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.summarizer import (
    clear_summary,
    estimate_tokens,
    format_summary,
    get_summary,
    summarize,
)


class TestEstimateTokens:
    """Tests for the token estimation heuristic."""

    def test_estimate_tokens_short(self) -> None:
        """100-char message → ~25 tokens."""
        messages = [{"role": "user", "content": "x" * 100}]
        assert estimate_tokens(messages) == 25

    def test_estimate_tokens_long(self) -> None:
        """4000-char message → ~1000 tokens."""
        messages = [{"role": "user", "content": "x" * 4000}]
        assert estimate_tokens(messages) == 1000

    def test_estimate_tokens_empty(self) -> None:
        """Empty messages list → 0 tokens."""
        assert estimate_tokens([]) == 0

    def test_estimate_tokens_with_tool_calls(self) -> None:
        """Tool calls are counted in token estimation."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query":"test"}'}}],
            }
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0


class TestSummarize:
    """Tests for the summarize function."""

    @pytest.mark.asyncio
    async def test_summarize_generates_text(self) -> None:
        """summarize() calls the LLM and returns formatted summary."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="User asked for X. Agent did Y."))

        messages = [
            {"role": "user", "content": "Do X"},
            {"role": "assistant", "content": "I did Y"},
        ]

        result = await summarize(messages, mock_llm, thread_id="test-123")

        assert "CONVERSATION SUMMARY" in result
        assert "User asked for X" in result
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_fallback_on_llm_failure(self) -> None:
        """summarize() falls back to extractive summary when LLM fails."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=ConnectionError("no LLM"))

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        result = await summarize(messages, mock_llm)

        assert "CONVERSATION SUMMARY" in result
        # Should still produce output via fallback
        assert len(result) > 50


class TestSummaryInjection:
    """Tests for summary injection into graph state."""

    @pytest.mark.asyncio
    async def test_check_saturation_under_threshold(self) -> None:
        """Short conversation routes to supervisor (under threshold)."""
        from kazma_core.agent.graph_builder import check_saturation_node

        # 100 chars = 25 tokens, well under 4000 threshold
        state = {"messages": [{"role": "user", "content": "x" * 100}]}
        result = await check_saturation_node(state)

        assert result["next_node"] == "supervisor"

    @pytest.mark.asyncio
    async def test_check_saturation_over_threshold(self) -> None:
        """Long conversation routes to summarize (over threshold)."""
        from kazma_core.agent.graph_builder import check_saturation_node

        # 20000 chars = 5000 tokens, over 4000 threshold
        state = {"messages": [{"role": "user", "content": "x" * 20000}]}
        result = await check_saturation_node(state)

        assert result["next_node"] == "summarize"

    @pytest.mark.asyncio
    async def test_summary_injected_as_system_message(self) -> None:
        """Summary is injected as a SystemMessage at position 0."""
        from kazma_core.agent.graph_builder import summarize_node

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="Summary of conversation."))

        state = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            "thread_id": "test-inject",
        }

        result = await summarize_node(state, llm=mock_llm)
        messages = result["messages"]

        assert len(messages) >= 3  # summary + original 2
        assert messages[0]["role"] == "system"
        assert "CONVERSATION SUMMARY" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_summary_persisted_to_memory(self) -> None:
        """Summary is retrievable after summarize()."""
        clear_summary("test-persist")

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="Persisted summary."))

        messages = [
            {"role": "user", "content": "Remember this"},
            {"role": "assistant", "content": "I will"},
        ]

        await summarize(messages, mock_llm, thread_id="test-persist")

        retrieved = get_summary("test-persist")
        assert retrieved is not None
        assert "Persisted summary" in retrieved

        # Cleanup
        clear_summary("test-persist")


class TestFormatSummary:
    """Tests for summary formatting."""

    def test_format_summary_template(self) -> None:
        """format_summary wraps text in the injection template."""
        result = format_summary("Test summary text.")
        assert "CONVERSATION SUMMARY" in result
        assert "Test summary text." in result
        assert "End summary" in result
