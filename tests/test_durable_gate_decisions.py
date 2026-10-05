"""Persist the approval intent before any projection or execution."""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from kazma_ui import hitl_decision as decisions
from kazma_ui import hitl_gate_bridge as bridge
from kazma_core.safety import hitl_gates as gates


@pytest.mark.asyncio
async def test_storage_failure_cannot_paint_an_approval(monkeypatch):
    monkeypatch.setattr(decisions, "_resolve_turn", lambda *a: ("session", "turn"))
    claim = AsyncMock(side_effect=sqlite3.OperationalError("locked"))
    monkeypatch.setattr(bridge, "gate_claimed", claim)
    import kazma_ui.sse_chat._streaming as streaming
    stamp = AsyncMock()
    monkeypatch.setattr(streaming, "stamp_hitl_part_state", stamp)
    with pytest.raises(decisions.GateDecisionUnavailable):
        await decisions.record_gate_decision("thread", decision="approved", actor="operator",
                                            interrupt_id="gate", require_durable=True)
    stamp.assert_not_called()


@pytest.mark.asyncio
async def test_real_bridge_surfaces_storage_failure(monkeypatch):
    monkeypatch.setattr(bridge, "registry_on", lambda: True)
    monkeypatch.setattr(gates, "gate_for_async", AsyncMock(side_effect=sqlite3.OperationalError("locked")))
    with pytest.raises(sqlite3.OperationalError):
        await bridge.gate_claimed("thread", "gate", "approve", "operator", strict=True)


@pytest.mark.asyncio
async def test_claim_precedes_projections_and_survives_projection_failure(monkeypatch):
    monkeypatch.setattr(decisions, "_resolve_turn", lambda *a: ("session", "turn"))
    order = []

    async def claim(*a, **kw):
        assert kw["strict"]
        order.append("persist")

    async def resuming(*a, **kw):
        order.append("resume-intent")

    def stamp(*a, **kw):
        order.append("projection")
        raise sqlite3.OperationalError("projection unavailable")

    monkeypatch.setattr(bridge, "gate_claimed", claim)
    monkeypatch.setattr(bridge, "gate_resuming", resuming)
    import kazma_ui.sse_chat._streaming as streaming
    import kazma_ui.delivery as delivery
    monkeypatch.setattr(streaming, "stamp_hitl_part_state", stamp)
    monkeypatch.setattr(delivery, "get_turn_broker", lambda: type("Broker", (), {"emit": AsyncMock()})())
    assert await decisions.record_gate_decision("thread", decision="approved", actor="operator",
                                               interrupt_id="gate", require_durable=True) == "gate"
    assert order == ["persist", "resume-intent", "projection"]


@pytest.mark.asyncio
async def test_identical_concurrent_decisions_have_one_execution_owner(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "1")
    gates.set_db_path_for_tests(str(tmp_path / "gates.db"))
    try:
        await gates.register_gate_async(gates.GateRow(gate_id="g", thread_id="t", tool="file_write"))
        results = await asyncio.gather(
            bridge.gate_claimed("t", "g", "approve", "op", strict=True),
            bridge.gate_claimed("t", "g", "approve", "op", strict=True),
            return_exceptions=True,
        )
        assert sum(isinstance(r, str) for r in results) == 1
        assert sum(isinstance(r, gates.TransitionConflict) for r in results) == 1
    finally:
        gates.set_db_path_for_tests(None)
