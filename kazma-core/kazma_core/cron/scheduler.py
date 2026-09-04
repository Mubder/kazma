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
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

__all__ = [
    "CronScheduler",
    "JobStatus",
    "SQLiteCronStore",
    "ScheduledJob",
    "get_cron_scheduler",
    "parse_timing",
    "set_cron_scheduler",
    "compose_cron_delivery",
]

logger = logging.getLogger(__name__)

# ── Cron-parent context (2026-08-27 approval-delivery incident) ────────
# Bound around each job execution so tools running INSIDE a cron-fired
# turn can inherit the firing job's delivery identity. The gateway's
# delivery-target ContextVar is empty on this path (no inbound user
# message), which is how agent-rescheduled jobs were born targetless.
from contextvars import ContextVar as _ContextVar

_cron_parent_ctx: _ContextVar[dict[str, str] | None] = _ContextVar(
    "kazma_cron_parent", default=None
)


def get_cron_parent() -> dict[str, str] | None:
    """The firing cron job's identity, when the current turn is cron-fired.

    Keys: job_id, delivery_target, platform, thread_id. None outside a
    cron execution (normal gateway turns).
    """
    val = _cron_parent_ctx.get()
    if isinstance(val, dict) and any(str(val.get(k) or "").strip() for k in val):
        return val
    return None

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
    tenant_id: str = "default"
    # Platform-prefixed delivery target (e.g. "telegram:<chat_id>") captured at
    # schedule time so `_deliver` can route the result back to the originating
    # chat long after the SessionStore row has been TTL-evicted (5-min default).
    # Empty for legacy rows scheduled before this field existed; `_deliver`
    # falls back to the thread_id in that case.
    delivery_target: str = ""
    failure_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = {
            "job_id": self.job_id,
            "timing": self.timing,
            "prompt": self.prompt[:200],
            "platform": self.platform,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "next_run": self.next_run,
            "last_result": self.last_result[:200] if self.last_result else None,
            "tenant_id": self.tenant_id,
            "delivery_target": self.delivery_target,
            "failure_count": self.failure_count,
        }
        out.update(annotate_fire_times(self.next_run))
        return out


# ══════════════════════════════════════════════════════════════════════════
# Timing parser
# ══════════════════════════════════════════════════════════════════════════

# Warn once per process about an unresolvable timezone name.
_tz_warned = False


def resolve_cron_timezone_name() -> tuple[str, str]:
    """Return ``(operator_zone_name_or_'UTC', source)`` for the cron zone.

    Source is ``"config"`` (ConfigStore ``cron.timezone``), ``"env"``
    (``KAZMA_TZ``), or ``"default"``. Does NOT validate — pair with
    :func:`get_cron_timezone` (which falls back to UTC on unresolvable
    names) or an explicit ``ZoneInfo(name)`` probe at the validation site
    (e.g. the Settings API rejects typos with a 400 instead of silently
    running UTC).
    """
    name = ""
    try:
        from kazma_core.config_store import get_config_store

        name = str(get_config_store().get("cron.timezone") or "").strip()
    except Exception:
        pass
    if name:
        return name, "config"
    name = os.environ.get("KAZMA_TZ", "").strip()
    if name:
        return name, "env"
    return "UTC", "default"


def get_cron_timezone() -> tzinfo:
    """Resolve the operator-configured timezone for clock-time anchors.

    Read order (audit M14): ConfigStore ``cron.timezone`` → env ``KAZMA_TZ``
    → UTC. Clock-time anchors ("daily at 9am") and NAIVE ISO timestamps are
    interpreted in this zone instead of assuming UTC — the previous UTC-only
    behavior made "9am" fire at noon for a UTC+3 operator. Explicit-offset
    ISO strings ("2026-12-01T09:00:00+03:00") are honored as-is.

    Live-resolved on every call (mirrors ``get_hitl_config``) so a Settings
    change takes effect on the next schedule/reschedule without a restart.
    An unresolvable name falls back to UTC with a warn-once log (never
    raises). Requires the stdlib ``zoneinfo`` database (the ``tzdata`` pip
    package on Windows).
    """
    global _tz_warned

    name, _source = resolve_cron_timezone_name()
    if name == "UTC":
        # Both the default AND an operator explicitly asking for UTC land
        # here; nothing to warn about either way.
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:
        if not _tz_warned:
            logger.warning(
                "[CronScheduler] invalid cron.timezone %r — falling back to UTC "
                "(IANA name required, e.g. 'Asia/Kuwait'; install 'tzdata' on Windows)",
                name,
            )
            _tz_warned = True
        return UTC


