"""Durable task queue for V2 memory consolidation.

Replaces the volatile ``loop.create_task`` dispatch for heavy
consolidation work (belief extraction, entity merge, macro sleep) with
a SQLite-backed queue that survives crashes. Pending rows are reclaimed
on worker restart; failed tasks retry up to ``max_attempts`` then dead-letter.

Task types:
  - ``micro_consolidation`` — post-turn belief extraction + mutation
  - ``entity_merge``        — resolve a pending entity merge candidate
  - ``macro_sleep``         — decay/demote/compact idle-cycle sweep

The worker is a singleton with a bounded concurrency pool. It is
started lazily on first enqueue (or explicitly via :func:`start_worker`).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "enqueue_task",
    "get_worker",
    "reset_worker",
    "start_worker",
    "stop_worker",
    "purge_completed_tasks",
    "MEMORY_LANE",
    "failed_because",
]

# Reclaim threshold: a task stuck in 'processing' longer than this (seconds)
# is considered crashed and becomes eligible for re-claim.
_STUCK_THRESHOLD_SEC = 300.0  # 5 minutes

# Lease heartbeat interval: while a handler runs, its lease (updated_at) is
# renewed at least this often so a legitimately long handler (>300s, e.g. a
# large pg_backup or an LLM extraction under retry) is NOT re-claimed and
# double-executed by the worker's own 2s poll loop.
_LEASE_RENEW_INTERVAL_SEC = 60.0

# Backoff after a failed attempt (Memory audit M12): 30 s, then four times as
# long each time, at most an hour. A failed task went back to 'pending' at
# once, so three attempts ran within three polls and a ten-minute provider
# outage dead-lettered every deep pass of it.
_BACKOFF_BASE_SEC = 30.0
_BACKOFF_MAX_SEC = 3600.0

#: Task types that write what a user said into memory. They have slots of
#: their own: a pg_dump or a restic check never holds up a user's turn
#: (Memory audit M13). Everything else is maintenance.
MEMORY_LANE = frozenset({"post_turn_memory", "micro_consolidation", "entity_merge", "topic_summary_rebuild",
                         "chat_move_memory"})


#: A task that failed because Postgres is away is tried again after this, its
#: attempts untouched (plan A3).
_OUTAGE_RETRY_S = 60.0


def _retry_delay(attempts: int) -> float:
    """Seconds before a task that has failed *attempts* times runs again."""
    return min(_BACKOFF_MAX_SEC, _BACKOFF_BASE_SEC * 4 ** max(0, int(attempts) - 1))


def _database_away(exc: BaseException | None = None) -> bool:
    """Did this failure come from Postgres being away? An outage error says so
    itself (and starts the database watch); any failure while the watch says
    Postgres is away counts too: handlers catch their errors and return False."""
    from kazma_core.db import availability

    if exc is not None and availability.note_failure("memory_queue", exc):
        return True
    return availability.is_away()


def _report_dead_letters(task_types: list[str], error: str | None) -> None:
    """A dead letter is an operation memory gave up on: logged, and an ops
    alert. It is kept until the operator retries or clears it (the Memory
    page's queue)."""
    if not task_types:
        return
    logger.warning(
        "[task_queue] %d memory task(s) gave up after their last attempt (%s): %s -- kept as dead "
        "letters until retried or cleared on the Memory page",
        len(task_types), ", ".join(sorted(set(task_types))), (error or "")[:200],
    )
    from kazma_core.memory import quality_watch

    # A deep pass that gave up while extraction as a whole is failing is
    # that outage's, which memory.extraction_failing pages once (part H,
    # F14): one page an hour for a week of the same outage was 168.
    if any(t in quality_watch.EXTRACTION_TASKS for t in task_types) and quality_watch.incident_open("extraction"):
        task_types = [t for t in task_types if t not in quality_watch.EXTRACTION_TASKS]
        logger.info("[task_queue] the deep passes that gave up belong to the open extraction incident: "
                    "memory.extraction_failing paged it")
        if not task_types:
            return
    from kazma_core.observability.ops_alerts import alert  # never raises (AGENTS §33)

    alert(
        "memory.dead_letter",
        "Memory gave up on a task",
        f"{len(task_types)} task(s) ({', '.join(sorted(set(task_types)))}) failed every attempt and "
        "are kept as dead letters. The Memory page's task queue retries or clears them.",
        severity="warn",
        cooldown_s=3600.0,
    )


# Handler registry: task_type → async callable(payload) -> bool (success)
TaskHandler = Callable[[dict[str, Any]], Awaitable[bool]]
_HANDLERS: dict[str, TaskHandler] = {}


#: How each queued task finds its space (Spaces S1-09). Every task type a
#: handler is registered for is in exactly one of these
#: (``tests/test_memory_spaces.py``):
#:
#: * it names its tenant (``tenant_id`` in the payload);
TENANT_TASKS = frozenset({
    "macro_sleep", "entity_merge", "global_reconsolidation", "post_turn_memory",
    "topic_summaries", "nightly_export",
})
#: * it names the row it works on, and that row's tenant is its space
#:   (task type -> the table and the payload key of the row's id);
ROW_NAMED_TASKS: dict[str, tuple[str, str]] = {
    "micro_consolidation": ("episodes", "episode_id"),
    "topic_summary_rebuild": ("memory_summaries", "summary_id"),
}
#: The tasks a turn queues for the person it was for (Spaces S2-05/S2-07
#: second review): they run as that person -- ``asker`` in the payload, else
#: ``said_by``, else the row's own ``metadata.said_by`` -- never as the
#: space's maker. Those that call a model (:data:`_PERSON_MODEL_TASKS`)
#: wait while the person is disabled or past their daily limit, and are not
#: run for a person who left the space.
_PERSON_TASKS = frozenset({"post_turn_memory", "micro_consolidation"})
_PERSON_MODEL_TASKS = frozenset({"micro_consolidation"})
#: The payload mark of a deep pass a chat move queued (``memory.chat_move``).
REBUILD_CHAT_MOVE = "chat_move"
#: How long a person's task waits before it is asked again.
_PERSON_WAIT_S = 900.0
#: The task a chat move queues once the chat switched (Spaces S2-12,
#: ``kazma_ui.chat_move``): what memory holds of the chat follows it
#: (``memory.chat_move.move_chat_memory``). It reads one space and writes
#: another, so it is Kazma's own work, counted against no one.
CHAT_MOVE_MEMORY = "chat_move_memory"
#: * it is the install's own work: whole stores, no space's content -- or,
#:   for :data:`CHAT_MOVE_MEMORY`, the two spaces a moved chat names.
INSTALL_TASKS = frozenset({
    "native_backup", "native_pg_backup", "native_pg_offsite", "restic_maintenance",
    "connector_health", "universal_backup", CHAT_MOVE_MEMORY,
})


@dataclasses.dataclass(frozen=True)
class TaskBinding:
    """What a queued task runs as: its space and the account it acts for,
    or the install's own work (``install`` set, no space)."""

    space: str | None = None
    asker: str | None = None
    install: str | None = None
    #: The person's task waits (put back, no attempt spent): why.
    wait: str | None = None
    #: The person's task does not run (done, nothing to do): why.
    dropped: str | None = None


def _task_binding(task_type: str, payload: Any) -> TaskBinding:
    """How the task *task_type* with *payload* runs. Blocking (the memory
    database for a row-named task, the identity store when Spaces is
    enforced): the worker calls it in a thread.

    The worker has no request, so a handler that asks for "the" space (the
    chat store, a writer given no tenant) has to be told. While Spaces is
    not enforced a task of another tenant binds that tenant and the owner's
    own tasks bind nothing, exactly as before S1-09. Once it is enforced
    every task is bound whole -- a space and the account it acts for (the
    space's own person, or its maker; the admin for a tenant no space
    holds, R6) -- or runs as the install's work: a space with no asker is
    half a scope, and ``require_scope`` refused every queued deep pass.
    """
    from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

    data = payload if isinstance(payload, dict) else {}
    tenant = str(data.get("tenant_id") or "").strip()
    if not spaces_enforced():
        if tenant and tenant != OWNER_SPACE_ID:
            return TaskBinding(space=tenant)
        return TaskBinding()
    if task_type in INSTALL_TASKS:
        return TaskBinding(install=f"the memory task {task_type}")
    if not tenant and task_type in ROW_NAMED_TASKS:
        tenant = _row_tenant(*ROW_NAMED_TASKS[task_type], data) or ""
    if not tenant:
        # A row that is gone, or a task type no table above names: nothing
        # of a space is read (a handler that asks is refused, and retried).
        return TaskBinding(install=f"the memory task {task_type}, which names no space")
    from kazma_core.spaces.identity import acting_account

    if task_type in _PERSON_MODEL_TASKS and data.get("rebuild") == REBUILD_CHAT_MOVE:
        # A moved chat's memory rebuilt in its new space (Spaces S2-12,
        # memory.chat_move): Kazma's own work, counted against no one's
        # daily limit (the memory session's decision). Its writes name
        # their tenant; a call that asks for "the" space is refused.
        return TaskBinding(install="the rebuild of a moved chat's memory")
    if task_type in _PERSON_TASKS:
        person = _person_of(task_type, data)
        if person:
            return _persons_binding(task_type, tenant, person)
    return TaskBinding(space=tenant, asker=acting_account(tenant))


def _person_of(task_type: str, payload: dict[str, Any]) -> str:
    """The person a turn's task is for: the payload's ``asker`` (or
    ``said_by``), else the episode row's ``metadata.said_by``; "" when none
    is recorded (a task queued before, a turn Kazma reconciled)."""
    person = str(payload.get("asker") or payload.get("said_by") or "").strip()
    if person or task_type not in ROW_NAMED_TASKS:
        return person
    table, key = ROW_NAMED_TASKS[task_type]
    row_id = str(payload.get(key) or "")
    if not row_id or table != "episodes":
        return ""
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db(), timeout=5.0)
    try:
        row = conn.execute("SELECT metadata_json FROM episodes WHERE id = ?", (row_id,)).fetchone()
    finally:
        conn.close()
    try:
        meta = json.loads(row[0]) if row and row[0] else {}
    except (TypeError, ValueError):
        return ""
    return str(meta.get("said_by") or "").strip() if isinstance(meta, dict) else ""


