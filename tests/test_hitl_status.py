"""HITL thread status — pending vs inflight vs idle."""

from __future__ import annotations

from typing import Any

import pytest

from kazma_ui.hitl_status import (
    hitl_thread_status,
    is_resume_claimed,
    is_truly_pending,
)


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
    def __init__(
        self,
        *,
        nxt: tuple[str, ...] = ("tool_worker",),
        tasks: list[Any] | None = None,
        values: dict[str, Any] | None = None,
    ) -> None:
        self.next = nxt
        self.tasks = tasks or []
        self.values = values or {}


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
async def test_abandoned_leftover_interrupt_is_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """/abort wrote abandoned; leftover interrupt() is not a live card."""
    monkeypatch.setattr("kazma_ui.hitl_gate_bridge.registry_on", lambda: False)
    snap = _Snap(
        tasks=[_Task([_Interrupt({
            "type": "hitl_approval",
            "tool": "schedule_task",
            "args": {},
        })])],
        values={"task_status": "abandoned"},
    )
    status = await hitl_thread_status("t-aborted", graph=_Graph(snap), snapshot=snap)
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


@pytest.mark.asyncio
async def test_is_truly_pending_false_when_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken helper must not emit a live card (fail closed)."""
    import kazma_ui.hitl_status as hs

    async def _boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("status exploded")

    monkeypatch.setattr(hs, "hitl_thread_status", _boom)
    assert await is_truly_pending("t-boom") is False


@pytest.mark.asyncio
async def test_refresh_during_claimed_resume_is_not_pending() -> None:
    """03:59 class as one story: interrupt still in the checkpoint, Approve claimed.

    Refresh asks the pending list + status helper. Neither may report a live
    gate while ``register_turn`` holds the resume.
    """
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.hitl_approval import _get_pending_approvals
    from tests.test_hitl_approval_ui import (
        MockCheckpointer,
        MockGraph,
        MockInterrupt,
        MockStateSnapshot,
        MockTask,
    )

    tid = "thread-refresh-claimed"
    snap = MockStateSnapshot(
        next_nodes=("tool_worker",),
        tasks=[MockTask(interrupts=[
            MockInterrupt({
                "type": "hitl_approval",
                "tool": "file_write",
                "args": {"path": "hitl-webui-test.txt"},
            })
        ])],
    )
    graph = MockGraph({tid: snap})
    checkpointer = MockCheckpointer(thread_ids=[tid])

    before = await _get_pending_approvals(graph, checkpointer)
    assert len(before) == 1
    assert await is_truly_pending(tid, graph=graph, snapshot=snap) is True

    task = _LiveTask()
    register_turn(tid, task)
    try:
        assert is_resume_claimed(tid) is True
        assert await hitl_thread_status(tid, graph=graph, snapshot=snap) == "inflight"
        assert await is_truly_pending(tid, graph=graph, snapshot=snap) is False
        after = await _get_pending_approvals(graph, checkpointer)
        assert after == []
    finally:
        unregister_turn(tid, task)


# ══════════════════════════════════════════════════════════════════════════
# Second gate during a claimed resume (2026-09-01 incident)
# ══════════════════════════════════════════════════════════════════════════


class _InterruptWithId(_Interrupt):
    def __init__(self, value: dict[str, Any], iid: str) -> None:
        super().__init__(value)
        self.id = iid


def _delete_snap(iid: str = "intr-B") -> _Snap:
    return _Snap(tasks=[_Task([_InterruptWithId({
        "type": "hitl_approval",
        "tool": "file_delete",
        "args": {"path": "hitl-webui-test.txt"},
    }, iid)])])


_APPROVED_WRITE_PART = {
    "type": "hitl",
    "state": "approved",
    "tool": "file_write",
    "interrupt_id": "intr-A",
}


@pytest.mark.asyncio
async def test_second_gate_during_claimed_resume_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write approved + resume running, graph pauses on a DIFFERENT gate:
    the second question must classify pending — not hide behind the claim
    (it only ever showed on the dashboard, 2026-09-01)."""
    import kazma_ui.hitl_status as hs
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.sse_chat._streaming import (
        mark_thread_paused,
        mark_thread_unpaused,
    )

    tid = "t-second-gate"
    snap = _delete_snap()
    monkeypatch.setattr(
        hs, "persisted_hitl_for_thread", lambda _tid: dict(_APPROVED_WRITE_PART)
    )
    task = _LiveTask()
    register_turn(tid, task)
    mark_thread_paused(tid)
    try:
        status = await hitl_thread_status(tid, graph=_Graph(snap), snapshot=snap)
        assert status == "pending"
        assert await is_truly_pending(tid, graph=_Graph(snap), snapshot=snap) is True
    finally:
        mark_thread_unpaused(tid)
        unregister_turn(tid, task)