def annotate_fire_times(next_run: datetime | str | None) -> dict[str, str]:
    """UTC + operator-local strings so the model does not report only UTC.

    ``cron.timezone`` (e.g. Asia/Kuwait) already anchors ``daily at 9am``.
    Compact ``5m``/ISO results were still returned as UTC ISO only, so the
    agent said "fires at 01:15:19 UTC" even when the operator zone was Kuwait.
    """
    if next_run is None or next_run == "":
        return {}
    try:
        dt = next_run if isinstance(next_run, datetime) else datetime.fromisoformat(str(next_run))
    except (TypeError, ValueError):
        return {}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    name, _src = resolve_cron_timezone_name()
    tz = get_cron_timezone()
    utc = dt.astimezone(UTC)
    local = dt.astimezone(tz)
    utc_s = utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    local_s = local.strftime("%Y-%m-%d %H:%M:%S") + f" {name}"
    return {
        "next_run_utc": utc_s,
        "next_run_local": local_s,
        "timezone": name,
    }


_QUOTED_RE = re.compile(r'["“”](.+?)["“”]', re.DOTALL)


def _reminder_from_prompt(prompt: str | None) -> str:
    """Reminder body from the stored prompt.

    Models often wrap a reminder as ``send via Telegram saying "X"``. Prefer
    the quoted message; otherwise the prompt itself.
    """
    p = str(prompt or "").strip()
    if not p:
        return ""
    quoted = [q.strip() for q in _QUOTED_RE.findall(p) if q.strip()]
    for q in reversed(quoted):
        if 4 <= len(q) <= 800:
            return q
    return p


def compose_cron_delivery(summary: str | None, prompt: str | None) -> str:
    """User-facing text for ``_deliver``.

    Cron child graphs ``auto_deny`` outbound tools (``send_message`` /
    ``dispatch_notification``). The fire-time agent then dumps a `````plan``
    and that used to be the Telegram alert (2026-09-02 test #2). Prefer the
    agent's prose; if there is none, send the reminder from the stored prompt.
    """
    from kazma_core.agent.plan_fence import prose_for_user

    prose = (prose_for_user(summary) or "").strip()
    if prose:
        return prose[:4000]
    body = _reminder_from_prompt(prompt)
    if body:
        return f"⏰ Reminder\n\n{body}"[:4000]
    leftover = str(summary or "").strip()
    return (leftover or "Scheduled task fired.")[:4000]


def parse_timing(timing: str, from_time: datetime | None = None) -> datetime:
    """Parse human-readable timing into next run time.

    Supported formats:
        - "5m", "30m", "1h", "2h" (relative)
        - "daily at 9am", "daily at 3pm" (recurring — anchored in the
          operator timezone, see :func:`get_cron_timezone`)
        - ISO timestamp: "2026-06-25T09:00:00" (naive → operator timezone;
          explicit-offset strings honored as-is)

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
    tz = get_cron_timezone()

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
        # The regex admits 1-99; "13pm" → hour 25 → now.replace(hour=25)
        # raises an opaque ValueError. Validate the 1-12 am/pm range up front
        # so the user gets the intended "Unparseable timing" error (audit).
        if not (1 <= hour <= 12):
            raise ValueError(f"Unparseable timing: '{timing}'. Hour must be 1-12 with am/pm.")
        if match.group(2) == "pm" and hour != 12:
            hour += 12
        if match.group(2) == "am" and hour == 12:
            hour = 0
        # Anchor in the operator zone via astimezone, not replace(tzinfo=)
        # (audit M-12): replace() on a UTC instant west of UTC can skip a
        # local calendar day ("daily at 10pm" at 01:00Z → 22:00 same UTC day
        # instead of yesterday evening).
        local_now = now.astimezone(tz)
        next_run = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= local_now:
            next_run += timedelta(days=1)
        return next_run

    # ISO timestamp
    try:
        dt = datetime.fromisoformat(timing)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
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
    last_result TEXT,
    failure_count INTEGER DEFAULT 0
)
"""