def _persons_binding(task_type: str, tenant: str, person: str) -> TaskBinding:
    """A turn's task bound to the person it was for (Spaces S2-05/S2-07
    second review): their models and their limit apply to its model calls,
    which wait while they are disabled or past their daily limit. A person
    who left the space: the model's work is not run for them, the rest is
    Kazma's own work for the space (their message stays remembered, N36)."""
    from kazma_core.spaces.identity import AskerLeft, acting_account, disabled_accounts, work_scope

    try:
        found = work_scope(tenant, person)
    except AskerLeft:
        if task_type in _PERSON_MODEL_TASKS:
            return TaskBinding(dropped="the person this turn was for is no longer in its space")
        return TaskBinding(space=tenant, asker=acting_account(tenant))
    if found is None:
        return TaskBinding(space=tenant, asker=acting_account(tenant))
    space, asker = found
    if task_type in _PERSON_MODEL_TASKS:
        if asker in disabled_accounts():
            return TaskBinding(wait="the person this turn was for is disabled")
        from kazma_core.spaces.daily_limits import verdict

        day = verdict(asker)
        if day is not None and day.get("over"):
            return TaskBinding(wait="the person this turn was for reached their daily limit")
    return TaskBinding(space=space, asker=asker)


def _row_tenant(table: str, key: str, payload: dict[str, Any]) -> str | None:
    """The tenant of the row a row-named task works on, or None."""
    from kazma_core.paths import primary_memory_db
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    row_id = str(payload.get(key) or "")
    if not row_id:
        return None
    conn = sqlite3.connect(primary_memory_db(), timeout=5.0)
    try:
        row = conn.execute(f"SELECT tenant_id FROM {table} WHERE id = ?", (row_id,)).fetchone()  # nosec B608 - a fixed table
    except sqlite3.OperationalError:
        return None  # no such table yet: nothing to work on
    finally:
        conn.close()
    return str(row[0] or OWNER_SPACE_ID) if row else None