@pytest.mark.asyncio
async def test_same_gate_during_claimed_resume_stays_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leftover checkpoint interrupt of the gate the user ALREADY
    approved stays inflight (no re-question)."""
    import kazma_ui.hitl_status as hs
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.sse_chat._streaming import (
        mark_thread_paused,
        mark_thread_unpaused,
    )

    tid = "t-same-gate"
    snap = _Snap(tasks=[_Task([_InterruptWithId({
        "type": "hitl_approval",
        "tool": "file_write",
        "args": {"path": "x"},
    }, "intr-A")])])
    monkeypatch.setattr(
        hs, "persisted_hitl_for_thread", lambda _tid: dict(_APPROVED_WRITE_PART)
    )
    task = _LiveTask()
    register_turn(tid, task)
    mark_thread_paused(tid)
    try:
        status = await hitl_thread_status(tid, graph=_Graph(snap), snapshot=snap)
        assert status == "inflight"
    finally:
        mark_thread_unpaused(tid)
        unregister_turn(tid, task)


def test_is_new_gate_evidence_rules() -> None:
    from kazma_ui.hitl_status import is_new_gate

    part = dict(_APPROVED_WRITE_PART)
    # Different interrupt ids → new gate.
    assert is_new_gate(part, _delete_snap("intr-B")) is True
    # Same id → leftover of the approved gate.
    assert is_new_gate(part, _delete_snap("intr-A")) is False
    # No ids anywhere, different tool → new gate.
    idless = {"type": "hitl", "state": "approved", "tool": "file_write"}
    snap_no_id = _Snap(tasks=[_Task([_Interrupt({
        "type": "hitl_approval", "tool": "file_delete", "args": {},
    })])])
    assert is_new_gate(idless, snap_no_id) is True
    # No evidence (no part / no snapshot) → not a new gate.
    assert is_new_gate(None, snap_no_id) is False
    assert is_new_gate(part, None) is False


@pytest.mark.asyncio
async def test_close_turn_keeps_new_gate_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close_turn during an approve-resume that paused on a NEW gate must
    keep the turn OPEN (interrupted=True) — closing it published the
    model's pre-pause narration as a fake wrap-up (2026-09-01)."""
    import kazma_ui.hitl_status as hs
    import kazma_ui.turn_runtime as tr
    from kazma_ui.active_turns import register_turn, unregister_turn

    tid = "t-close-new-gate"
    snap = _delete_snap()
    monkeypatch.setattr(
        hs, "persisted_hitl_for_thread", lambda _tid: dict(_APPROVED_WRITE_PART)
    )
    monkeypatch.setattr(tr, "resolve_session_id", lambda *_a, **_k: "sess-x")
    captured: dict[str, Any] = {}

    def _capture(session_id: str, turn_id: str, content: str, **kw: Any) -> bool:
        captured.update(kw, session_id=session_id, content=content)
        return True

    monkeypatch.setattr(tr, "persist_reply", _capture)
    task = _LiveTask()
    register_turn(tid, task)  # the resume drive counts itself as running
    try:
        ok = await tr.close_turn(
            _Graph(snap),
            {"configurable": {"thread_id": tid, "checkpoint_ns": ""}},
            session_id="sess-x",
            thread_id=tid,
            turn_id="turn-1",
            streamed_text="pre-pause narration",
        )
        assert ok is True
        assert captured.get("interrupted") is True
    finally:
        unregister_turn(tid, task)


@pytest.mark.asyncio
async def test_close_turn_same_gate_leftover_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approved gate's own leftover interrupt stays a stale pause —
    the turn closes normally."""
    import kazma_ui.hitl_status as hs
    import kazma_ui.turn_runtime as tr
    from kazma_ui.active_turns import register_turn, unregister_turn

    tid = "t-close-stale"
    snap = _Snap(tasks=[_Task([_InterruptWithId({
        "type": "hitl_approval",
        "tool": "file_write",
        "args": {"path": "x"},
    }, "intr-A")])])
    monkeypatch.setattr(
        hs, "persisted_hitl_for_thread", lambda _tid: dict(_APPROVED_WRITE_PART)
    )
    monkeypatch.setattr(tr, "resolve_session_id", lambda *_a, **_k: "sess-x")
    captured: dict[str, Any] = {}

    def _capture(session_id: str, turn_id: str, content: str, **kw: Any) -> bool:
        captured.update(kw, session_id=session_id, content=content)
        return True

    monkeypatch.setattr(tr, "persist_reply", _capture)
    task = _LiveTask()
    register_turn(tid, task)
    try:
        ok = await tr.close_turn(
            _Graph(snap),
            {"configurable": {"thread_id": tid, "checkpoint_ns": ""}},
            session_id="sess-x",
            thread_id=tid,
            turn_id="turn-1",
            streamed_text="the answer",
        )
        assert ok is True
        assert captured.get("interrupted") is False
    finally:
        unregister_turn(tid, task)
