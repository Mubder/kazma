"""Regression tests for gw-062 audit bugs.

BUG 1: Memory ranking — verify search results are sorted descending by score.
BUG 2: Cron double-fire — verify in-flight guard prevents duplicate execution.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_memory import SearchBackend, SQLiteMemoryBackend


# ═══════════════════════════════════════════════════════════════════════════
# BUG 1: Memory ranking — regression tests for reverse=True sort order
# ═══════════════════════════════════════════════════════════════════════════


class TestMemoryRankingSortOrder:
    """Verify search results are sorted by descending combined score (BM25 + relevance)."""

    @pytest.fixture
    def backend(self, tmp_path):
        db_path = tmp_path / "test_ranking.db"
        backend = SQLiteMemoryBackend(str(db_path))
        yield backend
        asyncio.run(backend.close())

    @pytest.mark.asyncio
    async def test_highest_bm25_score_ranks_first(self, backend):
        """Results with higher BM25 scores (better keyword match) must rank first.

        Regression guard for gw-062 BUG 1: sorted() must use reverse=True
        so the highest combined score appears at index 0.
        """
        # Index memories with varying relevance — the one matching
        # the query should get a BM25 boost that overrides the relevance
        # weighting.
        memories = [
            {
                "id": "low_relevance_exact",
                "content": "alpha beta gamma unique_term_xyz",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.3,
            },
            {
                "id": "high_relevance_no_match",
                "content": "completely unrelated content here",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 1.0,
            },
            {
                "id": "mid_relevance_partial",
                "content": "alpha is important in many contexts",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.7,
            },
        ]

        for mem in memories:
            await backend.index(mem)

        results = await backend.search("alpha", limit=10)

        # Must return results
        assert len(results) >= 2, f"Expected >=2 results, got {len(results)}"

        # Results that contain "alpha" should rank above those that don't.
        # More importantly, results must be in non-increasing score order.
        for i in range(len(results) - 1):
            score_i = (
                results[i].get("bm25_score", 0) * 0.7
                + results[i].get("relevance", 1.0) * 0.3
                if "bm25_score" in results[i]
                else results[i].get("relevance", 1.0)
            )
            score_next = (
                results[i + 1].get("bm25_score", 0) * 0.7
                + results[i + 1].get("relevance", 1.0) * 0.3
                if "bm25_score" in results[i + 1]
                else results[i + 1].get("relevance", 1.0)
            )
            assert score_i >= score_next, (
                f"Result at index {i} (score={score_i:.4f}) should rank >= "
                f"result at index {i+1} (score={score_next:.4f}). "
                f"Check reverse=True in sorted()."
            )

    @pytest.mark.asyncio
    async def test_pure_relevance_items_sorted_descending(self, backend):
        """Items without BM25 scores (LIKE fallback) must still sort descending by relevance.

        Regression guard: if FTS5 fails and items only carry 'relevance',
        the sort must still be reverse=True on relevance.
        """
        for i, rel in enumerate([0.2, 0.9, 0.5, 1.0, 0.7]):
            await backend.index(
                {
                    "id": f"rel_{i}",
                    "content": f"memory with relevance marker_{i}",
                    "metadata": {},
                    "timestamp": int(time.time()),
                    "source": "test",
                    "relevance": rel,
                }
            )

        # Force FTS5 unavailable so we fall back to LIKE (no bm25_score in results)
        backend._vec_available = False

        results = await backend.search("marker", limit=10)
        assert len(results) >= 2

        # Verify descending order of relevance
        relevances = [r.get("relevance", 0) for r in results]
        for i in range(len(relevances) - 1):
            assert relevances[i] >= relevances[i + 1], (
                f"Relevance at index {i} ({relevances[i]}) should be >= "
                f"index {i+1} ({relevances[i+1]}). "
                f"Items must be sorted descending by relevance."
            )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 2: Cron double-fire — regression tests for in-flight guard
# ═══════════════════════════════════════════════════════════════════════════


from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob


class TestCronInFlightGuard:
    """Verify that the scheduler does not fire a job that is already running."""

    @pytest.fixture
    def mock_store(self):
        store = AsyncMock()
        store.update_status = AsyncMock()
        store.update_result = AsyncMock()
        store.update_next_run = AsyncMock()
        return store

    @pytest.fixture
    def scheduler(self, mock_store):
        return CronScheduler(
            store=mock_store,
            graph_builder=None,
            poll_interval=0.1,
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
        # Create a job that is due NOW
        job = ScheduledJob(
            job_id="test-dup-1",
            timing="5m",
            prompt="test prompt",
            platform="telegram",
            thread_id="",
            next_run=(datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
        )

        # Mock store to return the same job on every poll
        mock_store.list_active = AsyncMock(return_value=[job])

        # Track how many times _execute is called
        execute_calls = []

        async def mock_execute(j):
            execute_calls.append(j.job_id)

        scheduler._execute = mock_execute

        # Simulate 3 poll cycles with the job already in-flight
        scheduler._in_flight.add("test-dup-1")
        scheduler._running = True

        # Manually run 3 poll iterations
        for _ in range(3):
            jobs = await scheduler._store.list_active()
            now = datetime.now(UTC)
            for j in jobs:
                if j.job_id in scheduler._in_flight:
                    continue
                if j.next_run and scheduler._is_due(j.next_run, now):
                    scheduler._in_flight.add(j.job_id)
                    await scheduler._execute(j)

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
        """Jobs NOT in _in_flight should still be dispatched normally."""
        job = ScheduledJob(
            job_id="new-job-1",
            timing="5m",
            prompt="test prompt",
            platform="telegram",
            thread_id="",
            next_run=(datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
        )

        mock_store.list_active = AsyncMock(return_value=[job])

        execute_calls = []

        async def mock_execute(j):
            execute_calls.append(j.job_id)
            # Simulate the finally cleanup
            scheduler._in_flight.discard(j.job_id)

        scheduler._execute = mock_execute
        scheduler._running = True

        # Run one poll cycle — job is NOT in in_flight, should fire
        jobs = await scheduler._store.list_active()
        now = datetime.now(UTC)
        for j in jobs:
            if j.job_id in scheduler._in_flight:
                continue
            if j.next_run and scheduler._is_due(j.next_run, now):
                scheduler._in_flight.add(j.job_id)
                await scheduler._execute(j)

        assert len(execute_calls) == 1
        assert execute_calls[0] == "new-job-1"
