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


class TestDeterministicTrimAndWorkingMemory:
    """Mid-turn LLM summarize_node is gone — trim + anchors replace it."""

    def test_trim_preserves_user_goal_and_last_tools(self) -> None:
        from kazma_core.agent.turn_input import (
            WORKING_MEMORY_MARKER,
            format_working_memory_anchor,
            trim_messages_deterministic,
        )

        goal = "JUST AUDIT this document — do not change code"
        wm = format_working_memory_anchor(
            active_goal=goal,
            active_attachments=[{"filename": "AR_v6.docx", "kind": "file"}],
            hard_constraints=["audit_only", "no_code_change"],
        )
        messages: list[dict] = [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "old zcode question"},
            {"role": "assistant", "content": "zcode answer"},
            {"role": "user", "content": goal},
        ]
        # Inflate with many tool rounds
        for i in range(40):
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": f"c{i}", "name": "file_read", "args": {}}],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"c{i}",
                    "content": ("blob " * 500) + f" round {i}",
                }
            )

        trimmed = trim_messages_deterministic(
            messages,
            max_tokens=3000,
            keep_last_tool_rounds=4,
            active_goal=goal,
            working_memory_block=wm,
        )
        roles = [m.get("role") for m in trimmed]
        assert "user" in roles
        user_msgs = [m for m in trimmed if m.get("role") == "user"]
        assert any(goal in str(m.get("content")) for m in user_msgs)
        assert any(
            WORKING_MEMORY_MARKER in str(m.get("content") or "")
            for m in trimmed
            if m.get("role") == "system"
        )
        # Must not keep the entire 40-round history
        assert len(trimmed) < len(messages)

    def test_audit_only_filters_write_tools(self) -> None:
        from kazma_core.agent.turn_input import (
            filter_tools_for_constraints,
            parse_hard_constraints,
        )

        cons = parse_hard_constraints(
            "what is wrong with this document? DONT CHANGE CODES JUST AUDIT now"
        )
        assert "audit_only" in cons
        tools = [
            {"type": "function", "function": {"name": "read_document", "parameters": {}}},
            {"type": "function", "function": {"name": "generate_docx", "parameters": {}}},
            {"type": "function", "function": {"name": "shell_exec", "parameters": {}}},
            {"type": "function", "function": {"name": "file_list", "parameters": {}}},
            {"type": "function", "function": {"name": "send_file", "parameters": {}}},
        ]
        filtered = filter_tools_for_constraints(tools, cons)
        names = []
        for t in filtered:
            fn = t.get("function") or {}
            names.append(fn.get("name") or t.get("name"))
        assert "read_document" in names
        assert "file_list" in names
        assert "generate_docx" not in names
        assert "shell_exec" not in names
        assert "send_file" not in names

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
