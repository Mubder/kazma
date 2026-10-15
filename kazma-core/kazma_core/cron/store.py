"""cron.db: scheduled jobs, the durable fire record, and the delivery outbox.

Scheduled audit SCH-E04, E02, E06, E07, E12 (2026-10-13). A fire used to be a
``running`` flag and three separate statements, so a restart during a fire
turned a reminder into a silent FAILED, a crash inside the daily finalize
left a recurring job DONE for good, and a delivery the platform refused was
recorded as done. Now:

* **A fire is a leased record on its job**: ``fire_owner`` (the scheduler
  that claimed it), ``lease_until``, ``fire_due`` (the occurrence it serves)
  and ``fire_attempt``. Claiming is one compare-and-swap.
* **A fire ends in one transaction** (:meth:`SQLiteCronStore.finish_fire`):
  its outcome, the delivery row in the outbox, the next occurrence of a
  recurring job, and a pending edit or cancellation recorded during the fire
  all commit together, or none does.
* **Deliveries go through the outbox** (``cron_deliveries``): a leased row,
  retried with backoff, acknowledged only on ``sent:``. A refused or
  undeliverable one ends ``undeliverable`` and its job says so, never DONE.
  A row whose sender died after claiming it ends ``unknown``: the message
  may or may not have arrived, and it is never sent twice.
* **Every mutation names its actor and source** (``created_by``,
  ``updated_by``, ``source``, and a ``cron_events`` row), carries a version,
  and is legal only in the states that allow it.
* **Alerts go through an outbox too** (``cron_alerts``, 2026-10-15). A state
  change that needs an ops alert -- a failed fire, a failure budget run out,
  a message ended ``undeliverable`` or ``unknown`` -- writes the alert's row
  in its own transaction (:meth:`SQLiteCronStore._enqueue_alert`); the
  scheduler raises it after the commit and marks it sent only once
  ``ops_alerts.alert`` returned. A crash between the commit and the alert
  loses nothing (the next pass, or the next start, raises it); a crash
  between the alert and its mark raises it once more. The alert used to be
  raised after the commit, and a delivery's was marked before it was raised:
  a crash in either window lost it (the Scheduled soak found both).

The connection is autocommit (AGENTS §16C); a group of writes is one
``BEGIN IMMEDIATE`` through :func:`kazma_core.db.sqlite_session.write_transaction`,
and every write goes through it, so a single statement from another
coroutine can never land inside someone else's group.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import aiosqlite

from kazma_core.cron.clock import SYSTEM_CLOCK, CronClock

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "JobStatus",
    "SQLiteCronStore",
    "ScheduledJob",
    "norm_iso",
]


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    #: A one-off whose fire finished; its message is in the outbox.
    DELIVERING = "delivering"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: The outbox gave up: the platform refused it, or there was no address.
    UNDELIVERABLE = "undeliverable"
    #: Kazma stopped while sending it: it may or may not have arrived, and it
    #: is never sent twice (an alert says to check the chat).
    DELIVERY_UNKNOWN = "delivery_unknown"
    #: Due while Kazma was down for longer than the job's catch-up window.
    MISSED = "missed"


#: Statuses a job never leaves without an explicit "run again".
TERMINAL_STATUSES = frozenset({
    JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.UNDELIVERABLE, JobStatus.MISSED,
    JobStatus.DELIVERY_UNKNOWN,
})
#: Statuses that still hold work (shown under "Upcoming" / counted toward caps).
ACTIVE_STATUSES = frozenset({JobStatus.PENDING, JobStatus.RUNNING, JobStatus.DELIVERING})

_TERMINAL_SQL = "('done', 'completed', 'failed', 'cancelled', 'undeliverable', 'missed', 'delivery_unknown')"


def norm_iso(value: datetime | str | None) -> str:
    """An instant as ``YYYY-MM-DDTHH:MM:SS+00:00`` (UTC), or ``""``.

    One fixed form, so ``next_run`` sorts as text and the ``(status,
    next_run)`` index answers "what is due" (a mix of local offsets did not).
    A naive value is UTC (what every writer here passes).
    """
    if value is None or value == "":
        return ""
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


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
    # schedule time so the result routes back to the originating chat long
    # after the SessionStore row has been TTL-evicted (5-min default). It
    # stays on the server: no tool result or page shows it (SCH-S10).
    delivery_target: str = ""
    failure_count: int = 0
    #: "reminder" delivers its stored message with no model call; "task" runs
    #: an agent turn with its declared tools and delivers the answer.
    kind: str = "task"
    #: The tools a fired task may use: "read" (default) or "read_write".
    capabilities: str = "read"
    version: int = 1
    fire_owner: str = ""
    lease_until: float = 0.0
    fire_attempt: int = 0
    fire_due: str = ""
    pending_edit: str = ""
    pending_cancel: int = 0
    finished_at: str = ""
    last_outcome: str = ""
    last_fire_at: str = ""
    delivery_state: str = ""
    delivery_error: str = ""
    catchup_seconds: int = 0
    series_start: str = ""
    created_by: str = ""
    updated_by: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The job for a list, a tool result or the page.

        Never the delivery address (SCH-S10): ``destination`` is the platform.
        ``prompt`` is the whole stored prompt (bounded at booking); the page's
        list shows a display summary and fetches the prompt only to edit it
        (SCH-S02: it used to edit what was cut to 200 characters here).
        """
        out = {
            "job_id": self.job_id,
            "timing": self.timing,
            "prompt": self.prompt,
            "platform": self.platform,
            "destination": destination_label(self.delivery_target, self.platform),
            "thread_id": self.thread_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "next_run": self.next_run,
            "last_result": self.last_result[:500] if self.last_result else None,
            "last_outcome": self.last_outcome,
            "last_fire_at": self.last_fire_at,
            "delivery_state": self.delivery_state,
            "delivery_error": self.delivery_error,
            "finished_at": self.finished_at,
            "tenant_id": self.tenant_id,
            "failure_count": self.failure_count,
            "kind": self.kind,
            "capabilities": self.capabilities,
            "version": self.version,
            "edit_pending": bool(self.pending_edit),
            "cancel_pending": bool(self.pending_cancel),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "source": self.source,
        }
        return out


def destination_label(target: str | None, platform: str | None = None) -> str:
    """The platform a job delivers to ("telegram"), never its address."""
    t = str(target or "").strip()
    if ":" in t:
        return t.split(":", 1)[0]
    return str(platform or "").strip()


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

