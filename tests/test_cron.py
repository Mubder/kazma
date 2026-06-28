"""Tests for cron scheduler (gw-029).

8 tests:
    1. parse_timing relative ("5m", "1h")
    2. parse_timing recurring ("daily at 9am")
    3. parse_timing ISO timestamp
    4. SQLiteCronStore insert + list_active
    5. CronScheduler schedule + list_jobs
    6. CronScheduler cancel
    7. schedule_task tool registered
    8. list_scheduled + cancel_scheduled tools registered
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.cron.scheduler import (
    CronScheduler,
    JobStatus,
    ScheduledJob,
    SQLiteCronStore,
    get_cron_scheduler,
    parse_timing,
    set_cron_scheduler,
)


class TestParseTiming:
    """Test timing string parsing."""

    def test_relative_minutes(self) -> None:
        """Test 1: '5m' → 5 minutes from now."""
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
        result = parse_timing("5m", from_time=now)
        assert result == now + timedelta(minutes=5)

    def test_relative_hours(self) -> None:
        """'1h' → 1 hour from now."""
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
        result = parse_timing("1h", from_time=now)
        assert result == now + timedelta(hours=1)

    def test_daily_am(self) -> None:
        """Test 2: 'daily at 9am' → next 9:00 UTC."""
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
        result = parse_timing("daily at 9am", from_time=now)
        assert result.hour == 9
        assert result > now  # Should be tomorrow since 9am already passed

    def test_daily_pm(self) -> None:
        """'daily at 3pm' → next 15:00 UTC."""
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
        result = parse_timing("daily at 3pm", from_time=now)
        assert result.hour == 15
        assert result > now

    def test_daily_am_before_hour(self) -> None:
        """'daily at 9am' when it's 8am → today at 9am."""
        now = datetime(2026, 6, 24, 8, 0, 0, tzinfo=UTC)
        result = parse_timing("daily at 9am", from_time=now)
        assert result.hour == 9
        assert result.day == now.day

    def test_iso_timestamp(self) -> None:
        """Test 3: ISO timestamp → parsed datetime."""
        result = parse_timing("2026-06-25T09:00:00")
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 25
        assert result.hour == 9

    def test_invalid_timing(self) -> None:
        """Invalid timing raises ValueError."""
        with pytest.raises(ValueError, match="Unparseable"):
            parse_timing("next tuesday")


class TestSQLiteCronStore:
    """Test 4: SQLite cron store operations."""

    @pytest.mark.asyncio
    async def test_insert_and_list(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()

        job = ScheduledJob(
            job_id="test-1",
            timing="5m",
            prompt="Say hello",
            platform="telegram",
            thread_id="tg-thread",
            next_run="2026-06-25T09:00:00",
        )
        await store.insert(job)

        active = await store.list_active()
        assert len(active) == 1
        assert active[0].job_id == "test-1"

        await store.close()

    @pytest.mark.asyncio
    async def test_update_status(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()

        job = ScheduledJob(
            job_id="test-2", timing="5m", prompt="task",
            platform="telegram", thread_id="t",
        )
        await store.insert(job)
        await store.update_status("test-2", JobStatus.DONE)

        active = await store.list_active()
        assert len(active) == 0  # DONE is not active

        all_jobs = await store.list_all()
        assert len(all_jobs) == 1
        assert all_jobs[0].status == JobStatus.DONE

        await store.close()

    @pytest.mark.asyncio
    async def test_cancel(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()

        job = ScheduledJob(
            job_id="test-3", timing="5m", prompt="task",
            platform="telegram", thread_id="t",
        )
        await store.insert(job)

        cancelled = await store.cancel("test-3")
        assert cancelled is True

        active = await store.list_active()
        assert len(active) == 0

        await store.close()


class TestCronScheduler:
    """Test 5-6: Scheduler operations."""

    @pytest.mark.asyncio
    async def test_schedule_and_list(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()
        scheduler = CronScheduler(store=store)

        result = await scheduler.schedule(timing="5m", prompt="Test task")
        assert result["status"] == "scheduled"
        assert "job_id" in result

        jobs = await scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["prompt"] == "Test task"

        await store.close()

    @pytest.mark.asyncio
    async def test_cancel_job(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()
        scheduler = CronScheduler(store=store)

        result = await scheduler.schedule(timing="5m", prompt="Cancel me")
        job_id = result["job_id"]

        cancel_result = await scheduler.cancel(job_id)
        assert cancel_result["status"] == "cancelled"

        jobs = await scheduler.list_jobs()
        assert jobs[0]["status"] == "cancelled"

        await store.close()


class TestToolsRegistered:
    """Test 7-8: Cron tools in registry."""

    def test_schedule_task_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "schedule_task" in tool_names

    def test_list_scheduled_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "list_scheduled" in tool_names

    def test_cancel_scheduled_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "cancel_scheduled" in tool_names


class TestSingleton:
    """Test cron scheduler singleton."""

    def test_set_get(self) -> None:
        store = SQLiteCronStore(":memory:")
        scheduler = CronScheduler(store=store)
        set_cron_scheduler(scheduler)
        assert get_cron_scheduler() is scheduler
        set_cron_scheduler(None)
        assert get_cron_scheduler() is None
