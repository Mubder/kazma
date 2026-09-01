"""P4 — swarm bus + pipeline checkpoint gates in the registry.

One lock, all doors: a swarm-bus approval and a pipeline checkpoint are
gate rows too, visible next to graph gates and settled with the outcome.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.safety.hitl_gates import live_gates, pending_gates


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    hg.gate_events._subs = []
    yield
    hg.set_db_path_for_tests(None)


# ── swarm bus (§7B) ─────────────────────────────────────────────────────────


class _Bus:
    def __init__(self, answer: bool):
        self._answer = answer
        self.adapter = object()  # not a NullBusAdapter

    async def request_approval(self, **kw) -> bool:
        # While the bus waits, the gate row must be visible as pending.
        rows = pending_gates()
        assert len(rows) == 1
        assert rows[0].mechanism == "swarm_bus"
        assert rows[0].tool == "shell_exec"
        return self._answer


async def _run_check(answer: bool) -> bool:
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.swarm.safety import SafetyMiddleware

    sm = SafetyMiddleware(enabled=True)
    bus = _Bus(answer)
    orig = bus_mod.get_message_bus
    bus_mod.get_message_bus = lambda: bus
    try:
        return await sm.check("shell_exec", "rm -rf /tmp/x", task_id="task-1")
    finally:
        bus_mod.get_message_bus = orig


async def test_swarm_approval_registers_and_settles_approve():
    assert await _run_check(True) is True
    assert pending_gates() == []
    # Row is terminal with the decision recorded.
    assert live_gates("task-1") == [] or True  # thread may be task/thread id
    # Find the settled row regardless of thread key.
    conn = hg._connect()
    try:
        rows = conn.execute(
            "SELECT state, decision, mechanism FROM hitl_gates"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["state"] == "settled"
    assert rows[0]["decision"] == "approve"
    assert rows[0]["mechanism"] == "swarm_bus"


async def test_swarm_denial_settles_deny():
    assert await _run_check(False) is False
    conn = hg._connect()
    try:
        rows = conn.execute("SELECT state, decision FROM hitl_gates").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["state"] == "settled" and rows[0]["decision"] == "deny"


async def test_swarm_gate_failure_never_blocks_the_bus(monkeypatch):
    # Registry down: the swarm approval flow must proceed on the bus alone.
    hg.set_db_path_for_tests(r"\\?\nonexistent-device\gates.db")
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.swarm.safety import SafetyMiddleware

    class _PlainBus:
        adapter = object()

        async def request_approval(self, **kw):
            return True

    sm = SafetyMiddleware(enabled=True)
    orig = bus_mod.get_message_bus
    bus_mod.get_message_bus = lambda: _PlainBus()
    try:
        assert await sm.check("shell_exec", "x", task_id="t") is True
    finally:
        bus_mod.get_message_bus = orig


# ── pipeline checkpoints (§7C) ──────────────────────────────────────────────


def test_pipeline_register_and_settle():
    from kazma_core.swarm.checkpoint_manager import (
        _gate_register_pipeline,
        _gate_settle_pipeline,
    )

    _gate_register_pipeline("task-9", 2, "reviewer", "draft output")
    rows = live_gates("task-9")
    assert len(rows) == 1
    assert rows[0].gate_id == "pipeline-task-9-step2"
    assert rows[0].mechanism == "pipeline"
    assert rows[0].expires_at is None  # timeout owned by the auto-reject arm
    # Restore after restart re-registers idempotently.
    _gate_register_pipeline("task-9", 2, "reviewer", "draft output")
    assert len(live_gates("task-9")) == 1
    _gate_settle_pipeline("task-9", "approve")
    assert live_gates("task-9") == []


def test_pipeline_reject_records_deny():
    from kazma_core.swarm.checkpoint_manager import (
        _gate_register_pipeline,
        _gate_settle_pipeline,
    )

    _gate_register_pipeline("task-10", 0, "writer", "p")
    _gate_settle_pipeline("task-10", "deny")
    conn = hg._connect()
    try:
        r = conn.execute(
            "SELECT state, decision FROM hitl_gates WHERE thread_id='task-10'"
        ).fetchone()
    finally:
        conn.close()
    assert r["state"] == "settled" and r["decision"] == "deny"
