"""In-process scheduler for reminders and scheduled agent tasks.

The agent books a job with ``schedule_task`` (or the Scheduled page does);
the scheduler stores it in cron.db and fires it at its time:

* a **reminder** delivers its stored message, with no model call, so a
  provider outage cannot lose it;
* a **task** runs one agent turn with the tools it declared at booking
  (:mod:`kazma_core.cron.capabilities`) and delivers the answer. A turn that
  fails is a failed fire -- counted, alerted, never delivered as if it were
  the answer (Scheduled audit SCH-E01).

Every fire is a leased record and ends in one transaction; every message goes
through the delivery outbox and is retried until the platform takes it or it
is recorded ``undeliverable`` with one alert (:mod:`kazma_core.cron.store`).
Every alert goes through an outbox as well: written in the transaction of the
change that needs it, raised after the commit (:meth:`CronScheduler._drain_alerts`),
so no crash loses one. A fire interrupted by a restart runs again within the
job's catch-up window; one due longer ago than that is ``missed``, and its
chat gets one notice (SCH-E04, E02, E06). Timing has one model:
:mod:`kazma_core.cron.timing`.

Usage:
    store = SQLiteCronStore()
    await store.init()
    scheduler = CronScheduler(store=store, graph_builder=build_fn)
    await scheduler.start()
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar as _ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from kazma_core.cron.clock import SYSTEM_CLOCK, CronClock
from kazma_core.cron.store import (
    FireResult,
    JobStatus,
    ScheduledJob,
    SQLiteCronStore,
    norm_iso,
)

__all__ = [
    "CronLimitError",
    "CronScheduler",
    "JobStatus",
    "SQLiteCronStore",
    "ScheduledJob",
    "compose_cron_delivery",
    "get_cron_scheduler",
    "resolve_cron_zone",
    "scheduled_tenant_filter",
    "set_cron_scheduler",
]

logger = logging.getLogger(__name__)

# ── Cron-parent context (2026-08-27 approval-delivery incident) ────────
# Bound around each job execution so tools running INSIDE a cron-fired
# turn can inherit the firing job's delivery identity. The gateway's
# delivery-target ContextVar is empty on this path (no inbound user
# message), which is how agent-rescheduled jobs were born targetless.
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


#: A fire's lease is its task budget plus this margin (the graph build, the
#: target, the finishing transaction), never shorter than the minimum: a
#: reminder needs no budget (audit 2026-10-26 AUD-019).
_FIRE_LEASE_MARGIN_S = 300.0
_FIRE_LEASE_MIN_S = 600.0

# Module-level singleton
_cron_scheduler: CronScheduler | None = None


def set_cron_scheduler(scheduler: CronScheduler | None) -> None:
    global _cron_scheduler
    _cron_scheduler = scheduler


def get_cron_scheduler() -> CronScheduler | None:
    return _cron_scheduler


class CronLimitError(ValueError):
    """A booking refused by a bound (prompt size, the job caps). ``code`` names it."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# ══════════════════════════════════════════════════════════════════════════
# The operator's time zone
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
    running UTC). A settings read: async callers use ``asyncio.to_thread``.
    """
    name = ""
    try:
        from kazma_core.config_store import get_config_store

        name = str(get_config_store().get("cron.timezone") or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable store falls through to the env
        logger.debug("[CronScheduler] cron.timezone unreadable", exc_info=True)
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
    package on Windows). A settings read: async callers use ``to_thread``.
    """
    name, _source = resolve_cron_timezone_name()
    return _zone_for(name)


def _zone_for(name: str) -> tzinfo:
    """The zone a configured *name* means: UTC when it is unresolvable."""
    global _tz_warned

    if name == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        if not _tz_warned:
            logger.warning(
                "[CronScheduler] invalid cron.timezone %r — falling back to UTC "
                "(IANA name required, e.g. 'Asia/Kuwait'; install 'tzdata' on Windows)",
                name,
            )
            _tz_warned = True
        return UTC


def resolve_cron_zone() -> tuple[tzinfo, str]:
    """``(zone, name)`` from ONE settings read -- the boundary's (SCH-E10).

    Blocking: async callers use ``asyncio.to_thread``.
    """
    name, _src = resolve_cron_timezone_name()
    zone = _zone_for(name)
    return zone, ("UTC" if zone is UTC else name)


def annotate_fire_times(next_run: datetime | str | None, *, zone: tzinfo | None = None,
                        zone_name: str | None = None) -> dict[str, str]:
    """UTC + operator-local strings so the model does not report only UTC.

    ``cron.timezone`` (e.g. Asia/Kuwait) already anchors ``daily at 9am``.
    Compact ``5m``/ISO results were still returned as UTC ISO only, so the
    agent said "fires at 01:15:19 UTC" even when the operator zone was Kuwait.
    Pass *zone*/*zone_name* resolved once; without them this reads settings.
    Async code resolves the zone off the loop and calls
    :func:`_fire_times_in_zone` (audit 2026-10-26 AUD-111).
    """
    if zone is None or zone_name is None:
        if next_run is None or next_run == "":
            return {}
        zone, zone_name = resolve_cron_zone()
    return _fire_times_in_zone(next_run, zone, zone_name)


def _fire_times_in_zone(next_run: datetime | str | None, zone: tzinfo, zone_name: str) -> dict[str, str]:
    """:func:`annotate_fire_times` in a zone already resolved: no settings read."""
    if next_run is None or next_run == "":
        return {}
    try:
        dt = next_run if isinstance(next_run, datetime) else datetime.fromisoformat(str(next_run))
    except (TypeError, ValueError):
        return {}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    utc = dt.astimezone(UTC)
    local = dt.astimezone(zone)
    return {
        "next_run_utc": utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "next_run_local": local.strftime("%Y-%m-%d %H:%M:%S") + f" {zone_name}",
        "timezone": zone_name,
    }


# ══════════════════════════════════════════════════════════════════════════
# What a fire delivers
# ══════════════════════════════════════════════════════════════════════════

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
    """User-facing text for an agent task's answer.

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


def _reminder_body(prompt: str) -> str:
    """A reminder's message: the stored text, as booked (no model call)."""
    text = str(prompt or "").strip()
    return f"⏰ Reminder\n\n{text}"[:4000] if text else "⏰ Reminder"


