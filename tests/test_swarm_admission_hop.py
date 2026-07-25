"""Swarm global admission (M11), hop budget on fallback (M13), concurrency cache."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.swarm.config import SwarmConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType, WorkerResult


@pytest.mark.asyncio
async def test_dispatch_admission_denied_when_at_capacity() -> None:
    engine = SwarmEngine(config=SwarmConfig(enabled=True, max_concurrent_tasks=1, workers=[]))
    # Pretend one task already in flight
    dummy = SwarmTask(prompt="running", workers=["w"], type=TaskType.DISPATCH)
    dummy.status = TaskStatus.RUNNING
    engine._active_tasks[dummy.id] = dummy

    denied = await engine.dispatch(
        SwarmTask(prompt="new", workers=["w"], type=TaskType.DISPATCH)
    )
    assert denied.status == "failed"
    assert denied.metadata.get("admission_denied") is True
    assert "capacity" in (denied.error or "").lower()


@pytest.mark.asyncio
async def test_fallback_threads_visited_budget() -> None:
    """Fallbacks must not reset hop visit counts (audit M13)."""
    engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[]))

    # Primary already failed; fallback will see visited with primary counted.
    primary = WorkerResult(
        worker="primary",
        task_id="t1",
        status="error",
        output="",
        error="boom",
    )
    visited = {"primary": 1}

    calls: list[tuple[str, dict | None, int]] = []

    async def fake_dispatch_worker(
        worker,
        prompt,
        context,
        *,
        timeout=None,
        validation_schema=None,
        trace_id=None,
        _visited=None,
        _depth=0,
    ):
        calls.append((worker.name if hasattr(worker, "name") else str(worker), _visited, _depth))
        return [
            WorkerResult(
                worker=getattr(worker, "name", "fb"),
                task_id="t1",
                status="success",
                output="ok",
            )
        ]

    # Register a fake fallback worker object on the engine map
    fb = MagicMock()
    fb.name = "fallback_a"
    engine._workers["fallback_a"] = fb
    engine._dispatch_worker = fake_dispatch_worker  # type: ignore[method-assign]

    final, all_results = await engine._execute_fallback_chain(
        primary,
        ["fallback_a"],
        prompt="p",
        context="",
        _visited=visited,
        _depth=1,
    )
    assert final.status == "success"
    assert calls, "fallback should have dispatched"
    assert calls[0][1] is visited  # same dict object threaded
    assert calls[0][2] == 2  # depth incremented


def test_get_bounded_concurrency_is_cached() -> None:
    engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[]))
    a = engine.get_bounded_concurrency(3)
    b = engine.get_bounded_concurrency(3)
    assert a is b
