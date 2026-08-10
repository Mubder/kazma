"""Postgres durable document-job repository — real multi-replica claims.

When ``KAZMA_DATABASE_URL`` points at Postgres, this repository backs the
document job queue with genuine multi-replica atomic claiming using
``SELECT ... FOR UPDATE SKIP LOCKED`` — two replicas racing for work never
claim the same job, and a claim is finalized with a compare-and-swap on
``(state, row_version)``. Leases carry an owner + expiry and are renewed by
heartbeat; expired leases are reclaimed exactly once on the next claim.

Scope and honesty (see AGENTS.md §5 and the Phase 9 gate): document *metadata*
(documents/versions/blobs/artifacts) still lives in SQLite, so a multi-replica
deployment is **degraded** — only *job claiming* is multi-replica-safe here.
:func:`document_storage_readiness` reports this truthfully; do not advertise
full multi-replica metadata until the metadata port lands.

SQLite remains the default (WAL + ``BEGIN IMMEDIATE``); this module is only
used when Postgres is configured. The public method surface mirrors
:class:`~kazma_core.documents.jobs.DocumentJobRepository` for the subset the
ingestion coordinator uses.
"""

from __future__ import annotations

import logging
import math
import os
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .jobs import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    DocumentJobRecord,
    InvalidJobTransitionError,
    JobConflictError,
    JobLeaseConflictError,
    JobNotFoundError,
    QueueStats,
    StaleJobUpdateError,
    TenantLoad,
    _ACTIVE_STATE_VALUES,
    _QUEUEABLE_STATE_VALUES,
    _TERMINAL_STATE_VALUES,
)
from .models import DocumentId, DocumentJobState, JobId, VersionId, new_job_id

logger = logging.getLogger(__name__)

__all__ = [
    "PostgresDocumentJobRepository",
    "document_jobs_backend",
    "document_metadata_backend",
    "document_storage_readiness",
    "resolve_job_repository",
]

# Postgres schema — no FKs to the SQLite metadata tables (they live in a
# different store); the job queue is self-contained. Idempotent DDL.
_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_jobs (
    id TEXT NOT NULL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    state TEXT NOT NULL,
    stage TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    retry_at TIMESTAMPTZ,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT,
    error_message TEXT,
    row_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT document_jobs_tenant_idem UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS document_job_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    from_version INTEGER,
    to_version INTEGER NOT NULL,
    stage TEXT NOT NULL,
    lease_owner TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT document_job_events_unique UNIQUE (tenant_id, job_id, to_version)
);

