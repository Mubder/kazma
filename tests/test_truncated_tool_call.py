"""Regression: a response truncated at max_tokens must NOT execute severed
tool-call arguments — the model must get a truncation-aware corrective error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_truncated_tool_call_gets_chunking_guidance() -> None:
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MagicMock()
    executor.execute = AsyncMock(return_value={"content": "unused", "is_error": False})

    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "create a big html file"}],
        "tool_calls_pending": [
            {
                "id": "tc1",
                "name": "file_write",
                # Severed JSON as parsed by llm_provider: unparseable → {"raw": ...}
                "arguments": {"raw": '{"path": "deck.html", "content": "<!DOCTYPE html>\\n<…'},
            }
        ],
        "_last_finish_reason": "length",
        "iteration": 1,
        "tool_results": {},
    }

    out = await tool_worker_node(state, tool_executor=executor, tracer=MagicMock())

    executor.execute.assert_not_called()
    results = out["tool_calls_done"]
    assert results and results[0]["is_error"] is True
    content = results[0]["content"]
    assert "TRUNCATED" in content
    assert "max_tokens" in content
    assert "SMALLER" in content.upper()


@pytest.mark.asyncio
async def test_malformed_args_without_truncation_still_schema_error() -> None:
    """Empty args WITHOUT a length finish_reason go through normal validation."""
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MagicMock()
    executor.execute = AsyncMock(return_value={
        "content": "Error: Tool 'file_write' was called with missing required argument(s)",
        "is_error": True,
    })

    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        "tool_calls_pending": [{"id": "tc1", "name": "file_write", "arguments": {}}],
        "_last_finish_reason": "tool_calls",
        "iteration": 1,
        "tool_results": {},
    }

    out = await tool_worker_node(state, tool_executor=executor, tracer=MagicMock())

    executor.execute.assert_called_once()
    assert "missing required argument" in out["tool_calls_done"][0]["content"]
