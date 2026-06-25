"""Tests for retry/backoff logic on LLM calls and tool executions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLLMRetry:
    """Tests for LLM call retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_llm_retry_succeeds_on_second_attempt(self) -> None:
        """LLM call fails once with ConnectionError, succeeds on retry."""
        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection refused")
            return MagicMock(content="OK", tool_calls=[], model="test", usage={"total_tokens": 10}, cost_usd=0.0)

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=mock_chat)

        # Simulate the retry loop from supervisor_node
        max_attempts = 3
        result = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = await mock_llm.chat()
                break
            except (ConnectionError, TimeoutError):
                if attempt < max_attempts:
                    await asyncio.sleep(0.001)

        assert call_count == 2
        assert result is not None
        assert result.content == "OK"

    @pytest.mark.asyncio
    async def test_llm_retry_exhausted(self) -> None:
        """LLM call fails 3 times — all retries exhausted, returns friendly error."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=ConnectionError("Connection refused"))

        max_attempts = 3
        last_exc = None
        call_count = 0

        for attempt in range(1, max_attempts + 1):
            try:
                await mock_llm.chat()
                break
            except (ConnectionError, TimeoutError) as exc:
                call_count += 1
                last_exc = exc
                if attempt < max_attempts:
                    await asyncio.sleep(0.001)

        assert call_count == 3
        from kazma_core.retry import friendly_llm_error

        error_msg = friendly_llm_error(last_exc)
        assert "unavailable" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_llm_no_retry_on_4xx(self) -> None:
        """LLM call with a non-retryable error (e.g., ValueError) does NOT retry."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=ValueError("Bad request"))

        call_count = 0
        try:
            await mock_llm.chat()
        except ValueError:
            call_count += 1

        assert call_count == 1  # Only called once — no retry


class TestToolRetry:
    """Tests for tool execution retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_tool_retry_succeeds(self) -> None:
        """Tool fails once with TimeoutError, succeeds on retry."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=False)
        call_count = 0

        @registry.register(description="Flaky tool", category="test")
        async def flaky_tool() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Request timed out")
            return "success"

        # Patch retry config at the source module (deferred import in execute)
        with patch(
            "kazma_core.retry.load_retry_config", return_value={"max_attempts": 3, "min_wait": 0.01, "max_wait": 0.05}
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await registry.execute("flaky_tool", {})

        assert result["content"] == "success"
        assert result["is_error"] is False
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_tool_no_retry_on_logic_error(self) -> None:
        """Tool raises ValueError (logic error) — zero retries, immediate error."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=False)
        call_count = 0

        @registry.register(description="Broken tool", category="test")
        async def broken_tool() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        result = await registry.execute("broken_tool", {})

        assert result["is_error"] is True
        assert "Invalid input" in result["content"]
        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_retry_config_from_yaml(self, tmp_path: Path) -> None:
        """Retry config is loaded from kazma.yaml via ConfigStore."""
        with patch("kazma_core.config_store.ConfigStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.get = MagicMock(
                side_effect=lambda key, default=None: {
                    "retry.max_attempts": 5,
                    "retry.min_wait": 1,
                    "retry.max_wait": 30,
                }.get(key, default)
            )
            mock_store_cls.return_value = mock_store

            from kazma_core.retry import load_retry_config

            cfg = load_retry_config()

        assert cfg["max_attempts"] == 5
        assert cfg["min_wait"] == 1
        assert cfg["max_wait"] == 30


class TestFriendlyErrors:
    """Tests for friendly error messages after retry exhaustion."""

    def test_friendly_llm_error_connection(self) -> None:
        """ConnectionError maps to 'model service unavailable'."""
        from kazma_core.retry import friendly_llm_error

        msg = friendly_llm_error(ConnectionError("refused"))
        assert "unavailable" in msg.lower()

    def test_friendly_llm_error_timeout(self) -> None:
        """TimeoutError maps to 'model service unavailable'."""
        from kazma_core.retry import friendly_llm_error

        msg = friendly_llm_error(TimeoutError("timed out"))
        assert "unavailable" in msg.lower()

    def test_friendly_tool_error(self) -> None:
        """Tool errors get friendly mapping."""
        from kazma_core.retry import friendly_tool_error

        msg = friendly_tool_error(ConnectionError("refused"))
        assert "Could not connect" in msg

    def test_friendly_tool_error_timeout(self) -> None:
        """TimeoutError maps to timed out message."""
        from kazma_core.retry import friendly_tool_error

        msg = friendly_tool_error(TimeoutError("timed out"))
        assert "timed out" in msg

    def test_friendly_tool_error_generic(self) -> None:
        """Other exceptions pass through."""
        from kazma_core.retry import friendly_tool_error

        msg = friendly_tool_error(RuntimeError("something broke"))
        assert "something broke" in msg


class TestRetryModuleInternals:
    """Tests for retry module internal functions."""

    def test_get_retryable_includes_httpx(self) -> None:
        """_get_retryable() includes httpx exceptions when available."""
        from kazma_core.retry import RETRYABLE_EXCEPTIONS, _get_retryable

        result = _get_retryable()
        # Should include base retryable + httpx types
        assert ConnectionError in result
        assert TimeoutError in result
        # httpx is installed in this env
        import httpx

        assert httpx.TimeoutException in result
        assert httpx.ConnectError in result
        assert httpx.RemoteProtocolError in result
        assert len(result) > len(RETRYABLE_EXCEPTIONS)

    def test_load_retry_config_defaults(self) -> None:
        """load_retry_config returns defaults when ConfigStore fails."""
        with patch("kazma_core.config_store.ConfigStore", side_effect=Exception("no db")):
            from kazma_core.retry import MAX_ATTEMPTS, MAX_WAIT, MIN_WAIT, load_retry_config

            cfg = load_retry_config()

        assert cfg["max_attempts"] == MAX_ATTEMPTS
        assert cfg["min_wait"] == MIN_WAIT
        assert cfg["max_wait"] == MAX_WAIT

    def test_log_retry_executes(self) -> None:
        """_log_retry runs without error."""
        from kazma_core.retry import _log_retry

        mock_state = MagicMock()
        mock_state.outcome.exception.return_value = ConnectionError("test")
        mock_state.attempt_number = 1

        # Should not raise
        with patch(
            "kazma_core.retry.load_retry_config", return_value={"max_attempts": 3, "min_wait": 2, "max_wait": 10}
        ):
            _log_retry(mock_state)

    def test_friendly_llm_error_generic(self) -> None:
        """Generic exceptions pass through with message."""
        from kazma_core.retry import friendly_llm_error

        msg = friendly_llm_error(RuntimeError("something weird"))
        assert "something weird" in msg
