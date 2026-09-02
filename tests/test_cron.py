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

Plus operator-timezone support (audit M14): clock-time anchors and naive ISO
strings resolve via ConfigStore ``cron.timezone`` → env ``KAZMA_TZ`` → UTC.
"""

from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta, timezone


def _zone_available(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
        return True
    except Exception:
        return False


requires_tzdb = pytest.mark.skipif(
    not _zone_available("Asia/Kuwait"),
    reason="IANA tz database unavailable (install 'tzdata' on Windows)",
)


@pytest.fixture()
def _no_tz_env(monkeypatch):
    """Ensure no operator timezone leaks in from the developer machine."""
    monkeypatch.delenv("KAZMA_TZ", raising=False)
    yield
from kazma_core.cron.scheduler import (
    CronScheduler,
    JobStatus,
    ScheduledJob,
    SQLiteCronStore,
    compose_cron_delivery,
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


class TestOperatorTimezone:
    """Audit M14: clock-time anchors + naive ISO honor the operator zone."""

    @requires_tzdb
    def test_daily_uses_operator_timezone(self, monkeypatch) -> None:
        """'daily at 9am' in Asia/Kuwait (UTC+3, no DST) → 06:00Z occurrence."""
        monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)  # 13:00 Kuwait
        result = parse_timing("daily at 9am", from_time=now)
        assert result == datetime(2026, 12, 2, 6, 0, tzinfo=UTC)

    @requires_tzdb
    def test_naive_iso_uses_operator_timezone(self, monkeypatch) -> None:
        """Naive ISO attaches the operator zone instead of UTC."""
        monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
        result = parse_timing("2026-12-01T09:00:00")
        assert result == datetime(2026, 12, 1, 6, 0, tzinfo=UTC)

    def test_explicit_offset_iso_honored_as_is(self, _no_tz_env, monkeypatch) -> None:
        """Aware ISO strings win over any configured operator zone."""
        monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
        result = parse_timing("2026-12-01T09:00:00+03:00")
        assert result == datetime(2026, 12, 1, 9, 0, tzinfo=timezone(timedelta(hours=3)))

    @requires_tzdb
    def test_annotate_fire_times_includes_kuwait_local(self, monkeypatch) -> None:
        """Compact 5m used to return UTC ISO only — agent said '01:15 UTC'."""
        from kazma_core.cron.scheduler import annotate_fire_times

        monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
        from zoneinfo import ZoneInfo

        monkeypatch.setattr(
            "kazma_core.cron.scheduler.resolve_cron_timezone_name",
            lambda: ("Asia/Kuwait", "env"),
        )
        monkeypatch.setattr(
            "kazma_core.cron.scheduler.get_cron_timezone",
            lambda: ZoneInfo("Asia/Kuwait"),
        )
        dt = datetime(2026, 9, 2, 1, 15, 19, tzinfo=UTC)
        out = annotate_fire_times(dt)
        assert out["timezone"] == "Asia/Kuwait"
        assert "01:15:19 UTC" in out["next_run_utc"]
        assert "04:15:19" in out["next_run_local"]
        assert "Asia/Kuwait" in out["next_run_local"]

    def test_default_utc_when_unset(self, _no_tz_env) -> None:
        """No config key and no env → UTC behavior unchanged (back-compat)."""
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        assert parse_timing("daily at 9am", from_time=now) == datetime(
            2026, 12, 2, 9, 0, tzinfo=UTC
        )
        assert parse_timing("2026-12-01T09:00:00") == datetime(
            2026, 12, 1, 9, 0, tzinfo=UTC
        )

    @requires_tzdb
    def test_configstore_key_wins_over_env(self, _no_tz_env, monkeypatch) -> None:
        """Read order: ConfigStore cron.timezone beats KAZMA_TZ."""
        from kazma_core.config_store import get_config_store

        monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
        get_config_store().set("cron.timezone", "UTC")
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        assert parse_timing("daily at 9am", from_time=now) == datetime(
            2026, 12, 2, 9, 0, tzinfo=UTC
        )

    @requires_tzdb
    def test_daily_west_of_utc_does_not_skip_local_evening(self, monkeypatch) -> None:
        """Audit M-12: 01:00Z + America/New_York + 'daily at 10pm' is still tonight.

        ``now.replace(..., tzinfo=tz)`` used to stamp 22:00 on the UTC date
        (Sept 2 22:00 NY) instead of Sept 1 22:00 NY.
        """
        monkeypatch.setenv("KAZMA_TZ", "America/New_York")
        now = datetime(2026, 9, 2, 1, 0, tzinfo=UTC)  # Sept 1 21:00 EDT
        result = parse_timing("daily at 10pm", from_time=now)
        # 22:00 America/New_York on Sept 1 = 02:00Z Sept 2
        assert result.astimezone(UTC) == datetime(2026, 9, 2, 2, 0, tzinfo=UTC)

    def test_invalid_zone_falls_back_to_utc(self, _no_tz_env, monkeypatch) -> None:
        """Unresolvable name never raises — falls back to UTC."""
        monkeypatch.setenv("KAZMA_TZ", "Mars/Olympus_Mons")
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        assert parse_timing("daily at 9am", from_time=now) == datetime(
            2026, 12, 2, 9, 0, tzinfo=UTC
        )


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

    @pytest.mark.asyncio
    async def test_reschedule_updates_prompt_and_timing(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()
        scheduler = CronScheduler(store=store)

        result = await scheduler.schedule(timing="5m", prompt="Original prompt")
        job_id = result["job_id"]

        res = await scheduler.reschedule(job_id, timing="1h", prompt="Edited prompt")
        assert res["status"] == "rescheduled"
        assert res["next_run"]

        jobs = await scheduler.list_jobs()
        assert jobs[0]["prompt"] == "Edited prompt"
        assert jobs[0]["timing"] == "1h"
        assert jobs[0]["status"] == "pending"

        await store.close()

    @pytest.mark.asyncio
    async def test_reschedule_not_found_and_bad_timing(self) -> None:
        store = SQLiteCronStore(":memory:")
        await store.init()
        scheduler = CronScheduler(store=store)

        missing = await scheduler.reschedule("cron-doesnotexist", timing="5m")
        assert missing["status"] == "not_found"

        result = await scheduler.schedule(timing="5m", prompt="x")
        bad = await scheduler.reschedule(result["job_id"], timing="not a time")
        assert bad["status"] == "error"

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

    def test_edit_scheduled_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "edit_scheduled" in tool_names


class TestComposeCronDelivery:
    """Fire-time Telegram body is the reminder, not a ```plan dump."""

    def test_prose_wins_over_prompt(self) -> None:
        assert compose_cron_delivery("Posted the tweet.", "post tweet 7") == (
            "Posted the tweet."
        )

    def test_plan_only_falls_back_to_quoted_reminder(self) -> None:
        plan = (
            "```plan\n"
            "- Send the test message to Mubder's Telegram\n"
            "- List scheduled jobs\n"
            "```"
        )
        prompt = (
            'Send a test message to Mubder via Telegram (dispatch_notification '
            'to 1804015016) saying "Test schedule fired successfully '
            '(2-minute test #2)". Then check the scheduled jobs.'
        )
        out = compose_cron_delivery(plan, prompt)
        assert "Test schedule fired successfully (2-minute test #2)" in out
        assert "```plan" not in out
        assert "dispatch_notification" not in out

    def test_plan_plus_prose_keeps_prose(self) -> None:
        text = "```plan\n- do it\n```\n\nAll set."
        assert compose_cron_delivery(text, "ignored mission") == "All set."

    def test_plain_prompt_when_no_quotes(self) -> None:
        out = compose_cron_delivery("", "Drink water")
        assert "Drink water" in out
        assert "```" not in out

    def test_empty_falls_back_to_fired(self) -> None:
        assert compose_cron_delivery("", "") == "Scheduled task fired."


class TestSingleton:
    """Test cron scheduler singleton."""

    def test_set_get(self) -> None:
        store = SQLiteCronStore(":memory:")
        scheduler = CronScheduler(store=store)
        set_cron_scheduler(scheduler)
        assert get_cron_scheduler() is scheduler
        set_cron_scheduler(None)
        assert get_cron_scheduler() is None
