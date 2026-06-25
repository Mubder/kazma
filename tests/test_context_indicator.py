"""Tests for context window indicator (/context command)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from kazma_core.tools.context_cmd import context_cmd


class TestContextIndicator:
    """Tests for the context_cmd tool."""

    @pytest.mark.asyncio
    async def test_context_shows_token_count(self) -> None:
        """/context returns token estimate and percentage."""
        messages = [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well, thanks!"},
        ]

        with patch("kazma_core.config_store.ConfigStore", side_effect=Exception("no config")):
            result = await context_cmd(messages)

        assert "Context Window" in result
        assert "Tokens:" in result
        assert "%" in result

    @pytest.mark.asyncio
    async def test_context_details_breakdown(self) -> None:
        """/context details shows per-role breakdown."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        with patch("kazma_core.config_store.ConfigStore", side_effect=Exception("no config")):
            result = await context_cmd(messages, detailed=True)

        assert "Role breakdown" in result
        assert "system=" in result
        assert "user=" in result
        assert "assistant=" in result

    @pytest.mark.asyncio
    async def test_context_empty_session(self) -> None:
        """Empty session shows 0 tokens."""
        with patch("kazma_core.config_store.ConfigStore", side_effect=Exception("no config")):
            result = await context_cmd([])

        assert "0" in result
        assert "Context Window" in result

    @pytest.mark.asyncio
    async def test_context_skips_llm(self) -> None:
        """/context is a pure function — no LLM call needed."""
        # This test verifies context_cmd works without any LLM mock
        messages = [{"role": "user", "content": "test"}]

        with patch("kazma_core.config_store.ConfigStore", side_effect=Exception("no config")):
            result = await context_cmd(messages)

        assert "Context Window" in result
        assert "Summarization threshold" in result