CREATE INDEX IF NOT EXISTS idx_pg_document_jobs_claim
    ON document_jobs(state, retry_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_pg_document_jobs_tenant
    ON document_jobs(tenant_id, document_id, version_id);
CREATE INDEX IF NOT EXISTS idx_pg_document_job_events
    ON document_job_events(tenant_id, job_id, id);
"""

_CLAIMABLE = ("ready_to_parse", "ocr_required")


def _now() -> datetime:
    return datetime.now(UTC)


def _default_jitter(base: float) -> float:
    return random.uniform(0.0, base * 0.2)


def _row_to_record(row: dict[str, Any]) -> DocumentJobRecord:
    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        return str(value)

    return DocumentJobRecord(
        id=JobId(row["id"]),
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        document_id=DocumentId(row["document_id"]),
        version_id=VersionId(row["version_id"]),
        state=DocumentJobState(row["state"]),
        stage=row["stage"],
        idempotency_key=row["idempotency_key"],
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        retry_at=_iso(row.get("retry_at")),
        lease_owner=row.get("lease_owner"),
        lease_expires_at=_iso(row.get("lease_expires_at")),
        heartbeat_at=_iso(row.get("heartbeat_at")),
        cancel_requested=bool(row.get("cancel_requested")),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        version=int(row["row_version"]),
        created_at=_iso(row["created_at"]) or "",
        updated_at=_iso(row["updated_at"]) or "",
    )


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class PostgresDocumentJobRepository:
    """Durable, multi-replica document job queue backed by Postgres."""

    def __init__(
        self,
        pool: Any,
        *,
        clock: Callable[[], datetime] = _now,
        jitter: Callable[[float], float] | None = None,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        ensure_schema: bool = True,
    ) -> None:
        self._pool = pool
        self._clock = clock
        self._jitter = jitter or _default_jitter
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        if ensure_schema:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)
            conn.commit()

    # ── Enqueue ──────────────────────────────────────────────────────────

    def enqueue(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        document_id: DocumentId,
        version_id: VersionId,
        idempotency_key: str,
        max_attempts: int = 3,
        job_id: JobId | None = None,
    ) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        workspace = _required(workspace_id, "workspace_id")
        key = _required(idempotency_key, "idempotency_key")
        doc_id = str(DocumentId(document_id))
        ver_id = str(VersionId(version_id))
        if isinstance(max_attempts, bool) or int(max_attempts) <= 0:
            raise ValueError("max_attempts must be a positive integer")
        identifier = str(JobId(job_id) if job_id else new_job_id())
        now = self._clock()
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND idempotency_key = %s",
                        (tenant, key),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        replay = (
                            existing["workspace_id"],
                            existing["document_id"],
                            existing["version_id"],
                            int(existing["max_attempts"]),
                        )
                        requested = (workspace, doc_id, ver_id, int(max_attempts))
                        if replay != requested:
                            raise JobConflictError(
                                "idempotency key is already bound to a different request"
                            )
                        conn.commit()
                        return _row_to_record(existing)
                    cur.execute(
                        """
                        INSERT INTO document_jobs (
                            id, tenant_id, workspace_id, document_id, version_id,
                            state, stage, idempotency_key, max_attempts,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            identifier, tenant, workspace, doc_id, ver_id,
                            "received", "received", key, int(max_attempts), now, now,
                        ),
                    )
                    self._insert_event(
                        cur, tenant_id=tenant, job_id=identifier, event_type="enqueued",
                        from_state=None, to_state="received", from_version=None,
                        to_version=0, stage="received", lease_owner=None,
                        error_code=None, error_message=None, created_at=now,
                    )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(row)

    def get(self, *, tenant_id: str, job_id: JobId) -> DocumentJobRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                    (tenant, str(JobId(job_id))),
                )
                row = cur.fetchone()
            conn.commit()
        return _row_to_record(row) if row is not None else None

    def interrupted_intake_jobs(
        self, *, tenant_id: str | None = None
    ) -> list[DocumentJobRecord]:
        """Return intake states that may have been stranded by process death."""

        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        clause = " AND tenant_id = %s" if tenant is not None else ""
        params: tuple[Any, ...] = (tenant,) if tenant is not None else ()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM document_jobs
                    WHERE state IN ('received', 'quarantined', 'validating')
                      AND cancel_requested = FALSE
                      {clause}
                    ORDER BY created_at, id
                    """,
                    params,
                )
                rows = cur.fetchall()
            conn.commit()
        return [_row_to_record(row) for row in rows]

    # ── Claim (multi-replica) ────────────────────────────────────────────

    def claim_next(
        self,
        *,
        owner: str,
        lease_seconds: float = 60.0,
        tenant_id: str | None = None,
    ) -> DocumentJobRecord | None:
        """Atomically reclaim expired work then claim one eligible job.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent replicas
        never contend for the same row, then finalizes the claim with a CAS
        on ``(state, row_version)``.
        """
        claimant = _required(owner, "owner")
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._clock()
        expires = now + timedelta(seconds=float(lease_seconds))
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    self._recover_expired(cur, now, tenant_id=tenant)
                    tenant_clause = " AND tenant_id = %s" if tenant is not None else ""
                    params: list[Any] = [now]
                    if tenant is not None:
                        params.append(tenant)
                    cur.execute(
                        f"""
                        SELECT * FROM document_jobs
                        WHERE (
                            state IN ('ready_to_parse', 'ocr_required')
                            OR (state = 'retry_wait' AND retry_at IS NOT NULL AND retry_at <= %s)
                        )
                        AND cancel_requested = FALSE
                        {tenant_clause}
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """,
                        params,
                    )
                    row = cur.fetchone()
                    if row is None:
                        conn.commit()
                        return None
                    if int(row["attempt"]) >= int(row["max_attempts"]):
                        self._apply_transition(
                            cur, row, target="dead_letter", stage=row["stage"],
                            event_type="attempts_exhausted", error_code="max_attempts",
                            error_message="Maximum processing attempts exhausted", now=now,
                        )
                        conn.commit()
                        return None
                    source = DocumentJobState(row["state"])
                    target = self._claim_target(row)
                    self._validate(source, target)
                    next_version = int(row["row_version"]) + 1
                    cur.execute(
                        """
                        UPDATE document_jobs
                        SET state = %s, stage = %s, attempt = attempt + 1,
                            retry_at = NULL, lease_owner = %s, lease_expires_at = %s,
                            heartbeat_at = %s, error_code = NULL, error_message = NULL,
                            row_version = %s, updated_at = %s
                        WHERE tenant_id = %s AND id = %s AND state = %s AND row_version = %s
                        """,
                        (
                            target.value, target.value, claimant, expires, now,
                            next_version, now, row["tenant_id"], row["id"],
                            source.value, int(row["row_version"]),
                        ),
                    )
                    if cur.rowcount != 1:
                        conn.rollback()
                        return None
                    self._insert_event(
                        cur, tenant_id=row["tenant_id"], job_id=row["id"], event_type="claimed",
                        from_state=source.value, to_state=target.value,
                        from_version=int(row["row_version"]), to_version=next_version,
                        stage=target.value, lease_owner=claimant, error_code=None,
                        error_message=None, created_at=now,
                    )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (row["tenant_id"], row["id"]),
                    )
                    claimed = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(claimed) if claimed is not None else None

    def renew_lease(
        self, *, tenant_id: str, job_id: JobId, owner: str, lease_seconds: float = 60.0
    ) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        claimant = _required(owner, "owner")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._clock()
        expires = now + timedelta(seconds=float(lease_seconds))
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE document_jobs
                        SET heartbeat_at = %s, lease_expires_at = %s, updated_at = %s
                        WHERE tenant_id = %s AND id = %s AND lease_owner = %s
                          AND state IN ('validating','parsing','ocr_running',
                                        'normalizing','indexing','verifying')
                        """,
                        (now, expires, now, tenant, identifier, claimant),
                    )
                    if cur.rowcount != 1:
                        cur.execute(
                            "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                            (tenant, identifier),
                        )
                        row = cur.fetchone()
                        conn.rollback()
                        if row is None:
                            raise JobNotFoundError("job is unavailable in this tenant")
                        raise JobLeaseConflictError(
                            "lease may only be renewed by its current owner"
                        )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(row)

    def transition(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
        expected_state: DocumentJobState,
        expected_version: int,
        new_state: DocumentJobState,
        stage: str | None = None,
        lease_owner: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        event_type: str = "transitioned",
    ) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        source = DocumentJobState(expected_state)
        target = DocumentJobState(new_state)
        now = self._clock()
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise JobNotFoundError("job is unavailable in this tenant")
                    actual_state = DocumentJobState(row["state"])
                    if actual_state is not source or int(row["row_version"]) != int(expected_version):
                        raise StaleJobUpdateError(
                            f"expected {source.value}@{expected_version}, "
                            f"found {actual_state.value}@{row['row_version']}"
                        )
                    if actual_state in TERMINAL_STATES:
                        raise InvalidJobTransitionError(
                            f"terminal job {actual_state.value} is immutable"
                        )
                    self._validate(source, target)
                    if row["lease_owner"] is not None and lease_owner is not None and row["lease_owner"] != lease_owner:
                        raise JobLeaseConflictError(
                            "leased job may only be changed by its current owner"
                        )
                    self._apply_transition(
                        cur, row, target=target.value, stage=stage or target.value,
                        event_type=event_type, error_code=error_code,
                        error_message=error_message, now=now,
                    )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    updated = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(updated)

    def record_failure(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
        expected_state: DocumentJobState,
        expected_version: int,
        owner: str,
        error_code: str,
        error_message: str,
        transient: bool,
    ) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        source = DocumentJobState(expected_state)
        claimant = _required(owner, "owner")
        now = self._clock()
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise JobNotFoundError("job is unavailable in this tenant")
                    actual_state = DocumentJobState(row["state"])
                    if actual_state is not source or int(row["row_version"]) != int(expected_version):
                        raise StaleJobUpdateError("job changed before failure was recorded")
                    if row["lease_owner"] is not None and row["lease_owner"] != claimant:
                        raise JobLeaseConflictError("only the lease owner may record failure")
                    retry_at = None
                    if not transient:
                        target = "rejected"
                        event_type = "permanent_failure"
                    elif int(row["attempt"]) >= int(row["max_attempts"]):
                        target = "dead_letter"
                        event_type = "attempts_exhausted"
                    else:
                        target = "retry_wait"
                        delay = self._retry_delay(int(row["attempt"]))
                        retry_at = now + timedelta(seconds=delay)
                        event_type = "transient_failure"
                    self._validate(source, DocumentJobState(target))
                    self._apply_transition(
                        cur, row, target=target, stage=row["stage"],
                        event_type=event_type, error_code=error_code,
                        error_message=error_message, retry_at=retry_at, now=now,
                    )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    updated = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(updated)

    def recover_expired_leases(self, *, tenant_id: str | None = None) -> int:
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        now = self._clock()
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    count = self._recover_expired(cur, now, tenant_id=tenant)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def request_cancel(self, *, tenant_id: str, job_id: JobId) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        now = self._clock()
        pending = {s.value for s in (
            DocumentJobState.RECEIVED, DocumentJobState.QUARANTINED,
            DocumentJobState.READY_TO_PARSE, DocumentJobState.OCR_REQUIRED,
            DocumentJobState.RETRY_WAIT,
        )}
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise JobNotFoundError("job is unavailable in this tenant")
                    state = DocumentJobState(row["state"])
                    if state in TERMINAL_STATES:
                        raise InvalidJobTransitionError(f"terminal job {state.value} is immutable")
                    if row["state"] in pending:
                        self._apply_transition(
                            cur, row, target="cancelled", stage=row["stage"],
                            event_type="cancelled", error_code=None,
                            error_message=None, cancel_requested=True, now=now,
                        )
                    else:
                        next_version = int(row["row_version"]) + 1
                        cur.execute(
                            """
                            UPDATE document_jobs SET cancel_requested = TRUE,
                                row_version = %s, updated_at = %s
                            WHERE tenant_id = %s AND id = %s AND row_version = %s
                            """,
                            (next_version, now, tenant, identifier, int(row["row_version"])),
                        )
                        self._insert_event(
                            cur, tenant_id=tenant, job_id=identifier,
                            event_type="cancellation_requested", from_state=row["state"],
                            to_state=row["state"], from_version=int(row["row_version"]),
                            to_version=next_version, stage=row["stage"],
                            lease_owner=row["lease_owner"], error_code=None,
                            error_message=None, created_at=now,
                        )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    updated = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(updated)

    def cancel_claimed(self, *, tenant_id: str, job_id: JobId, owner: str) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        claimant = _required(owner, "owner")
        now = self._clock()
        active = set(_ACTIVE_STATE_VALUES)
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                        (tenant, identifier),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise JobNotFoundError("job is unavailable in this tenant")
                    if row["state"] not in active:
                        raise InvalidJobTransitionError("only active work can be cooperatively cancelled")
                    if row["lease_owner"] is not None and row["lease_owner"] != claimant:
                        raise JobLeaseConflictError("leased job may only be changed by its current owner")
                    if not bool(row["cancel_requested"]):
                        raise JobConflictError("cancellation has not been requested")
                    self._apply_transition(
                        cur, row, target="cancelled", stage=row["stage"],
                        event_type="cancelled", error_code=None, error_message=None,
                        cancel_requested=True, now=now,
                    )
                    cur.execute(
                        "SELECT * FROM document_jobs WHERE tenant_id = %s AND id = %s",
                        (tenant, identifier),
                    )
                    updated = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_record(updated)

    def events(self, *, tenant_id: str, job_id: JobId) -> list[Any]:
        from .jobs import DocumentJobEvent

        tenant = _required(tenant_id, "tenant_id")
        identifier = str(JobId(job_id))
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM document_jobs WHERE tenant_id = %s AND id = %s",
                    (tenant, identifier),
                )
                if cur.fetchone() is None:
                    raise JobNotFoundError("job is unavailable in this tenant")
                cur.execute(
                    "SELECT * FROM document_job_events WHERE tenant_id = %s AND job_id = %s ORDER BY id",
                    (tenant, identifier),
                )
                rows = cur.fetchall()
            conn.commit()
        events: list[Any] = []
        for row in rows:
            created = row["created_at"]
            events.append(
                DocumentJobEvent(
                    id=int(row["id"]), tenant_id=row["tenant_id"], job_id=JobId(row["job_id"]),
                    event_type=row["event_type"],
                    from_state=DocumentJobState(row["from_state"]) if row["from_state"] else None,
                    to_state=DocumentJobState(row["to_state"]),
                    from_version=int(row["from_version"]) if row["from_version"] is not None else None,
                    to_version=int(row["to_version"]), stage=row["stage"],
                    lease_owner=row["lease_owner"], error_code=row["error_code"],
                    error_message=row["error_message"],
                    created_at=created.astimezone(UTC).isoformat() if isinstance(created, datetime) else str(created),
                )
            )
        return events

    def queue_stats(self, *, tenant_id: str | None = None) -> QueueStats:
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        tenant_clause = " AND tenant_id = %s" if tenant is not None else ""
        params: list[Any] = [tenant] if tenant is not None else []
        queueable = ",".join(f"'{s}'" for s in _QUEUEABLE_STATE_VALUES)
        terminal = ",".join(f"'{s}'" for s in _TERMINAL_STATE_VALUES)
        active = ",".join(f"'{s}'" for s in _ACTIVE_STATE_VALUES)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      COALESCE(SUM(CASE WHEN state IN ({queueable}) THEN 1 ELSE 0 END),0) AS depth,
                      COALESCE(SUM(CASE WHEN state IN ({active}) AND lease_owner IS NOT NULL THEN 1 ELSE 0 END),0) AS active_leases,
                      COALESCE(SUM(CASE WHEN state = 'retry_wait' THEN 1 ELSE 0 END),0) AS retry_waiting,
                      COALESCE(SUM(CASE WHEN state = 'dead_letter' THEN 1 ELSE 0 END),0) AS dead_letter,
                      COALESCE(SUM(CASE WHEN state NOT IN ({terminal}) THEN 1 ELSE 0 END),0) AS non_terminal
                    FROM document_jobs WHERE TRUE{tenant_clause}
                    """,
                    params,
                )
                row = cur.fetchone()
                cur.execute(
                    f"SELECT MIN(created_at) AS oldest FROM document_jobs WHERE state IN ({queueable}){tenant_clause}",
                    params,
                )
                oldest = cur.fetchone()
            conn.commit()
        oldest_age = 0.0
        if oldest and oldest["oldest"] is not None:
            oldest_dt = oldest["oldest"]
            if isinstance(oldest_dt, datetime):
                oldest_age = max(0.0, (self._clock() - oldest_dt.astimezone(UTC)).total_seconds())
        return QueueStats(
            depth=int(row["depth"]), active_leases=int(row["active_leases"]),
            retry_waiting=int(row["retry_waiting"]), dead_letter=int(row["dead_letter"]),
            non_terminal=int(row["non_terminal"]), oldest_age_seconds=oldest_age,
        )

    def tenant_load(self, *, tenant_id: str) -> TenantLoad:
        tenant = _required(tenant_id, "tenant_id")
        terminal = ",".join(f"'{s}'" for s in _TERMINAL_STATE_VALUES)
        active = ",".join(f"'{s}'" for s in _ACTIVE_STATE_VALUES)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      COALESCE(SUM(CASE WHEN state NOT IN ({terminal}) THEN 1 ELSE 0 END),0) AS queued,
                      COALESCE(SUM(CASE WHEN state IN ({active}) AND lease_owner IS NOT NULL THEN 1 ELSE 0 END),0) AS active
                    FROM document_jobs WHERE tenant_id = %s
                    """,
                    (tenant,),
                )
                row = cur.fetchone()
            conn.commit()
        return TenantLoad(queued=int(row["queued"]), active=int(row["active"]))

    def document_job_ids(
        self, *, tenant_id: str, document_id: DocumentId | str
    ) -> list[JobId]:
        tenant = _required(tenant_id, "tenant_id")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM document_jobs
                    WHERE tenant_id = %s AND document_id = %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    (tenant, str(document_id)),
                )
                rows = cur.fetchall()
            conn.commit()
        return [JobId(row["id"]) for row in rows]

    # ── Internal helpers ─────────────────────────────────────────────────

    def _recover_expired(self, cur: Any, now: datetime, *, tenant_id: str | None) -> int:
        tenant_clause = " AND tenant_id = %s" if tenant_id is not None else ""
        params: list[Any] = [now]
        if tenant_id is not None:
            params.append(tenant_id)
        cur.execute(
            f"""
            SELECT * FROM document_jobs
            WHERE state IN ('validating','parsing','ocr_running','normalizing','indexing','verifying')
              AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= %s{tenant_clause}
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
            """,
            params,
        )
        rows = cur.fetchall()
        for row in rows:
            if bool(row["cancel_requested"]):
                target, event_type, code, message, retry_at = (
                    "cancelled", "cancelled", None, None, None,
                )
            elif int(row["attempt"]) >= int(row["max_attempts"]):
                target, event_type, code, message, retry_at = (
                    "dead_letter", "attempts_exhausted", "lease_expired",
                    "Worker lease expired after maximum attempts", None,
                )
            else:
                target, event_type, code, message, retry_at = (
                    "retry_wait", "lease_expired", "lease_expired",
                    "Worker lease expired before completion", now,
                )
            self._apply_transition(
                cur, row, target=target, stage=row["stage"], event_type=event_type,
                error_code=code, error_message=message, retry_at=retry_at,
                cancel_requested=bool(row["cancel_requested"]), now=now,
            )
        return len(rows)

    def _apply_transition(
        self,
        cur: Any,
        row: dict[str, Any],
        *,
        target: str,
        stage: str,
        event_type: str,
        error_code: str | None,
        error_message: str | None,
        now: datetime,
        retry_at: datetime | None = None,
        cancel_requested: bool | None = None,
    ) -> None:
        source = DocumentJobState(row["state"])
        self._validate(source, DocumentJobState(target))
        next_version = int(row["row_version"]) + 1
        clear_lease = target not in _ACTIVE_STATE_VALUES
        next_cancel = bool(cancel_requested) if cancel_requested is not None else bool(row["cancel_requested"])
        cur.execute(
            """
            UPDATE document_jobs
            SET state = %s, stage = %s, retry_at = %s, lease_owner = %s,
                lease_expires_at = %s, heartbeat_at = %s, cancel_requested = %s,
                error_code = %s, error_message = %s, row_version = %s, updated_at = %s
            WHERE tenant_id = %s AND id = %s AND state = %s AND row_version = %s
            """,
            (
                target, stage, retry_at,
                None if clear_lease else row["lease_owner"],
                None if clear_lease else row["lease_expires_at"],
                None if clear_lease else row["heartbeat_at"],
                next_cancel, error_code, error_message, next_version, now,
                row["tenant_id"], row["id"], source.value, int(row["row_version"]),
            ),
        )
        if cur.rowcount != 1:
            raise StaleJobUpdateError("job changed during transition")
        self._insert_event(
            cur, tenant_id=row["tenant_id"], job_id=row["id"], event_type=event_type,
            from_state=source.value, to_state=target, from_version=int(row["row_version"]),
            to_version=next_version, stage=stage, lease_owner=row["lease_owner"],
            error_code=error_code, error_message=error_message, created_at=now,
        )

    @staticmethod
    def _insert_event(
        cur: Any,
        *,
        tenant_id: str,
        job_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str,
        from_version: int | None,
        to_version: int,
        stage: str,
        lease_owner: str | None,
        error_code: str | None,
        error_message: str | None,
        created_at: datetime,
    ) -> None:
        cur.execute(
            """
            INSERT INTO document_job_events (
                tenant_id, job_id, event_type, from_state, to_state,
                from_version, to_version, stage, lease_owner,
                error_code, error_message, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id, job_id, event_type, from_state, to_state,
                from_version, to_version, stage, lease_owner,
                error_code, error_message, created_at,
            ),
        )

    @staticmethod
    def _claim_target(row: dict[str, Any]) -> DocumentJobState:
        state = DocumentJobState(row["state"])
        if state is DocumentJobState.READY_TO_PARSE:
            return DocumentJobState.PARSING
        if state is DocumentJobState.OCR_REQUIRED:
            return DocumentJobState.OCR_RUNNING
        if state is not DocumentJobState.RETRY_WAIT:
            raise InvalidJobTransitionError(f"{state.value} is not claimable")
        stage = DocumentJobState(row["stage"])
        if stage.value not in _ACTIVE_STATE_VALUES:
            raise InvalidJobTransitionError(f"retry stage {stage.value} is not processable")
        return stage

    @staticmethod
    def _validate(source: DocumentJobState, target: DocumentJobState) -> None:
        if target not in ALLOWED_TRANSITIONS[source]:
            raise InvalidJobTransitionError(
                f"transition {source.value} -> {target.value} is not allowed"
            )

    def _retry_delay(self, attempt: int) -> float:
        base = min(self._retry_max_seconds, self._retry_base_seconds * (2 ** max(0, attempt - 1)))
        jitter = float(self._jitter(base))
        if not math.isfinite(jitter):
            raise ValueError("retry jitter must be finite")
        return min(self._retry_max_seconds, max(0.0, base + jitter))


# ── Backend resolution + readiness ───────────────────────────────────────


def document_jobs_backend() -> str:
    """Return the active document-job backend ('postgres' or 'sqlite')."""
    if (os.environ.get("KAZMA_DOCUMENTS_JOBS_BACKEND") or "").strip().lower() in ("sqlite", "sqlite3"):
        return "sqlite"
    try:
        from kazma_core.db.pg_helpers import use_postgres

        return "postgres" if use_postgres() else "sqlite"
    except Exception:  # noqa: BLE001
        return "sqlite"


def resolve_job_repository(
    sqlite_repo: Any,
    *,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 300.0,
) -> Any:
    """Return the Postgres job repo when configured, else the SQLite one.

    Falls back to the provided SQLite repository (truthful degraded) if the
    Postgres pool is unavailable, so a momentary DB blip never breaks intake.
    """
    if document_jobs_backend() != "postgres":
        return sqlite_repo
    try:
        from kazma_core.db.pg_helpers import get_pool

        pool = get_pool()
        repo = PostgresDocumentJobRepository(
            pool, retry_base_seconds=retry_base_seconds, retry_max_seconds=retry_max_seconds
        )
        logger.info("[documents.jobs] using Postgres multi-replica job repository")
        return repo
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[documents.jobs] Postgres job repo unavailable (%s); "
            "falling back to single-node SQLite queue",
            exc,
        )
        return sqlite_repo


def document_metadata_backend() -> str:
    """Return active document metadata backend ('postgres' or 'sqlite')."""
    forced = (os.environ.get("KAZMA_DOCUMENTS_METADATA_BACKEND") or "").strip().lower()
    if forced in {"sqlite", "sqlite3"}:
        return "sqlite"
    if forced in {"postgres", "postgresql", "pg"}:
        return "postgres"
    # Auto: follow the jobs backend (Postgres when the platform is multi-replica).
    if forced in {"", "auto"}:
        return document_jobs_backend()
    return "sqlite"


def document_storage_readiness(
    *, jobs_repo: Any = None, metadata_repo: Any = None
) -> dict[str, Any]:
    """Report the honest multi-replica readiness of document storage.

    Job claiming is multi-replica-safe on Postgres. Metadata is multi-replica
    when a Postgres-backed repository is active (see
    ``resolve_document_repository``); otherwise it remains SQLite single-replica.
    Never lies about it.
    """
    backend = document_jobs_backend()
    jobs_multi = backend == "postgres" and isinstance(
        jobs_repo, PostgresDocumentJobRepository
    )
    meta_backend = "sqlite"
    meta_multi = False
    if metadata_repo is not None:
        meta_backend = str(
            getattr(metadata_repo, "backend_name", None)
            or getattr(type(metadata_repo), "backend_name", "sqlite")
        )
        meta_multi = bool(getattr(metadata_repo, "multi_replica", False))
    else:
        meta_backend = document_metadata_backend()
        meta_multi = meta_backend == "postgres"
    reasons: list[str] = []
    if backend == "postgres" and not jobs_multi:
        reasons.append("jobs_postgres_unavailable_fell_back_to_sqlite")
    if not meta_multi:
        reasons.append("metadata_single_replica")
    if jobs_multi and meta_multi:
        status = "ready"
    elif backend == "postgres" or meta_backend == "postgres":
        status = "degraded"
    else:
        status = "ready"
    return {
        "status": status,
        "jobs_backend": backend,
        "jobs_multi_replica": jobs_multi,
        "metadata_backend": meta_backend,
        "metadata_multi_replica": meta_multi,
        "degraded_reasons": reasons,
    }