def _late_note(due: datetime | None, now: datetime, zone: tzinfo, zone_name: str) -> str:
    """"(due 09:00 Asia/Kuwait, 25 min late)" for a fire well past its time."""
    if due is None:
        return ""
    late = (now - due).total_seconds()
    if late < 120:
        return ""
    local = due.astimezone(zone)
    minutes = int(late // 60)
    span = f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"
    return f"(due {local.strftime('%Y-%m-%d %H:%M')} {zone_name}, {span} late)"


def _web_session_for_thread(thread_id: str) -> Any:
    """The web chat session bound to *thread_id*, or None.

    A chat-store read (a Postgres round trip when the session is not cached);
    the target resolution runs it in a thread.
    """
    from kazma_ui.session_manager import get_session_manager

    mgr = get_session_manager()
    return mgr.get_by_thread_id(thread_id) if mgr else None


def _valid_target(t: str) -> bool:
    return bool(t) and ":" in t and not t.endswith(":")


def _accepts(builder: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(builder).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _outside_text_json(carried: dict[str, Any] | None) -> str:
    """The job's ``outside_text`` column for a booking turn's state."""
    if not isinstance(carried, dict) or not carried:
        return ""
    import json

    return json.dumps(carried, ensure_ascii=False, sort_keys=True)


def _outside_text_state(stored: str | None) -> dict[str, Any] | None:
    """The fired turn's starting ``outside_text``, or None (booked with none).
    A column that does not read as a state starts the turn having read
    outside text with no host named by the user: it can only have been
    written as one."""
    if not stored:
        return None
    import json

    try:
        value = json.loads(stored)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return {"read": True, "hosts": [], "user_hosts": []}
    return value


_STORED_INSTRUCTION_NOTE = (
    "You are running a scheduled task. The next user message is a STORED "
    "instruction the operator booked earlier (job {job_id}, booked {created}); it "
    "is not a live message, and nobody is watching while it runs. You may use only "
    "the tools you are offered ({capabilities}). Do NOT call send_message or "
    "dispatch_notification: the scheduler delivers your final answer to the "
    "operator's chat. Write that answer as plain text. Do not emit a ```plan fence."
)


# ══════════════════════════════════════════════════════════════════════════
# Cron Scheduler
# ══════════════════════════════════════════════════════════════════════════


class CronScheduler:
    """Polls for due jobs, fires them, and drains the delivery outbox.

    Args:
        store:          SQLiteCronStore for persistence.
        graph_builder:  Callable that builds a compiled graph for a task's fire
                        (called with ``capabilities=`` when it accepts it).
        poll_interval:  Seconds between polls (default 30).
        max_concurrent: Fires at once (default 4).
        drain_seconds:  How long ``stop()`` waits for fires in flight.
        clock:          Wall time and the poll loop's sleep; the store's clock
                        unless given (the real one in production, a fake one
                        in the qualification soak).
    """

    def __init__(
        self,
        store: SQLiteCronStore,
        graph_builder: Any = None,
        poll_interval: float = 30.0,
        max_concurrent: int = 4,
        *,
        drain_seconds: float = 20.0,
        clock: CronClock | None = None,
    ) -> None:
        self._store = store
        self._clock: CronClock = clock or (store.clock if isinstance(store, SQLiteCronStore) else SYSTEM_CLOCK)
        self._graph_builder = graph_builder
        self._poll_interval = poll_interval
        self._max_concurrent = max(1, int(max_concurrent))
        self._drain_seconds = max(0.0, float(drain_seconds))
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._in_flight: set[str] = set()
        #: Paused occurrences already said (S2-07), so a wait is said once.
        self._said_paused: set[tuple[str, str]] = set()
        #: Delivery ids this scheduler is sending now: recovery never calls
        #: them interrupted, however long the send outlives its lease
        #: (audit 2026-10-26 AUD-017), as ``_in_flight`` does for fires.
        self._sending: set[int] = set()
        # Strong refs to exec tasks -- the loop holds only weak refs, and a
        # GC'd task left its job id stuck in _in_flight.
        self._exec_tasks: set[asyncio.Task] = set()
        self._sem: asyncio.Semaphore | None = None
        self._poll_count: int = 0
        #: Who holds a fire's lease: one id per scheduler instance. A row
        #: leased by another id at start belongs to a process that is gone
        #: (the runtime-ownership fence admits one runtime per data dir).
        self._owner = f"cron-{uuid.uuid4().hex[:12]}"
        #: The poll loop's heartbeat (SCH-E11): wall time of the last pass.
        self.last_poll_at: float | None = None
        self.started_at: float | None = None
        #: One alert-outbox pass at a time; stop() takes it before cancelling,
        #: so a cancel never lands between raising an alert and marking it.
        self._alert_lock = asyncio.Lock()
        #: Rows raised in this process whose mark failed (a store error): the
        #: next pass marks them and never raises them again.
        self._alerts_raised: set[int] = set()
        #: Alerts queued and not yet raised, as the last pass left them (readiness).
        self.alerts_pending: int | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Recover what a previous process left, then start the polling loop."""
        if self._running:
            return
        self._running = True
        self.started_at = self._clock.time()
        self._sem = asyncio.Semaphore(self._max_concurrent)
        try:
            await self._store.purge_terminal_jobs()
        except Exception:  # noqa: BLE001 — the purge must never stop the scheduler
            logger.warning("[CronScheduler] startup purge failed", exc_info=True)
        try:
            await self._recover(boot=True)
        except Exception:  # noqa: BLE001
            logger.exception("[CronScheduler] recovery at start failed")
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        logger.info(
            "[CronScheduler] Started (poll_interval=%.0fs max_concurrent=%d)",
            self._poll_interval,
            self._max_concurrent,
        )

    async def stop(self) -> None:
        """Stop polling, then let fires in flight finish -- within a bound.

        ``asyncio.wait`` with a timeout, never ``wait_for``: a fire that does
        not end in time is cancelled and left ``running``, and the next start
        fires it again within its catch-up window (SCH-E04: stop() used to
        leave fires running under an agent that was then closed).

        Every cancel waits for an alert-outbox pass in progress to finish: a
        cancel between raising an alert and marking it sent would raise it
        again at the next start.
        """
        self._running = False
        if self._task:
            async with self._no_alert_pass():
                self._task.cancel()
            # Bounded (FA-137): a cancel the poll loop absorbed waited out a
            # whole poll interval, inside the guard's 60 s graceful stop.
            from kazma_core.bounded_cancel import cancel_and_wait

            await cancel_and_wait(self._task, what="the cron poll loop", timeout=5.0)
        pending = [t for t in self._exec_tasks if not t.done()]
        if pending:
            from kazma_core.shutdown import step_bound

            _done, still = await asyncio.wait(pending, timeout=step_bound(self._drain_seconds))
            if still:
                logger.warning(
                    "[CronScheduler] %d fire(s) still running after %.0fs; stopping them -- "
                    "each runs again at the next start", len(still), self._drain_seconds)
                async with self._no_alert_pass():
                    for t in still:
                        t.cancel()
                await asyncio.wait(still, timeout=5.0)
        logger.info("[CronScheduler] Stopped")

    @contextlib.asynccontextmanager
    async def _no_alert_pass(self, timeout: float = 10.0) -> AsyncIterator[None]:
        """Hold the alert outbox while tasks are cancelled (a pass in progress
        finishes first). Bounded: a pass that does not finish in *timeout* is
        cancelled anyway, and what it raised but did not mark is raised again
        at the next start."""
        held = False
        try:
            await asyncio.wait_for(self._alert_lock.acquire(), timeout)
            held = True
        except TimeoutError:
            logger.warning("[CronScheduler] an alert pass did not finish within %.0fs; stopping it anyway", timeout)
        try:
            yield
        finally:
            if held:
                self._alert_lock.release()

    def active_fire_count(self) -> int:
        """Fires in flight now (``/health/activity`` and ``--when-idle``)."""
        return len([t for t in self._exec_tasks if not t.done()])

    def fire_owner_alive(self, job: ScheduledJob) -> bool | None:
        """Whether a running job's fire still has its owner, as this scheduler knows.

        Its own fire is alive while its task runs (``_in_flight``), however
        long the turn takes; another owner's is not this scheduler's to say
        (None: judge by its lease). The run supervisor asks this: judged by
        the lease alone, a healthy 15-minute task fire was "stuck: nothing is
        running it" once its lease ran out (audit 2026-10-26 AUD-019).
        """
        if not job.fire_owner or job.fire_owner != self._owner:
            return None
        return job.job_id in self._in_flight

    async def metrics(self) -> list[str]:
        """Prometheus lines for the last 24 hours (SCH-E11): fires by outcome,
        deliveries by state, missed jobs, lateness p50/p95, the outbox, and
        the poll loop's heartbeat age."""
        since = self._clock.time() - 86400
        counts = await self._store.outcome_counts(since=since)
        late = sorted(await self._store.lateness(since=since))
        out = ["# HELP kazma_cron_fires_24h Scheduled fires in the last 24 hours, by outcome",
               "# TYPE kazma_cron_fires_24h gauge"]
        out += [f'kazma_cron_fires_24h{{outcome="{k[5:]}"}} {v}' for k, v in sorted(counts.items())
                if k.startswith("fire_")]
        out += ["# HELP kazma_cron_deliveries_24h Scheduled messages in the last 24 hours, by state",
                "# TYPE kazma_cron_deliveries_24h gauge"]
        out += [f'kazma_cron_deliveries_24h{{state="{k[9:]}"}} {v}' for k, v in sorted(counts.items())
                if k.startswith("delivery_")]
        out += ["# HELP kazma_cron_missed_24h Scheduled jobs missed while Kazma was down, last 24 hours",
                "# TYPE kazma_cron_missed_24h gauge", f"kazma_cron_missed_24h {counts.get('missed', 0)}"]
        out += ["# HELP kazma_cron_fire_lateness_seconds How late fires started, last 24 hours",
                "# TYPE kazma_cron_fire_lateness_seconds gauge"]
        for q in (0.5, 0.95):
            value = late[min(len(late) - 1, int(q * len(late)))] if late else 0.0
            out.append(f'kazma_cron_fire_lateness_seconds{{quantile="{q}"}} {value}')
        out += ["# HELP kazma_cron_outbox_pending Scheduled messages waiting to be sent",
                "# TYPE kazma_cron_outbox_pending gauge",
                f"kazma_cron_outbox_pending {await self._store.pending_delivery_count()}"]
        out += ["# HELP kazma_cron_alerts_pending Scheduled-job alerts recorded and not yet raised",
                "# TYPE kazma_cron_alerts_pending gauge",
                f"kazma_cron_alerts_pending {await self._store.pending_alert_count()}"]
        age = self.liveness().get("last_poll_age_s")
        out += ["# HELP kazma_cron_poll_age_seconds Seconds since the scheduler's last poll",
                "# TYPE kazma_cron_poll_age_seconds gauge", f"kazma_cron_poll_age_seconds {age if age is not None else -1}"]
        return out

    def liveness(self) -> dict[str, Any]:
        """The poll loop's heartbeat, for readiness (SCH-E11)."""
        now = self._clock.time()
        task_alive = self._task is not None and not self._task.done()
        last = self.last_poll_at
        age = None if last is None else round(now - last, 1)
        stale_after = 2 * self._poll_interval + 30.0
        if not self._running:
            state = "stopped"
        elif not task_alive:
            state = "dead"
        elif age is None:
            started = self.started_at or now
            state = "starting" if now - started < stale_after else "stale"
        elif age > stale_after:
            state = "stale"
        else:
            state = "ok"
        return {"state": state, "last_poll_age_s": age, "stale_after_s": stale_after,
                "fires_in_flight": self.active_fire_count(), "poll_interval_s": self._poll_interval,
                "alerts_pending": self.alerts_pending}

    # ── booking and the operator's actions ───────────────────────────────

    async def _zone(self, zone: tzinfo | None, zone_name: str | None) -> tuple[tzinfo, str]:
        if zone is not None and zone_name is not None:
            return zone, zone_name
        return await asyncio.to_thread(resolve_cron_zone)

    async def schedule(
        self,
        timing: str,
        prompt: str,
        platform: str = "telegram",
        thread_id: str = "",
        delivery_target: str = "",
        *,
        kind: str = "task",
        capabilities: str = "read",
        catchup_minutes: int | None = None,
        tenant_id: str | None = None,
        actor: str = "",
        source: str = "",
        zone: tzinfo | None = None,
        zone_name: str | None = None,
        now: datetime | None = None,
        outside_text: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Book a job. Raises ``ValueError`` (a :class:`TimingError` or
        :class:`CronLimitError`) with the reason when it cannot be booked.

        *timing* is read by the one time model (future, finite, the operator's
        zone, a typed recurrence). *kind*: ``reminder`` (deliver *prompt* as
        written, no model) or ``task`` (run the agent with *capabilities*).
        *outside_text*: the booking turn's state when it had read outside
        text (``safety.outside_text_fetch.delegation_state``); the fired
        turn starts with it.
        """
        from kazma_core.cron.capabilities import normalize_capabilities, normalize_kind
        from kazma_core.cron.settings import PROMPT_MAX_CHARS, cron_settings
        from kazma_core.cron.timing import resolve_timing

        kind = normalize_kind(kind, default="task")
        capabilities = normalize_capabilities(capabilities)
        text = str(prompt or "").strip()
        if not text:
            raise CronLimitError("A prompt is required.", code="prompt_required")
        if len(text) > PROMPT_MAX_CHARS:
            raise CronLimitError(f"A prompt is at most {PROMPT_MAX_CHARS} characters.", code="prompt_too_long")
        zone, zone_name = await self._zone(zone, zone_name)
        now = now or self._clock.now()
        resolved = resolve_timing(timing, zone=zone, zone_name=zone_name, now=now)
        if not tenant_id:
            # None named: the space of the work bound now (Spaces S1-02; ""
            # made the job the owner's whatever space booked it).
            from kazma_core.tenant_isolation import require_tenant_id

            tenant_id = require_tenant_id()
        from kazma_core.spaces.scope import bound_asker, spaces_enforced

        # The person who booked it (Spaces S1-14), always stamped: once Spaces
        # is enforced the job fires as them.
        creator = bound_asker() or ""
        if spaces_enforced():
            who = creator or await asyncio.to_thread(_acting_account, tenant_id)
            if who != _owner_account():
                # Every address Kazma can send to before people link their own
                # chat apps (S3-05) is the owner's: a job booked by anyone else
                # would carry their words into the owner's chat.
                raise CronLimitError(NO_OWN_ADDRESS, code="no_own_address")
        settings = await asyncio.to_thread(cron_settings)
        if await self._store.count_jobs(tenant_id=tenant_id) >= settings.max_jobs_per_tenant:
            raise CronLimitError(
                f"This install already holds {settings.max_jobs_per_tenant} scheduled jobs; cancel some "
                "first, or raise cron.max_jobs_per_tenant in Settings.", code="tenant_job_limit")
        if thread_id and await self._store.count_jobs(thread_id=thread_id) >= settings.max_jobs_per_thread:
            raise CronLimitError(
                f"This conversation already holds {settings.max_jobs_per_thread} scheduled jobs.",
                code="thread_job_limit")
        catchup = 0
        if catchup_minutes:
            catchup = max(1, min(7 * 24 * 60, int(catchup_minutes))) * 60
        job = ScheduledJob(
            job_id=f"cron-{uuid.uuid4().hex[:12]}",
            timing=" ".join(str(timing).split()),
            prompt=text,
            platform=platform,
            thread_id=thread_id,
            created_at=self._clock.now().isoformat(),
            next_run=norm_iso(resolved.first),
            tenant_id=tenant_id,
            delivery_target=delivery_target,
            kind=kind,
            capabilities=capabilities,
            catchup_seconds=catchup,
            series_start=norm_iso(resolved.first) if resolved.recurring else "",
            created_by=actor,
            updated_by=actor,
            source=source,
            outside_text=_outside_text_json(outside_text),
            creator=creator,
        )
        await self._store.insert(job)
        logger.info(
            "[CronScheduler] Scheduled %s (%s) for %s tenant=%s source=%s",
            job.job_id, kind, job.next_run, job.tenant_id, source or "-",
        )
        from kazma_core.cron.capabilities import describe_capabilities
        from kazma_core.cron.store import destination_label

        out = {
            "job_id": job.job_id,
            "timing": job.timing,
            "next_run": job.next_run,
            "status": "scheduled",
            "kind": kind,
            "capabilities": capabilities,
            "what_it_does": describe_capabilities(kind, capabilities),
            "recurring": resolved.recurring,
            "tenant_id": job.tenant_id,
            "destination": destination_label(delivery_target, platform),
            "version": job.version,
        }
        out.update(_fire_times_in_zone(resolved.first, zone, zone_name))
        return out

    async def list_jobs(self, *, tenant_id: str | None = None, scoped: bool | None = None,
                        zone: tzinfo | None = None, zone_name: str | None = None) -> list[dict[str, Any]]:
        """Jobs as dicts, the caller's tenant's in multi-user/production.

        *scoped*: pass the boundary's answer to "is this install tenant-scoped"
        (resolved off the loop); None asks here, in a thread.
        """
        if scoped is None:
            scoped, tenant_id = await asyncio.to_thread(_tenant_scope)
        jobs = await self._store.list_all(tenant_id=tenant_id if scoped else None)
        zone, zone_name = await self._zone(zone, zone_name)
        # A paused job says why (S2-07): its person is disabled.
        disabled = await self._disabled_accounts() if any(j.creator for j in jobs) else frozenset()
        out = []
        for job in jobs:
            d = job.to_dict()
            d.update(_fire_times_in_zone(job.next_run, zone, zone_name))
            if job.creator and job.creator in disabled and job.status.value in ("pending", "running"):
                d["paused"] = "creator_disabled"
            out.append(d)
        return out

    async def get_job(self, job_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        """One job with its whole prompt and recent history, or None
        (another tenant's id reads as unknown)."""
        job = await self._store.get(job_id, tenant_id=tenant_id)
        if job is None:
            return None
        out = job.to_dict()
        out["history"] = await self._store.list_events(job_id, limit=20)
        out["deliveries"] = await self._store.deliveries_for(job_id, limit=10)
        return out

    async def cancel(self, job_id: str, *, tenant_id: str | None = None, expected_version: int | None = None,
                     actor: str = "", source: str = "") -> dict[str, Any]:
        """Cancel a job. ``status`` is ``cancelled``, ``cancel_pending`` (it is
        firing: applied when the fire ends), ``not_found``, ``conflict`` or
        ``not_cancellable``."""
        status = await self._store.cancel(job_id, tenant_id=tenant_id, expected_version=expected_version,
                                          actor=actor, source=source)
        logger.info("[CronScheduler] cancel %s -> %s actor=%s source=%s", job_id, status, actor or "-",
                    source or "-")
        return {"status": status, "job_id": job_id}

    async def reschedule(
        self,
        job_id: str,
        *,
        timing: str | None = None,
        prompt: str | None = None,
        tenant_id: str | None = None,
        expected_version: int | None = None,
        actor: str = "",
        source: str = "",
        zone: tzinfo | None = None,
        zone_name: str | None = None,
        outside_text: dict[str, Any] | None = None,
        written_by_person: bool = False,
    ) -> dict[str, Any]:
        """Edit a job's timing and/or prompt.

        ``status``: ``rescheduled``; ``edit_pending`` (it is firing: applied
        when the fire ends); ``not_found``; ``conflict``; ``not_editable`` (it
        ended -- use :meth:`restore`); ``error`` (a bad timing or prompt).

        A new prompt takes the writer's outside-text state: the agent's turn
        state (*outside_text*, None when it had read none) or, when a person
        wrote it (*written_by_person*, the Scheduled page), none.
        """
        from kazma_core.cron.settings import PROMPT_MAX_CHARS
        from kazma_core.cron.timing import TimingError, resolve_timing

        next_run = series_start = None
        if prompt is not None:
            prompt = prompt.strip()
            if not prompt:
                prompt = None
            elif len(prompt) > PROMPT_MAX_CHARS:
                return {"status": "error", "job_id": job_id,
                        "error": f"A prompt is at most {PROMPT_MAX_CHARS} characters."}
        if timing:
            zone, zone_name = await self._zone(zone, zone_name)
            try:
                resolved = resolve_timing(timing, zone=zone, zone_name=zone_name, now=self._clock.now())
            except TimingError as exc:
                return {"status": "error", "job_id": job_id, "error": str(exc)}
            next_run = norm_iso(resolved.first)
            series_start = next_run if resolved.recurring else ""
            timing = " ".join(timing.split())
        else:
            timing = None
        pinned: int | None = None
        if prompt is not None or timing:
            refused, pinned = await self._change_rule(
                job_id, tenant_id, prompt=prompt is not None, recurring=bool(series_start))
            if refused:
                logger.info("[CronScheduler] edit %s -> %s actor=%s source=%s", job_id, refused,
                            actor or "-", source or "-")
                return {"status": refused, "job_id": job_id}
        carried: str | None = None
        if prompt is not None and (written_by_person or outside_text):
            carried = "" if written_by_person else _outside_text_json(outside_text)
        # Someone else's change was allowed for the job as it was read: it is
        # made only to that version, while it is still pending (a fire that
        # began meanwhile, or another change, answers conflict; S1-16
        # follow-up). A claim keeps the version, so pending is checked too.
        expected = expected_version if expected_version is not None else pinned
        status = await self._store.update_job(
            job_id, timing=timing, prompt=prompt, next_run=next_run, series_start=series_start,
            tenant_id=tenant_id, expected_version=expected, actor=actor, source=source,
            outside_text=carried, pending_only=pinned is not None)
        logger.info("[CronScheduler] edit %s -> %s (timing=%r prompt_edited=%s) actor=%s source=%s",
                    job_id, status, timing or "(unchanged)", prompt is not None, actor or "-", source or "-")
        return {"status": status, "job_id": job_id, "timing": timing or "", "next_run": next_run or ""}

    async def restore(self, job_id: str, *, timing: str, tenant_id: str | None = None,
                      expected_version: int | None = None, actor: str = "", source: str = "",
                      zone: tzinfo | None = None, zone_name: str | None = None) -> dict[str, Any]:
        """Run a finished job again, at *timing* (the explicit "Run again")."""
        from kazma_core.cron.timing import TimingError, resolve_timing

        refused, _pinned = await self._change_rule(job_id, tenant_id)
        if refused:
            logger.info("[CronScheduler] restore %s -> %s actor=%s source=%s", job_id, refused,
                        actor or "-", source or "-")
            return {"status": refused, "job_id": job_id}
        zone, zone_name = await self._zone(zone, zone_name)
        try:
            resolved = resolve_timing(timing, zone=zone, zone_name=zone_name, now=self._clock.now())
        except TimingError as exc:
            return {"status": "error", "job_id": job_id, "error": str(exc)}
        next_run = norm_iso(resolved.first)
        status = await self._store.restore_job(
            job_id, timing=" ".join(timing.split()), next_run=next_run,
            series_start=next_run if resolved.recurring else "", tenant_id=tenant_id,
            expected_version=expected_version, actor=actor, source=source)
        return {"status": status, "job_id": job_id, "next_run": next_run if status == "restored" else ""}

    async def _change_rule(self, job_id: str, tenant_id: str | None, *,
                           prompt: bool = True, recurring: bool = False) -> tuple[str, int | None]:
        """``(refusal, pinned version)`` for this change to *job_id*: the
        refusal of :meth:`_not_theirs_to_change`, and -- for a change someone
        else may make -- the version of the job it was allowed for, which the
        write must still find (a fire or another change in between answers
        ``conflict``, Spaces S1-16 follow-up). ``("", None)`` for the
        caller's own change, and while Spaces is not enforced."""
        found = await self._not_theirs_to_change(job_id, tenant_id, prompt=prompt, recurring=recurring)
        if isinstance(found, _SomeoneElses):
            return "", found.version
        return str(found or ""), None

    async def _not_theirs_to_change(self, job_id: str, tenant_id: str | None, *,
                                    prompt: bool = True, recurring: bool = False) -> str | _SomeoneElses:
        """Why the caller may not make this change to *job_id*, or ``""``
        (Spaces S1-14 review); for a change someone else may make, a
        :class:`_SomeoneElses` naming the version it was allowed for.

        Once Spaces is enforced a job fires as the account it acts for (its
        creator, ``_fire_binding``). Anyone else may only move a pending
        one-off to another one-off time: a new prompt (*prompt*, Run again's
        default) would put their words into that person's chat as that
        person, and a time change that makes it recurring (*recurring*), or
        one recorded while it fires (applied when the fire ends, which puts a
        finished one-off back), would run it again as them. ``not_yours``
        then; ``identity_unavailable`` when the identity store cannot say
        who the job acts for. Not enforced, and for a job the caller cannot
        see (the store answers ``not_found``), nothing is refused here.
        """
        from kazma_core.spaces.identity import IdentityUnavailable
        from kazma_core.spaces.scope import bound_asker, spaces_enforced

        if not spaces_enforced():
            return ""
        job = await self._store.get(job_id, tenant_id=tenant_id)
        if job is None:
            return ""
        try:
            binding = await asyncio.to_thread(_fire_binding, job)
        except IdentityUnavailable:
            logger.warning("[CronScheduler] who %s acts for cannot be read; the change is refused", job_id)
            return "identity_unavailable"
        acts_for = binding.asker if binding is not None else ""
        caller = bound_asker() or ""
        if caller and caller == acts_for:
            return ""
        if prompt or recurring or job.status != JobStatus.PENDING or job.series_start:
            return "not_yours"
        # Someone else moving a pending one-off to another one-off time: for
        # this version only (_change_rule).
        return _SomeoneElses(version=int(job.version))

    async def _hold_for_identity(self, job: ScheduledJob, exc: BaseException) -> bool:
        """A fire whose binding cannot be read because the database is away
        gives its claim back: the job is pending again at the same
        occurrence, as a task held while the database is away (§44), and the
        availability watch holds the next passes before they claim. True
        then. False for any other failure (a registry that is not a mapping):
        the caller fails the fire once, never claims it again every pass.
        A locked or busy SQLite settings file passes too, so it is held the
        same way, without being an outage (Spaces S1-16 follow-up)."""
        from kazma_core.db import availability

        cause = exc.__cause__ if exc.__cause__ is not None else exc
        if not (_sqlite_lock_passes(cause) or availability.note_failure("scheduled", cause)):
            logger.error("[CronScheduler] %s: who it fires as cannot be read (%s); the fire fails", job.job_id,
                         type(cause).__name__)
            return False
        released = await self._store.release_fire(job.job_id, owner=self._owner, reason="identity_unavailable")
        logger.warning("[CronScheduler] %s waits: who it fires as cannot be read (%s); %s", job.job_id,
                       type(cause).__name__, "held for the next pass" if released else "the claim was gone")
        return True

    async def _pause_fire(self, job: ScheduledJob, refusal: str) -> None:
        """A fire whose job is paused (``_PAUSES``, S2-07) gives its claim
        back: the job waits at the same occurrence, and a later pass fires
        it once the pause ends -- or, past its catch-up window, records it
        missed with the reason. Said once per occurrence."""
        released = await self._store.release_fire(job.job_id, owner=self._owner, reason=refusal)
        key = (job.job_id, str(job.next_run))
        if key not in self._said_paused:
            if len(self._said_paused) >= 1000:
                self._said_paused.clear()
            self._said_paused.add(key)
            logger.info("[CronScheduler] %s waits (%s); %s", job.job_id, refusal,
                        "held for a later pass" if released else "the claim was gone")

    # ── the poll loop ────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Check every N seconds for due jobs, recover, and drain the outbox."""
        while self._running:
            try:
                from kazma_core.shutdown import is_shutting_down

                if is_shutting_down():
                    logger.info("[CronScheduler] shutdown signal — exiting poll loop")
                    self._running = False
                    break
            except ImportError:
                pass
            self.last_poll_at = self._clock.time()
            try:
                await self._poll_once()
            except Exception:  # noqa: BLE001 — one bad pass must not end the loop
                logger.exception("[CronScheduler] Poll error")
            self._poll_count += 1
            if self._poll_count % 120 == 0:
                try:
                    await self._store.purge_terminal_jobs()
                except Exception:  # noqa: BLE001
                    # A purge that fails every pass lets cron.db grow forever.
                    logger.warning("[CronScheduler] purge of finished jobs failed", exc_info=True)
            await self._clock.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        await self._recover(boot=False)
        now = self._clock.now()
        sem = self._sem or asyncio.Semaphore(self._max_concurrent)
        settings = None
        held = 0
        waiting = 0
        paused: frozenset[str] | None = None
        from kazma_core.db import availability
        from kazma_core.spaces.scope import spaces_enforced

        for job in await self._store.due_jobs(now=now, limit=max(50, self._max_concurrent * 10)):
            if not self._running:
                break
            if job.job_id in self._in_flight:
                continue
            if settings is None:
                from kazma_core.cron.settings import cron_settings

                settings = await asyncio.to_thread(cron_settings)
            if self._is_missed(job, now, settings.catchup_seconds):
                await self._record_missed(job, now)
                continue
            if availability.is_away() and (job.kind != "reminder" or spaces_enforced()):
                # A task fire runs an agent turn on the settings, the vault's
                # keys and the model list: with the database away it only
                # fails and spends one of its three runs. It waits for the
                # database; past its catch-up window it is missed, and says
                # so, as any late job. A reminder needs only cron.db and the
                # chat apps, and still rings -- except once Spaces is
                # enforced: who it fires as is read from the identity store,
                # which is in the database (S1-14 review).
                held += 1
                continue
            if spaces_enforced() and job.creator:
                # A job whose person is disabled waits (S2-07): not claimed,
                # so not fired; past its window it is missed, with the reason.
                if paused is None:
                    paused = await self._disabled_accounts()
                if job.creator in paused:
                    waiting += 1
                    continue
            # Every due job of the pass is started; the semaphore holds all
            # but max_concurrent until a slot frees (the claim, and so the
            # lease, comes after it). Starting only max_concurrent a pass left
            # the rest for later passes: 50 reminders due at once took 13
            # passes -- six minutes at the default interval (SCH-E14).
            self._in_flight.add(job.job_id)
            exec_task = asyncio.create_task(self._execute_bounded(job, sem), name=f"cron-exec-{job.job_id}")
            self._exec_tasks.add(exec_task)
            exec_task.add_done_callback(self._exec_done(job.job_id))
        if held:
            logger.debug("[CronScheduler] %d task fire(s) wait for the database", held)
        if waiting:
            logger.debug("[CronScheduler] %d job(s) wait: the person who booked them is disabled", waiting)
        await self._drain_deliveries()
        await self._drain_alerts()

    async def _disabled_accounts(self) -> frozenset[str]:
        """The disabled accounts, read once a pass (S2-07); none when the
        identity store cannot answer -- the fire's own binding decides then."""
        from kazma_core.spaces import identity

        try:
            return await asyncio.to_thread(identity.disabled_accounts)
        except identity.IdentityUnavailable:
            return frozenset()

    def _exec_done(self, job_id: str) -> Callable[[asyncio.Task], None]:
        def done(t: asyncio.Task) -> None:
            self._exec_tasks.discard(t)
            self._in_flight.discard(job_id)
            # Retrieve the exception, or it is lost ("Task exception was
            # never retrieved") and the job sits RUNNING with nobody saying
            # why (SCH-E04). The next pass recovers the fire.
            if not t.cancelled() and t.exception() is not None:
                logger.error("[CronScheduler] fire of %s ended with an error; it will be recovered",
                             job_id, exc_info=t.exception())
        return done

    async def _execute_bounded(self, job: ScheduledJob, sem: asyncio.Semaphore) -> None:
        """Run job under the global concurrency semaphore."""
        async with sem:
            await self._execute(job)

    @staticmethod
    def _is_missed(job: ScheduledJob, now: datetime, default_catchup_s: int) -> bool:
        """Due longer ago than the job's catch-up window (a day, a reload...)."""
        try:
            due = datetime.fromisoformat(str(job.next_run))
        except (TypeError, ValueError):
            return False
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        window = job.catchup_seconds or default_catchup_s
        return (now - due).total_seconds() > window

    # ── recovery ─────────────────────────────────────────────────────────

    async def _recover(self, *, boot: bool) -> None:
        """Decide, per phase, what an interrupted fire and send left (SCH-E04).

        A running job whose fire nobody holds produced nothing (a fire's
        result and its delivery commit together), so it goes back to pending
        at the same occurrence and fires again -- or is recorded missed by the
        catch-up rule if that took too long. It used to be marked FAILED,
        delivered or not. A send whose sender died ends ``unknown`` with an
        alert: it may have arrived, and it is never sent twice. At a start,
        the alerts a dead process committed and never raised are raised here.
        """
        now_ts = self._clock.time()
        for job in await self._store.list_running():
            if boot:
                # One runtime per data dir: at start, every lease is a dead one's.
                orphaned = job.fire_owner != self._owner
            elif job.fire_owner == self._owner:
                # Ours, but its task is gone (it raised): recover now.
                orphaned = job.job_id not in self._in_flight
            else:
                orphaned = job.lease_until < now_ts
            if not orphaned:
                continue
            if await self._store.release_fire(job.job_id, owner=job.fire_owner,
                                              reason="interrupted" if boot else "lease expired"):
                logger.warning("[CronScheduler] fire of %s was interrupted (owner %s); it runs again",
                               job.job_id, job.fire_owner or "-")
        # A send this scheduler is making now is not interrupted, even past
        # its lease (AUD-017); at start nothing of ours is being sent.
        recovered = await self._store.recover_deliveries(
            owner=self._owner, live=() if boot else frozenset(self._sending))
        for row in recovered:
            logger.warning("[CronScheduler] delivery %s of %s was interrupted mid-send; outcome unknown",
                           row["id"], row["job_id"])
        if boot or recovered:
            await self._drain_alerts()

    # ── the fire ─────────────────────────────────────────────────────────

    async def _execute(self, job: ScheduledJob) -> None:
        """Fire one job: claim, produce, finish in one transaction, deliver."""
        from kazma_core.cron.settings import cron_settings

        # In flight BEFORE the claim commits: recovery treats our own running
        # row that is not in flight as a dead fire.
        self._in_flight.add(job.job_id)
        settings = await asyncio.to_thread(cron_settings)
        # The lease covers the whole fire: the task budget (up to an hour)
        # plus the time around the turn. It was a fixed 600 s, so a healthy
        # 15-minute task fire outlived its own lease (audit 2026-10-26 AUD-019).
        lease = max(_FIRE_LEASE_MIN_S, float(settings.task_budget_seconds) + _FIRE_LEASE_MARGIN_S)
        claimed = await self._store.claim_job(job.job_id, owner=self._owner, lease_seconds=lease,
                                              expected_version=job.version)
        if not claimed:
            self._in_flight.discard(job.job_id)
            logger.debug("[CronScheduler] %s claimed by another instance or changed — skipping", job.job_id)
            return
        from kazma_core.observability.log_words import words_for_log

        logger.info("[CronScheduler] Executing %s (%s): %s", job.job_id, job.kind, words_for_log(job.prompt, 80))

        # Bind the cron-parent context for the whole execution: jobs the
        # agent RESCHEDULES from inside this turn inherit this job's
        # delivery_target/platform instead of being born targetless (the
        # 2026-08-27 incident).
        parent_token = _cron_parent_ctx.set(
            {
                "job_id": job.job_id,
                "delivery_target": job.delivery_target or "",
                "platform": job.platform or "",
                "thread_id": job.thread_id or "",
            }
        )
        # Install the job's tenant for the whole execution: a cron turn ran
        # context-less, and the vault resolves a tenant-scoped secret only
        # for its tenant (live 2026-09-12: both daily reminders failed with
        # "no usable API key" while the key was there). Fixed here rather than
        # by loosening the vault's fallback.
        from kazma_core.spaces.scope import space_of_record
        from kazma_core.tenant_context import (
            reset_current_tenant_id,
            set_current_tenant_id,
        )

        # A job stored with no tenant was booked before Spaces: the owner's (N19).
        tenant_token = set_current_tenant_id(space_of_record(job.tenant_id))
        # Once Spaces is enforced the fire runs as a whole scope: the job's
        # space and the account that booked it (S1-14). A job whose tenant is
        # no space of this install, or one that acts for anyone but the owner
        # while the owner's are the only addresses, does not fire. Inside the
        # try: an identity store that cannot answer gives the claim back (the
        # database-away wait), and the context is reset either way (S1-14
        # review: outside it, the claim stayed RUNNING with nothing reset).
        scope_tokens = None
        try:
            from kazma_core.spaces.identity import IdentityUnavailable

            try:
                binding = await asyncio.to_thread(_fire_binding, job)
            except IdentityUnavailable as exc:
                if await self._hold_for_identity(job, exc):
                    return
                binding = _FireBinding(space="", asker="", refusal="identity_unavailable")
            if binding is not None and binding.refusal in _PAUSES:
                await self._pause_fire(job, binding.refusal)
                return
            if binding is not None and not binding.refusal:
                from kazma_core.spaces.scope import set_scope

                scope_tokens = set_scope(binding.space, binding.asker)
            zone, zone_name = await asyncio.to_thread(resolve_cron_zone)
            current = await self._store.get(job.job_id) or job
            now = self._clock.now()
            due = _parse_dt(current.fire_due or current.next_run)
            if binding is not None and binding.refusal:
                result = FireResult(success=False, outcome=binding.refusal,
                                    result_text=_fire_refusal_text(binding.refusal, current.prompt))
            else:
                result = await self._produce(current, settings, zone, zone_name, due=due, now=now)
            result.target = await self._resolve_target(current)
            result.lateness_s = max(0.0, (now - due).total_seconds()) if due else 0.0
            advance = self._advancer(zone)
            done = await self._store.finish_fire(job.job_id, owner=self._owner, result=result, advance=advance)
            if done is None:
                logger.warning("[CronScheduler] %s: this fire lost its lease; its outcome was not recorded",
                               job.job_id)
                return
            logger.info("[cron] outcome job=%s outcome=%s status=%s", job.job_id, result.outcome, done["status"])
            if done.get("budget_exhausted"):
                logger.error("[CronScheduler] Recurring job %s exceeded failure budget → marked FAILED", job.job_id)
            if done.get("delivery_id"):
                await self._drain_deliveries(only_id=done["delivery_id"])
            # A failed fire's alert committed with its outcome; raise it now.
            await self._drain_alerts()
        finally:
            if scope_tokens is not None:
                from kazma_core.spaces.scope import reset_scope

                reset_scope(scope_tokens)
            reset_current_tenant_id(tenant_token)
            _cron_parent_ctx.reset(parent_token)
            self._in_flight.discard(job.job_id)

    async def _produce(self, job: ScheduledJob, settings: Any, zone: tzinfo, zone_name: str, *,
                       due: datetime | None, now: datetime) -> FireResult:
        """What this fire delivers, or why it failed. Never raises (but cancellation)."""
        late = _late_note(due, now, zone, zone_name)
        if job.kind == "reminder":
            body = _reminder_body(job.prompt)
            if late:
                body = body.replace("⏰ Reminder", f"⏰ Reminder {late}", 1)
            return FireResult(success=True, outcome="delivering", result_text="Reminder sent as booked.",
                              body=body)
        from kazma_core.cron.settings import hitl_enabled

        if not settings.fire_without_hitl and not await asyncio.to_thread(hitl_enabled):
            reason = ("Approvals are switched off, so scheduled agent tasks do not run unattended. "
                      "Turn approvals on, or allow it in Settings (safety.cron_fire_without_hitl).")
            return FireResult(success=False, outcome="hitl_off", result_text=reason)
        if self._graph_builder is None:
            return FireResult(success=False, outcome="error", result_text="Error: No graph builder configured")
        try:
            graph = await asyncio.to_thread(self._build_graph, job.capabilities)
            from kazma_core.cron.capabilities import describe_capabilities

            config = {"configurable": {"thread_id": f"cron-{job.job_id}"}}
            state: dict[str, Any] = {
                "messages": [
                    {"role": "system", "content": _STORED_INSTRUCTION_NOTE.format(
                        job_id=job.job_id, created=job.created_at,
                        capabilities=describe_capabilities(job.kind, job.capabilities))},
                    {"role": "user", "content": job.prompt},
                ],
            }
            # Booked by a turn that had read outside text: the stored prompt
            # is that model's words, so the fired turn starts having read
            # outside text, with the hosts the real user named
            # (safety.outside_text_fetch.delegation_state).
            carried = _outside_text_state(job.outside_text)
            if carried is not None:
                state["outside_text"] = carried
            from kazma_core.agent.turn import run_agent_turn

            # The turn runs in this fire's own task: on Python 3.11
            # ``asyncio.wait_for`` ran it in a task of its own, which a second
            # cancel of the fire left running after the fire had ended
            # (tests/test_work_deadlines_stay_in_the_task.py).
            async with asyncio.timeout(settings.task_budget_seconds):
                turn = await run_agent_turn(graph=graph, thread_id=str(config["configurable"]["thread_id"]),
                                            state=state, config=config, persist=False)
        except TimeoutError:
            logger.warning("[CronScheduler] %s timed out", job.job_id)
            return FireResult(success=False, outcome="timed_out",
                              result_text=f"Timed out after {int(settings.task_budget_seconds)}s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a fire that cannot run is a failed fire
            logger.exception("[CronScheduler] %s failed", job.job_id)
            return FireResult(success=False, outcome="error", result_text=f"Error: {str(exc)[:500]}")
        error = getattr(turn, "error", None)
        failed = bool(getattr(turn, "turn_failed", False))
        interrupted = bool(getattr(turn, "interrupted", False))
        if error or failed or interrupted:
            reason = str(error or ("The turn stopped at an approval nobody can answer."
                                   if interrupted else "The turn failed."))
            logger.warning("[CronScheduler] %s: the agent turn failed: %s", job.job_id, reason[:200])
            return FireResult(success=False, outcome="turn_failed", result_text=f"Turn failed: {reason[:500]}")
        summary = (getattr(turn, "text", "") or "")[:2000]
        body = compose_cron_delivery(summary, job.prompt)
        if late:
            body = f"{body}\n\n{late}"
        return FireResult(success=True, outcome="delivering", result_text=summary, body=body)

    def _build_graph(self, capabilities: str) -> Any:
        builder = self._graph_builder
        if _accepts(builder, "capabilities"):
            return builder(capabilities=capabilities)
        return builder()

    def _advancer(self, zone: tzinfo) -> Callable[[str, datetime, str], datetime | None]:
        """The pure next-occurrence function :meth:`SQLiteCronStore.finish_fire` applies."""
        from kazma_core.cron.timing import TimingError, next_occurrence, parse_recurrence

        def advance(timing: str, after: datetime, series_start: str) -> datetime | None:
            try:
                rec = parse_recurrence(timing)
            except TimingError:
                return None
            if rec is None:
                return None
            start = _parse_dt(series_start)
            return next_occurrence(rec, zone=zone, after=after, series_start=start)

        return advance

    async def _resolve_target(self, job: ScheduledJob) -> str:
        """The job's delivery address, with the repair chain.

        1. ``job.delivery_target`` when well-formed (``platform:id``).
        2. A sibling job's valid target on the same thread -- adopted AND
           persisted so the broken row self-heals (2026-08-27).
        3. The session store for the thread (best effort; 5-minute TTL).
        ``""`` when nothing works: the outbox records it undeliverable, with
        an alert naming the job -- never a guessed address.
        """
        target_id = str(job.delivery_target or "").strip()
        if _valid_target(target_id):
            return target_id
        healed = ""
        try:
            healed = await self._store.sibling_delivery_target(job.thread_id, job.tenant_id)
        except Exception:  # noqa: BLE001
            logger.warning("[CronScheduler] sibling target lookup failed for %s", job.job_id, exc_info=True)
        if _valid_target(healed):
            logger.warning("[CronScheduler] %s had malformed/empty delivery_target %r — adopted sibling "
                           "target (persisted)", job.job_id, target_id or job.thread_id)
            try:
                await self._store.update_delivery_target(job.job_id, healed)
            except Exception:  # noqa: BLE001
                logger.warning("[CronScheduler] target repair persist failed", exc_info=True)
            return healed
        try:
            sess = await asyncio.to_thread(_web_session_for_thread, job.thread_id)
            if sess is not None:
                plat = str(getattr(sess, "platform", "") or job.platform or "")
                chat = str(getattr(sess, "chat_id", "") or "")
                if plat and chat and _valid_target(f"{plat}:{chat}"):
                    return f"{plat}:{chat}"
        except Exception:  # noqa: BLE001 — best effort
            logger.debug("[CronScheduler] session lookup failed for %s", job.job_id, exc_info=True)
        return ""

    async def _record_missed(self, job: ScheduledJob, now: datetime) -> None:
        # The fire rule holds for the notice too (S1-14 review): a job that
        # would not fire (no space, or acting for anyone but the owner) is
        # recorded missed and queues nothing -- its notice carries its prompt.
        from kazma_core.spaces.identity import IdentityUnavailable

        try:
            binding = await asyncio.to_thread(_fire_binding, job)
        except IdentityUnavailable as exc:
            from kazma_core.db import availability

            if availability.note_failure("scheduled", exc.__cause__ if exc.__cause__ is not None else exc):
                logger.warning("[CronScheduler] %s missed, but who it acts for cannot be read; recorded next "
                               "pass", job.job_id)
                return
            # Not an outage: recorded missed now, and no notice (who would
            # read it cannot be told).
            binding = _FireBinding(space="", asker="", refusal="identity_unavailable")
        refused = binding.refusal if binding is not None else ""
        zone, zone_name = await asyncio.to_thread(resolve_cron_zone)
        due = _parse_dt(job.next_run)
        when = due.astimezone(zone).strftime("%Y-%m-%d %H:%M") + f" {zone_name}" if due else "earlier"
        if job.kind == "reminder":
            notice = f"⏰ Missed while Kazma was offline (due {when}):\n\n{job.prompt.strip()}"
        else:
            notice = (f"⏰ A scheduled task did not run: it was due {when}, while Kazma was offline.\n\n"
                      f"Task: {job.prompt.strip()[:500]}")
        target = "" if refused else await self._resolve_target(job)
        reason = _fire_refusal_text(refused, job.prompt) if refused in _PAUSES else ""
        res = await self._store.mark_missed(job.job_id, expected_next_run=str(job.next_run), notice=notice,
                                            target=target, advance=self._advancer(zone), now=now,
                                            refused=refused, reason=reason)
        if res is None:
            return
        logger.warning("[cron] outcome job=%s outcome=missed status=%s (due %s)", job.job_id, res["status"], when)
        if res.get("delivery_id"):
            # A notice that cannot be delivered queues its own alert.
            await self._drain_deliveries(only_id=res["delivery_id"])
            await self._drain_alerts()

    # ── the delivery outbox ──────────────────────────────────────────────

    async def _drain_deliveries(self, *, only_id: int | None = None, limit: int = 20) -> int:
        """Send due deliveries; acknowledge only what a platform took.

        A message that ends undeliverable queues its alert in the same
        transaction; the caller drains the alert outbox after this."""
        delivered = 0
        for _ in range(1 if only_id is not None else limit):
            row = await self._store.claim_delivery(owner=self._owner, delivery_id=only_id)
            if row is None:
                break
            # Live until finish_delivery has recorded it; a send whose task
            # ended without that is no longer in the set, and recovery takes
            # it once its lease lapses.
            self._sending.add(int(row["id"]))
            try:
                ok, error = await self._send(row)
                res = await self._store.finish_delivery(row["id"], owner=self._owner, delivered=ok, error=error)
            finally:
                self._sending.discard(int(row["id"]))
            if res is None:
                continue
            delivered += int(ok)
            if res["state"] == "undeliverable":
                logger.critical(
                    "[CronScheduler] UNDELIVERABLE job %s ('%.60s'): %s. Result NOT sent — fix the "
                    "job's delivery target or re-create it from a live conversation.",
                    row["job_id"], row["body"], error or "no valid delivery target")
            logger.info("[cron] outcome job=%s outcome=delivery_%s status=-", row["job_id"], res["state"])
        return delivered

    async def _send(self, row: dict[str, Any]) -> tuple[bool, str]:
        """One send attempt. ``(True, "")`` only on ``sent:``."""
        target = str(row.get("target") or "")
        job_id = row.get("job_id")
        if not _valid_target(target):
            return False, "no valid delivery target"
        from kazma_core.tools.send_message import send_failed, send_message

        try:
            logger.info("[CronScheduler] delivering %s -> %s (attempt %d)", job_id, target, int(row["attempts"]))
            result = await send_message(target, str(row["body"]), backend=target.split(":", 1)[0])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a send that raises is a refused send
            logger.warning("[CronScheduler] delivery FAILED for %s -> %s: %s", job_id, target, exc)
            return False, f"{type(exc).__name__}: {str(exc)[:300]}"
        if send_failed(result) or not str(result).startswith("sent:"):
            logger.warning("[CronScheduler] delivery FAILED for %s -> %s: %s", job_id, target, result)
            return False, str(result)[:500]
        logger.info("[CronScheduler] delivered %s -> %s", job_id, target)
        return True, ""

    # ── the alert outbox ─────────────────────────────────────────────────

    async def _drain_alerts(self, *, limit: int = 50) -> int:
        """Raise the alerts the outbox holds; mark each sent once raised.

        An alert's row commits with the change that needs it (a failed fire,
        a budget run out, a message undeliverable or unknown), so a crash
        before this runs loses nothing: the next pass, or the next start,
        raises it. A row is marked sent only after ``ops_alerts.alert``
        returned: a crash between the two raises it once more at the next
        start -- the only duplicate. Within a process a row is raised once:
        one pass at a time (a call while a pass runs returns at once; that
        pass, or the next poll, raises what was queued), and a row whose mark
        failed is marked by the next pass, never raised again.

        Never raises for the store: a pass the database refuses (a held
        lock) is a WARNING, and its alerts wait for the next pass.
        """
        if self._alert_lock.locked():
            return 0
        raised = 0
        async with self._alert_lock:
            try:
                for alert_id in sorted(self._alerts_raised):
                    await self._store.mark_alert_sent(alert_id)
                    self._alerts_raised.discard(alert_id)
                while raised < limit:
                    rows = [r for r in await self._store.pending_alerts(limit=limit - raised)
                            if int(r["id"]) not in self._alerts_raised]
                    if not rows:
                        break
                    progress = 0
                    for row in rows:
                        progress += int(await self._raise_alert(row))
                    raised += progress
                    if not progress:
                        break  # every row failed: each waits out its backoff
                self.alerts_pending = await self._store.pending_alert_count()
            except sqlite3.Error as exc:
                logger.warning("[CronScheduler] the alert outbox could not be read or marked (%s); its alerts "
                               "are raised at the next pass", exc)
        return raised

    async def _raise_alert(self, row: dict[str, Any]) -> bool:
        """Raise one queued alert, then mark it sent. False when it could not
        be raised: it stays queued, with a WARNING, tried again after a backoff.

        The one place the cron engine calls ``ops_alerts.alert`` (gate:
        ``tests/test_cron_alert_outbox.py``).
        """
        from kazma_core.observability.ops_alerts import alert

        alert_id = int(row["id"])
        try:
            # alert() reads its routing settings: off the loop (SCH-E10).
            await asyncio.to_thread(alert, str(row["ops_key"]), str(row["title"]), detail=str(row["detail"]),
                                    severity=str(row["severity"]))
        except (RuntimeError, OSError) as exc:
            # ops_alerts.alert never raises (it catches everything itself);
            # what reaches here is the hand-off to the thread (an executor
            # shutting down). One alert that cannot be raised must not hold
            # up the rest: it stays queued.
            logger.warning("[CronScheduler] alert %d (%s, job %s) could not be raised; it stays queued and is "
                           "tried again: %s", alert_id, row["ops_key"], row["job_id"], exc)
            await self._store.alert_failed(alert_id, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return False
        self._alerts_raised.add(alert_id)
        await self._store.mark_alert_sent(alert_id)
        self._alerts_raised.discard(alert_id)
        return True


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


#: Why a booking is refused once Spaces is enforced (code ``no_own_address``).
NO_OWN_ADDRESS = (
    "Kazma can only send a scheduled message to the owner's chat app for now, "
    "so only the owner can book one. Sending to your own chat app comes later."
)

#: Why a fire is refused once Spaces is enforced, by outcome.
_FIRE_REFUSALS = {
    "no_space": "Error: this job belongs to no space of this install, so it does not fire.",
    "no_own_address": ("Error: this job acts for someone other than the owner, and the only "
                       "addresses Kazma has are the owner's, so it does not fire."),
    "identity_unavailable": ("Error: the accounts of this install cannot be read, so who this job "
                             "acts for is unknown and it does not fire."),
    "feature_off": ("Error: Scheduled is off for the person this job acts for (Settings → "
                    "People), so it does not fire."),
    "asker_left": ("Error: the person who booked this job is no longer in its space, so it "
                   "does not fire."),
    "creator_disabled": ("Paused: the person who booked this job is disabled, so it does not run "
                         "until the admin enables them (Settings → People)."),
}

#: Refusals that pause a job rather than fail it (S2-07): the fire gives its
#: claim back, the job waits at its occurrence, and past its catch-up window
#: the occurrence is missed with the reason -- never a failure.
_PAUSES = frozenset({"creator_disabled"})

#: The refusals said in Arabic too, for a job written in Arabic.
_FIRE_REFUSALS_AR = {
    "feature_off": ("خطأ: ميزة المجدولة غير مفعّلة للشخص الذي تعمل له هذه المهمة (الإعدادات ← "
                    "الأشخاص)، فلا تعمل."),
    "asker_left": "خطأ: الشخص الذي حجز هذه المهمة لم يعد في مساحتها، فلا تعمل.",
    "creator_disabled": ("متوقفة مؤقتًا: حساب الشخص الذي حجز هذه المهمة معطّل، فلا تعمل حتى يفعّله "
                         "المسؤول (الإعدادات ← الأشخاص)."),
}


def _fire_refusal_text(refusal: str, prompt: str | None) -> str:
    """A refused fire's text, in the language the job was written in."""
    arabic = _FIRE_REFUSALS_AR.get(refusal)
    if arabic is None:
        return _FIRE_REFUSALS[refusal]
    from kazma_core.language_lock import in_their_language

    return in_their_language(prompt, _FIRE_REFUSALS[refusal], arabic)


@dataclass(frozen=True)
class _FireBinding:
    """What a fire runs as once Spaces is enforced: the job's space and the
    account it acts for, or why it does not fire (``refusal``, a key of
    ``_FIRE_REFUSALS``)."""

    space: str
    asker: str
    refusal: str = ""


def _owner_account() -> str:
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    return OWNER_ACCOUNT_ID


def _acting_account(space_id: str | None) -> str:
    """``identity.acting_account``. Blocking (the identity store)."""
    from kazma_core.spaces import identity

    return identity.acting_account(str(space_id or ""))


@dataclass(frozen=True)
class _SomeoneElses:
    """A change to a job that acts for someone else, allowed for the job at
    *version* only (``CronScheduler._change_rule``)."""

    version: int


def _sqlite_lock_passes(exc: BaseException) -> bool:
    """A locked or busy SQLite file: it passes once the writer is done, as an
    outage does when Postgres comes back (it is never one: the availability
    watch is Postgres's)."""
    import sqlite3

    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def _fire_binding(job: ScheduledJob) -> _FireBinding | None:
    """How *job* fires (Spaces S1-14). Blocking (the identity store).

    None while Spaces is not enforced: the job's tenant alone is bound, as
    before. Enforced: the job's space, as the account that booked it -- or,
    for a job booked with no person bound, the account Kazma's own work for
    that space acts for (``identity.acting_account``). It does not fire
    (``refusal``) when the job's tenant is no space of the install
    (``no_space``), or when it acts for anyone but the owner
    (``no_own_address``): until people link their own chat apps (S3-05)
    every address a job can deliver to is the owner's. Nor when the person
    it acts for no longer has the feature Scheduled (``feature_off``, S2-11:
    the check made again when the work runs). A job whose person is disabled
    waits (``creator_disabled``, S2-07: a pause, never a failure).
    """
    from kazma_core.spaces import identity
    from kazma_core.spaces.features import grants_of, work_refusal
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return None
    try:
        found = identity.work_scope(str(job.tenant_id or ""), job.creator)
    except identity.AskerLeft:
        # Never fired as someone else (the S2-12 review, finding 6).
        return _FireBinding(space=str(job.tenant_id or ""), asker="", refusal="asker_left")
    if found is None:
        return _FireBinding(space="", asker="", refusal="no_space")
    space, asker = found
    if grants_of(identity.read_registry(), asker).disabled:
        return _FireBinding(space=space, asker=asker, refusal="creator_disabled")
    if work_refusal("scheduled", asker):
        return _FireBinding(space=space, asker=asker, refusal="feature_off")
    if asker != _owner_account():
        return _FireBinding(space=space, asker=asker, refusal="no_own_address")
    return _FireBinding(space=space, asker=asker)


def _tenant_scope() -> tuple[bool, str | None]:
    """``(scoped, tenant)`` -- the one tenant rule of every Scheduled surface:
    reads and changes alike are the caller's tenant's in multi-user or
    production mode (SCH-S03: reads were scoped, changes were not).

    Blocking (an RBAC read): async callers use ``asyncio.to_thread``.
    """
    from kazma_core.spaces.scope import spaces_enforced
    from kazma_core.tenant_isolation import multi_user_or_production, require_tenant_id

    if spaces_enforced():
        # Every Scheduled read and change keeps to the caller's space (S1-14).
        return True, require_tenant_id()

    try:
        scoped = multi_user_or_production()
    except Exception:  # noqa: BLE001 — fail closed: scope to the caller
        scoped = True
    return scoped, (require_tenant_id() if scoped else None)


def scheduled_tenant_filter() -> str | None:
    """The tenant every Scheduled read and change is limited to, or None
    (single-operator, not production: the install's whole schedule).
    Blocking: async callers use ``asyncio.to_thread``."""
    scoped, tenant = _tenant_scope()
    return tenant if scoped else None
