"""Tests for HITL auto-deny on new user message (supersede)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.agent.hitl_supersede import cancel_pending_hitl, has_pending_hitl


def _interrupt_snapshot(with_hitl: bool = True) -> Any:
    if not with_hitl:
        return SimpleNamespace(next=(), tasks=[])
    payload = {"type": "hitl_approval", "tool": "shell_exec", "args": {}}
    intr = SimpleNamespace(value=payload)
    task = SimpleNamespace(interrupts=[intr])
    return SimpleNamespace(next=("tools",), tasks=[task])


@pytest.mark.asyncio
async def test_has_pending_hitl_true() -> None:
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_interrupt_snapshot(True))
    assert await has_pending_hitl(graph, {"configurable": {"thread_id": "t"}}) is True


@pytest.mark.asyncio
async def test_has_pending_hitl_false() -> None:
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_interrupt_snapshot(False))
    assert await has_pending_hitl(graph, {"configurable": {"thread_id": "t"}}) is False


@pytest.mark.asyncio
async def test_cancel_pending_hitl_resumes_deny() -> None:
    graph = MagicMock()
    # First call: pending; after resume: clear
    graph.aget_state = AsyncMock(
        side_effect=[
            _interrupt_snapshot(True),
            _interrupt_snapshot(True),  # inside cancel loop check before ainvoke... 
            _interrupt_snapshot(False),
            _interrupt_snapshot(False),
        ]
    )
    graph.ainvoke = AsyncMock(return_value={})

    # has_pending called once, then ainvoke, then chained checks
    # Simplify: first has_pending True, ainvoke once, subsequent False
    async def _aget(_config):
        if graph.ainvoke.await_count == 0:
            return _interrupt_snapshot(True)
        return _interrupt_snapshot(False)

    graph.aget_state = AsyncMock(side_effect=_aget)

    ok = await cancel_pending_hitl(
        graph,
        {"configurable": {"thread_id": "t1"}},
        reason="test supersede",
    )
    assert ok is True
    assert graph.ainvoke.await_count >= 1
    call_args = graph.ainvoke.await_args
    cmd = call_args.args[0]
    assert cmd.resume.get("approved") is False
