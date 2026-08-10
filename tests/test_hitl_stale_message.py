"""HITL stale approval messaging — no false 'nothing was executed'."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_stale_approve_after_completion_is_honest() -> None:
    from kazma_gateway.agent_handler.hitl import _stale_approval_message

    msgs = [
        {"role": "user", "content": "do thing"},
        {"role": "assistant", "content": "Done: file written successfully."},
    ]
    snap = SimpleNamespace(next=(), values={"messages": msgs})
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=snap)

    text = await _stale_approval_message(
        graph,
        {"configurable": {"thread_id": "t1"}},
        "t1",
        action="approve_task",
        approved=True,
    )
    assert text is not None
    assert "Already handled" in text or "already" in text.lower()
    assert "Nothing was executed" not in text


@pytest.mark.asyncio
async def test_stale_debounce_silences_second_tap(tmp_path, monkeypatch) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_gateway.agent_handler.hitl import _stale_approval_message
    import time

    tid = "debounce-thread-hitl"
    get_config_store().set(
        f"hitl.last_resume.{tid}",
        {"at": time.time(), "action": "approve_task", "approved": True},
        category="safety",
    )
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(next=(), values={}))
    text = await _stale_approval_message(
        graph,
        {"configurable": {"thread_id": tid}},
        tid,
        action="approve_task",
        approved=True,
    )
    assert text is None  # silenced
