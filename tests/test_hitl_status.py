"""HITL thread status — pending vs inflight vs idle."""

from __future__ import annotations

from typing import Any

import pytest

from kazma_ui.hitl_status import hitl_thread_status, is_resume_claimed


class _LiveTask:
    def done(self) -> bool:
        return False


class _Interrupt:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _Task:
    def __init__(self, interrupts: list[Any]) -> None:
        self.interrupts = interrupts


class _Snap:
    def __init__(self, *, nxt: tuple[str, ...] = ("tool_worker",), tasks: list[Any] | None = None) -> None:
        self.next = nxt
        self.tasks = tasks or []


class _Graph:
    def __init__(self, snap: Any) -> None:
        self._snap = snap

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return self._snap


@pytest.mark.asyncio
async def test_interrupt_not_running_is_pending() -> None:
    snap = _Snap(tasks=[_Task([_Interrupt({
        "type": "hitl_approval",
        "tool": "file_write",
        "args": {"path": "x"},
    })])])
    status = await hitl_thread_status("t-pending", graph=_Graph(snap), snapshot=snap)
    assert status == "pending"


@pytest.mark.asyncio
async def test_running_resume_is_inflight_even_with_interrupt() -> None:
    """The 03:59 reproduction: checkpoint still interrupted, Approve already claimed."""
    from kazma_ui.active_turns import register_turn, unregister_turn

    tid = "t-inflight-hitl"
    task = _LiveTask()
    snap = _Snap(tasks=[_Task([_Interrupt({
        "type": "hitl_approval",
        "tool": "file_write",
        "args": {"path": "x"},
    })])])
    register_turn(tid, task)
    try:
        assert is_resume_claimed(tid) is True
        status = await hitl_thread_status(tid, graph=_Graph(snap), snapshot=snap)
        assert status == "inflight"
    finally:
        unregister_turn(tid, task)


@pytest.mark.asyncio
async def test_no_interrupt_is_idle() -> None:
    snap = _Snap(nxt=(), tasks=[])
    status = await hitl_thread_status("t-idle", graph=_Graph(snap), snapshot=snap)
    assert status == "idle"


@pytest.mark.asyncio
async def test_pending_list_excludes_running_thread() -> None:
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.hitl_approval import _get_pending_approvals
    from tests.test_hitl_approval_ui import (
        MockCheckpointer,
        MockGraph,
        MockInterrupt,
        MockStateSnapshot,
        MockTask,
    )

    tid = "thread-running-hitl"
    graph = MockGraph({
        tid: MockStateSnapshot(
            next_nodes=("tool_worker",),
            tasks=[MockTask(interrupts=[
                MockInterrupt({
                    "type": "hitl_approval",
                    "tool": "file_write",
                    "args": {"path": "x"},
                })
            ])],
        ),
    })
    checkpointer = MockCheckpointer(thread_ids=[tid])
    task = _LiveTask()
    register_turn(tid, task)
    try:
        pending = await _get_pending_approvals(graph, checkpointer)
        assert pending == []
    finally:
        unregister_turn(tid, task)
