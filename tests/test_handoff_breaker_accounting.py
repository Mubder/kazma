"""Source circuit breaker must record once per handoff (not double)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.swarm.reliability import CircuitBreaker, CircuitState
from kazma_core.swarm.task import WorkerResult


@pytest.mark.asyncio
async def test_handoff_records_source_breaker_once() -> None:
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.config import SwarmConfig
    from kazma_core.swarm.handoff import HandoffRequest

    engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[]))
    breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=30.0)

    source = MagicMock()
    source.name = "source"
    source.mark_completed = MagicMock()

    target_ok = [
        WorkerResult(
            worker="target",
            task_id="t1",
            status="success",
            output="done",
        )
    ]
    engine._dispatch_worker_by_name_all = AsyncMock(return_value=target_ok)  # type: ignore

    # Simulate what worker_dispatch used to do (pre-count success) — we assert
    # handle_handoff alone leaves breaker closed after one success, not two.
    results = await engine._handle_handoff(
        handoff_req=HandoffRequest(
            target_worker="target",
            task="continue",
            context="",
        ),
        source_worker=source,
        prompt="p",
        context="",
        timeout=None,
        validation_schema=None,
        started=0.0,
        breaker=breaker,
        _visited={},
        _depth=1,
    )
    assert results[0].status == "success"
    # One success should leave breaker CLOSED (default); failure_count 0
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_handoff_failure_records_once_not_success_then_fail() -> None:
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.config import SwarmConfig
    from kazma_core.swarm.handoff import HandoffRequest

    engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[]))
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)

    source = MagicMock()
    source.name = "source"
    source.mark_completed = MagicMock()

    target_fail = [
        WorkerResult(
            worker="target",
            task_id="t1",
            status="error",
            output="",
            error="nope",
        )
    ]
    engine._dispatch_worker_by_name_all = AsyncMock(return_value=target_fail)  # type: ignore

    await engine._handle_handoff(
        handoff_req=HandoffRequest(
            target_worker="target",
            task="continue",
            context="",
        ),
        source_worker=source,
        prompt="p",
        context="",
        timeout=None,
        validation_schema=None,
        started=0.0,
        breaker=breaker,
        _visited={},
        _depth=1,
    )
    # Exactly one failure counted
    assert breaker.consecutive_failures == 1
