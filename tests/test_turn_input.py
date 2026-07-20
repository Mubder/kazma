"""Tests for checkpointer-first turn message assembly."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.agent.turn_input import build_turn_messages, load_checkpoint_messages


class _Snap:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.values = {"messages": messages}


@pytest.mark.asyncio
async def test_build_uses_checkpoint_not_fallback() -> None:
    prior = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    graph = MagicMock()
    graph.checkpointer = object()
    graph.aget_state = AsyncMock(return_value=_Snap(prior))

    fallback = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "stale text only"},
    ]
    msgs = await build_turn_messages(
        graph,
        {"configurable": {"thread_id": "t1"}},
        user_text="next question",
        fallback_history=fallback,
    )
    # Must include tool chain from checkpoint
    assert any(m.get("role") == "tool" for m in msgs)
    assert msgs[-1] == {"role": "user", "content": "next question"}
    # Must not be only the fallback text path
    assert not any(m.get("content") == "stale text only" for m in msgs)


@pytest.mark.asyncio
async def test_fallback_when_no_checkpoint() -> None:
    graph = MagicMock()
    graph.checkpointer = None

    msgs = await build_turn_messages(
        graph,
        {"configurable": {"thread_id": "t1"}},
        user_text="hello",
        system_messages=[{"role": "system", "content": "You are Kazma"}],
        fallback_history=[{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}],
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "hello"
    assert any(m.get("content") == "earlier" for m in msgs)


@pytest.mark.asyncio
async def test_load_empty_without_graph() -> None:
    assert await load_checkpoint_messages(None, {}) == []
