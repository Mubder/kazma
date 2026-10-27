"""Durable intake reservations shared by all callers of a job backend.

Published jobs prove their own reservation can be closed. Every reservation
records its OWNER -- install, host, process id and process start time -- and,
once it starts publishing, the ids it publishes under. A reservation whose
owner is provably dead (same install, same host, the process gone or its id
reused by another process) can be claimed and rolled back: a dead process
cannot publish again.

An owner that cannot be proven dead (another host, an unreadable process)
holds a LEASE (2026-10-13): it renews ``heartbeat_epoch`` every quarter of
``lease_seconds`` (its own setting, stored on the row) while the intake runs.
Once the lease has lapsed, another process may take the reservation over,
and the stale owner -- perhaps only partitioned or paused -- is FENCED: the
job that publishes an intake is created only inside a transaction that finds
the reservation still active and still its own (``jobs.enqueue(intake_fence=)``),
under the same serialization as the takeover, and a takeover refuses a
reservation whose job exists. Rows a fenced owner writes after the takeover
are tagged with its token: it removes them itself when it finds itself
fenced, and the recovery sweep removes them for a day after the takeover
(``fenced_epoch``). A legacy row (no owner, or no heartbeat: written by a
build without the fence) is never taken over; it is reported as stranded.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from kazma_core.db.pg_helpers import store_errors

from .capacity import CapacityError
from .config import DocumentConfig
from .jobs import _ACTIVE_STATE_VALUES, _QUEUEABLE_STATE_VALUES, _TERMINAL_STATE_VALUES

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS document_intake_reservations (
        token TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, request_key TEXT NOT NULL,
        request_digest TEXT NOT NULL, job_id TEXT NOT NULL,
        byte_size BIGINT NOT NULL, created_epoch DOUBLE PRECISION NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, publishing INTEGER NOT NULL DEFAULT 0,
        source_sha256 TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_document_intake_request
        ON document_intake_reservations(tenant_id, request_key) WHERE active = 1""",
    """CREATE INDEX IF NOT EXISTS idx_document_intake_window
        ON document_intake_reservations(tenant_id, created_epoch)""",
    "CREATE TABLE IF NOT EXISTS document_intake_mutex (id INTEGER PRIMARY KEY)",
    "INSERT INTO document_intake_mutex(id) VALUES (1) ON CONFLICT (id) DO NOTHING",
)
#: Added 2026-10-13: who holds a reservation and what it publishes under.
_COLUMNS = (
    ("owner", "TEXT NOT NULL DEFAULT ''"),
    ("document_id", "TEXT NOT NULL DEFAULT ''"),
    ("version_id", "TEXT NOT NULL DEFAULT ''"),
    ("created_document", "INTEGER NOT NULL DEFAULT 0"),
    # Added later the same day: the lease (see the module docstring).
    ("heartbeat_epoch", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("lease_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("fenced_epoch", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
)
#: How long a taken-over reservation is kept, so the sweep can remove rows a
#: fenced owner writes after the takeover.
FENCED_RETENTION_SECONDS = 86_400.0


def lease_lapsed(row: dict[str, Any], now: float) -> bool:
    """True when the row's owner holds a lease (it renews a heartbeat) and
    has not renewed it within its own lease."""
    heartbeat = float(row.get("heartbeat_epoch") or 0.0)
    lease = int(row.get("lease_seconds") or 0)
    return heartbeat > 0 and lease > 0 and now - heartbeat > lease


@lru_cache(maxsize=1)
def _process_identity() -> tuple[str, str, int, str]:
    """(install, host, pid, start) of THIS process; start guards pid reuse."""

    import psutil

    try:
        from kazma_core.db.shared_store_peers import install_id

        install = install_id() or ""
    except (ImportError, OSError):
        install = ""
    started = f"{psutil.Process(os.getpid()).create_time():.3f}"
    return install, socket.gethostname(), os.getpid(), started


def current_owner() -> str:
    install, host, pid, started = _process_identity()
    return json.dumps({"install": install, "host": host, "pid": pid, "started": started}, sort_keys=True)


def owner_state(owner: str) -> str:
    """``self``, ``alive``, ``dead`` (provably) or ``unknown`` (never taken over)."""

    import psutil

    try:
        recorded = json.loads(owner) if owner else None
    except json.JSONDecodeError:
        recorded = None
    if not isinstance(recorded, dict):
        return "unknown"
    if recorded.get("restored"):
        # Restored from a backup snapshot: its publisher wrote to another
        # database and can never resume against this one.
        return "dead"
    install, host, pid, started = _process_identity()
    if not install or recorded.get("install") != install or recorded.get("host") != host:
        return "unknown"
    try:
        owner_pid = int(recorded["pid"])
        owner_started = str(recorded["started"])
    except (KeyError, TypeError, ValueError):
        return "unknown"
    if owner_pid == pid and owner_started == started:
        return "self"
    try:
        process = psutil.Process(owner_pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return "dead"
        return "alive" if f"{process.create_time():.3f}" == owner_started else "dead"
    except psutil.NoSuchProcess:
        return "dead"
    except psutil.Error:
        return "unknown"  # access denied or unreadable: not proof of death


def _refuse(code: str, status: int, reason: str, message: str) -> CapacityError:
    return CapacityError(code, message, status=status, reason=reason, retry_after=30)


class IntakeAdmissionStore:
    """Short, serialized DB transactions; no lock is held while reading bytes."""

    def __init__(self, jobs: Any) -> None:
        self._jobs = jobs
        self._postgres = hasattr(jobs, "_pool")
        with self._transaction() as cur:
            if self._postgres:
                from kazma_core.db.pg_schema import ensure_schema

                ensure_schema(cur, [
                    *_SCHEMA,
                    *(f"ALTER TABLE document_intake_reservations ADD COLUMN IF NOT EXISTS {name} {definition}"
                      for name, definition in _COLUMNS),
                ])
            else:
                for statement in _SCHEMA:
                    self._execute(cur, statement)
        if not self._postgres:
            from kazma_core.db.sqlite_columns import add_missing_columns

            with self._jobs._lock:
                add_missing_columns(self._jobs._conn, "document_intake_reservations", _COLUMNS)

    def _execute(self, cur: Any, sql: str, params: tuple | None = None) -> Any:
        text = sql.replace("?", "%s") if self._postgres else sql
        if params is None:
            cur.execute(text)
        else:
            cur.execute(text, params)
        return cur

    @contextmanager
    def _transaction(self):
        if self._postgres:
            with self._jobs._pool.connection() as conn:
                try:
                    with conn.cursor() as cur:
                        yield cur
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        else:
            with self._jobs._lock:
                conn = self._jobs._conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn.cursor()
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise

    def _serialize(self, cur: Any) -> None:
        # SQLite's BEGIN IMMEDIATE already serializes writers. On PostgreSQL
        # all replicas lock the same row before observing/updating admission.
        if self._postgres:
            self._execute(cur, "SELECT id FROM document_intake_mutex WHERE id = 1 FOR UPDATE")
            if cur.fetchone() is None:
                raise RuntimeError("document admission mutex is unavailable")

    def _now(self) -> float:
        # On PostgreSQL use one database clock across replicas; caller clocks
        # may differ. SQLite uses the queue's injectable clock for tests.
        return self._jobs._clock().timestamp()

    def _epoch(self, cur: Any) -> float:
        if not self._postgres:
            return self._now()
        self._execute(cur, "SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS epoch")
        return float(cur.fetchone()["epoch"])

    def reserve(
        self,
        *,
        tenant_id: str,
        request_key: str,
        request_digest: str,
        job_id: str,
        byte_size: int,
        config: DocumentConfig,
    ) -> str:
        """Atomically bind a request key and consume queue/rate/byte budgets."""
        token = uuid.uuid4().hex
        try:
            with self._transaction() as cur:
                self._serialize(cur)
                now = self._epoch(cur)
                self._execute(
                    cur,
                    "DELETE FROM document_intake_reservations WHERE active = 0 AND created_epoch < ? AND fenced_epoch < ?",
                    (now - 60, now - FENCED_RETENTION_SECONDS),
                )
                self._execute(
                    cur,
                    "SELECT request_digest FROM document_intake_reservations WHERE tenant_id = ? AND request_key = ? AND active = 1",
                    (tenant_id, request_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if existing["request_digest"] != request_digest:
                        raise _refuse(
                            "idempotency_conflict",
                            409,
                            "request_conflict",
                            "Idempotency key belongs to another request",
                        )
                    raise _refuse(
                        "intake_in_progress",
                        409,
                        "request_in_progress",
                        "This document intake is already reserved; retry or inspect recovery status.",
                    )
                # A publisher can finish between the caller's replay lookup
                # and this transaction. Do not charge or create a second one.
                self._execute(cur, "SELECT id FROM document_jobs WHERE tenant_id = ? AND id = ?", (tenant_id, job_id))
                if cur.fetchone() is not None:
                    raise _refuse(
                        "intake_in_progress",
                        409,
                        "request_in_progress",
                        "This document intake was published; retry to verify its result.",
                    )
                self._check_budgets(cur, tenant_id, byte_size, config, now)
                self._execute(
                    cur,
                    """INSERT INTO document_intake_reservations
                    (token, tenant_id, request_key, request_digest, job_id, byte_size, created_epoch, owner,
                     heartbeat_epoch, lease_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (token, tenant_id, request_key, request_digest, job_id, byte_size, now, current_owner(),
                     now, int(config.intake_lease_seconds)),
                )
            return token
        except CapacityError:
            raise
        except store_errors() as exc:
            raise _refuse(
                "capacity_unavailable",
                503,
                "admission_unavailable",
                "Document admission is unavailable; retry shortly.",
            ) from exc

    def check(self, *, tenant_id: str, byte_size: int, config: DocumentConfig) -> None:
        """Refuse NOW what ``reserve`` would refuse, consuming nothing.

        The Web upload asks this before reading a byte of the body, so a
        full queue or a spent rate window answers at once instead of after
        the whole upload has been received. ``reserve`` stays the authority:
        a request that passes here can still be refused there by a racing
        intake (2026-10-13, F08).
        """
        try:
            with self._transaction() as cur:
                self._check_budgets(cur, tenant_id, byte_size, config, self._epoch(cur))
        except CapacityError:
            raise
        except store_errors() as exc:
            raise _refuse(
                "capacity_unavailable",
                503,
                "admission_unavailable",
                "Document admission is unavailable; retry shortly.",
            ) from exc

    def _check_budgets(
        self, cur: Any, tenant_id: str, byte_size: int, config: DocumentConfig, now: float
    ) -> None:
        """The queue, tenant and rate budgets ``reserve`` enforces; nothing written."""
        terminal = ",".join(f"'{value}'" for value in _TERMINAL_STATE_VALUES)
        queued = ",".join(f"'{value}'" for value in _QUEUEABLE_STATE_VALUES)
        active = ",".join(f"'{value}'" for value in _ACTIVE_STATE_VALUES)
        self._execute(
            cur,
            f"""SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN tenant_id = ? AND state IN ({queued}) THEN 1 ELSE 0 END), 0) AS queued,
            COALESCE(SUM(CASE WHEN tenant_id = ? AND state IN ({active}) THEN 1 ELSE 0 END), 0) AS active
            FROM document_jobs WHERE state NOT IN ({terminal})""",
            (tenant_id, tenant_id),
        )
        jobs = cur.fetchone()
        self._execute(
            cur,
            """SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN r.tenant_id = ? THEN 1 ELSE 0 END), 0) AS tenant
            FROM document_intake_reservations r WHERE r.active = 1
            AND NOT EXISTS (SELECT 1 FROM document_jobs j WHERE j.tenant_id = r.tenant_id AND j.id = r.job_id)""",
            (tenant_id,),
        )
        pending = cur.fetchone()
        if jobs["total"] + pending["total"] >= config.capacity_max_queued_jobs:
            raise _refuse(
                "queue_full",
                503,
                "queue_backpressure",
                "The document processing queue is at capacity; retry shortly.",
            )
        if jobs["queued"] + pending["tenant"] >= config.capacity_max_tenant_queued_jobs:
            raise _refuse(
                "tenant_queue_full",
                429,
                "tenant_queue_backpressure",
                "This tenant has too many queued documents; retry shortly.",
            )
        if jobs["active"] + pending["tenant"] >= config.capacity_max_tenant_active_jobs:
            raise _refuse(
                "tenant_active_full",
                429,
                "tenant_active_backpressure",
                "This tenant has too many documents processing; retry shortly.",
            )
        files, size = self._usage(cur, tenant_id, now)
        if files >= config.capacity_intake_rate_per_minute or (
            config.capacity_intake_bytes_per_minute > 0
            and size + byte_size > config.capacity_intake_bytes_per_minute
        ):
            raise _refuse(
                "rate_limited",
                429,
                "rate_limited",
                "Intake rate limit exceeded for this tenant; retry shortly.",
            )

    def mark_publishing(
        self,
        token: str,
        *,
        byte_size: int,
        source_sha256: str,
        document_id: str,
        version_id: str,
        created_document: bool,
    ) -> None:
        """Bind actual bytes and the ids about to be published, before any
        metadata write, so a rollback can find exactly what this intake made.
        Only the owner may do it: a reservation claimed for recovery refuses."""
        with self._transaction() as cur:
            self._execute(
                cur,
                """UPDATE document_intake_reservations SET
                publishing = 1, byte_size = ?, source_sha256 = ?,
                document_id = ?, version_id = ?, created_document = ?
                WHERE token = ? AND active = 1 AND byte_size >= ? AND owner = ?""",
                (byte_size, source_sha256, document_id, version_id, 1 if created_document else 0,
                 token, byte_size, current_owner()),
            )
            if cur.rowcount != 1:
                raise RuntimeError("document admission reservation is unavailable")

    def get(self, token: str) -> dict[str, Any] | None:
        with self._transaction() as cur:
            self._execute(cur, "SELECT * FROM document_intake_reservations WHERE token = ?", (token,))
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def reservation_for_job(self, *, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        """The newest reservation that published (or is publishing) *job_id*.

        Kept after it closes (``active = 0``): its ``owner`` still says which
        process advanced the job's intake, so recovery can tell a live
        upload from one stranded by a crash (audit 2026-10-26 AUD-022).
        """
        with self._transaction() as cur:
            self._execute(
                cur,
                """SELECT * FROM document_intake_reservations
                WHERE tenant_id = ? AND job_id = ? ORDER BY created_epoch DESC LIMIT 1""",
                (tenant_id, job_id),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def unpublished(self) -> list[dict[str, Any]]:
        """Active reservations with no published job (candidates for recovery)."""
        with self._transaction() as cur:
            self._execute(
                cur,
                """SELECT * FROM document_intake_reservations r WHERE r.active = 1
                AND NOT EXISTS (SELECT 1 FROM document_jobs j WHERE j.tenant_id = r.tenant_id
                AND j.id = r.job_id AND j.request_digest = r.request_digest)""",
            )
            return [dict(row) for row in cur.fetchall()]

    def heartbeat(self, token: str) -> bool:
        """Renew this process's lease; False once it is no longer the owner
        (taken over: the intake is fenced and must not publish)."""
        with self._transaction() as cur:
            self._execute(
                cur,
                "UPDATE document_intake_reservations SET heartbeat_epoch = ? WHERE token = ? AND active = 1 AND owner = ?",
                (self._epoch(cur), token, current_owner()),
            )
            return cur.rowcount == 1

    def now_epoch(self) -> float:
        """The clock leases are judged by (the database's, on PostgreSQL)."""
        with self._transaction() as cur:
            return self._epoch(cur)

    def claim(
        self, token: str, *, expected_owner: str, expected_heartbeat: float | None = None,
    ) -> bool:
        """Take a reservation over (compare-and-set, so two recoverers cannot
        both act on it, and a heartbeat that lands first defeats a lease
        takeover). Refused when the intake's job exists: it published.

        ``expected_heartbeat`` marks a LEASE takeover of an owner that may be
        alive: the row is stamped ``fenced_epoch`` and kept for a day after it
        is released, for the sweep.
        """
        lease = expected_heartbeat is not None
        with self._transaction() as cur:
            self._serialize(cur)
            now = self._epoch(cur)
            self._execute(
                cur,
                """UPDATE document_intake_reservations SET owner = ?, fenced_epoch = ?
                WHERE token = ? AND active = 1 AND owner = ?"""
                + (" AND heartbeat_epoch = ?" if lease else "")
                + """ AND NOT EXISTS (SELECT 1 FROM document_jobs j
                    WHERE j.tenant_id = document_intake_reservations.tenant_id
                    AND j.id = document_intake_reservations.job_id)""",
                (current_owner(), now if lease else 0.0, token, expected_owner)
                + ((float(expected_heartbeat),) if lease else ()),
            )
            return cur.rowcount == 1

    def fenced_recent(self) -> list[dict[str, Any]]:
        """Reservations taken over by lease within the retention window that
        recorded what they publish: the sweep removes what their stale owner
        may still have written."""
        with self._transaction() as cur:
            now = self._epoch(cur)
            self._execute(
                cur,
                """SELECT * FROM document_intake_reservations
                WHERE fenced_epoch > ? AND publishing = 1""",
                (now - FENCED_RETENTION_SECONDS,),
            )
            return [dict(row) for row in cur.fetchall()]

    def release_owned(self, token: str) -> bool:
        """Close a reservation this process owns after rolling its publication back."""
        with self._transaction() as cur:
            self._execute(
                cur,
                "UPDATE document_intake_reservations SET active = 0 WHERE token = ? AND active = 1 AND owner = ?",
                (token, current_owner()),
            )
            return cur.rowcount == 1

    def finish(self, token: str) -> None:
        """Release only pre-publication failures or a matching durable job.

        A failed partial publication stays active. Never silently expire it
        and allow another publisher to overwrite metadata with different bytes.
        """
        with self._transaction() as cur:
            self._execute(
                cur,
                """UPDATE document_intake_reservations SET active = 0
                WHERE token = ? AND owner = ? AND (publishing = 0 OR EXISTS (
                    SELECT 1 FROM document_jobs j WHERE
                    j.tenant_id = document_intake_reservations.tenant_id
                    AND j.id = document_intake_reservations.job_id
                    AND j.request_digest = document_intake_reservations.request_digest))""",
                (token, current_owner()),
            )

    def reconcile_published(self) -> None:
        """Jobs are durable proof; age alone never proves a publisher is gone."""
        with self._transaction() as cur:
            self._execute(
                cur,
                """UPDATE document_intake_reservations SET active = 0
                WHERE active = 1 AND EXISTS (SELECT 1 FROM document_jobs j WHERE
                j.tenant_id = document_intake_reservations.tenant_id
                AND j.id = document_intake_reservations.job_id
                AND j.request_digest = document_intake_reservations.request_digest)""",
            )

    def _usage(self, cur: Any, tenant_id: str, now: float) -> tuple[int, int]:
        self._execute(
            cur,
            """SELECT COUNT(*) AS files, COALESCE(SUM(byte_size), 0) AS bytes
            FROM document_intake_reservations WHERE tenant_id = ?
            AND (active = 1 OR created_epoch >= ?)""",
            (tenant_id, now - 60),
        )
        row = cur.fetchone()
        return int(row["files"]), int(row["bytes"])

    def snapshot(self, *, tenant_id: str | None = None) -> dict[str, int]:
        with self._transaction() as cur:
            now = self._epoch(cur)
            clause, params = (" AND tenant_id = ?", (tenant_id,)) if tenant_id is not None else ("", ())
            self._execute(
                cur,
                "SELECT COUNT(*) AS pending, COALESCE(SUM(byte_size), 0) AS bytes FROM document_intake_reservations WHERE active = 1"
                + clause,
                params,
            )
            row = cur.fetchone()
            result = {"pending": int(row["pending"]), "reserved_bytes": int(row["bytes"])}
            self._execute(
                cur,
                """SELECT owner, heartbeat_epoch, lease_seconds FROM document_intake_reservations r
                WHERE r.active = 1
                AND NOT EXISTS (SELECT 1 FROM document_jobs j WHERE j.tenant_id = r.tenant_id
                AND j.id = r.job_id AND j.request_digest = r.request_digest)""" + clause.replace("tenant_id", "r.tenant_id"),
                params,
            )
            held = [dict(item) for item in cur.fetchall()]
            if tenant_id is not None:
                result["window_files"], result["window_bytes"] = self._usage(cur, tenant_id, now)
        # Held by an owner nobody can prove dead and whose lease is not being
        # renewed (lapsed: the sweep takes it over within a minute; never
        # held: a legacy row the operator must free). A remote intake that is
        # renewing its lease is working, not stranded.
        result["stranded"] = sum(
            1 for row in held
            if owner_state(str(row["owner"])) == "unknown"
            and (float(row.get("heartbeat_epoch") or 0) <= 0 or lease_lapsed(row, now))
        )
        return result
