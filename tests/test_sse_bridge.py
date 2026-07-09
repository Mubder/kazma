"""Unit tests for swarm.sse_bridge.SseBridge."""

from __future__ import annotations

from unittest.mock import MagicMock

from kazma_core.swarm.sse_bridge import SseBridge


def test_emit_noop_without_bus():
    b = SseBridge()
    b.emit("t1", "task_completed", {"ok": True})  # no raise


def test_emit_delegates_to_bus():
    b = SseBridge()
    bus = MagicMock()
    b.set_bus(bus)
    b.emit("t1", "checkpoint", {"step": 1})
    bus.emit.assert_called_once_with("t1", "checkpoint", {"step": 1})


def test_emit_swallows_bus_errors():
    b = SseBridge()
    bus = MagicMock()
    bus.emit.side_effect = RuntimeError("bus down")
    b.set_bus(bus)
    b.emit("t1", "x", {})  # must not raise


def test_bus_property():
    b = SseBridge()
    assert b.bus is None
    bus = object()
    b.set_bus(bus)
    assert b.bus is bus
