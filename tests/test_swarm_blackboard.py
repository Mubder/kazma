"""Tests for the shared swarm blackboard."""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.swarm.blackboard import BlackboardStore


@pytest.mark.asyncio
async def test_blackboard_get_returns_none_for_missing_key() -> None:
    """Missing keys return None."""
    blackboard = BlackboardStore()

    assert await blackboard.get("missing") is None


@pytest.mark.asyncio
async def test_blackboard_set_get_round_trip_and_keys() -> None:
    """Values can be stored, read back, and enumerated."""
    blackboard = BlackboardStore()

    await blackboard.set("status", "ready")
    await blackboard.set("count", 2)

    assert await blackboard.get("status") == "ready"
    assert await blackboard.get("count") == 2
    assert set(await blackboard.keys()) == {"status", "count"}


@pytest.mark.asyncio
async def test_blackboard_update_is_atomic_under_concurrency() -> None:
    """Concurrent updates do not lose writes."""
    blackboard = BlackboardStore()

    async def increment(current: int | None) -> int:
        await asyncio.sleep(0)
        return (current or 0) + 1

    await asyncio.gather(*(blackboard.update("counter", increment) for _ in range(50)))

    assert await blackboard.get("counter") == 50


@pytest.mark.asyncio
async def test_blackboard_snapshot_returns_copy_and_clear_resets_state() -> None:
    """Snapshots are copies and clear removes all stored state."""
    blackboard = BlackboardStore()
    await blackboard.set("status", "ready")

    snapshot = await blackboard.snapshot()
    snapshot["status"] = "changed"

    assert await blackboard.get("status") == "ready"

    await blackboard.clear()

    assert await blackboard.snapshot() == {}
