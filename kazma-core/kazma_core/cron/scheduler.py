"""In-process cron scheduler for autonomous agent actions.

The agent calls schedule_task(timing, prompt) as a tool.
The scheduler stores jobs in SQLite and fires them via
LangGraph invocations at the specified time.

Usage:
    store = SQLiteCronStore()
    await store.init()
    scheduler = CronScheduler(store=store, graph_builder=build_fn)
    await scheduler.start()
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# Module-level singleton
_cron_scheduler: CronScheduler | None = None


def set_cron_scheduler(scheduler: CronScheduler) -> None:
    global _cron_scheduler
    _cron_scheduler = scheduler


def get_cron_scheduler() -> CronScheduler | None:
    return _cron_scheduler


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledJob:
    job_id: str
    timing: str
    prompt: str
    platform: str
    thread_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    next_run: str | None = None
    last_result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "timing": self.timing,
            "prompt": self.prompt[:200],
            "platform": self.platform,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "next_run": self.next_run,
            "last_result": self.last_result[:200] if self.last_result else None,
        }


# ══════════════════════════════════════════════════════════════════════════
# Timing parser
# ══════════════════════════════════════════════════════════════════════════


def parse_timing(timing: str, from_time: datetime | None = None) -> datetime:
    """Parse human-readable timing into next run time.

    Supported formats:
        - "5m", "30m", "1h", "2h" (relative)
        - "daily at 9am", "daily at 3pm" (recurring)
        - ISO timestamp: "2026-06-25T09:00:00"

    Args:
        timing:     Timing string.
        from_time:  Base time for relative calculations (default: now).

    Returns:
        Absolute datetime for next run.

    Raises:
        ValueError: If timing cannot be parsed.
    """
    now = from_time or datetime.now(UTC)
    timing = timing.strip().lower()

    # Relative: "5m", "30m", "1h", "2h"
    match = re.match(r"^(\d+)(m|h)$", timing)
    if match:
        value, unit = int(match.group(1)), match.group(2)
        delta = timedelta(minutes=value) if unit == "m" else timedelta(hours=value)
        return now + delta

    # Recurring: "daily at 9am", "daily at 3pm"
    match = re.match(r"^daily at (\d{1,2})(am|pm)$", timing)
    if match:
        hour = int(match.group(1))
        if match.group(2) == "pm" and hour != 12:
            hour += 12
        if match.group(2) == "am" and hour == 12:
            hour = 0
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=UTC)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    # ISO timestamp
    try:
        dt = datetime.fromisoformat(timing)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass

    raise ValueError(f"Unparseable timing: '{timing}'. Use '5m', '1h', 'daily at 9am', or ISO timestamp.")


# ══════════════════════════════════════════════════════════════════════════
# SQLite Cron Store
# ══════════════════════════════════════════════════════════════════════════

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    job_id TEXT PRIMARY KEY,
    timing TEXT NOT NULL,
    prompt TEXT NOT NULL,
    platform TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    next_run TEXT,
    last_result TEXT
)
"""


