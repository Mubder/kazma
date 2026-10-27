"""Regression tests for gw-062 audit bugs.

BUG 1: Memory ranking — verify search results are sorted descending by score.
BUG 2: Cron double-fire — verify in-flight guard prevents duplicate execution.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# BUG 1: Memory ranking — regression tests for reverse=True sort order
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# BUG 2: Cron double-fire — regression tests for in-flight guard
# ═══════════════════════════════════════════════════════════════════════════
from kazma_core.cron.scheduler import CronScheduler, ScheduledJob


class TestCronInFlightGuard:
    """Verify that the scheduler does not fire a job that is already running.

    The poll pass is the scheduler's own (``_poll_once``) over a store that
    reports nothing to recover, deliver or alert. (These tests ran a copy of
    the loop around the scheduler's ``_is_due``, which the poll never used:
    removed with it, audit 2026-10-26 AUD-076.)
    """

    @pytest.fixture
    def mock_store(self):
        store = AsyncMock()
        store.list_running = AsyncMock(return_value=[])
        store.recover_deliveries = AsyncMock(return_value=[])
        store.claim_delivery = AsyncMock(return_value=None)
        store.pending_alerts = AsyncMock(return_value=[])
        store.pending_alert_count = AsyncMock(return_value=0)
        return store

    @pytest.fixture
    def scheduler(self, mock_store):
        return CronScheduler(
            store=mock_store,
            graph_builder=None,
            poll_interval=0.1,
        )

    @staticmethod
    def _due_job(job_id: str) -> ScheduledJob:
        return ScheduledJob(
            job_id=job_id,
            timing="5m",
            prompt="test prompt",
            platform="telegram",
            thread_id="",
            next_run=(datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
        )

    def test_in_flight_set_initialized(self, scheduler):
        """Scheduler must have an empty _in_flight set on init."""
        assert hasattr(scheduler, "_in_flight")
        assert isinstance(scheduler._in_flight, set)
        assert len(scheduler._in_flight) == 0

    @pytest.mark.asyncio
    async def test_in_flight_prevents_double_fire(self, scheduler, mock_store):
        """A job already in _in_flight must not be dispatched again.

        Regression guard for gw-062 BUG 2: if a job takes longer than the
        poll interval, the next poll must skip it.
        """
        mock_store.due_jobs = AsyncMock(return_value=[self._due_job("test-dup-1")])
        execute_calls = []

        async def mock_execute(j):
            execute_calls.append(j.job_id)

        scheduler._execute = mock_execute
        scheduler._in_flight.add("test-dup-1")
        scheduler._running = True

        for _ in range(3):
            await scheduler._poll_once()
        await asyncio.gather(*scheduler._exec_tasks)

        # _execute should never have been called because the job was in-flight
        assert len(execute_calls) == 0, (
            f"Job was executed {len(execute_calls)} times despite being in-flight. "
            f"In-flight guard is broken."
        )

    @pytest.mark.asyncio
    async def test_job_removed_from_in_flight_after_execution(self, scheduler):
        """After a job finishes (success or failure), it must be removed from _in_flight.

        Regression guard: the finally block must call _in_flight.discard().
        """
        scheduler._in_flight.add("cleanup-test")

        job = ScheduledJob(
            job_id="cleanup-test",
            timing="5m",
            prompt="test",
            platform="telegram",
            thread_id="",
        )

        # Mock _graph_builder to None so _execute raises RuntimeError
        scheduler._graph_builder = None

        # Call _execute — it should fail but still remove from in_flight
        await scheduler._execute(job)

        assert "cleanup-test" not in scheduler._in_flight, (
            "Job ID was not removed from _in_flight after execution. "
            "The finally block is missing or not calling discard()."
        )

    @pytest.mark.asyncio
    async def test_in_flight_allows_new_jobs(self, scheduler, mock_store):
        """Jobs NOT in _in_flight should still be dispatched normally -- once,
        however many passes run while the fire is in flight."""
        mock_store.due_jobs = AsyncMock(return_value=[self._due_job("new-job-1")])
        release = asyncio.Event()
        execute_calls = []

        async def mock_execute(j):
            execute_calls.append(j.job_id)
            await release.wait()

        scheduler._execute = mock_execute
        scheduler._running = True

        await scheduler._poll_once()
        await asyncio.sleep(0)  # the fire starts
        await scheduler._poll_once()  # a second pass while it runs
        release.set()
        await asyncio.gather(*scheduler._exec_tasks)

        assert execute_calls == ["new-job-1"]
        assert "new-job-1" not in scheduler._in_flight