@contextlib.contextmanager
def _task_space(binding: TaskBinding) -> Iterator[None]:
    """Run a task's handler as *binding* (:func:`_task_binding`)."""
    from kazma_core.spaces.scope import bind_scope, install_work
    from kazma_core.tenant_context import tenant_scope

    if binding.install:
        with install_work(binding.install):
            yield
    elif binding.space and binding.asker:
        with bind_scope(binding.space, binding.asker):
            yield
    elif binding.space:
        with tenant_scope(binding.space):
            yield
    else:
        yield


def register_handler(task_type: str, handler: TaskHandler) -> None:
    """Register an async handler for a task type."""
    _HANDLERS[task_type] = handler


#: The reason the running handler gave for failing (a list the queue binds
#: around each call: a list, so a reason given in a worker thread, whose
#: context is a copy, still reaches it).
_FAILURE_REASONS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "kazma_task_failure_reasons", default=None
)


def failed_because(reason: str) -> bool:
    """A handler's failure with its reason: ``return failed_because("...")``.

    The task's ``error_log`` -- what the Running work card, the Dashboard's
    incident and the Memory page's queue show -- says what went wrong, in a
    sentence the owner can act on. A bare ``return False`` recorded only
    "handler returned False" (live 2026-11-19: a failed Postgres dump copy
    shown with no reason). Always False, so the handler's answer is unchanged."""
    from kazma_core.errors import redact_secrets

    box = _FAILURE_REASONS.get()
    if box is not None:
        box.append(redact_secrets(str(reason or "").strip())[:500])
    return False


