"""P6 web read cutover — the gate registry is the only decision author.

Covers: hitl_thread_status registry classification (+ thin execution
fallback), close_turn keeping a turn open on a pending gate row,
paused+no-row backfill, and the orphan-settle rule.
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


async def test_close_turn_paused_no_row_backfills_and_stays_open(monkeypatch, tmp_path):
    """P6 amendment: paused + no registry row is an unregistered pending
    gate — register from the snapshot and keep the turn open."""
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    tid = "t-close-backfill"
    ok = await tr.close_turn(
        _G(_PausedSnap()),
        {"configurable": {"thread_id": tid}},
        session_id="sess-1",
        turn_id="turn-4",
        streamed_text="narration before register",
    )
    assert ok is True
    assert captured.get("interrupted") is True
    from kazma_core.safety.hitl_gates import live_gates, pending_gates

    pending = pending_gates()
    assert any(r.thread_id == tid and r.state == "pending" for r in pending)
    assert live_gates(tid)


def test_close_turn_does_not_import_is_new_gate_as_reader() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "turn_runtime.py"
    ).read_text(encoding="utf-8")
    fn = src.split("async def close_turn", 1)[1].split("async def ", 1)[0]
    assert "is_new_gate" not in fn


def test_pending_approvals_happy_path_is_registry() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "routes_direct"
        / "misc.py"
    ).read_text(encoding="utf-8")
    fn = src.split("async def list_pending_approvals", 1)[1].split(
        "async def clear_pending_approvals_route", 1
    )[0]
    assert "pending_items_from_registry" in fn
    assert fn.find("pending_items_from_registry") < fn.find("_get_pending_approvals")


async def test_close_turn_registry_off_second_gate_stays_open(monkeypatch, tmp_path):
    """Thin fallback negative control: kill-switch OFF, resume claimed, and
    the pause is a NEW gate (write approved -> delete asks). The degraded
    path must route through hitl_thread_status and keep the turn OPEN —
    closing it published the fake wrap-up (2026-09-01 incident class)."""
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    import kazma_ui.hitl_status as hs

    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: True)
    monkeypatch.setattr(
        hs,
        "persisted_hitl_for_thread",
        lambda t: {"state": "approved", "tool": "file_write", "interrupt_id": "w1"},
    )
    ok = await tr.close_turn(
        _G(_PausedSnap()),  # live interrupt: file_delete — a DIFFERENT gate
        {"configurable": {"thread_id": "t-close-thin"}},
        session_id="sess-1",
        turn_id="turn-5",
        streamed_text="pre-pause narration",
    )
    assert ok is True
    assert captured.get("interrupted") is True


async def test_close_turn_registry_off_leftover_same_gate_closes(monkeypatch, tmp_path):
    """Negative control for the control: same leftover interrupt after
    Approve (SAME gate) must still close — the fallback must not hold every
    post-approve turn open forever."""
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    tr, captured = _wire_close_turn(monkeypatch, tmp_path)
    import kazma_ui.hitl_status as hs

    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: True)
    monkeypatch.setattr(
        hs,
        "persisted_hitl_for_thread",
        lambda t: {"state": "approved", "tool": "file_delete", "interrupt_id": ""},
    )
    ok = await tr.close_turn(
        _G(_PausedSnap()),  # live interrupt: file_delete — the SAME gate
        {"configurable": {"thread_id": "t-close-thin2"}},
        session_id="sess-1",
        turn_id="turn-6",
        streamed_text="the actual answer",
    )
    assert ok is True
    assert captured.get("interrupted") in (False, None)


# ── watchdog auto-deny dual-write + ghost-row sweep (2026-09-01) ───────────


class _AsyncBroker:
    async def emit(self, *a, **k):
        return {}


async def test_watchdog_auto_deny_claims_gate(monkeypatch):
    """An auto-deny must CAS the gate pending->claimed(deny)->resuming like
    the manual endpoint — leaving it pending was a permanent ghost card."""
    import kazma_core.safety.commitment.resume as resume_mod
    import kazma_ui.hitl_status as hs
    import kazma_ui.hitl_timeout as wd
    import kazma_ui.reply_sink as reply_sink
    import kazma_ui.sse_chat._streaming as streaming
    import kazma_ui.turn_runtime as tr_mod
    import kazma_ui.delivery as delivery

    payload = {"interrupt_id": "gwd1", "tool": "file_write", "kind": "security"}
    register_gate(GateRow(gate_id="gwd1", thread_id="t-wd-1", tool="file_write"))

    async def _read_pending(graph, cfg):
        return dict(payload)

    monkeypatch.setattr(resume_mod, "read_pending_interrupt", _read_pending)
    monkeypatch.setattr(
        resume_mod, "build_resume_command", lambda *a, **k: object()
    )
    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: False)
    monkeypatch.setattr(tr_mod, "ensure_session_for_thread", lambda t: "sess-wd")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a, **k: "turn-wd")
    monkeypatch.setattr(delivery, "get_turn_broker", lambda: _AsyncBroker())

    async def _no_drive(*a, **k):
        return None

    monkeypatch.setattr(streaming, "_drive_graph_to_journal", _no_drive)
    monkeypatch.setattr(streaming, "mark_thread_unpaused", lambda t: None)

    await wd._auto_deny(object(), "t-wd-1", 300.0)

    row = gate_for("gwd1")
    assert row is not None
    assert row.state in ("claimed", "resuming")
    assert row.decision == "deny"


async def test_watchdog_sweeps_ghost_pending_row(monkeypatch):
    """Registry row pending + NO live checkpoint interrupt + no running turn
    => the watchdog orphan-settles the ghost instead of skipping forever."""
    import kazma_core.safety.commitment.resume as resume_mod
    import kazma_ui.hitl_gate_bridge as bridge
    import kazma_ui.hitl_timeout as wd

    register_gate(GateRow(gate_id="ghost1", thread_id="t-wd-2", tool="file_write"))

    async def _read_none(graph, cfg):
        return None

    monkeypatch.setattr(resume_mod, "read_pending_interrupt", _read_none)
    monkeypatch.setattr(wd, "_SCAN_INTERVAL_SECONDS", 0.02)

    import kazma_core.safety.hitl as hitl_mod

    monkeypatch.setattr(
        hitl_mod,
        "get_hitl_config",
        lambda: {
            "enabled": True,
            "approval_timeout_seconds": 0.01,
            "auto_deny_on_timeout": True,
        },
    )

    async def _items():
        return [
            bridge.gate_row_to_pending_item(r)
            for r in __import__(
                "kazma_core.safety.hitl_gates", fromlist=["pending_gates"]
            ).pending_gates()
        ]

    monkeypatch.setattr(bridge, "pending_items_from_registry", _items)

    import asyncio

    task = asyncio.get_event_loop().create_task(
        wd._watchdog_loop(lambda: object(), lambda: object())
    )
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            row = gate_for("ghost1")
            if row is not None and row.state == "settled":
                break
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    row = gate_for("ghost1")
    assert row is not None and row.state == "settled"
    assert row.decision == "orphaned"
