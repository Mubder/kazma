"""P5 — reconciler + chaos: every failure mode has ONE defined behavior.

The failure-mode table in docs/plans/HITL_GATE_REGISTRY_PLAN.md §1, row by
row. Negative controls included (§28).
"""

from __future__ import annotations

import time

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.safety.hitl_gates import (
    GateRow,
    boot_sweep,
    claim_gate,
    gate_for,
    live_gates,
    mark_resuming,
    register_gate,
)
from kazma_ui.hitl_status import hitl_thread_status


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    hg.gate_events._subs = []
    yield
    hg.set_db_path_for_tests(None)


# ── crash between claim and resume ──────────────────────────────────────────


def test_boot_sweep_orphans_stale_claimed_rows():
    register_gate(GateRow(gate_id="g1", thread_id="t1", tool="file_write"))
    claim_gate("g1", "approve", "web:me")
    # Simulate age: claimed long before the (restarted) process came up.
    conn = hg._connect()
    conn.execute(
        "UPDATE hitl_gates SET claimed_at = ? WHERE gate_id = 'g1'",
        (time.time() - 3600,),
    )
    conn.commit()
    conn.close()
    out = boot_sweep(grace_seconds=300)
    assert out["orphaned"] == 1
    row = gate_for("g1")
    assert row.state == "settled" and row.decision == "orphaned"


def test_boot_sweep_keeps_fresh_claimed_rows():
    # Negative control: a JUST-claimed row (live drive) must not be touched.
    register_gate(GateRow(gate_id="g2", thread_id="t2", tool="file_write"))
    claim_gate("g2", "approve", "web:me")
    out = boot_sweep(grace_seconds=300)
    assert out["orphaned"] == 0
    assert gate_for("g2").state == "claimed"


def test_boot_sweep_never_touches_pending():
    # A pending card survives restart — the checkpoint pause is still real.
    register_gate(GateRow(gate_id="g3", thread_id="t3", tool="file_write"))
    conn = hg._connect()
    conn.execute(
        "UPDATE hitl_gates SET created_at = ? WHERE gate_id = 'g3'",
        (time.time() - 7200,),
    )
    conn.commit()
    conn.close()
    out = boot_sweep(grace_seconds=300)
    assert out["orphaned"] == 0
    assert gate_for("g3").state == "pending"


def test_boot_sweep_orphans_stale_resuming_too():
    register_gate(GateRow(gate_id="g4", thread_id="t4", tool="x"))
    claim_gate("g4", "approve", "a")
    mark_resuming("g4")
    conn = hg._connect()
    conn.execute(
        "UPDATE hitl_gates SET claimed_at = ? WHERE gate_id = 'g4'",
        (time.time() - 3600,),
    )
    conn.commit()
    conn.close()
    assert boot_sweep(grace_seconds=300)["orphaned"] == 1
    assert gate_for("g4").state == "settled"


def test_boot_sweep_runs_ttl_expiry():
    register_gate(
        GateRow(gate_id="g5", thread_id="t5", tool="x"), ttl_seconds=0.001
    )
    time.sleep(0.01)
    out = boot_sweep()
    assert out["expired"] == 1
    assert gate_for("g5").state == "timeout"


# ── registry DB unreachable — readers degrade to legacy, never crash ───────


class _IdleSnap:
    next = ()
    tasks = []


class _PausedSnap:
    next = ("tool_worker",)

    class _T:
        class _I:
            value = {"type": "hitl_approval", "tool": "file_write", "args": {}}

        interrupts = [_I()]

    tasks = [_T()]


class _G:
    def __init__(self, snap):
        self._snap = snap

    async def aget_state(self, config):
        return self._snap


async def test_status_degrades_to_legacy_when_registry_down():
    hg.set_db_path_for_tests(r"\\?\nonexistent-device\gates.db")
    snap = _PausedSnap()
    # Legacy classification still answers: live interrupt → pending.
    assert await hitl_thread_status(
        "t-chaos-1", graph=_G(snap), snapshot=snap
    ) == "pending"
    snap2 = _IdleSnap()
    assert await hitl_thread_status(
        "t-chaos-2", graph=_G(snap2), snapshot=snap2
    ) == "idle"


async def test_close_turn_survives_registry_down(monkeypatch):
    hg.set_db_path_for_tests(r"\\?\nonexistent-device\gates.db")
    import kazma_ui.turn_runtime as tr

    captured = {}
    monkeypatch.setattr(
        tr, "persist_reply",
        lambda sid, tid, text, **kw: captured.update(kw) or True,
    )
    monkeypatch.setattr(tr, "resolve_session_id", lambda t, s: s or "sess-1")

    class _S:
        next = ()
        tasks = []
        values = {"messages": []}

    ok = await tr.close_turn(
        _G(_S()),
        {"configurable": {"thread_id": "t-chaos-3"}},
        session_id="sess-1",
        turn_id="turn-1",
        streamed_text="answer",
    )
    assert ok is True  # the turn still lands


# ── duplicated events / double-emit protection ──────────────────────────────


def test_transitions_emit_exactly_once_under_repeat_calls():
    from kazma_core.safety.hitl_gates import settle_gate

    events: list[str] = []
    hg.gate_events.subscribe(lambda ev, r: events.append(ev))
    register_gate(GateRow(gate_id="e1", thread_id="te", tool="x"))
    register_gate(GateRow(gate_id="e1", thread_id="te", tool="x"))  # repeat
    claim_gate("e1", "approve", "a")
    claim_gate("e1", "approve", "a")  # idempotent repeat
    settle_gate("e1")
    settle_gate("e1")  # idempotent repeat
    assert events == ["gate_pending", "gate_claimed", "gate_settled"]