class SQLiteCronStore:
    """Persist scheduled jobs in SQLite to survive restarts.

    Args:
        db_path: Path to the SQLite database.
    """

    def __init__(self, db_path: str | None = None) -> None:
        # Default resolves via paths.data_dir() (walks up to the
        # pyproject.toml root) instead of a bare CWD-relative literal, so a
        # server started from a subdirectory opens the SAME cron.db instead
        # of silently minting an empty one (deep-audit 2026-08-19, #14).
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = str(data_dir() / "cron.db")
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize the database and create the table."""
        from pathlib import Path

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        from kazma_core.config_store import apply_sqlite_pragmas_async

        await apply_sqlite_pragmas_async(self._db)
        await self._db.execute(_CREATE_TABLE)
        # Idempotent tenant_id column for multi-tenant UI filtering
        try:
            await self._db.execute(
                "ALTER TABLE cron_jobs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
            await self._db.commit()
        except Exception as exc:
            # "duplicate column name" is the expected idempotency path; anything
            # else (locked/corrupt DB, permission error) must be visible.
            if "duplicate column" not in str(exc).lower():
                logger.warning("[CronStore] tenant_id migration failed: %s", exc)
        # Idempotent delivery_target column for direct result routing
        # (the chat a reminder was scheduled from). Empty for legacy rows.
        try:
            await self._db.execute(
                "ALTER TABLE cron_jobs ADD COLUMN delivery_target TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                logger.warning("[CronStore] delivery_target migration failed: %s", exc)
        # Idempotent failure_count column for retry budgeting (audit H3)
        try:
            await self._db.execute(
                "ALTER TABLE cron_jobs ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.commit()
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                logger.warning("[CronStore] failure_count migration failed: %s", exc)
        await self._db.commit()
        logger.info("[CronStore] Initialized at %s", self._db_path)

    async def insert(self, job: ScheduledJob) -> None:
        """Insert a new scheduled job."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        await self._db.execute(
            "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, "
            "status, created_at, next_run, tenant_id, delivery_target, failure_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.job_id, job.timing, job.prompt, job.platform, job.thread_id,
             job.status.value, job.created_at, job.next_run, job.tenant_id,
             job.delivery_target, getattr(job, "failure_count", 0)),
        )
        await self._db.commit()

    def _row_to_job(self, row: Any) -> ScheduledJob:
        tenant = "default"
        if len(row) > 9 and row[9] is not None:
            tenant = str(row[9])
        delivery_target = ""
        if len(row) > 10 and row[10] is not None:
            delivery_target = str(row[10])
        failure_count = 0
        if len(row) > 11 and row[11] is not None:
            try:
                failure_count = int(row[11])
            except (ValueError, TypeError):
                failure_count = 0
        return ScheduledJob(
            job_id=row[0],
            timing=row[1],
            prompt=row[2],
            platform=row[3],
            thread_id=row[4],
            status=JobStatus(row[5]),
            created_at=row[6],
            next_run=row[7],
            last_result=row[8],
            tenant_id=tenant,
            delivery_target=delivery_target,
            failure_count=failure_count,
        )

    async def list_active(self) -> list[ScheduledJob]:
        """List all pending/running jobs (all tenants — executor path)."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        async with self._db.execute(
            "SELECT job_id, timing, prompt, platform, thread_id, status, created_at, "
            "next_run, last_result, tenant_id, delivery_target, failure_count "
            "FROM cron_jobs WHERE status IN ('pending', 'running')"
        ) as cursor:
            jobs = []
            async for row in cursor:
                jobs.append(self._row_to_job(row))
            return jobs

    async def list_all(
        self,
        *,
        tenant_id: str | None = None,
        limit: int | None = 500,
    ) -> list[ScheduledJob]:
        """List jobs; optionally filter by tenant for multi-user UI."""
        if self._db is None:
            raise RuntimeError("CronDB not initialized")
        limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""
        if tenant_id:
            sql = (
                "SELECT job_id, timing, prompt, platform, thread_id, status, created_at, "
                "next_run, last_result, tenant_id, delivery_target, failure_count "
                f"FROM cron_jobs WHERE tenant_id = ? ORDER BY created_at DESC{limit_clause}"
            )
            args: tuple[Any, ...] = (tenant_id,)
        else:
            sql = (
                "SELECT job_id, timing, prompt, platform, thread_id, status, created_at, "
                "next_run, last_result, tenant_id, delivery_target, failure_count "
                f"FROM cron_jobs ORDER BY created_at DESC{limit_clause}"
            )
            args = ()
        async with self._db.execute(sql, args) as cursor:
            jobs = []
            async for row in cursor:
                jobs.append(self._row_to_job(row))
            return jobs

    async def purge_terminal_jobs(
        self,
        *,
        older_than_days: int = 14,
        keep_last: int = 500,
    ) -> int:
        """Purge terminal jobs (completed/failed/cancelled) older than retention window.

        Also enforces a global ceiling by keeping at most `keep_last` terminal rows.
        Returns the number of deleted rows.
        """
        if self._db is None:
            return 0
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        deleted = 0
        try:
            # 1. Purge terminal jobs older than cutoff date
            cursor = await self._db.execute(
                "DELETE FROM cron_jobs WHERE status IN ('done', 'completed', 'failed', 'cancelled') "
                "AND created_at < ?",
                (cutoff,),
            )
            deleted += cursor.rowcount or 0

            # 2. If terminal rows still exceed keep_last, delete excess oldest
            cursor2 = await self._db.execute(
                "DELETE FROM cron_jobs WHERE job_id IN ("
                "  SELECT job_id FROM cron_jobs "
                "  WHERE status IN ('done', 'completed', 'failed', 'cancelled') "
                "  ORDER BY created_at DESC LIMIT -1 OFFSET ?"
                ")",
                (keep_last,),
            )
            deleted += cursor2.rowcount or 0
            if deleted > 0:
                await self._db.commit()
                logger.info("[CronStore] Purged %d old terminal job rows", deleted)
        except Exception:
            logger.debug("[CronStore] purge_terminal_jobs error", exc_info=True)
        return deleted

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

    async def bump_failure(self, job_id: str) -> int:
        """Increment failure_count for a job and return the updated count."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET failure_count = failure_count + 1 WHERE job_id = ?",
            (job_id,),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT failure_count FROM cron_jobs WHERE job_id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 1

    async def reset_failure(self, job_id: str) -> None:
        """Reset failure_count to 0 on success."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET failure_count = 0 WHERE job_id = ?",
            (job_id,),
        )
        await self._db.commit()

    async def update_job(
        self,
        job_id: str,
        *,
        timing: str | None = None,
        prompt: str | None = None,
        next_run: str | None = None,
    ) -> bool:
        """Edit a job's timing/prompt/next_run in place (only supplied fields).

        Returns True when the job existed and was updated. Used by the
        reschedule path (chat ``edit_scheduled`` + the /scheduled page) so a
        job can be modified without recreating it.
        """
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        sets: list[str] = []
        params: list[Any] = []
        if timing is not None:
            sets.append("timing = ?")
            params.append(timing)
        if prompt is not None:
            sets.append("prompt = ?")
            params.append(prompt)
        if next_run is not None:
            sets.append("next_run = ?")
            params.append(next_run)
            sets.append("status = 'pending'")
        if not sets:
            return False
        params.append(job_id)
        cursor = await self._db.execute(
            f"UPDATE cron_jobs SET {', '.join(sets)} WHERE job_id = ?",
            tuple(params),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def job_exists(self, job_id: str) -> bool:
        """True when a job row exists (any status)."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        async with self._db.execute(
            "SELECT 1 FROM cron_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def update_delivery_target(self, job_id: str, delivery_target: str) -> None:
        """Repair a job's delivery_target (self-heal from a sibling job)."""
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        await self._db.execute(
            "UPDATE cron_jobs SET delivery_target = ? WHERE job_id = ?",
            (str(delivery_target or "").strip(), job_id),
        )
        await self._db.commit()

    async def sibling_delivery_target(self, thread_id: str) -> str:
        """A VALID delivery_target from any other job on the same thread.

        Used by _deliver's repair chain: rescheduled/legacy rows born with
        an empty or malformed target adopt a sibling's routing — the whole
        batch shares one conversation (2026-08-27 incident).
        """
        if self._db is None or not str(thread_id or "").strip():
            return ""
        try:
            rows = await self._db.execute_fetchall(
                "SELECT delivery_target FROM cron_jobs "
                "WHERE thread_id = ? AND delivery_target LIKE '%:%' "
                "ORDER BY created_at DESC LIMIT 1",
                (str(thread_id),),
            )
            for row in rows or []:
                cand = str(row[0] or "").strip()
                if ":" in cand:
                    return cand
        except Exception:
            pass
        return ""

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
        max_concurrent: int = 4,
    ) -> None:
        self._store = store
        self._graph_builder = graph_builder
        self._checkpointer = checkpointer
        self._poll_interval = poll_interval
        self._max_concurrent = max(1, int(max_concurrent))
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._in_flight: set[str] = set()
        # Strong refs to exec tasks — the loop holds only weak refs, and a
        # GC'd task left its job id stuck in _in_flight (job shown RUNNING
        # until restart, where recovery marks it FAILED).
        self._exec_tasks: set[asyncio.Task] = set()
        self._sem: asyncio.Semaphore | None = None
        self._poll_count: int = 0

    async def start(self) -> None:
        """Start the scheduler polling loop."""
        if self._running:
            return
        self._running = True
        self._sem = asyncio.Semaphore(self._max_concurrent)
        # Purge stale terminal jobs to bound table size
        try:
            await self._store.purge_terminal_jobs()
        except Exception:
            logger.debug("[CronScheduler] startup purge skipped", exc_info=True)
        # Audit H10: recover jobs left RUNNING after crash
        try:
            await self._recover_stale_running()
        except Exception:
            logger.exception("[CronScheduler] stale RUNNING recovery failed")
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        logger.info(
            "[CronScheduler] Started (poll_interval=%.0fs max_concurrent=%d)",
            self._poll_interval,
            self._max_concurrent,
        )

    async def _recover_stale_running(self) -> None:
        """Mark leftover RUNNING jobs as FAILED so they do not storm on restart."""
        try:
            jobs = await self._store.list_all()
        except Exception:
            jobs = await self._store.list_active()
        for job in jobs:
            status = getattr(job, "status", None)
            if status == JobStatus.RUNNING or getattr(status, "value", None) == "running":
                # A recurring job (e.g. "daily at 09:00") interrupted mid-fire
                # must NOT be stranded as FAILED — list_active() filters it out
                # and the user stops receiving the reminder forever (audit
                # finding). Reschedule it to its next run instead.
                timing = getattr(job, "timing", "") or ""
                if timing.strip().lower().startswith("daily"):
                    try:
                        nr = parse_timing(timing)
                        await self._store.update_next_run(job.job_id, nr.isoformat())
                        logger.warning(
                            "[CronScheduler] recovered stale RUNNING daily job %s → rescheduled to %s",
                            job.job_id, nr.isoformat(),
                        )
                        continue
                    except Exception:
                        logger.warning(
                            "[CronScheduler] could not reschedule daily job %s; marking FAILED",
                            job.job_id, exc_info=True,
                        )
                await self._store.update_status(job.job_id, JobStatus.FAILED)
                try:
                    await self._store.update_result(
                        job.job_id,
                        "Marked failed on scheduler restart (stale RUNNING)",
                    )
                except Exception:
                    pass
                logger.warning(
                    "[CronScheduler] recovered stale RUNNING job %s → FAILED",
                    job.job_id,
                )

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
        delivery_target: str = "",
    ) -> dict[str, Any]:
        """Schedule a new task.

        Args:
            timing:   When to run: "5m", "1h", "daily at 9am", ISO timestamp.
            prompt:   Self-contained task description.
            platform: Delivery platform (default "telegram").
            thread_id: Parent thread for context.
            delivery_target: Platform-prefixed target (e.g. "telegram:12345")
                captured at schedule time so the result reaches the chat that
                asked for the reminder. Survives the long gap between schedule
                and fire, which the SessionStore row does not (5-min TTL).
                Empty → ``_deliver`` falls back to ``thread_id``.

        Returns:
            Dict with job_id, timing, next_run.
        """
        next_run = parse_timing(timing)
        try:
            from kazma_core.tenant_isolation import require_tenant_id

            tid = require_tenant_id()
        except Exception:
            tid = "default"
        job = ScheduledJob(
            job_id=f"cron-{uuid.uuid4().hex[:8]}",
            timing=timing,
            prompt=prompt,
            platform=platform,
            thread_id=thread_id,
            next_run=next_run.isoformat(),
            tenant_id=tid,
            delivery_target=delivery_target,
        )
        await self._store.insert(job)
        logger.info(
            "[CronScheduler] Scheduled %s for %s tenant=%s target=%s",
            job.job_id,
            job.next_run,
            tid,
            delivery_target or "(none)",
        )
        out = {
            "job_id": job.job_id,
            "timing": timing,
            "next_run": job.next_run,
            "status": "scheduled",
            "tenant_id": tid,
            "delivery_target": delivery_target,
        }
        out.update(annotate_fire_times(next_run))
        return out

    async def list_jobs(self) -> list[dict[str, Any]]:
        """List scheduled jobs (scoped to current tenant when multi-user/prod)."""
        tenant_filter: str | None = None
        try:
            from kazma_core.tenant_isolation import multi_user_or_production, require_tenant_id

            if multi_user_or_production():
                tenant_filter = require_tenant_id()
        except Exception:
            pass
        jobs = await self._store.list_all(tenant_id=tenant_filter)
        return [j.to_dict() for j in jobs]

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a pending job."""
        cancelled = await self._store.cancel(job_id)
        if cancelled:
            return {"status": "cancelled", "job_id": job_id}
        return {"status": "not_found", "job_id": job_id}

    async def reschedule(
        self,
        job_id: str,
        *,
        timing: str | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """Edit a scheduled job in place (its timing and/or prompt).

        Re-parses ``timing`` (when supplied) into a fresh ``next_run`` and
        resets the job to ``pending`` so the poll loop picks it up. Returns
        ``not_found`` when the job does not exist. Mirrors the chat
        ``edit_scheduled`` tool and backs the /scheduled page edit action.
        """
        if not await self._store.job_exists(job_id):
            return {"status": "not_found", "job_id": job_id}

        next_run: str | None = None
        if timing:
            try:
                next_run = parse_timing(timing).isoformat()
            except ValueError as exc:
                return {"status": "error", "job_id": job_id, "error": str(exc)}

        await self._store.update_job(
            job_id,
            timing=timing or None,
            prompt=prompt or None,
            next_run=next_run,
        )
        logger.info(
            "[CronScheduler] Rescheduled %s (timing=%r prompt_edited=%s next_run=%s)",
            job_id, timing or "(unchanged)", bool(prompt), next_run or "(unchanged)",
        )
        return {
            "status": "rescheduled",
            "job_id": job_id,
            "timing": timing or "",
            "next_run": next_run or "",
        }

    async def _loop(self) -> None:
        """Check every N seconds for due jobs."""
        while self._running:
            try:
                from kazma_core.shutdown import is_shutting_down

                if is_shutting_down():
                    logger.info("[CronScheduler] shutdown signal — exiting poll loop")
                    self._running = False
                    break
            except Exception:
                pass

            try:
                jobs = await self._store.list_active()
                now = datetime.now(UTC)
                sem = self._sem or asyncio.Semaphore(self._max_concurrent)

                for job in jobs:
                    if not self._running:
                        break
                    if job.job_id in self._in_flight:
                        continue
                    if len(self._in_flight) >= self._max_concurrent:
                        break
                    if job.next_run and self._is_due(job.next_run, now):
                        self._in_flight.add(job.job_id)
                        exec_task = asyncio.create_task(
                            self._execute_bounded(job, sem),
                            name=f"cron-exec-{job.job_id}",
                        )
                        self._exec_tasks.add(exec_task)

                        def _exec_done(t: asyncio.Task, _jid: str = job.job_id) -> None:
                            self._exec_tasks.discard(t)
                            # Clear _in_flight even if _execute exited
                            # without its own discard (line above).
                            self._in_flight.discard(_jid)

                        exec_task.add_done_callback(_exec_done)
            except Exception:
                logger.exception("[CronScheduler] Poll error")

            self._poll_count += 1
            if self._poll_count % 120 == 0:
                try:
                    await self._store.purge_terminal_jobs()
                except Exception:
                    pass

            await asyncio.sleep(self._poll_interval)

    async def _execute_bounded(self, job: ScheduledJob, sem: asyncio.Semaphore) -> None:
        """Run job under the global concurrency semaphore."""
        async with sem:
            await self._execute(job)

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

        # Bind the cron-parent context for the whole execution: jobs the
        # agent RESCHEDULES from inside this turn inherit this job's
        # delivery_target/platform instead of being born targetless (the
        # 2026-08-27 incident — "Rescheduled batch job N/8" rows carried an
        # empty delivery_target, so _deliver fell back to the bare thread
        # UUID and the gateway rejected every result message).
        parent_token = _cron_parent_ctx.set(
            {
                "job_id": job.job_id,
                "delivery_target": job.delivery_target or "",
                "platform": job.platform or "",
                "thread_id": job.thread_id or "",
            }
        )
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
                            "Do NOT call send_message or dispatch_notification — "
                            "the scheduler delivers your final answer to the user's "
                            "chat. Write the user-facing reminder or result as plain "
                            "text. Do not emit a ```plan fence unless you will also "
                            "call tools this graph can run (read/search). Outbound "
                            "send tools are auto-denied on this child graph."
                        ),
                    },
                    {"role": "user", "content": job.prompt},
                ],
            }

            from kazma_core.agent.turn import run_agent_turn

            _turn = await asyncio.wait_for(
                run_agent_turn(
                    graph=graph,
                    thread_id=str(config["configurable"]["thread_id"]),
                    state=state,
                    config=config,
                    persist=False,
                ),
                timeout=120.0,
            )
            summary = (_turn.text or "")[:2000]

            await self._store.update_result(job.job_id, summary)
            await self._finalize(job, failed=False)

            # Deliver result
            await self._deliver(job, summary)
            logger.info("[CronScheduler] %s completed", job.job_id)

        except TimeoutError:
            logger.warning("[CronScheduler] %s timed out", job.job_id)
            await self._store.update_result(job.job_id, "Timed out after 120s")
            await self._finalize(job, failed=True)

        except Exception as exc:
            logger.exception("[CronScheduler] %s failed", job.job_id)
            await self._store.update_result(job.job_id, f"Error: {str(exc)[:500]}")
            await self._finalize(job, failed=True)
        finally:
            _cron_parent_ctx.reset(parent_token)
            self._in_flight.discard(job.job_id)

    async def _finalize(self, job: ScheduledJob, failed: bool = False) -> None:
        """Handle job state transition and retry budgeting for recurring jobs."""
        timing = (job.timing or "").strip().lower()
        if timing.startswith("daily"):
            if not failed:
                await self._store.update_status(job.job_id, JobStatus.DONE)
                await self._store.reset_failure(job.job_id)
                next_run = parse_timing(job.timing)
                await self._store.update_next_run(job.job_id, next_run.isoformat())
                logger.info("[CronScheduler] Recurring job %s rescheduled for %s", job.job_id, next_run)
                return

            # Failed execution path
            new_failures = await self._store.bump_failure(job.job_id)
            if new_failures == 1:
                try:
                    from kazma_core.observability.ops_alerts import alert
                    alert(
                        "cron.job_failure",
                        f"Recurring cron job {job.job_id} failed on first attempt",
                        detail=f"Prompt: {job.prompt[:100]} | Timing: {job.timing}",
                        severity="warn",
                    )
                except Exception:
                    logger.warning("[CronScheduler] Ops alert failed for job %s", job.job_id, exc_info=True)

            if new_failures >= 3:
                await self._store.update_status(job.job_id, JobStatus.FAILED)
                logger.error(
                    "[CronScheduler] Recurring job %s exceeded failure budget (%d/3) → marked FAILED",
                    job.job_id, new_failures,
                )
            else:
                next_run = parse_timing(job.timing)
                await self._store.update_next_run(job.job_id, next_run.isoformat())
                logger.warning(
                    "[CronScheduler] Recurring job %s failed (attempt %d/3) → rescheduled for %s",
                    job.job_id, new_failures, next_run,
                )
        else:
            await self._store.update_status(job.job_id, JobStatus.FAILED if failed else JobStatus.DONE)

    async def _deliver(self, job: ScheduledJob, text: str) -> None:
        """Send result to the user via the original platform — with repair.

        Target resolution chain (2026-08-27 'target_id must be platform:id
        format' incident — agent-rescheduled jobs were born with an empty
        delivery_target and every result message died):

            1. ``job.delivery_target`` when well-formed (``platform:id``).
            2. A sibling job's valid delivery_target on the same thread —
               adopted AND persisted back so the broken row self-heals.
            3. The session store lookup for the thread (best-effort; the
               5-minute TTL makes this a bonus, not a strategy).
            4. Nothing works → one CRITICAL log naming the job — never a
               silent warning, never a bare-UUID send that the gateway
               rejects.
        """
        target_id = str(job.delivery_target or "").strip()

        def _valid(t: str) -> bool:
            return bool(t) and ":" in t and not t.endswith(":")

        if not _valid(target_id):
            healed = ""
            try:
                healed = await self._store.sibling_delivery_target(job.thread_id)
            except Exception:
                healed = ""
            if _valid(healed):
                logger.warning(
                    "[CronScheduler] %s had malformed/empty delivery_target %r — "
                    "adopted sibling target %r (persisted)",
                    job.job_id, target_id or job.thread_id, healed,
                )
                target_id = healed
                try:
                    await self._store.update_delivery_target(job.job_id, healed)
                except Exception:
                    logger.debug("[CronScheduler] target repair persist failed", exc_info=True)
            else:
                # Session-store fallback (best effort — TTL is 5 min; the
                # web SessionManager knows platform/chat_id per thread).
                try:
                    from kazma_ui.session_manager import get_session_manager

                    mgr = get_session_manager()
                    sess = mgr.get_by_thread_id(job.thread_id) if mgr else None
                    if sess is not None:
                        plat = str(getattr(sess, "platform", "") or job.platform or "")
                        chat = str(getattr(sess, "chat_id", "") or "")
                        if plat and chat and _valid(f"{plat}:{chat}"):
                            target_id = f"{plat}:{chat}"
                except Exception:
                    pass
        if not _valid(target_id):
            logger.critical(
                "[CronScheduler] UNDELIVERABLE job %s ('%.60s'): no valid "
                "delivery_target (stored=%r thread=%r platform=%r). Result "
                "NOT sent — fix the job's delivery target or re-create it "
                "from a live conversation.",
                job.job_id, job.prompt, job.delivery_target, job.thread_id, job.platform,
            )
            return
        try:
            from kazma_core.tools.send_message import send_message

            platform = target_id.split(":", 1)[0]
            body = compose_cron_delivery(text, job.prompt)
            logger.info("[CronScheduler] delivering %s -> %s", job.job_id, target_id)
            await send_message(target_id, body, backend=platform)
        except Exception as exc:
            logger.critical(
                "[CronScheduler] delivery FAILED for %s -> %s: %s",
                job.job_id, target_id, exc,
            )
