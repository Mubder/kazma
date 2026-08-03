"""Same-session continuity: session history can outrank thin checkpoints."""

from __future__ import annotations

import pytest

from kazma_core.agent.turn_input import (
    build_turn_messages,
    contentful_turn_count,
    is_short_continuation,
    normalize_history_messages,
)


def test_is_short_continuation():
    assert is_short_continuation("Proceed") is True
    assert is_short_continuation("try now") is True
    assert is_short_continuation("continue") is True
    assert is_short_continuation("clean up memory garbage please") is False


def test_contentful_ignores_empty_pending():
    msgs = [
        {"role": "user", "content": "clean memory junk entities"},
        {"role": "assistant", "content": "", "pending": True},
        {"role": "user", "content": "Proceed"},
    ]
    assert contentful_turn_count(normalize_history_messages(msgs)) == 2


@pytest.mark.asyncio
async def test_session_richer_than_checkpoint_wins():
    class _Graph:
        checkpointer = object()

        async def aget_state(self, config):
            class Snap:
                values = {
                    "messages": [
                        {"role": "system", "content": "sys"},
                        {"role": "user", "content": "Proceed"},
                    ]
                }

            return Snap()

    session = [
        {"role": "user", "content": "But there is so garbage in the memory: ShipX true false"},
        {
            "role": "assistant",
            "content": "I will use memory_list_entities to clean junk nodes.",
        },
        {"role": "user", "content": "Try now"},
        {"role": "assistant", "content": "⚠️ empty"},
        {"role": "user", "content": "Proceed"},
    ]
    out = await build_turn_messages(
        _Graph(),
        {"configurable": {"thread_id": "t1"}},
        user_text="Proceed",
        fallback_history=session,
    )
    texts = [m.get("content") for m in out if m.get("role") == "user"]
    assert any("garbage" in str(t) for t in texts)
    assert texts[-1] == "Proceed"


@pytest.mark.asyncio
async def test_empty_checkpoint_uses_session():
    class _Graph:
        checkpointer = object()

        async def aget_state(self, config):
            class Snap:
                values = {"messages": []}

            return Snap()

    session = [
        {"role": "user", "content": "fix memory after restart"},
        {"role": "assistant", "content": "Working on it."},
    ]
    out = await build_turn_messages(
        _Graph(),
        {"configurable": {"thread_id": "t1"}},
        user_text="continue",
        fallback_history=session,
    )
    assert any("Fix memory" in str(m.get("content")) for m in out)
    assert out[-1]["content"] == "continue"