#: Columns added after the table first shipped, in order.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("delivery_target", "TEXT NOT NULL DEFAULT ''"),
    ("failure_count", "INTEGER NOT NULL DEFAULT 0"),
    ("kind", "TEXT NOT NULL DEFAULT 'task'"),
    ("capabilities", "TEXT NOT NULL DEFAULT 'read'"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("fire_owner", "TEXT NOT NULL DEFAULT ''"),
    ("lease_until", "REAL NOT NULL DEFAULT 0"),
    ("fire_attempt", "INTEGER NOT NULL DEFAULT 0"),
    ("fire_due", "TEXT NOT NULL DEFAULT ''"),
    ("pending_edit", "TEXT NOT NULL DEFAULT ''"),
    ("pending_cancel", "INTEGER NOT NULL DEFAULT 0"),
    ("finished_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_outcome", "TEXT NOT NULL DEFAULT ''"),
    ("last_fire_at", "TEXT NOT NULL DEFAULT ''"),
    ("delivery_state", "TEXT NOT NULL DEFAULT ''"),
    ("delivery_error", "TEXT NOT NULL DEFAULT ''"),
    ("catchup_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("series_start", "TEXT NOT NULL DEFAULT ''"),
    ("created_by", "TEXT NOT NULL DEFAULT ''"),
    ("updated_by", "TEXT NOT NULL DEFAULT ''"),
    ("source", "TEXT NOT NULL DEFAULT ''"),
)

_EXTRA_SCHEMA = (
    "CREATE INDEX IF NOT EXISTS idx_cron_jobs_due ON cron_jobs(status, next_run)",
    "CREATE INDEX IF NOT EXISTS idx_cron_jobs_tenant ON cron_jobs(tenant_id, status)",
    """CREATE TABLE IF NOT EXISTS cron_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        fire_key TEXT NOT NULL UNIQUE,
        purpose TEXT NOT NULL DEFAULT 'result',
        target TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        due_at TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0,
        lease_owner TEXT NOT NULL DEFAULT '',
        lease_until REAL NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        finished_at REAL,
        alerted INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cron_deliveries_due ON cron_deliveries(state, next_attempt)",
    "CREATE INDEX IF NOT EXISTS idx_cron_deliveries_job ON cron_deliveries(job_id)",
    """CREATE TABLE IF NOT EXISTS cron_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        at REAL NOT NULL,
        event TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cron_events_job ON cron_events(job_id, id)",
    # The alert outbox. ``dedupe`` names the state change the row belongs to
    # (``fire:<event id>``, ``delivery:<id>``), so a change can never queue
    # its alert twice; ``sent_at`` stays NULL until ops_alerts.alert returned.
    """CREATE TABLE IF NOT EXISTS cron_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe TEXT NOT NULL UNIQUE,
        job_id TEXT NOT NULL,
        ops_key TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT 'warn',
        created_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        sent_at REAL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cron_alerts_pending ON cron_alerts(sent_at, next_attempt)",
    "CREATE INDEX IF NOT EXISTS idx_cron_alerts_job ON cron_alerts(job_id)",
)

_JOB_COLUMNS = (
    "job_id, timing, prompt, platform, thread_id, status, created_at, next_run, last_result, "
    + ", ".join(name for name, _ in _ADDED_COLUMNS)
)

#: Delivery attempts before a message is undeliverable, and the longest it
#: may keep trying. Backoff: 30 s doubling, at most 15 minutes.
DELIVERY_MAX_ATTEMPTS = 6
DELIVERY_DEADLINE_S = 2 * 3600.0
DELIVERY_LEASE_S = 60.0


def _delivery_backoff(attempts: int) -> float:
    return float(min(900, 30 * 2 ** max(0, attempts - 1)))


@dataclass
class FireResult:
    """What one fire produced, applied by :meth:`SQLiteCronStore.finish_fire`."""

    success: bool
    outcome: str  # delivered-pending / turn_failed / timed_out / error / hitl_off ...
    result_text: str = ""
    body: str = ""  # the message to deliver; "" = none
    target: str = ""
    purpose: str = "result"
    failure_budget: int = 3
    #: How late the fire started after its due time (metrics: p50/p95).
    lateness_s: float = 0.0


#: Alerts handed out per drain pass, at most.
ALERT_PASS_LIMIT = 50


def _fire_alert(job_id: str, result: FireResult, *, timing: str, failures: int, status: str,
                budget_exhausted: bool) -> dict[str, str] | None:
    """The ops alert a fire's end needs, or None.

    One when a run of failures starts (or a one-off fails), and one when a
    recurring job's failure budget runs out; a success needs none. Decided
    inside :meth:`SQLiteCronStore.finish_fire`, so the alert commits with
    the outcome that needs it.
    """
    if result.success:
        return None
    text = (result.result_text or "")[:200]
    if budget_exhausted:
        return {"ops_key": "cron.job_failed",
                "title": f"Scheduled job {job_id} failed {failures} times in a row and stopped",
                "detail": f"{result.outcome}: {text} | Timing: {timing}", "severity": "error"}
    if failures == 1 or status == "failed":
        return {"ops_key": "cron.job_failure", "title": f"Scheduled job {job_id} failed ({result.outcome})",
                "detail": f"{text} | Timing: {timing}", "severity": "warn"}
    return None


def _delivery_alert(delivery_id: int, job_id: str, *, state: str, purpose: str, error: str) -> dict[str, Any]:
    """The ops alert of a message that ended ``undeliverable`` or ``unknown``."""
    if state == "unknown":
        alert = {"ops_key": "cron.delivery_unknown",
                 "title": f"A scheduled message for job {job_id} may not have arrived",
                 "detail": "Kazma stopped while sending it; check the chat. It is not resent.", "severity": "warn"}
    else:
        alert = {"ops_key": "cron.delivery_failed",
                 "title": f"A scheduled message for job {job_id} was not delivered",
                 "detail": f"{purpose}: {str(error or 'no delivery target')[:200]}", "severity": "error"}
    return {**alert, "dedupe": f"delivery:{delivery_id}", "job_id": job_id, "delivery_id": delivery_id}


class SQLiteCronStore:
    """Persist scheduled jobs in SQLite to survive restarts.

    Args:
        db_path: Path to the SQLite database.
        clock:   Where every time this store records comes from (leases,
                 backoff, events, finish times). The real clock unless a
                 fake one is passed (the qualification soak).
    """

    def __init__(self, db_path: str | None = None, *, clock: CronClock | None = None) -> None:
        # Default resolves via paths.data_dir() (walks up to the
        # pyproject.toml root) instead of a bare CWD-relative literal, so a
        # server started from a subdirectory opens the SAME cron.db instead
        # of silently minting an empty one (deep-audit 2026-08-19, #14).
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = str(data_dir() / "cron.db")
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        #: The store's clock; a scheduler on this store reads the same one.
        self.clock: CronClock = clock or SYSTEM_CLOCK

    def _now_iso(self) -> str:
        return norm_iso(self.clock.now())

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("CronStore DB not initialized")
        return self._db

    def _tx(self) -> Any:
        from kazma_core.db.sqlite_session import write_transaction

        return write_transaction(self._conn())

    async def init(self) -> None:
        """Initialize the database, its tables, columns and indexes."""
        from pathlib import Path

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Autocommit: every statement is its own transaction, so no path --
        # a write that changed nothing, or one that raised -- returns with
        # the write lock held. In Python's default mode a DELETE opens a
        # transaction even when it matches nothing, and the purge committed
        # only when it had deleted something: from each boot until the next
        # reminder fired, the server held cron.db's write lock, and every
        # other writer (a second instance, a maintenance script) waited out
        # its timeout and failed (live 2026-09-27). A group of writes is an
        # explicit BEGIN IMMEDIATE (self._tx()).
        self._db = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._db.row_factory = aiosqlite.Row
        from kazma_core.config_store import apply_sqlite_pragmas_async

        await apply_sqlite_pragmas_async(self._db)
        await self._db.execute(_CREATE_TABLE)
        from kazma_core.db.sqlite_columns import add_missing_columns_async

        await add_missing_columns_async(self._db, "cron_jobs", _ADDED_COLUMNS)
        for statement in _EXTRA_SCHEMA:
            await self._db.execute(statement)
        await self._normalize_next_runs()
        await self._queue_unalerted_deliveries()
        logger.info("[CronStore] Initialized at %s", self._db_path)

    async def _queue_unalerted_deliveries(self) -> None:
        """A cron.db an older build left: a message that ended undeliverable or
        unknown and whose alert was never raised gets it queued now (that
        build's drain raised it at its next pass; this one drains the outbox)."""
        rows = await self._conn().execute_fetchall(
            "SELECT id, job_id, state, purpose, last_error FROM cron_deliveries "
            "WHERE state IN ('undeliverable', 'unknown') AND alerted = 0 ORDER BY id")
        if not rows:
            return
        async with self._tx() as db:
            for row in rows:
                await self._enqueue_alert(db, **_delivery_alert(
                    int(row["id"]), str(row["job_id"]), state=str(row["state"]), purpose=str(row["purpose"]),
                    error=str(row["last_error"] or "")))
        logger.info("[CronStore] queued the alerts of %d earlier undelivered message(s)", len(rows))

    async def _normalize_next_runs(self) -> None:
        """Rewrite every stored ``next_run`` in the one sortable UTC form, and
        give a finished row from an older build the time it is first seen as
        its finish (retention counts from there, never from its creation)."""
        rows = await self._conn().execute_fetchall("SELECT job_id, next_run FROM cron_jobs WHERE next_run IS NOT NULL")
        changes = [(norm_iso(r["next_run"]), r["job_id"]) for r in rows if norm_iso(r["next_run"]) != r["next_run"]]
        async with self._tx() as db:
            if changes:
                await db.executemany("UPDATE cron_jobs SET next_run = ? WHERE job_id = ?", changes)
            await db.execute(
                f"UPDATE cron_jobs SET finished_at = ? WHERE status IN {_TERMINAL_SQL} AND finished_at = ''",
                (self._now_iso(),))
        if changes:
            logger.info("[CronStore] normalized next_run on %d row(s)", len(changes))

    # ── reads ────────────────────────────────────────────────────────────

    def _row_to_job(self, row: Any) -> ScheduledJob:
        def val(name: str, default: Any) -> Any:
            try:
                v = row[name]
            except (IndexError, KeyError):
                return default
            return default if v is None else v

        status = str(val("status", "pending"))
        if status == "completed":
            status = "done"
        try:
            st = JobStatus(status)
        except ValueError:
            st = JobStatus.FAILED
        return ScheduledJob(
            job_id=row["job_id"], timing=row["timing"], prompt=row["prompt"], platform=row["platform"],
            thread_id=row["thread_id"], status=st, created_at=val("created_at", ""),
            next_run=row["next_run"], last_result=row["last_result"],
            tenant_id=str(val("tenant_id", "default")), delivery_target=str(val("delivery_target", "")),
            failure_count=int(val("failure_count", 0) or 0), kind=str(val("kind", "task")),
            capabilities=str(val("capabilities", "read")), version=int(val("version", 1) or 1),
            fire_owner=str(val("fire_owner", "")), lease_until=float(val("lease_until", 0) or 0),
            fire_attempt=int(val("fire_attempt", 0) or 0), fire_due=str(val("fire_due", "")),
            pending_edit=str(val("pending_edit", "")), pending_cancel=int(val("pending_cancel", 0) or 0),
            finished_at=str(val("finished_at", "")), last_outcome=str(val("last_outcome", "")),
            last_fire_at=str(val("last_fire_at", "")), delivery_state=str(val("delivery_state", "")),
            delivery_error=str(val("delivery_error", "")), catchup_seconds=int(val("catchup_seconds", 0) or 0),
            series_start=str(val("series_start", "")), created_by=str(val("created_by", "")),
            updated_by=str(val("updated_by", "")), source=str(val("source", "")),
        )

    async def _jobs(self, sql: str, args: tuple[Any, ...] = ()) -> list[ScheduledJob]:
        rows = await self._conn().execute_fetchall(f"SELECT {_JOB_COLUMNS} FROM cron_jobs {sql}", args)
        return [self._row_to_job(r) for r in rows]

    async def get(self, job_id: str, *, tenant_id: str | None = None) -> ScheduledJob | None:
        """One job, or None -- another tenant's id reads exactly like an unknown one."""
        if tenant_id:
            jobs = await self._jobs("WHERE job_id = ? AND tenant_id = ?", (job_id, tenant_id))
        else:
            jobs = await self._jobs("WHERE job_id = ?", (job_id,))
        return jobs[0] if jobs else None

    async def list_active(self) -> list[ScheduledJob]:
        """Jobs that still hold work: pending, running, delivering (all tenants)."""
        return await self._jobs("WHERE status IN ('pending', 'running', 'delivering')")

    async def list_all(self, *, tenant_id: str | None = None, limit: int | None = 500) -> list[ScheduledJob]:
        """Jobs newest first; optionally one tenant's (multi-user / production UI)."""
        limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""
        if tenant_id:
            return await self._jobs(f"WHERE tenant_id = ? ORDER BY created_at DESC{limit_clause}", (tenant_id,))
        return await self._jobs(f"ORDER BY created_at DESC{limit_clause}")

    async def due_jobs(self, *, now: datetime, limit: int = 100) -> list[ScheduledJob]:
        """Pending jobs due by *now*, soonest first (the ``(status, next_run)`` index)."""
        return await self._jobs(
            "WHERE status = 'pending' AND next_run IS NOT NULL AND next_run != '' AND next_run <= ? "
            "ORDER BY next_run LIMIT ?", (norm_iso(now), int(limit)))

    async def list_running(self) -> list[ScheduledJob]:
        """Every running job -- no limit: recovery must see the oldest one too (SCH-E07)."""
        return await self._jobs("WHERE status = 'running'")

    async def count_jobs(self, *, tenant_id: str | None = None, thread_id: str | None = None) -> int:
        """Jobs still holding work, for a tenant or a thread (the booking caps)."""
        where = ["status IN ('pending', 'running', 'delivering')"]
        args: list[Any] = []
        if tenant_id is not None:
            where.append("tenant_id = ?")
            args.append(tenant_id)
        if thread_id:
            where.append("thread_id = ?")
            args.append(thread_id)
        rows = await self._conn().execute_fetchall(
            f"SELECT COUNT(*) AS n FROM cron_jobs WHERE {' AND '.join(where)}", tuple(args))
        return int(rows[0]["n"]) if rows else 0

    async def job_exists(self, job_id: str, *, tenant_id: str | None = None) -> bool:
        """True when a job row exists (any status)."""
        return await self.get(job_id, tenant_id=tenant_id) is not None

    async def sibling_delivery_target(self, thread_id: str) -> str:
        """A VALID delivery_target from any other job on the same thread.

        The repair chain: rescheduled/legacy rows born with an empty or
        malformed target adopt a sibling's routing -- the whole batch shares
        one conversation (2026-08-27 incident).
        """
        if self._db is None or not str(thread_id or "").strip():
            return ""
        try:
            rows = await self._conn().execute_fetchall(
                "SELECT delivery_target FROM cron_jobs "
                "WHERE thread_id = ? AND delivery_target LIKE '%:%' "
                "ORDER BY created_at DESC LIMIT 1",
                (str(thread_id),),
            )
        except aiosqlite.Error:
            logger.warning("[CronStore] sibling target lookup failed", exc_info=True)
            return ""
        for row in rows or []:
            cand = str(row["delivery_target"] or "").strip()
            if ":" in cand:
                return cand
        return ""

    # ── writes: booking and the operator's actions ───────────────────────

    async def insert(self, job: ScheduledJob) -> None:
        """Insert a new scheduled job (its creation is an event).

        A row inserted already finished (an import, a test) finished now:
        retention counts from when a job ended, never from when it was made.
        """
        if job.status in TERMINAL_STATUSES and not job.finished_at:
            job.finished_at = self._now_iso()
        async with self._tx() as db:
            await db.execute(
                f"INSERT INTO cron_jobs ({_JOB_COLUMNS}) VALUES ({', '.join('?' * (9 + len(_ADDED_COLUMNS)))})",
                (job.job_id, job.timing, job.prompt, job.platform, job.thread_id, job.status.value,
                 job.created_at, norm_iso(job.next_run) or None, job.last_result,
                 job.tenant_id, job.delivery_target, job.failure_count, job.kind, job.capabilities,
                 job.version, job.fire_owner, job.lease_until, job.fire_attempt, job.fire_due,
                 job.pending_edit, job.pending_cancel, job.finished_at, job.last_outcome,
                 job.last_fire_at, job.delivery_state, job.delivery_error, job.catchup_seconds,
                 job.series_start, job.created_by, job.updated_by, job.source),
            )
            await self._event(db, job.job_id, "created", actor=job.created_by, source=job.source,
                              detail={"timing": job.timing, "kind": job.kind, "capabilities": job.capabilities})

    async def update_result(self, job_id: str, result: str) -> None:
        """Record a job's last result text."""
        async with self._tx() as db:
            await db.execute("UPDATE cron_jobs SET last_result = ? WHERE job_id = ?", (result[:5000], job_id))

    async def update_delivery_target(self, job_id: str, delivery_target: str) -> None:
        """Repair a job's delivery_target (self-heal from a sibling job)."""
        async with self._tx() as db:
            await db.execute(
                "UPDATE cron_jobs SET delivery_target = ? WHERE job_id = ?",
                (str(delivery_target or "").strip(), job_id),
            )

    async def _row(self, db: Any, job_id: str, tenant_id: str | None) -> Any:
        if tenant_id:
            cur = await db.execute(f"SELECT {_JOB_COLUMNS} FROM cron_jobs WHERE job_id = ? AND tenant_id = ?",
                                   (job_id, tenant_id))
        else:
            cur = await db.execute(f"SELECT {_JOB_COLUMNS} FROM cron_jobs WHERE job_id = ?", (job_id,))
        return await cur.fetchone()

    async def cancel(self, job_id: str, *, tenant_id: str | None = None, expected_version: int | None = None,
                     actor: str = "", source: str = "") -> str:
        """Cancel a job. Returns one of:

        ``cancelled``; ``cancel_pending`` (it is firing now: recorded, applied
        when the fire ends); ``not_found`` (unknown, or another tenant's);
        ``conflict`` (the version the caller saw is stale);
        ``not_cancellable`` (it already ended).
        """
        async with self._tx() as db:
            row = await self._row(db, job_id, tenant_id)
            if row is None:
                return "not_found"
            if expected_version is not None and int(row["version"]) != int(expected_version):
                return "conflict"
            status = str(row["status"])
            if status == "pending":
                await db.execute(
                    "UPDATE cron_jobs SET status = 'cancelled', finished_at = ?, last_outcome = 'cancelled', "
                    "version = version + 1, updated_by = ? WHERE job_id = ? AND status = 'pending'",
                    (self._now_iso(), actor, job_id))
                outcome = "cancelled"
            elif status == "running":
                await db.execute(
                    "UPDATE cron_jobs SET pending_cancel = 1, version = version + 1, updated_by = ? "
                    "WHERE job_id = ? AND status = 'running'", (actor, job_id))
                outcome = "cancel_pending"
            elif status == "delivering":
                # The run is over and its message is waiting to be sent:
                # cancelling stops the message.
                await db.execute(
                    "UPDATE cron_deliveries SET state = 'cancelled', finished_at = ? "
                    "WHERE job_id = ? AND state = 'pending'", (self.clock.time(), job_id))
                await db.execute(
                    "UPDATE cron_jobs SET status = 'cancelled', finished_at = ?, last_outcome = 'cancelled', "
                    "delivery_state = 'cancelled', version = version + 1, updated_by = ? WHERE job_id = ?",
                    (self._now_iso(), actor, job_id))
                outcome = "cancelled"
            else:
                return "not_cancellable"
            await self._event(db, job_id, "cancel" if outcome == "cancelled" else "cancel_recorded",
                              actor=actor, source=source)
            return outcome

    async def update_job(
        self,
        job_id: str,
        *,
        timing: str | None = None,
        prompt: str | None = None,
        next_run: str | None = None,
        series_start: str | None = None,
        tenant_id: str | None = None,
        expected_version: int | None = None,
        actor: str = "",
        source: str = "",
    ) -> str:
        """Edit a job's timing and/or prompt. Returns one of:

        ``rescheduled`` (applied); ``edit_pending`` (it is firing now:
        recorded, applied when the fire ends); ``not_found``; ``conflict``;
        ``not_editable`` (it ended -- bringing it back is :meth:`restore_job`,
        an explicit action, never a side effect of an edit); ``unchanged``.

        A timing edit never made a finished job pending again here; it did
        until 2026-10-14, so the chat tool could bring back a reminder the
        user had cancelled (SCH-S07).
        """
        if timing is None and prompt is None:
            return "unchanged"
        async with self._tx() as db:
            row = await self._row(db, job_id, tenant_id)
            if row is None:
                return "not_found"
            if expected_version is not None and int(row["version"]) != int(expected_version):
                return "conflict"
            status = str(row["status"])
            change = {k: v for k, v in (("timing", timing), ("prompt", prompt), ("next_run", norm_iso(next_run) or None),
                                         ("series_start", series_start)) if v is not None}
            if status == "pending":
                sets = ", ".join(f"{k} = ?" for k in change)
                await db.execute(
                    f"UPDATE cron_jobs SET {sets}, version = version + 1, updated_by = ? "
                    "WHERE job_id = ? AND status = 'pending'", (*change.values(), actor, job_id))
                outcome = "rescheduled"
            elif status == "running":
                merged = json.loads(row["pending_edit"] or "{}")
                merged.update(change)
                await db.execute(
                    "UPDATE cron_jobs SET pending_edit = ?, version = version + 1, updated_by = ? "
                    "WHERE job_id = ? AND status = 'running'", (json.dumps(merged, ensure_ascii=False), actor, job_id))
                outcome = "edit_pending"
            else:
                return "not_editable"
            await self._event(db, job_id, "edit" if outcome == "rescheduled" else "edit_recorded",
                              actor=actor, source=source,
                              detail={"timing": timing, "prompt_changed": prompt is not None})
            return outcome

    async def restore_job(self, job_id: str, *, timing: str, next_run: str, series_start: str = "",
                          tenant_id: str | None = None, expected_version: int | None = None,
                          actor: str = "", source: str = "") -> str:
        """Run a finished job again ("Run again"): ``restored`` / ``not_found`` /
        ``conflict`` / ``not_finished`` (it still holds work)."""
        async with self._tx() as db:
            row = await self._row(db, job_id, tenant_id)
            if row is None:
                return "not_found"
            if expected_version is not None and int(row["version"]) != int(expected_version):
                return "conflict"
            if str(row["status"]) not in {s.value for s in TERMINAL_STATUSES} | {"completed"}:
                return "not_finished"
            await db.execute(
                "UPDATE cron_jobs SET status = 'pending', timing = ?, next_run = ?, series_start = ?, "
                "failure_count = 0, finished_at = '', pending_edit = '', pending_cancel = 0, "
                "version = version + 1, updated_by = ? WHERE job_id = ?",
                (timing, norm_iso(next_run), series_start, actor, job_id))
            await self._event(db, job_id, "restored", actor=actor, source=source, detail={"timing": timing})
            return "restored"

    # ── the fire record ──────────────────────────────────────────────────

    async def claim_job(self, job_id: str, *, owner: str = "", lease_seconds: float = 300.0,
                        expected_version: int | None = None) -> bool:
        """Claim a due job's fire: ``pending`` -> ``running``, leased to *owner*.

        One compare-and-swap (audit M-P5: two schedulers on one cron.db both
        executed a due job). The claim records the occurrence it serves
        (``fire_due``) and counts attempts at that occurrence.
        """
        async with self._tx() as db:
            args: list[Any] = [owner, self.clock.time() + max(1.0, lease_seconds), job_id]
            version_clause = ""
            if expected_version is not None:
                version_clause = " AND version = ?"
                args.append(int(expected_version))
            cur = await db.execute(
                "UPDATE cron_jobs SET status = 'running', fire_owner = ?, lease_until = ?, "
                "fire_attempt = CASE WHEN fire_due = next_run THEN fire_attempt + 1 ELSE 1 END, "
                f"fire_due = COALESCE(next_run, '') WHERE job_id = ? AND status = 'pending'{version_clause}",
                tuple(args))
            if not cur.rowcount:
                return False
            await self._event(db, job_id, "fire_claimed", source="scheduler", detail={"owner": owner})
            return True

    async def release_fire(self, job_id: str, *, owner: str, reason: str = "") -> bool:
        """An interrupted fire that produced nothing goes back to ``pending`` at
        the same occurrence, to be fired again (recovery's "retry")."""
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE cron_jobs SET status = 'pending', fire_owner = '', lease_until = 0, "
                "next_run = CASE WHEN fire_due != '' THEN fire_due ELSE next_run END "
                "WHERE job_id = ? AND status = 'running' AND fire_owner = ?", (job_id, owner))
            if not cur.rowcount:
                return False
            await self._event(db, job_id, "fire_interrupted_retry", source="scheduler", detail={"reason": reason})
            return True

    async def finish_fire(
        self,
        job_id: str,
        *,
        owner: str,
        result: FireResult,
        advance: Callable[[str, datetime, str], datetime | None],
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """End a fire in ONE transaction. None when *owner* no longer holds it.

        *advance(timing, after, series_start)* is the job's next occurrence after
        *after* (None: not recurring, or the series ended). It is pure: the
        zone was resolved before the transaction.
        """
        now = now or self.clock.now()
        now_iso = norm_iso(now)
        async with self._tx() as db:
            row = await self._row(db, job_id, None)
            if row is None or str(row["status"]) != "running" or str(row["fire_owner"]) != owner:
                return None
            edit = json.loads(row["pending_edit"] or "{}")
            cancel = bool(row["pending_cancel"])
            timing = str(edit.get("timing") or row["timing"])
            prompt = str(edit.get("prompt") or row["prompt"])
            series_start = str(edit.get("series_start") or row["series_start"] or "")
            due = str(row["fire_due"] or row["next_run"] or "")
            delivery_id: int | None = None
            if result.body and not cancel:
                fire_key = f"{job_id}:{due}:{result.purpose}"
                cur = await db.execute(
                    "INSERT OR IGNORE INTO cron_deliveries (job_id, tenant_id, fire_key, purpose, target, body, "
                    "due_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, row["tenant_id"], fire_key, result.purpose, result.target, result.body[:4000],
                     due, self.clock.time()))
                delivery_id = cur.lastrowid if cur.rowcount else None
            failures = 0 if result.success else int(row["failure_count"] or 0) + 1
            budget_exhausted = False
            finished = ""
            nxt: datetime | None = None
            due_dt = _parse(due) or now
            recurring_next = advance(timing, max(now, due_dt), series_start)
            if cancel:
                status, finished = "cancelled", now_iso
            elif "next_run" in edit:
                status = "pending"
                nxt = _parse(edit["next_run"])
            elif recurring_next is not None or _looks_recurring(timing):
                if not result.success and failures >= max(1, result.failure_budget):
                    status, finished, budget_exhausted = "failed", now_iso, True
                elif recurring_next is None:
                    status, finished = "done", now_iso  # the series ended
                else:
                    status, nxt = "pending", recurring_next
            elif result.success:
                status = "delivering" if delivery_id is not None else "done"
                finished = "" if status == "delivering" else now_iso
            else:
                status, finished = "failed", now_iso
            await db.execute(
                "UPDATE cron_jobs SET status = ?, next_run = ?, timing = ?, prompt = ?, series_start = ?, "
                "pending_edit = '', pending_cancel = 0, fire_owner = '', lease_until = 0, fire_due = '', "
                "last_result = ?, last_outcome = ?, last_fire_at = ?, failure_count = ?, finished_at = ?, "
                "delivery_state = ?, delivery_error = '' WHERE job_id = ?",
                (status, norm_iso(nxt) if nxt is not None else row["next_run"], timing, prompt, series_start,
                 (result.result_text or "")[:5000], result.outcome, now_iso, failures, finished,
                 "pending" if delivery_id is not None else ("" if result.success else row["delivery_state"]),
                 job_id))
            event_id = await self._event(db, job_id, "fire_finished", source="scheduler", detail={
                "outcome": result.outcome, "status": status, "due": due,
                "delivery_id": delivery_id, "failures": failures,
                "lateness_s": round(max(0.0, float(result.lateness_s or 0.0)), 1)})
            # The failure's alert commits with the failure (it was raised
            # after this commit, and a crash in between lost it).
            alert = _fire_alert(job_id, result, timing=str(row["timing"]), failures=failures, status=status,
                                budget_exhausted=budget_exhausted)
            if alert is not None:
                await self._enqueue_alert(db, dedupe=f"fire:{event_id}", job_id=job_id, **alert)
            return {"status": status, "delivery_id": delivery_id, "failures": failures,
                    "budget_exhausted": budget_exhausted, "next_run": norm_iso(nxt) if nxt else "",
                    "due": due, "cancelled": cancel}

    async def mark_missed(
        self, job_id: str, *, expected_next_run: str, notice: str, target: str,
        advance: Callable[[str, datetime, str], datetime | None], now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Record an occurrence missed while Kazma was down: one notice, never "done".

        A one-off ends ``missed``; a recurring job moves to its next occurrence
        after *now*. None when the job changed meanwhile (compare-and-swap on
        status and ``next_run``).
        """
        now = now or self.clock.now()
        async with self._tx() as db:
            row = await self._row(db, job_id, None)
            if row is None or str(row["status"]) != "pending" or str(row["next_run"]) != expected_next_run:
                return None
            due = str(row["next_run"])
            cur = await db.execute(
                "INSERT OR IGNORE INTO cron_deliveries (job_id, tenant_id, fire_key, purpose, target, body, "
                "due_at, created_at) VALUES (?, ?, ?, 'missed', ?, ?, ?, ?)",
                (job_id, row["tenant_id"], f"{job_id}:{due}:missed", target, notice[:4000], due, self.clock.time()))
            delivery_id = cur.lastrowid if cur.rowcount else None
            nxt = advance(str(row["timing"]), now, str(row["series_start"] or ""))
            if nxt is not None:
                await db.execute(
                    "UPDATE cron_jobs SET next_run = ?, last_outcome = 'missed', last_fire_at = ? WHERE job_id = ?",
                    (norm_iso(nxt), norm_iso(now), job_id))
                status = "pending"
            else:
                await db.execute(
                    "UPDATE cron_jobs SET status = 'missed', last_outcome = 'missed', finished_at = ?, "
                    "last_result = ? WHERE job_id = ?",
                    (norm_iso(now), f"Missed: due {due} while Kazma was not running.", job_id))
                status = "missed"
            await self._event(db, job_id, "missed", source="scheduler",
                              detail={"due": due, "delivery_id": delivery_id, "status": status})
            return {"status": status, "delivery_id": delivery_id, "due": due, "next_run": norm_iso(nxt) if nxt else ""}

    # ── the delivery outbox ──────────────────────────────────────────────

    async def claim_delivery(self, *, owner: str, now: float | None = None,
                             delivery_id: int | None = None) -> dict[str, Any] | None:
        """Lease the next due delivery (or *delivery_id*) to *owner* before sending."""
        ts = self.clock.time() if now is None else now
        async with self._tx() as db:
            if delivery_id is not None:
                cur = await db.execute(
                    "SELECT * FROM cron_deliveries WHERE id = ? AND state = 'pending' AND next_attempt <= ?",
                    (delivery_id, ts))
            else:
                cur = await db.execute(
                    "SELECT * FROM cron_deliveries WHERE state = 'pending' AND next_attempt <= ? "
                    "ORDER BY id LIMIT 1", (ts,))
            row = await cur.fetchone()
            if row is None:
                return None
            await db.execute(
                "UPDATE cron_deliveries SET state = 'sending', lease_owner = ?, lease_until = ?, "
                "attempts = attempts + 1 WHERE id = ? AND state = 'pending'",
                (owner, ts + DELIVERY_LEASE_S, row["id"]))
            claimed = dict(row)
            claimed["attempts"] = int(row["attempts"]) + 1
            claimed["state"] = "sending"
            return claimed

    async def finish_delivery(self, delivery_id: int, *, owner: str, delivered: bool, error: str = "",
                              now: float | None = None) -> dict[str, Any] | None:
        """Acknowledge a send (``delivered``) or record a refused one.

        A refusal is retried with backoff until :data:`DELIVERY_MAX_ATTEMPTS`
        or :data:`DELIVERY_DEADLINE_S`, then ends ``undeliverable``; the job
        records each outcome in the same transaction. None when *owner* lost
        the lease.
        """
        ts = self.clock.time() if now is None else now
        async with self._tx() as db:
            cur = await db.execute("SELECT * FROM cron_deliveries WHERE id = ? AND state = 'sending' AND lease_owner = ?",
                                   (delivery_id, owner))
            row = await cur.fetchone()
            if row is None:
                return None
            job_id = row["job_id"]
            purpose = row["purpose"]
            if delivered:
                state = "delivered"
                await db.execute(
                    "UPDATE cron_deliveries SET state = 'delivered', finished_at = ?, lease_owner = '', "
                    "lease_until = 0, last_error = '' WHERE id = ?", (ts, delivery_id))
                if purpose == "result":
                    await db.execute(
                        "UPDATE cron_jobs SET status = CASE WHEN status = 'delivering' THEN 'done' ELSE status END, "
                        "finished_at = CASE WHEN status = 'delivering' THEN ? ELSE finished_at END, "
                        "delivery_state = 'delivered', delivery_error = '', "
                        "last_outcome = CASE WHEN last_outcome IN ('delivering', 'delivered') THEN 'delivered' "
                        "ELSE last_outcome END WHERE job_id = ?", (self._now_iso(), job_id))
                else:
                    await db.execute("UPDATE cron_jobs SET delivery_state = 'delivered' WHERE job_id = ?", (job_id,))
            else:
                attempts = int(row["attempts"])
                terminal = (not row["target"]) or attempts >= DELIVERY_MAX_ATTEMPTS \
                    or ts - float(row["created_at"]) >= DELIVERY_DEADLINE_S
                state = "undeliverable" if terminal else "pending"
                await db.execute(
                    "UPDATE cron_deliveries SET state = ?, next_attempt = ?, finished_at = ?, lease_owner = '', "
                    "lease_until = 0, last_error = ? WHERE id = ?",
                    (state, ts + _delivery_backoff(attempts), ts if terminal else None, error[:500], delivery_id))
                if terminal:
                    await db.execute(
                        "UPDATE cron_jobs SET status = CASE WHEN status = 'delivering' THEN 'undeliverable' "
                        "ELSE status END, finished_at = CASE WHEN status = 'delivering' THEN ? ELSE finished_at END, "
                        "delivery_state = 'undeliverable', delivery_error = ?, last_outcome = 'undeliverable' "
                        "WHERE job_id = ?", (self._now_iso(), error[:500], job_id))
                    await self._enqueue_alert(db, **_delivery_alert(
                        delivery_id, str(job_id), state="undeliverable", purpose=str(purpose), error=error[:500]))
                else:
                    await db.execute(
                        "UPDATE cron_jobs SET delivery_state = 'retrying', delivery_error = ? WHERE job_id = ?",
                        (error[:500], job_id))
            await self._event(db, job_id, f"delivery_{state}", source="scheduler",
                              detail={"delivery_id": delivery_id, "attempt": int(row["attempts"]),
                                      "purpose": purpose, "error": error[:200]})
            return {**dict(row), "state": state}

    async def recover_deliveries(self, *, owner: str, now: float | None = None) -> list[dict[str, Any]]:
        """Sends a dead sender claimed: ``unknown`` (it may have arrived; never resent)."""
        ts = self.clock.time() if now is None else now
        async with self._tx() as db:
            cur = await db.execute(
                "SELECT * FROM cron_deliveries WHERE state = 'sending' AND (lease_owner != ? OR lease_until < ?)",
                (owner, ts))
            rows = [dict(r) for r in await cur.fetchall()]
            for row in rows:
                await db.execute(
                    "UPDATE cron_deliveries SET state = 'unknown', finished_at = ?, lease_owner = '', "
                    "last_error = 'Kazma stopped while sending; the message may or may not have arrived.' "
                    "WHERE id = ? AND state = 'sending'", (ts, row["id"]))
                await db.execute(
                    "UPDATE cron_jobs SET status = CASE WHEN status = 'delivering' THEN 'delivery_unknown' "
                    "ELSE status END, "
                    "finished_at = CASE WHEN status = 'delivering' THEN ? ELSE finished_at END, "
                    "delivery_state = 'unknown', last_outcome = 'delivery_unknown', "
                    "delivery_error = 'Kazma stopped while sending; check the chat.' WHERE job_id = ?",
                    (self._now_iso(), row["job_id"]))
                await self._event(db, row["job_id"], "delivery_unknown", source="scheduler",
                                  detail={"delivery_id": row["id"]})
                await self._enqueue_alert(db, **_delivery_alert(
                    int(row["id"]), str(row["job_id"]), state="unknown", purpose=str(row["purpose"]), error=""))
            return rows

    # ── the alert outbox ─────────────────────────────────────────────────

    async def _enqueue_alert(self, db: Any, *, dedupe: str, job_id: str, ops_key: str, title: str,
                             detail: str = "", severity: str = "warn", delivery_id: int | None = None) -> None:
        """Queue one ops alert INSIDE the transaction of the change that needs it.

        Called only within ``async with self._tx()``: the row commits with the
        change or not at all (gate: ``tests/test_cron_alert_outbox.py``). A
        change queues its alert once (``dedupe``). A delivery's ``alerted``
        flag means its alert is queued.
        """
        await db.execute(
            "INSERT OR IGNORE INTO cron_alerts (dedupe, job_id, ops_key, title, detail, severity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dedupe, job_id, ops_key, title[:500], detail[:1000], severity, self.clock.time()))
        if delivery_id is not None:
            await db.execute("UPDATE cron_deliveries SET alerted = 1 WHERE id = ?", (delivery_id,))

    async def pending_alerts(self, *, limit: int = ALERT_PASS_LIMIT, now: float | None = None) -> list[dict[str, Any]]:
        """Alerts queued and not yet raised, oldest first (one that failed waits out its backoff)."""
        ts = self.clock.time() if now is None else now
        rows = await self._conn().execute_fetchall(
            "SELECT * FROM cron_alerts WHERE sent_at IS NULL AND next_attempt <= ? ORDER BY id LIMIT ?",
            (ts, int(limit)))
        return [dict(r) for r in rows]

    async def mark_alert_sent(self, alert_id: int, *, now: float | None = None) -> bool:
        """Record that ``ops_alerts.alert`` returned for this row; False if it was already."""
        ts = self.clock.time() if now is None else now
        async with self._tx() as db:
            cur = await db.execute("UPDATE cron_alerts SET sent_at = ? WHERE id = ? AND sent_at IS NULL",
                                   (ts, int(alert_id)))
            return bool(cur.rowcount)

    async def alert_failed(self, alert_id: int, *, error: str, now: float | None = None) -> None:
        """An alert that could not be raised stays queued, tried again after a backoff."""
        ts = self.clock.time() if now is None else now
        async with self._tx() as db:
            cur = await db.execute("SELECT attempts FROM cron_alerts WHERE id = ? AND sent_at IS NULL",
                                   (int(alert_id),))
            row = await cur.fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            await db.execute(
                "UPDATE cron_alerts SET attempts = ?, last_error = ?, next_attempt = ? WHERE id = ?",
                (attempts, error[:500], ts + _delivery_backoff(attempts), int(alert_id)))

    async def pending_alert_count(self) -> int:
        """Alerts queued and not yet raised (/metrics, readiness)."""
        rows = await self._conn().execute_fetchall("SELECT COUNT(*) AS n FROM cron_alerts WHERE sent_at IS NULL")
        return int(rows[0]["n"]) if rows else 0

    async def deliveries_for(self, job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """A job's deliveries, newest first (the retry history)."""
        rows = await self._conn().execute_fetchall(
            "SELECT id, purpose, due_at, state, attempts, last_error, created_at, finished_at "
            "FROM cron_deliveries WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, int(limit)))
        return [dict(r) for r in rows]

    async def pending_delivery_count(self) -> int:
        rows = await self._conn().execute_fetchall(
            "SELECT COUNT(*) AS n FROM cron_deliveries WHERE state IN ('pending', 'sending')")
        return int(rows[0]["n"]) if rows else 0

    async def outcome_counts(self, *, since: float) -> dict[str, int]:
        """Deliveries and fire outcomes since *since* (epoch), for metrics and the digest."""
        out: dict[str, int] = {}
        rows = await self._conn().execute_fetchall(
            "SELECT state, COUNT(*) AS n FROM cron_deliveries WHERE created_at >= ? GROUP BY state", (since,))
        for r in rows:
            out[f"delivery_{r['state']}"] = int(r["n"])
        rows = await self._conn().execute_fetchall(
            "SELECT json_extract(detail, '$.outcome') AS outcome, COUNT(*) AS n FROM cron_events "
            "WHERE event = 'fire_finished' AND at >= ? GROUP BY outcome", (since,))
        for r in rows:
            out[f"fire_{r['outcome']}"] = int(r["n"])
        rows = await self._conn().execute_fetchall(
            "SELECT COUNT(*) AS n FROM cron_events WHERE event = 'missed' AND at >= ?", (since,))
        out["missed"] = int(rows[0]["n"]) if rows else 0
        return out

    async def lateness(self, *, since: float, limit: int = 5000) -> list[float]:
        """How late each fire since *since* started (seconds), oldest first."""
        rows = await self._conn().execute_fetchall(
            "SELECT json_extract(detail, '$.lateness_s') AS late FROM cron_events "
            "WHERE event = 'fire_finished' AND at >= ? ORDER BY id DESC LIMIT ?", (since, int(limit)))
        return [float(r["late"]) for r in rows if r["late"] is not None][::-1]

    # ── events and retention ─────────────────────────────────────────────

    async def _event(self, db: Any, job_id: str, event: str, *, actor: str = "", source: str = "",
                     detail: dict[str, Any] | None = None) -> int:
        """Append one history row inside the caller's transaction; its id
        (never reused: AUTOINCREMENT) names the change, e.g. for its alert."""
        cur = await db.execute(
            "INSERT INTO cron_events (job_id, at, event, actor, source, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, self.clock.time(), event, actor or "", source or "",
             json.dumps(detail or {}, ensure_ascii=False, default=str)))
        return int(cur.lastrowid or 0)

    async def record_event(self, job_id: str, event: str, *, actor: str = "", source: str = "",
                           detail: dict[str, Any] | None = None) -> None:
        """Append one row to the job's history."""
        async with self._tx() as db:
            await self._event(db, job_id, event, actor=actor, source=source, detail=detail)

    async def list_events(self, job_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._conn().execute_fetchall(
            "SELECT at, event, actor, source, detail FROM cron_events WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, int(limit)))
        return [dict(r) for r in rows]

    async def purge_terminal_jobs(self, *, older_than_days: int = 14, keep_last: int = 500) -> int:
        """Drop finished jobs older than the window, by when they FINISHED.

        It went by ``created_at`` (SCH-E07): a daily job created a month ago
        that failed today was erased at the next purge, with its failure. A
        recurring job is a finished job only once it ended; its last outcome
        stays on its row. Delivered outbox rows and events go with their job,
        and delivered rows of live jobs past the window go too. Alerts follow
        the same window once raised; one not yet raised is kept whatever its
        age (it is the record that someone still has to be told). Returns the
        number of jobs deleted.
        """
        if self._db is None:
            return 0
        cutoff_dt = self.clock.now() - timedelta(days=older_than_days)
        cutoff = norm_iso(cutoff_dt)
        finished = "COALESCE(NULLIF(finished_at, ''), NULLIF(last_fire_at, ''), created_at)"
        async with self._tx() as db:
            cur = await db.execute(
                f"SELECT job_id FROM cron_jobs WHERE status IN {_TERMINAL_SQL} AND {finished} < ?", (cutoff,))
            doomed = [r["job_id"] for r in await cur.fetchall()]
            cur = await db.execute(
                f"SELECT job_id FROM cron_jobs WHERE status IN {_TERMINAL_SQL} "
                f"ORDER BY {finished} DESC LIMIT -1 OFFSET ?", (int(keep_last),))
            doomed += [r["job_id"] for r in await cur.fetchall() if r["job_id"] not in doomed]
            for job_id in doomed:
                await db.execute("DELETE FROM cron_deliveries WHERE job_id = ?", (job_id,))
                await db.execute("DELETE FROM cron_events WHERE job_id = ?", (job_id,))
                await db.execute("DELETE FROM cron_alerts WHERE job_id = ? AND sent_at IS NOT NULL", (job_id,))
                await db.execute("DELETE FROM cron_jobs WHERE job_id = ?", (job_id,))
            await db.execute(
                "DELETE FROM cron_deliveries WHERE state IN ('delivered', 'cancelled') AND finished_at < ?",
                (cutoff_dt.timestamp(),))
            await db.execute("DELETE FROM cron_alerts WHERE sent_at IS NOT NULL AND sent_at < ?",
                             (cutoff_dt.timestamp(),))
        if doomed:
            logger.info("[CronStore] Purged %d old finished job row(s)", len(doomed))
        return len(doomed)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _looks_recurring(timing: str) -> bool:
    """Whether *timing* is a recurrence (its series may have just ended)."""
    from kazma_core.cron.timing import looks_recurring

    return looks_recurring(timing)