class SQLiteCronStore:
    """Persist scheduled jobs in SQLite to survive restarts.

    Args:
        db_path: Path to the SQLite database.
    """

    def __init__(self, db_path: str = "kazma-data/cron.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize the database and create the table."""
        from pathlib import Path

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        logger.info("[CronStore] Initialized at %s", self._db_path)

    async def insert(self, job: ScheduledJob) -> None:
        """Insert a new scheduled job."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        await self._db.execute(
            "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, status, created_at, next_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job.job_id, job.timing, job.prompt, job.platform, job.thread_id,
             job.status.value, job.created_at, job.next_run),
        )
        await self._db.commit()

    async def list_active(self) -> list[ScheduledJob]:
        """List all pending/running jobs."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        async with self._db.execute(
            "SELECT job_id, timing, prompt, platform, thread_id, status, created_at, next_run, last_result "
            "FROM cron_jobs WHERE status IN ('pending', 'running')"
        ) as cursor:
            jobs = []
            async for row in cursor:
                jobs.append(ScheduledJob(
                    job_id=row[0], timing=row[1], prompt=row[2],
                    platform=row[3], thread_id=row[4],
                    status=JobStatus(row[5]), created_at=row[6],
                    next_run=row[7], last_result=row[8],
                ))
            return jobs

    async def list_all(self) -> list[ScheduledJob]:
        """List all jobs regardless of status."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        async with self._db.execute(
            "SELECT job_id, timing, prompt, platform, thread_id, status, created_at, next_run, last_result "
            "FROM cron_jobs ORDER BY created_at DESC"
        ) as cursor:
            jobs = []
            async for row in cursor:
                jobs.append(ScheduledJob(
                    job_id=row[0], timing=row[1], prompt=row[2],
                    platform=row[3], thread_id=row[4],
                    status=JobStatus(row[5]), created_at=row[6],
                    next_run=row[7], last_result=row[8],
                ))
            return jobs

    async def update_status(self, job_id: str, status: JobStatus) -> None:
        """Update a job's status."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET status = ? WHERE job_id = ?",
            (status.value, job_id),
        )
        await self._db.commit()

    async def update_result(self, job_id: str, result: str) -> None:
        """Update a job's last result."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET last_result = ? WHERE job_id = ?",
            (result[:5000], job_id),
        )
        await self._db.commit()

    async def update_next_run(self, job_id: str, next_run: str) -> None:
        """Update the next run time for a job."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET next_run = ?, status = 'pending' WHERE job_id = ?",
            (next_run, job_id),
        )
        await self._db.commit()

    async def cancel(self, job_id: str) -> bool:
        """Cancel a pending job. Returns True if found."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        cursor = await self._db.execute(
            "UPDATE cron_jobs SET status = 'cancelled' WHERE job_id = ? AND status = 'pending'",
            (job_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None


# ══════════════════════════════════════════════════════════════════════════
# Cron Scheduler
# ══════════════════════════════════════════════════════════════════════════


class CronScheduler:
    """Polls for due jobs and executes them via LangGraph.

    Args:
        store:         SQLiteCronStore for persistence.
        graph_builder: Callable that builds a compiled graph.
        checkpointer:  Optional checkpointer for graph state.
        poll_interval: Seconds between polls (default 30).
    """

    def __init__(
        self,
        store: SQLiteCronStore,
        graph_builder: Any = None,
        checkpointer: Any = None,
        poll_interval: float = 30.0,
    ) -> None:
        self._store = store
        self._graph_builder = graph_builder
        self._checkpointer = checkpointer
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._in_flight: set[str] = set()

    async def start(self) -> None:
        """Start the scheduler polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        logger.info("[CronScheduler] Started (poll_interval=%.0fs)", self._poll_interval)

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[CronScheduler] Stopped")

    async def schedule(
        self,
        timing: str,
        prompt: str,
        platform: str = "telegram",
        thread_id: str = "",
    ) -> dict[str, Any]:
        """Schedule a new task.

        Args:
            timing:   When to run: "5m", "1h", "daily at 9am", ISO timestamp.
            prompt:   Self-contained task description.
            platform: Delivery platform (default "telegram").
            thread_id: Parent thread for context.

        Returns:
            Dict with job_id, timing, next_run.
        """
        next_run = parse_timing(timing)
        job = ScheduledJob(
            job_id=f"cron-{uuid.uuid4().hex[:8]}",
            timing=timing,
            prompt=prompt,
            platform=platform,
            thread_id=thread_id,
            next_run=next_run.isoformat(),
        )
        await self._store.insert(job)
        logger.info("[CronScheduler] Scheduled %s for %s", job.job_id, job.next_run)
        return {
            "job_id": job.job_id,
            "timing": timing,
            "next_run": job.next_run,
            "status": "scheduled",
        }

    async def list_jobs(self) -> list[dict[str, Any]]:
        """List all scheduled jobs."""
        jobs = await self._store.list_all()
        return [j.to_dict() for j in jobs]

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a pending job."""
        cancelled = await self._store.cancel(job_id)
        if cancelled:
            return {"status": "cancelled", "job_id": job_id}
        return {"status": "not_found", "job_id": job_id}

    async def _loop(self) -> None:
        """Check every N seconds for due jobs."""
        while self._running:
            try:
                jobs = await self._store.list_active()
                now = datetime.now(UTC)

                for job in jobs:
                    if job.job_id in self._in_flight:
                        continue
                    if job.next_run and self._is_due(job.next_run, now):
                        self._in_flight.add(job.job_id)
                        asyncio.create_task(
                            self._execute(job),
                            name=f"cron-exec-{job.job_id}",
                        )
            except Exception:
                logger.exception("[CronScheduler] Poll error")

            await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _is_due(next_run_str: str, now: datetime) -> bool:
        """Check if a job is due."""
        try:
            next_run = datetime.fromisoformat(next_run_str)
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            return now >= next_run
        except (ValueError, TypeError):
            return False

    async def _execute(self, job: ScheduledJob) -> None:
        """Execute a scheduled job via LangGraph."""
        await self._store.update_status(job.job_id, JobStatus.RUNNING)
        logger.info("[CronScheduler] Executing %s: %.80s", job.job_id, job.prompt)

        try:
            if self._graph_builder is None:
                raise RuntimeError("No graph builder configured")

            graph = self._graph_builder()
            config = {"configurable": {"thread_id": f"cron-{job.job_id}"}}
            state: dict[str, Any] = {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are running a scheduled task. "
                            "Complete it autonomously and return a clear summary."
                        ),
                    },
                    {"role": "user", "content": job.prompt},
                ],
            }

            result = await asyncio.wait_for(
                graph.ainvoke(state, config=config),
                timeout=120.0,
            )

            # Extract summary
            messages = result.get("messages", [])
            summary = ""
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    summary = str(msg.get("content", ""))[:2000]
                    break

            await self._store.update_status(job.job_id, JobStatus.DONE)
            await self._store.update_result(job.job_id, summary)

            # Schedule next run for recurring jobs
            if job.timing.startswith("daily"):
                next_run = parse_timing(job.timing)
                await self._store.update_next_run(job.job_id, next_run.isoformat())
                logger.info("[CronScheduler] Recurring job %s rescheduled for %s", job.job_id, next_run)

            # Deliver result
            await self._deliver(job, summary)
            logger.info("[CronScheduler] %s completed", job.job_id)

        except TimeoutError:
            logger.warning("[CronScheduler] %s timed out", job.job_id)
            await self._store.update_status(job.job_id, JobStatus.FAILED)
            await self._store.update_result(job.job_id, "Timed out after 120s")

        except Exception as exc:
            logger.exception("[CronScheduler] %s failed", job.job_id)
            await self._store.update_status(job.job_id, JobStatus.FAILED)
            await self._store.update_result(job.job_id, f"Error: {str(exc)[:500]}")
        finally:
            self._in_flight.discard(job.job_id)

    async def _deliver(self, job: ScheduledJob, text: str) -> None:
        """Send result to the user via the original platform."""
        try:
            from kazma_core.tools.send_message import send_message

            target_id = job.thread_id or f"{job.platform}:unknown"
            await send_message(target_id, text, backend=job.platform)
        except Exception as exc:
            logger.warning("[CronScheduler] Failed to deliver result for %s: %s", job.job_id, exc)
