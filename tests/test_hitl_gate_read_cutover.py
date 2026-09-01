"""P2 web read cutover — the gate registry is decision truth for readers.

Covers: hitl_thread_status registry-first classification (+ legacy
fallback), close_turn keeping a turn open on a pending gate row, and the
orphan-settle rule (pending row, checkpoint not paused → settled in place).
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.safety.hitl_gates import (
    GateRow,
    claim_gate,
    gate_for,
    register_gate,
)
from kazma_ui.hitl_status import hitl_thread_status


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    hg.gate_events._subs = []
    yield
    hg.set_db_path_for_tests(None)


class _Snap:
    def __init__(self, *, nxt=(), tasks=None) -> None:
        self.next = nxt
        self.tasks = tasks or []


class _Graph:
    def __init__(self, snap: Any) -> None:
        self._snap = snap

    async def aget_state(self, config):
        return self._snap


# ── hitl_thread_status: registry-first ─────────────────────────────────────


async def test_pending_row_classifies_pending():
    register_gate(GateRow(gate_id="g1", thread_id="t-cut-1", tool="file_write"))
    assert await hitl_thread_status("t-cut-1") == "pending"


async def test_claimed_row_classifies_inflight():
    register_gate(GateRow(gate_id="g1", thread_id="t-cut-2", tool="file_write"))
    claim_gate("g1", "approve", "web:me")
    assert await hitl_thread_status("t-cut-2") == "inflight"


async def test_second_pending_beside_claimed_is_pending():
    # THE incident: gate #1 claimed, gate #2 pending — chat must ask again.
    register_gate(GateRow(gate_id="w", thread_id="t-cut-3", tool="file_write"))
    claim_gate("w", "approve", "web:me")
    register_gate(GateRow(gate_id="d", thread_id="t-cut-3", tool="file_delete"))
    assert await hitl_thread_status("t-cut-3") == "pending"


async def test_no_rows_falls_back_to_legacy_idle():
    snap = _Snap(nxt=(), tasks=[])
    assert await hitl_thread_status(
        "t-cut-legacy", graph=_Graph(snap), snapshot=snap
    ) == "idle"


async def test_registry_off_uses_legacy(monkeypatch):
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    register_gate(GateRow(gate_id="g1", thread_id="t-cut-off", tool="file_write"))
    snap = _Snap(nxt=(), tasks=[])
    # Kill-switch off: pending row ignored, legacy says idle.
    assert await hitl_thread_status(
        "t-cut-off", graph=_Graph(snap), snapshot=snap
    ) == "idle"


# ── close_turn: silence rule + orphan settle ────────────────────────────────


def _wire_close_turn(monkeypatch, tmp_path):
    """Minimal close_turn harness (mirrors tests/test_hitl_status.py)."""
    import kazma_ui.turn_runtime as tr

    captured: dict[str, Any] = {}

    def fake_persist(session_id, turn_id, text, **kw):
        captured.update(kw)
        captured["text"] = text
        return True

    monkeypatch.setattr(tr, "persist_reply", fake_persist)
    monkeypatch.setattr(tr, "resolve_session_id", lambda t, s: s or "sess-1")
    return tr, captured


class _PausedSnap:
    next = ("tool_worker",)

    class _T:
        class _I:
            value = {"type": "hitl_approval", "tool": "file_delete", "args": {}}

        interrupts = [_I()]

    tasks = [_T()]
    values = {"messages": []}


class _IdleSnap:
    next = ()
    tasks = []
    values = {"messages": []}


class _G:
    def __init__(self, snap):
        self._snap = snap

    async def aget_state(self, config):
        return self._snap


async def test_close_turn_pending_row_keeps_turn_open(monkeypatch, tmp_path):
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    tid = "t-close-open"
    register_gate(GateRow(gate_id="d2", thread_id=tid, tool="file_delete"))
    ok = await tr.close_turn(
        _G(_PausedSnap()),
        {"configurable": {"thread_id": tid}},
        session_id="sess-1",
        turn_id="turn-1",
        streamed_text="narration while paused",
    )
    assert ok is True
    assert captured.get("interrupted") is True  # the silence rule
    assert gate_for("d2").state == "pending"    # question untouched


async def test_close_turn_orphan_pending_settled_not_kept_open(monkeypatch, tmp_path):
    # Pending row but the checkpoint is NOT paused and nothing is running:
    # the pause never happened (or was cleared) — settle as orphaned in
    # seconds, and do NOT hold the turn open for a phantom question.
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    tid = "t-close-orphan"
    register_gate(GateRow(gate_id="ghost", thread_id=tid, tool="file_write"))
    ok = await tr.close_turn(
        _G(_IdleSnap()),
        {"configurable": {"thread_id": tid}},
        session_id="sess-1",
        turn_id="turn-2",
        streamed_text="the actual answer",
    )
    assert ok is True
    assert captured.get("interrupted") in (False, None)
    assert gate_for("ghost").state == "settled"
    assert gate_for("ghost").decision == "orphaned"


async def test_close_turn_no_rows_behaves_legacy(monkeypatch, tmp_path):
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    ok = await tr.close_turn(
        _G(_IdleSnap()),
        {"configurable": {"thread_id": "t-close-none"}},
        session_id="sess-1",
        turn_id="turn-3",
        streamed_text="answer",
    )
    assert ok is True
    assert captured.get("interrupted") in (False, None)