# ── Enqueue ───────────────────────────────────────────────────────────────


def _spaces_take_turns() -> bool:
    """Whether the memory lane claims in turn by space: once Spaces is
    enforced (S4-04, N37). An install that does not enforce it claims oldest
    first, as before. In memory."""
    from kazma_core.spaces.scope import spaces_enforced

    return spaces_enforced()


def _claim_space_of(payload: dict[str, Any], space: str | None) -> str:
    """The space a task is claimed in turn by (Spaces S4-04, N37): *space*
    when the caller names it, else the payload's tenant, else the bound
    scope's or tenant's, else none."""
    if space:
        return str(space)
    tenant = str(payload.get("tenant_id") or "") if isinstance(payload, dict) else ""
    if tenant:
        return tenant
    from kazma_core.spaces.scope import current_scope
    from kazma_core.tenant_context import get_current_tenant_id

    bound = current_scope()
    return str(bound.space_id if bound is not None else (get_current_tenant_id() or ""))


def enqueue_task(
    task_type: str,
    payload: dict[str, Any],
    *,
    max_attempts: int | None = None,
    space: str | None = None,
) -> str | None:
    """Enqueue a durable task. Returns the task id, or None on failure.

    *max_attempts* defaults to 6 for a memory write (``MEMORY_LANE``: with the
    backoff, about an hour and forty minutes of outage before it is a dead
    letter) and 3 for maintenance.

    *space* is the space the memory lane takes turns by (Spaces S4-04, N37:
    :func:`_claim_space_of`) -- a deep pass names its episode alone, so the
    writer that queues it names its space.

    Best-effort: never raises — a queue failure logs and returns None so
    the caller (post-turn hook) is never blocked.
    """
    try:
        from kazma_core.config_store import apply_sqlite_pragmas
        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        try:
            apply_sqlite_pragmas(conn)
            ensure_ops_schema(conn)
            tid = "t_" + uuid.uuid4().hex[:20]
            now = time.time()
            if max_attempts is None:
                max_attempts = 6 if task_type in MEMORY_LANE else 3
            conn.execute(
                """INSERT INTO memory_task_queue
                   (id, task_type, payload_json, status, max_attempts, created_at, updated_at, claim_space)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (tid, task_type, json.dumps(payload, ensure_ascii=False), max_attempts, now, now,
                 _claim_space_of(payload, space)),
            )
            conn.commit()
            # Nudge the worker to wake up and poll. enqueue_task may be called
            # from a non-asyncio OS thread (the kazma-v2-extract consolidator)
            # — asyncio.Event.set() is not thread-safe, so route the wake
            # through the worker's own loop (no-op if the worker isn't
            # running; the 2s poll catches up).
            try:
                get_worker().wake()
            except Exception:
                pass
            return tid
        finally:
            conn.close()
    except Exception:
        logger.warning("[task_queue] enqueue failed for %s", task_type, exc_info=True)
        return None


# ── Worker ────────────────────────────────────────────────────────────────


class _MemoryWorker:
    """Async durable-task worker draining ``memory_ops.db``.

    Runs a bounded concurrency pool. On each tick it claims a batch of
    pending (or stuck-processing) tasks and dispatches them to registered
    handlers. Crashes are safe: unclaimed tasks remain pending.
    """

    def __init__(
        self, *, max_concurrency: int = 2, poll_interval: float = 2.0, memory_concurrency: int = 2,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.memory_concurrency = memory_concurrency
        self.poll_interval = poll_interval
        # Slots per lane, and how many of each are running (loop thread only).
        self._lane_sizes = {"memory": max(1, memory_concurrency), "maintenance": max(1, max_concurrency)}
        self._busy = {"memory": 0, "maintenance": 0}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._renewal_task: asyncio.Task | None = None
        self._running = False
        self._lock = threading.Lock()
        # Strong references to dispatched handler tasks. Without these, CPython
        # may garbage-collect a task mid-handler (the asyncio docs warn to keep
        # a reference), silently dropping a micro_consolidation/entity_merge
        # run (audit finding).
        self._inflight: set[asyncio.Task] = set()
        # In-flight lease registry: task_id → claim lease_token. The renewal
        # pass (_renew_leases) extends updated_at ONLY for rows still holding
        # our token — a reclaimed row (rotated token) is left alone.
        self._leases: dict[str, str] = {}
        self._last_lease_renew = 0.0  # time.monotonic() of last renewal pass

    def wake(self) -> None:
        """Thread-safe wake for enqueue_task (may run on a non-asyncio thread)."""
        try:
            task = self._task
            if task is None:
                return
            loop = task.get_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:
            pass

    def start(self) -> None:
        """Start the background poll loop (idempotent)."""
        with self._lock:
            if self._running:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("[task_queue] no running loop — worker start deferred")
                return
            self._running = True
            self._wake = asyncio.Event()
            self._task = loop.create_task(self._run())
            self._renewal_task = loop.create_task(self._run_lease_renewal())
            logger.info("[task_queue] worker started (concurrency=%d)", self.max_concurrency)

    async def stop(self) -> None:
        """Stop the worker and await in-flight tasks."""
        with self._lock:
            self._running = False
            self._wake.set()
        from kazma_core.bounded_cancel import cancel_and_wait, wait_then_cancel

        if self._renewal_task is not None:
            await cancel_and_wait(self._renewal_task, what="memory queue lease renewal")
            self._renewal_task = None
        if self._task is not None:
            await wait_then_cancel(self._task, what="memory queue worker", wait=5.0)
            self._task = None
        # Drain handler tasks that were dispatched but not yet finished, so a
        # handler mid-execution (e.g. an LLM belief extraction holding a SQLite
        # transaction) is awaited/cancelled rather than abandoned on loop close.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()

    async def _run_lease_renewal(self) -> None:
        """Dedicated background task to renew leases independently of poll/concurrency (M4)."""
        interval = max(1.0, _LEASE_RENEW_INTERVAL_SEC / 3.0)
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                if self._leases:
                    await asyncio.to_thread(self._renew_leases)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("[task_queue] lease renewal failed", exc_info=True)

    async def _run(self) -> None:
        """Main poll loop: each lane claims what it has room for and runs it at once.

        A lane claims no more rows than it has free slots (Memory audit M13).
        It claimed eight and ran two: the rest waited on a semaphore,
        "processing" with no lease, and once one had waited past the stuck
        threshold the next claim took it again -- the task ran twice. The
        lease is registered at the claim. Memory writes (``MEMORY_LANE``)
        have slots of their own, so maintenance never holds them up.
        """
        while self._running:
            try:
                for lane, size in self._lane_sizes.items():
                    free = size - self._busy[lane]
                    if free <= 0:
                        continue
                    # Claim off the event loop (audit M-P4): _claim_batch does
                    # a sync BEGIN IMMEDIATE round-trip with busy_timeout=5000.
                    claimed = await asyncio.to_thread(self._claim_batch, free, lane)
                    for task in claimed:
                        self._busy[lane] += 1
                        if task.get("lease_token"):
                            self._leases[task["id"]] = task["lease_token"]
                        t = asyncio.create_task(self._process(lane, task))
                        self._inflight.add(t)
                        t.add_done_callback(self._inflight.discard)
            except Exception:
                logger.warning("[task_queue] poll cycle failed", exc_info=True)
            # Wait for the next poll interval or an explicit wake
            try:
                self._wake.clear()
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    def _claim_batch(self, batch_size: int = 8, lane: str | None = None) -> list[dict[str, Any]]:
        """Atomically claim up to ``batch_size`` pending/stuck tasks of *lane*
        (``"memory"``, ``"maintenance"``; None = any).

        A pending task waiting out its backoff (``next_attempt_at``) is not
        claimable yet.

        Each claim mints a per-batch ``lease_token``; acks and lease renewals
        verify it, and a reclaim rotates it — so a stale handler (whose task
        was reclaimed after 300s) can no longer finalize a row now owned by
        a newer lease. Reclaiming a stuck row increments ``attempts`` and
        dead-letters at ``max_attempts``, so a crash-looping handler that
        never acks terminates instead of being re-claimed forever.
        """
        try:
            from kazma_core.config_store import apply_sqlite_pragmas
            from kazma_core.memory.schema_v2 import ensure_ops_schema
            from kazma_core.paths import memory_ops_db

            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                apply_sqlite_pragmas(conn)
                # Ensures the lease_token column exists on pre-upgrade DBs
                # even when the worker claims before any new enqueue.
                ensure_ops_schema(conn)
                conn.isolation_level = None  # manual BEGIN/COMMIT below
                now = time.time()
                stuck_cutoff = now - _STUCK_THRESHOLD_SEC
                kinds = sorted(MEMORY_LANE)
                marks = ",".join("?" for _ in kinds)
                if lane == "memory":
                    lane_sql, lane_args = f" AND task_type IN ({marks})", kinds
                elif lane == "maintenance":
                    lane_sql, lane_args = f" AND task_type NOT IN ({marks})", kinds
                else:
                    lane_sql, lane_args = "", []
                # Claim pending tasks + reclaim stuck 'processing' ones.
                # BEGIN IMMEDIATE takes the write lock BEFORE the SELECT so
                # two processes (app + CLI / HA replicas) cannot claim the
                # same rows and double-run a task (double belief-extraction
                # / double pg_dump); the UPDATE re-checks status as a
                # backstop for degraded isolation.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Dead-letter stuck rows whose next attempt would hit
                    # max_attempts (crash-looping handlers that never ack).
                    dead = [r[0] for r in conn.execute(
                        """UPDATE memory_task_queue
                           SET status = 'failed', attempts = attempts + 1,
                               updated_at = ?,
                               error_log = 'lease expired: max attempts exceeded'
                           WHERE status = 'processing' AND updated_at < ?
                             AND attempts + 1 >= max_attempts
                           RETURNING task_type""",
                        (now, stuck_cutoff),
                    ).fetchall()]
                    claimable = f"""
                           WHERE ((status = 'pending'
                                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                              OR (status = 'processing' AND updated_at < ?)){lane_sql}"""
                    if lane == "memory" and _spaces_take_turns():
                        # The memory lane claims in turn by space (Spaces
                        # S4-04, N37): each space's oldest task, then each
                        # space's next, so one space's hundreds of deep
                        # passes never hold up another's memory of its turns.
                        rows = conn.execute(
                            f"""SELECT * FROM (
                                  SELECT q.*, ROW_NUMBER() OVER (
                                      PARTITION BY COALESCE(q.claim_space, '') ORDER BY q.created_at
                                  ) AS fair_turn
                                  FROM memory_task_queue q {claimable})
                               ORDER BY fair_turn, created_at ASC LIMIT ?""",
                            (now, stuck_cutoff, *lane_args, max(0, int(batch_size))),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            f"SELECT * FROM memory_task_queue {claimable} ORDER BY created_at ASC LIMIT ?",
                            (now, stuck_cutoff, *lane_args, max(0, int(batch_size))),
                        ).fetchall()
                    claimed_ids = [r["id"] for r in rows]
                    if not claimed_ids:
                        # COMMIT (not ROLLBACK): the dead-letter pass above
                        # is global and must persist even when nothing is
                        # claimable, or an exhausted stuck row rolls back to
                        # 'processing' and re-queues forever.
                        conn.execute("COMMIT")
                        _report_dead_letters(dead, "lease expired: max attempts exceeded")
                        return []
                    placeholders = ",".join("?" * len(claimed_ids))
                    lease_token = uuid.uuid4().hex
                    cur = conn.execute(
                        f"""UPDATE memory_task_queue
                            SET status = 'processing', updated_at = ?,
                                lease_token = ?,
                                attempts = CASE WHEN status = 'pending'
                                    THEN attempts ELSE attempts + 1 END
                            WHERE id IN ({placeholders})
                              AND (status = 'pending'
                                   OR (status = 'processing' AND updated_at < ?))""",
                        [now, lease_token, *claimed_ids, stuck_cutoff],
                    )
                    if not cur.rowcount:
                        conn.execute("COMMIT")
                        _report_dead_letters(dead, "lease expired: max attempts exceeded")
                        return []
                    conn.execute("COMMIT")
                    _report_dead_letters(dead, "lease expired: max attempts exceeded")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    raise
                claimed = [dict(r) for r in rows]
                for row in claimed:
                    row.pop("fair_turn", None)
                    row["lease_token"] = lease_token
                return claimed
            finally:
                conn.close()
        except Exception:
            logger.warning("[task_queue] claim failed", exc_info=True)
            return []

    async def _process(self, lane: str, task: dict[str, Any]) -> None:
        """Dispatch one task to its handler; ack on success, retry/nack on failure."""
        task_id = task["id"]
        lease_token = task.get("lease_token")
        if lease_token:
            self._leases[task_id] = lease_token
        try:
            try:
                task_type = task["task_type"]
                handler = _HANDLERS.get(task_type)
                if handler is None:
                    logger.warning("[task_queue] no handler for type %s — failing", task_type)
                    await asyncio.to_thread(
                        self._ack, task, success=False, error=f"no handler for {task_type}"
                    )
                    return
                try:
                    payload = json.loads(task["payload_json"] or "{}")
                except Exception:
                    payload = {}
                binding = await asyncio.to_thread(_task_binding, task_type, payload)
                if binding.dropped:
                    logger.info("[task_queue] %s not run: %s", task_type, binding.dropped)
                    await asyncio.to_thread(self._ack, task, success=True, error=None)
                    return
                if binding.wait:
                    logger.info("[task_queue] %s waits: %s", task_type, binding.wait)
                    await asyncio.to_thread(self._put_back, task, binding.wait)
                    return
                reasons: list[str] = []
                reasons_token = _FAILURE_REASONS.set(reasons)
                try:
                    with _task_space(binding):
                        success = await handler(payload)
                finally:
                    _FAILURE_REASONS.reset(reasons_token)
                await asyncio.to_thread(
                    self._ack, task,
                    success=bool(success),
                    error=None if success else (reasons[-1] if reasons and reasons[-1]
                                                else "handler returned False"),
                    # A handler that answered False while Postgres is away
                    # failed for the database, not for itself (plan A3).
                    outage=not success and _database_away(),
                )
            except Exception as exc:
                await asyncio.to_thread(
                    self._ack, task,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    outage=_database_away(exc),
                )
        finally:
            self._leases.pop(task_id, None)
            if lane in self._busy:
                self._busy[lane] = max(0, self._busy[lane] - 1)
                self._wake.set()  # a slot is free: claim again now

    def _put_back(self, task: dict[str, Any], why: str) -> None:
        """Put a person's task back without spending an attempt, asked again
        after :data:`_PERSON_WAIT_S` (they are disabled or past their daily
        limit: it waits, it did not fail)."""
        from kazma_core.config_store import apply_sqlite_pragmas
        from kazma_core.paths import memory_ops_db

        conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        try:
            apply_sqlite_pragmas(conn)
            now = time.time()
            conn.execute(
                """UPDATE memory_task_queue
                   SET status='pending', updated_at=?, error_log=?, lease_token=NULL, next_attempt_at=?
                   WHERE id=? AND lease_token=?""",
                (now, ("waits: " + why)[:1000], now + _PERSON_WAIT_S, task["id"], task.get("lease_token")),
            )
            conn.commit()
        finally:
            conn.close()

    def _ack(self, task: dict[str, Any], *, success: bool, error: str | None,
             outage: bool = False) -> None:
        """Mark a task completed (success) or retry/dead-letter (failure).

        The UPDATE is guarded by the claim's ``lease_token``: if the task was
        reclaimed (token rotated) while this handler ran, the ack matches zero
        rows and the newer lease keeps ownership — a stale handler can no
        longer finalize a row it lost. The token is cleared on finalize so a
        pending/failed row carries no stale lease.

        ``outage``: the task failed because Postgres is away. It goes back to
        pending without spending an attempt, to be tried again after
        ``_OUTAGE_RETRY_S``: a two-hour outage dead-lettered every deep pass
        and every backup of it (plan A3), and the operator had to retry each.
        """
        try:
            from kazma_core.config_store import apply_sqlite_pragmas
            from kazma_core.paths import memory_ops_db

            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            try:
                apply_sqlite_pragmas(conn)
                now = time.time()
                token = task.get("lease_token")
                if not success and outage:
                    conn.execute(
                        """UPDATE memory_task_queue
                           SET status='pending', updated_at=?, error_log=?, lease_token=NULL,
                               next_attempt_at=?
                           WHERE id=? AND lease_token=?""",
                        (now, ("waits for the database: " + (error or ""))[:1000],
                         now + _OUTAGE_RETRY_S, task["id"], token),
                    )
                    logger.debug("[task_queue] %s waits for the database", task.get("task_type"))
                elif success:
                    conn.execute(
                        """UPDATE memory_task_queue
                           SET status='completed', updated_at=?, lease_token=NULL
                           WHERE id=? AND lease_token=?""",
                        (now, task["id"], token),
                    )
                else:
                    # The count is the row's, incremented in SQL: the claimed
                    # dict holds what the claim READ, so a reclaim's own
                    # ``attempts + 1`` was overwritten by ``task["attempts"] + 1``
                    # here, and a crash-looping task ran up to about twice
                    # ``max_attempts`` times (audit 2026-10-26 AUD-048). The
                    # increment opens this ack's transaction, so the status
                    # write below decides on the count it made.
                    rows = conn.execute(
                        """UPDATE memory_task_queue SET attempts = attempts + 1
                           WHERE id=? AND lease_token=?
                           RETURNING attempts, max_attempts""",
                        (task["id"], token),
                    ).fetchall()  # all: a RETURNING statement left unstepped blocks the commit
                    counted = rows[0] if rows else None
                    if counted is None:
                        # Reclaimed meanwhile (the token rotated): the newer
                        # lease owns the row and its count.
                        conn.rollback()
                        return
                    attempts, max_attempts = int(counted[0]), int(counted[1] or 3)
                    if attempts >= max_attempts:
                        cur = conn.execute(
                            """UPDATE memory_task_queue
                               SET status='failed', attempts=?, updated_at=?, error_log=?, lease_token=NULL
                               WHERE id=? AND lease_token=?""",
                            (attempts, now, (error or "")[:1000], task["id"], token),
                        )
                        if cur.rowcount:
                            conn.commit()
                            _report_dead_letters([str(task.get("task_type") or "?")], error)
                    else:
                        conn.execute(
                            """UPDATE memory_task_queue
                               SET status='pending', attempts=?, updated_at=?, error_log=?, lease_token=NULL,
                                   next_attempt_at=?
                               WHERE id=? AND lease_token=?""",
                            (attempts, now, (error or "")[:1000], now + _retry_delay(attempts),
                             task["id"], token),
                        )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning("[task_queue] ack failed for %s", task.get("id"), exc_info=True)

    def _renew_leases(self) -> None:
        """Extend the stuck-threshold window for in-flight tasks (heartbeat).

        For every registered lease, bumps ``updated_at`` ONLY where the row
        still holds our ``lease_token`` and is still ``processing`` — a row
        reclaimed by another claim (rotated token) is left untouched, and a
        row already finalized by our own ack simply matches zero rows.
        """
        if not self._leases:
            return
        try:
            from kazma_core.config_store import apply_sqlite_pragmas
            from kazma_core.paths import memory_ops_db

            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            try:
                apply_sqlite_pragmas(conn)
                now = time.time()
                for task_id, token in list(self._leases.items()):
                    conn.execute(
                        """UPDATE memory_task_queue
                           SET updated_at = ?
                           WHERE id = ? AND lease_token = ? AND status = 'processing'""",
                        (now, task_id, token),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("[task_queue] lease renewal failed", exc_info=True)


# ── Singleton ─────────────────────────────────────────────────────────────

_worker: _MemoryWorker | None = None
_worker_lock = threading.Lock()


def get_worker() -> _MemoryWorker:
    """Return the process-wide memory worker singleton."""
    global _worker
    if _worker is not None:
        return _worker
    with _worker_lock:
        if _worker is None:
            _worker = _MemoryWorker()
        return _worker


def reset_worker() -> None:
    """Drop the singleton (tests)."""
    global _worker
    with _worker_lock:
        _worker = None


def start_worker() -> None:
    """Start the singleton worker (idempotent)."""
    get_worker().start()


async def stop_worker() -> None:
    """Stop the singleton worker."""
    global _worker
    with _worker_lock:
        w = _worker
    if w is not None:
        await w.stop()


def retry_failed_task(task_id: str) -> bool:
    """Put a failed (dead-letter) or waiting task back to run now.

    The one "Retry" for the Memory page's queue and the run supervisor's
    action (``kazma_core.runs``). True when a row changed. Blocking.
    """
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_core.paths import memory_ops_db

    conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
    try:
        ensure_ops_schema(conn)
        cur = conn.execute(
            """
            UPDATE memory_task_queue
            SET status='pending', attempts=0, error_log=NULL, updated_at=?, next_attempt_at=NULL
            WHERE id=? AND status IN ('failed', 'pending')
            """,
            (time.time(), task_id),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def purge_completed_tasks(retention_days: int = 7) -> int:
    """Delete completed tasks older than retention_days (M2).

    Never a dead letter (``failed``): it is an operation memory gave up on,
    kept until the operator retries or clears it (Memory audit M12 -- dead
    letters were deleted after a week, and the health check that counted
    them cleared itself)."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import memory_ops_db

    cutoff = time.time() - (max(1, int(retention_days)) * 86400.0)
    conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
    try:
        apply_sqlite_pragmas(conn)
        cur = conn.execute(
            """DELETE FROM memory_task_queue
               WHERE status = 'completed' AND updated_at < ?""",
            (cutoff,),
        )
        conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("[task_queue] purged %d completed tasks (>%dd old)", deleted, retention_days)
        return deleted
    finally:
        conn.close()


# ``async_purge_completed_tasks`` (an asyncio.to_thread wrapper) is gone: it
# had no caller -- the purge runs on the maintenance cadence
# (worker_bootstrap._purge_task_queue) -- and an unused second entry point is
# dead code that drifts (audit 2026-10-26 AUD-078).

