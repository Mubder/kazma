"""Durable, tenant-isolated document job queue."""

from __future__ import annotations

import math
import random
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from .models import DocumentId, DocumentJobState, JobId, VersionId, new_job_id
from .repository import DocumentRepository

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DocumentJobEvent",
    "DocumentJobRecord",
    "DocumentJobRepository",
    "InvalidJobTransitionError",
    "JobConflictError",
    "JobLeaseConflictError",
    "JobNotFoundError",
    "QueueStats",
    "StaleJobUpdateError",
    "TenantLoad",
]

# States considered "claimable / pending" for queue-depth reporting: not yet
# actively leased by a worker and not terminal.
_QUEUEABLE_STATE_VALUES = (
    "received",
    "quarantined",
    "validating",
    "ready_to_parse",
    "ocr_required",
    "retry_wait",
)
_ACTIVE_STATE_VALUES = (
    "validating",
    "parsing",
    "ocr_running",
    "normalizing",
    "indexing",
    "verifying",
)
_TERMINAL_STATE_VALUES = ("rejected", "ready", "cancelled", "dead_letter")

_ACTIVE_STATES = frozenset(
    {
        DocumentJobState.VALIDATING,
        DocumentJobState.PARSING,
        DocumentJobState.OCR_RUNNING,
        DocumentJobState.NORMALIZING,
        DocumentJobState.INDEXING,
        DocumentJobState.VERIFYING,
    }
)
_PENDING_STATES = frozenset(
    {
        DocumentJobState.RECEIVED,
        DocumentJobState.QUARANTINED,
        DocumentJobState.READY_TO_PARSE,
        DocumentJobState.OCR_REQUIRED,
        DocumentJobState.RETRY_WAIT,
    }
)
TERMINAL_STATES = frozenset(
    {
        DocumentJobState.REJECTED,
        DocumentJobState.READY,
        DocumentJobState.CANCELLED,
        DocumentJobState.DEAD_LETTER,
    }
)

ALLOWED_TRANSITIONS = MappingProxyType(
    {
        DocumentJobState.RECEIVED: frozenset(
            {
                DocumentJobState.QUARANTINED,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
            }
        ),
        DocumentJobState.QUARANTINED: frozenset(
            {
                DocumentJobState.VALIDATING,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
            }
        ),
        DocumentJobState.VALIDATING: frozenset(
            {
                DocumentJobState.READY_TO_PARSE,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.READY_TO_PARSE: frozenset(
            {DocumentJobState.PARSING, DocumentJobState.CANCELLED}
        ),
        DocumentJobState.PARSING: frozenset(
            {
                DocumentJobState.OCR_REQUIRED,
                DocumentJobState.NORMALIZING,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.OCR_REQUIRED: frozenset(
            {DocumentJobState.OCR_RUNNING, DocumentJobState.CANCELLED}
        ),
        DocumentJobState.OCR_RUNNING: frozenset(
            {
                DocumentJobState.NORMALIZING,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.NORMALIZING: frozenset(
            {
                DocumentJobState.INDEXING,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.INDEXING: frozenset(
            {
                DocumentJobState.VERIFYING,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.VERIFYING: frozenset(
            {
                DocumentJobState.READY,
                DocumentJobState.RETRY_WAIT,
                DocumentJobState.REJECTED,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.RETRY_WAIT: frozenset(
            {
                DocumentJobState.VALIDATING,
                DocumentJobState.PARSING,
                DocumentJobState.OCR_RUNNING,
                DocumentJobState.NORMALIZING,
                DocumentJobState.INDEXING,
                DocumentJobState.VERIFYING,
                DocumentJobState.CANCELLED,
                DocumentJobState.DEAD_LETTER,
            }
        ),
        DocumentJobState.REJECTED: frozenset(),
        DocumentJobState.READY: frozenset(),
        DocumentJobState.CANCELLED: frozenset(),
        DocumentJobState.DEAD_LETTER: frozenset(),
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_jobs (
    id TEXT NOT NULL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'received', 'quarantined', 'validating', 'rejected', 'ready_to_parse',
        'parsing', 'ocr_required', 'ocr_running', 'normalizing', 'indexing',
        'verifying', 'ready', 'retry_wait', 'cancelled', 'dead_letter'
    )),
    stage TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
    retry_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error_code TEXT,
    error_message TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id),
    FOREIGN KEY (version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, job_id, to_version),
    FOREIGN KEY (job_id, tenant_id) REFERENCES document_jobs(id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_document_jobs_claim
    ON document_jobs(state, retry_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_document_jobs_tenant_document
    ON document_jobs(tenant_id, document_id, version_id);
CREATE INDEX IF NOT EXISTS idx_document_job_events_history
    ON document_job_events(tenant_id, job_id, id);

CREATE TRIGGER IF NOT EXISTS document_job_events_no_update
BEFORE UPDATE ON document_job_events
BEGIN
    SELECT RAISE(ABORT, 'document job events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS document_job_events_no_delete
BEFORE DELETE ON document_job_events
BEGIN
    SELECT RAISE(ABORT, 'document job events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS document_jobs_terminal_no_update
BEFORE UPDATE ON document_jobs
WHEN OLD.state IN ('rejected', 'ready', 'cancelled', 'dead_letter')
BEGIN
    SELECT RAISE(ABORT, 'terminal document jobs are immutable');
END;
"""

_SAFE_CODE_RE = re.compile(r"[^a-z0-9_.-]+")
_MAX_ERROR_MESSAGE = 1024


class JobConflictError(RuntimeError):
    """Base class for focused durable-job conflicts."""


class JobNotFoundError(JobConflictError):
    """Raised when a job is unavailable in the requested tenant."""


class InvalidJobTransitionError(JobConflictError):
    """Raised when the canonical lifecycle forbids a transition."""


class StaleJobUpdateError(JobConflictError):
    """Raised when expected state/version no longer matches durable state."""


class JobLeaseConflictError(JobConflictError):
    """Raised when a non-owner tries to modify a leased job."""


@dataclass(frozen=True, slots=True)
class DocumentJobRecord:
    id: JobId
    tenant_id: str
    workspace_id: str
    document_id: DocumentId
    version_id: VersionId
    state: DocumentJobState
    stage: str
    idempotency_key: str
    attempt: int
    max_attempts: int
    retry_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DocumentJobEvent:
    id: int
    tenant_id: str
    job_id: JobId
    event_type: str
    from_state: DocumentJobState | None
    to_state: DocumentJobState
    from_version: int | None
    to_version: int
    stage: str
    lease_owner: str | None
    error_code: str | None
    error_message: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class QueueStats:
    """Aggregate queue health for capacity/backpressure and metrics."""

    depth: int
    active_leases: int
    retry_waiting: int
    dead_letter: int
    non_terminal: int
    oldest_age_seconds: float


@dataclass(frozen=True, slots=True)
class TenantLoad:
    """Per-tenant in-flight job counts for backpressure enforcement."""

    queued: int
    active: int


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_jitter(base: float) -> float:
    return random.uniform(0.0, base * 0.2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _sanitize_error(
    error_code: str | None, error_message: str | None
) -> tuple[str | None, str | None]:
    code = None
    if error_code:
        code = _SAFE_CODE_RE.sub("_", str(error_code).strip().lower())[:64].strip("_")
        code = code or "document_error"
    message = None
    if error_message:
        printable = "".join(
            char if char.isprintable() and char not in "\r\n" else " "
            for char in str(error_message)
        )
        message = " ".join(printable.split())[:_MAX_ERROR_MESSAGE]
    return code, message


def _job(row: sqlite3.Row) -> DocumentJobRecord:
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
        retry_at=row["retry_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        cancel_requested=bool(row["cancel_requested"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        version=int(row["row_version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event(row: sqlite3.Row) -> DocumentJobEvent:
    return DocumentJobEvent(
        id=int(row["id"]),
        tenant_id=row["tenant_id"],
        job_id=JobId(row["job_id"]),
        event_type=row["event_type"],
        from_state=DocumentJobState(row["from_state"]) if row["from_state"] else None,
        to_state=DocumentJobState(row["to_state"]),
        from_version=(
            int(row["from_version"]) if row["from_version"] is not None else None
        ),
        to_version=int(row["to_version"]),
        stage=row["stage"],
        lease_owner=row["lease_owner"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


class DocumentJobRepository:
    """Job repository sharing a :class:`DocumentRepository` connection and lock."""

    def __init__(
        self,
        repository: DocumentRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
        jitter: Callable[[float], float] | None = None,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
    ) -> None:
        if not isinstance(repository, DocumentRepository):
            raise TypeError("repository must be a DocumentRepository")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        if retry_max_seconds < retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")
        self._repository = repository
        self._conn = repository._conn
        self._lock = repository._lock
        self._clock = clock
        self._jitter = jitter or _default_jitter
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        with self._lock:
            self._conn.executescript(_SCHEMA)

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
        """Create a received job, returning the existing row on idempotent replay."""
        tenant = _required(tenant_id, "tenant_id")
        workspace = _required(workspace_id, "workspace_id")
        key = _required(idempotency_key, "idempotency_key")
        doc_id = DocumentId(document_id)
        ver_id = VersionId(version_id)
        if isinstance(max_attempts, bool) or int(max_attempts) <= 0:
            raise ValueError("max_attempts must be a positive integer")
        identifier = JobId(job_id) if job_id else new_job_id()
        now = _timestamp(self._clock())
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    """
                    SELECT * FROM document_jobs
                    WHERE tenant_id = ? AND idempotency_key = ?
                    """,
                    (tenant, key),
                ).fetchone()
                if existing is not None:
                    replay_fields = (
                        existing["workspace_id"],
                        existing["document_id"],
                        existing["version_id"],
                        int(existing["max_attempts"]),
                    )
                    requested_fields = (
                        workspace,
                        str(doc_id),
                        str(ver_id),
                        int(max_attempts),
                    )
                    if replay_fields != requested_fields:
                        raise JobConflictError(
                            "idempotency key is already bound to a different request"
                        )
                    self._conn.execute("COMMIT")
                    return _job(existing)
                self._conn.execute(
                    """
                    INSERT INTO document_jobs (
                        id, tenant_id, workspace_id, document_id, version_id,
                        state, stage, idempotency_key, max_attempts,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(identifier),
                        tenant,
                        workspace,
                        str(doc_id),
                        str(ver_id),
                        DocumentJobState.RECEIVED.value,
                        DocumentJobState.RECEIVED.value,
                        key,
                        int(max_attempts),
                        now,
                        now,
                    ),
                )
                self._insert_event_locked(
                    tenant_id=tenant,
                    job_id=identifier,
                    event_type="enqueued",
                    from_state=None,
                    to_state=DocumentJobState.RECEIVED,
                    from_version=None,
                    to_version=0,
                    stage=DocumentJobState.RECEIVED.value,
                    lease_owner=None,
                    error_code=None,
                    error_message=None,
                    created_at=now,
                )
                row = self._select_locked(tenant, identifier)
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        if row is None:
            raise RuntimeError("job insert succeeded but record was not found")
        return _job(row)

    def get(self, *, tenant_id: str, job_id: JobId) -> DocumentJobRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        with self._lock:
            row = self._select_locked(tenant, identifier)
        return _job(row) if row is not None else None

    def interrupted_intake_jobs(
        self, *, tenant_id: str | None = None
    ) -> list[DocumentJobRecord]:
        """Return intake states that may have been stranded by process death."""

        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        clause = " AND tenant_id = ?" if tenant is not None else ""
        params: tuple[object, ...] = (tenant,) if tenant is not None else ()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM document_jobs
                WHERE state IN ('received', 'quarantined', 'validating')
                  AND cancel_requested = 0
                  {clause}
                ORDER BY created_at, id
                """,
                params,
            ).fetchall()
        return [_job(row) for row in rows]

    def events(
        self, *, tenant_id: str, job_id: JobId
    ) -> list[DocumentJobEvent]:
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        with self._lock:
            if self._select_locked(tenant, identifier) is None:
                raise JobNotFoundError("job is unavailable in this tenant")
            rows = self._conn.execute(
                """
                SELECT * FROM document_job_events
                WHERE tenant_id = ? AND job_id = ? ORDER BY id
                """,
                (tenant, str(identifier)),
            ).fetchall()
        return [_event(row) for row in rows]

    def queue_stats(self, *, tenant_id: str | None = None) -> QueueStats:
        """Return aggregate queue health (optionally scoped to one tenant).

        ``depth`` counts claimable/pending jobs, ``active_leases`` counts
        in-flight leased jobs, ``retry_waiting`` and ``dead_letter`` count
        those states, ``non_terminal`` is everything not in a terminal state
        (the true backlog), and ``oldest_age_seconds`` is the age of the
        oldest claimable job.
        """
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        tenant_clause = " AND tenant_id = ?" if tenant is not None else ""
        params: list[object] = [tenant] if tenant is not None else []
        queueable = ",".join(f"'{s}'" for s in _QUEUEABLE_STATE_VALUES)
        terminal = ",".join(f"'{s}'" for s in _TERMINAL_STATE_VALUES)
        active = ",".join(f"'{s}'" for s in _ACTIVE_STATE_VALUES)
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                  SUM(CASE WHEN state IN ({queueable}) THEN 1 ELSE 0 END) AS depth,
                  SUM(CASE WHEN state IN ({active}) AND lease_owner IS NOT NULL
                           THEN 1 ELSE 0 END) AS active_leases,
                  SUM(CASE WHEN state = 'retry_wait' THEN 1 ELSE 0 END) AS retry_waiting,
                  SUM(CASE WHEN state = 'dead_letter' THEN 1 ELSE 0 END) AS dead_letter,
                  SUM(CASE WHEN state NOT IN ({terminal}) THEN 1 ELSE 0 END) AS non_terminal
                FROM document_jobs
                WHERE 1=1{tenant_clause}
                """,
                params,
            ).fetchone()
            oldest_row = self._conn.execute(
                f"""
                SELECT MIN(created_at) AS oldest FROM document_jobs
                WHERE state IN ({queueable}){tenant_clause}
                """,
                params,
            ).fetchone()
        oldest_age = 0.0
        if oldest_row is not None and oldest_row["oldest"]:
            try:
                created = datetime.fromisoformat(oldest_row["oldest"])
                oldest_age = max(
                    0.0, (_as_utc(self._clock()) - _as_utc(created)).total_seconds()
                )
            except (ValueError, TypeError):
                oldest_age = 0.0
        return QueueStats(
            depth=int(row["depth"] or 0),
            active_leases=int(row["active_leases"] or 0),
            retry_waiting=int(row["retry_waiting"] or 0),
            dead_letter=int(row["dead_letter"] or 0),
            non_terminal=int(row["non_terminal"] or 0),
            oldest_age_seconds=oldest_age,
        )

    def tenant_load(self, *, tenant_id: str) -> TenantLoad:
        """Return one tenant's in-flight job counts for backpressure caps.

        ``queued`` = non-terminal jobs (the tenant's backlog); ``active`` =
        currently leased jobs in an active processing state.
        """
        tenant = _required(tenant_id, "tenant_id")
        terminal = ",".join(f"'{s}'" for s in _TERMINAL_STATE_VALUES)
        active = ",".join(f"'{s}'" for s in _ACTIVE_STATE_VALUES)
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                  SUM(CASE WHEN state NOT IN ({terminal}) THEN 1 ELSE 0 END) AS queued,
                  SUM(CASE WHEN state IN ({active}) AND lease_owner IS NOT NULL
                           THEN 1 ELSE 0 END) AS active
                FROM document_jobs
                WHERE tenant_id = ?
                """,
                (tenant,),
            ).fetchone()
        return TenantLoad(
            queued=int(row["queued"] or 0),
            active=int(row["active"] or 0),
        )

    def document_job_ids(
        self, *, tenant_id: str, document_id: DocumentId | str
    ) -> list[JobId]:
        """Return a document's job ids, newest first (backend-agnostic API)."""
        tenant = _required(tenant_id, "tenant_id")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id FROM document_jobs
                WHERE tenant_id = ? AND document_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (tenant, str(document_id)),
            ).fetchall()
        return [JobId(row["id"]) for row in rows]

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
        """Apply an allowed state transition using state/version CAS."""
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        source = DocumentJobState(expected_state)
        target = DocumentJobState(new_state)
        code, message = _sanitize_error(error_code, error_message)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._require_expected_locked(
                    tenant, identifier, source, expected_version
                )
                self._validate_transition(source, target)
                self._require_lease_owner(row, lease_owner)
                updated = self._transition_locked(
                    row,
                    target=target,
                    stage=stage,
                    event_type=event_type,
                    error_code=code,
                    error_message=message,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return _job(updated)

    def claim_next(
        self,
        *,
        owner: str,
        lease_seconds: float = 60.0,
        tenant_id: str | None = None,
    ) -> DocumentJobRecord | None:
        """Atomically recover expired work and claim one eligible job."""
        claimant = _required(owner, "owner")
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_dt = _as_utc(self._clock())
        now = _timestamp(now_dt)
        expires = _timestamp(now_dt + timedelta(seconds=float(lease_seconds)))
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._recover_expired_locked(now_dt, tenant_id=tenant)
                tenant_clause = " AND tenant_id = ?" if tenant is not None else ""
                params: list[object] = [now]
                if tenant is not None:
                    params.append(tenant)
                rows = self._conn.execute(
                    f"""
                    SELECT * FROM document_jobs
                    WHERE (
                        state IN ('ready_to_parse', 'ocr_required')
                        OR (state = 'retry_wait' AND retry_at IS NOT NULL AND retry_at <= ?)
                    )
                    AND cancel_requested = 0
                    {tenant_clause}
                    ORDER BY created_at, id
                    """,
                    params,
                ).fetchall()
                claimed: sqlite3.Row | None = None
                for row in rows:
                    if int(row["attempt"]) >= int(row["max_attempts"]):
                        self._transition_locked(
                            row,
                            target=DocumentJobState.DEAD_LETTER,
                            stage=row["stage"],
                            event_type="attempts_exhausted",
                            error_code="max_attempts",
                            error_message="Maximum processing attempts exhausted",
                        )
                        continue
                    source = DocumentJobState(row["state"])
                    target = self._claim_target(row)
                    self._validate_transition(source, target)
                    next_version = int(row["row_version"]) + 1
                    cursor = self._conn.execute(
                        """
                        UPDATE document_jobs
                        SET state = ?, stage = ?, attempt = attempt + 1,
                            retry_at = NULL, lease_owner = ?, lease_expires_at = ?,
                            heartbeat_at = ?, error_code = NULL, error_message = NULL,
                            row_version = ?, updated_at = ?
                        WHERE tenant_id = ? AND id = ? AND state = ? AND row_version = ?
                        """,
                        (
                            target.value,
                            target.value,
                            claimant,
                            expires,
                            now,
                            next_version,
                            now,
                            row["tenant_id"],
                            row["id"],
                            source.value,
                            int(row["row_version"]),
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                    self._insert_event_locked(
                        tenant_id=row["tenant_id"],
                        job_id=JobId(row["id"]),
                        event_type="claimed",
                        from_state=source,
                        to_state=target,
                        from_version=int(row["row_version"]),
                        to_version=next_version,
                        stage=target.value,
                        lease_owner=claimant,
                        error_code=None,
                        error_message=None,
                        created_at=now,
                    )
                    claimed = self._select_locked(row["tenant_id"], JobId(row["id"]))
                    break
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return _job(claimed) if claimed is not None else None

    def renew_lease(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
        owner: str,
        lease_seconds: float = 60.0,
    ) -> DocumentJobRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        claimant = _required(owner, "owner")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_dt = _as_utc(self._clock())
        now = _timestamp(now_dt)
        expires = _timestamp(now_dt + timedelta(seconds=float(lease_seconds)))
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE document_jobs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ? AND lease_owner = ?
                  AND state IN ('validating', 'parsing', 'ocr_running',
                                'normalizing', 'indexing', 'verifying')
                """,
                (now, expires, now, tenant, str(identifier), claimant),
            )
            if cursor.rowcount != 1:
                row = self._select_locked(tenant, identifier)
                if row is None:
                    raise JobNotFoundError("job is unavailable in this tenant")
                raise JobLeaseConflictError(
                    "lease may only be renewed by its current owner"
                )
            row = self._select_locked(tenant, identifier)
        if row is None:
            raise RuntimeError("lease renewal succeeded but job was not found")
        return _job(row)

    def recover_expired_leases(self, *, tenant_id: str | None = None) -> int:
        """Move expired active jobs to retry/dead-letter/cancelled exactly once."""
        tenant = _required(tenant_id, "tenant_id") if tenant_id is not None else None
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                count = self._recover_expired_locked(
                    _as_utc(self._clock()), tenant_id=tenant
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return count

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
        """Persist a sanitized permanent rejection or bounded transient retry."""
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        source = DocumentJobState(expected_state)
        claimant = _required(owner, "owner")
        code, message = _sanitize_error(error_code, error_message)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._require_expected_locked(
                    tenant, identifier, source, expected_version
                )
                self._require_lease_owner(row, claimant)
                if not transient:
                    target = DocumentJobState.REJECTED
                    retry_at = None
                    event_type = "permanent_failure"
                elif int(row["attempt"]) >= int(row["max_attempts"]):
                    target = DocumentJobState.DEAD_LETTER
                    retry_at = None
                    event_type = "attempts_exhausted"
                else:
                    target = DocumentJobState.RETRY_WAIT
                    delay = self._retry_delay(int(row["attempt"]))
                    retry_at = _timestamp(
                        _as_utc(self._clock()) + timedelta(seconds=delay)
                    )
                    event_type = "transient_failure"
                self._validate_transition(source, target)
                updated = self._transition_locked(
                    row,
                    target=target,
                    stage=row["stage"],
                    event_type=event_type,
                    error_code=code,
                    error_message=message,
                    retry_at=retry_at,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return _job(updated)

    def request_cancel(
        self, *, tenant_id: str, job_id: JobId
    ) -> DocumentJobRecord:
        """Request cooperative cancellation, immediately cancelling pending work."""
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._select_locked(tenant, identifier)
                if row is None:
                    raise JobNotFoundError("job is unavailable in this tenant")
                state = DocumentJobState(row["state"])
                if state in TERMINAL_STATES:
                    raise InvalidJobTransitionError(
                        f"terminal job {state.value} is immutable"
                    )
                if state in _PENDING_STATES:
                    updated = self._transition_locked(
                        row,
                        target=DocumentJobState.CANCELLED,
                        stage=row["stage"],
                        event_type="cancelled",
                        error_code=None,
                        error_message=None,
                        cancel_requested=True,
                    )
                else:
                    next_version = int(row["row_version"]) + 1
                    now = _timestamp(self._clock())
                    self._conn.execute(
                        """
                        UPDATE document_jobs
                        SET cancel_requested = 1, row_version = ?, updated_at = ?
                        WHERE tenant_id = ? AND id = ? AND row_version = ?
                        """,
                        (
                            next_version,
                            now,
                            tenant,
                            str(identifier),
                            int(row["row_version"]),
                        ),
                    )
                    self._insert_event_locked(
                        tenant_id=tenant,
                        job_id=identifier,
                        event_type="cancellation_requested",
                        from_state=state,
                        to_state=state,
                        from_version=int(row["row_version"]),
                        to_version=next_version,
                        stage=row["stage"],
                        lease_owner=row["lease_owner"],
                        error_code=None,
                        error_message=None,
                        created_at=now,
                    )
                    updated = self._select_locked(tenant, identifier)
                    if updated is None:
                        raise RuntimeError("cancel update succeeded but job was not found")
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return _job(updated)

    def cancel_claimed(
        self, *, tenant_id: str, job_id: JobId, owner: str
    ) -> DocumentJobRecord:
        """Cooperatively finish cancellation of an active owned job."""
        tenant = _required(tenant_id, "tenant_id")
        identifier = JobId(job_id)
        claimant = _required(owner, "owner")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._select_locked(tenant, identifier)
                if row is None:
                    raise JobNotFoundError("job is unavailable in this tenant")
                state = DocumentJobState(row["state"])
                if state not in _ACTIVE_STATES:
                    raise InvalidJobTransitionError(
                        "only active work can be cooperatively cancelled"
                    )
                self._require_lease_owner(row, claimant)
                if not bool(row["cancel_requested"]):
                    raise JobConflictError("cancellation has not been requested")
                updated = self._transition_locked(
                    row,
                    target=DocumentJobState.CANCELLED,
                    stage=row["stage"],
                    event_type="cancelled",
                    error_code=None,
                    error_message=None,
                    cancel_requested=True,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback_if_needed()
                raise
        return _job(updated)

    def _recover_expired_locked(
        self, now_dt: datetime, *, tenant_id: str | None
    ) -> int:
        now = _timestamp(now_dt)
        tenant_clause = " AND tenant_id = ?" if tenant_id is not None else ""
        params: list[object] = [now]
        if tenant_id is not None:
            params.append(tenant_id)
        rows = self._conn.execute(
            f"""
            SELECT * FROM document_jobs
            WHERE state IN ('validating', 'parsing', 'ocr_running',
                            'normalizing', 'indexing', 'verifying')
              AND lease_owner IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
              {tenant_clause}
            ORDER BY created_at, id
            """,
            params,
        ).fetchall()
        for row in rows:
            source = DocumentJobState(row["state"])
            if bool(row["cancel_requested"]):
                target = DocumentJobState.CANCELLED
                event_type = "cancelled"
                code = None
                message = None
                retry_at = None
            elif int(row["attempt"]) >= int(row["max_attempts"]):
                target = DocumentJobState.DEAD_LETTER
                event_type = "attempts_exhausted"
                code = "lease_expired"
                message = "Worker lease expired after maximum attempts"
                retry_at = None
            else:
                target = DocumentJobState.RETRY_WAIT
                event_type = "lease_expired"
                code = "lease_expired"
                message = "Worker lease expired before completion"
                retry_at = now
            self._validate_transition(source, target)
            self._transition_locked(
                row,
                target=target,
                stage=row["stage"],
                event_type=event_type,
                error_code=code,
                error_message=message,
                retry_at=retry_at,
                cancel_requested=bool(row["cancel_requested"]),
            )
        return len(rows)

    @staticmethod
    def _claim_target(row: sqlite3.Row) -> DocumentJobState:
        state = DocumentJobState(row["state"])
        if state is DocumentJobState.READY_TO_PARSE:
            return DocumentJobState.PARSING
        if state is DocumentJobState.OCR_REQUIRED:
            return DocumentJobState.OCR_RUNNING
        if state is not DocumentJobState.RETRY_WAIT:
            raise InvalidJobTransitionError(f"{state.value} is not claimable")
        stage = DocumentJobState(row["stage"])
        if stage not in _ACTIVE_STATES:
            raise InvalidJobTransitionError(
                f"retry stage {stage.value} is not processable"
            )
        return stage

    @staticmethod
    def _validate_transition(
        source: DocumentJobState, target: DocumentJobState
    ) -> None:
        if target not in ALLOWED_TRANSITIONS[source]:
            raise InvalidJobTransitionError(
                f"transition {source.value} -> {target.value} is not allowed"
            )

    @staticmethod
    def _require_lease_owner(row: sqlite3.Row, owner: str | None) -> None:
        current_owner = row["lease_owner"]
        if current_owner is not None and current_owner != owner:
            raise JobLeaseConflictError(
                "leased job may only be changed by its current owner"
            )

    def _require_expected_locked(
        self,
        tenant_id: str,
        job_id: JobId,
        expected_state: DocumentJobState,
        expected_version: int,
    ) -> sqlite3.Row:
        row = self._select_locked(tenant_id, job_id)
        if row is None:
            raise JobNotFoundError("job is unavailable in this tenant")
        actual_state = DocumentJobState(row["state"])
        actual_version = int(row["row_version"])
        if actual_state is not expected_state or actual_version != int(expected_version):
            raise StaleJobUpdateError(
                f"expected {expected_state.value}@{expected_version}, "
                f"found {actual_state.value}@{actual_version}"
            )
        if actual_state in TERMINAL_STATES:
            raise InvalidJobTransitionError(
                f"terminal job {actual_state.value} is immutable"
            )
        return row

    def _transition_locked(
        self,
        row: sqlite3.Row,
        *,
        target: DocumentJobState,
        stage: str | None,
        event_type: str,
        error_code: str | None,
        error_message: str | None,
        retry_at: str | None = None,
        cancel_requested: bool | None = None,
    ) -> sqlite3.Row:
        source = DocumentJobState(row["state"])
        self._validate_transition(source, target)
        now = _timestamp(self._clock())
        next_version = int(row["row_version"]) + 1
        next_stage = _required(stage or target.value, "stage")
        clear_lease = target not in _ACTIVE_STATES
        next_cancel = (
            int(bool(cancel_requested))
            if cancel_requested is not None
            else int(row["cancel_requested"])
        )
        cursor = self._conn.execute(
            """
            UPDATE document_jobs
            SET state = ?, stage = ?, retry_at = ?,
                lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?,
                cancel_requested = ?, error_code = ?, error_message = ?,
                row_version = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND state = ? AND row_version = ?
            """,
            (
                target.value,
                next_stage,
                retry_at,
                None if clear_lease else row["lease_owner"],
                None if clear_lease else row["lease_expires_at"],
                None if clear_lease else row["heartbeat_at"],
                next_cancel,
                error_code,
                error_message,
                next_version,
                now,
                row["tenant_id"],
                row["id"],
                source.value,
                int(row["row_version"]),
            ),
        )
        if cursor.rowcount != 1:
            raise StaleJobUpdateError("job changed during transition")
        self._insert_event_locked(
            tenant_id=row["tenant_id"],
            job_id=JobId(row["id"]),
            event_type=event_type,
            from_state=source,
            to_state=target,
            from_version=int(row["row_version"]),
            to_version=next_version,
            stage=next_stage,
            lease_owner=row["lease_owner"],
            error_code=error_code,
            error_message=error_message,
            created_at=now,
        )
        updated = self._select_locked(row["tenant_id"], JobId(row["id"]))
        if updated is None:
            raise RuntimeError("transition succeeded but job was not found")
        return updated

    def _insert_event_locked(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
        event_type: str,
        from_state: DocumentJobState | None,
        to_state: DocumentJobState,
        from_version: int | None,
        to_version: int,
        stage: str,
        lease_owner: str | None,
        error_code: str | None,
        error_message: str | None,
        created_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO document_job_events (
                tenant_id, job_id, event_type, from_state, to_state,
                from_version, to_version, stage, lease_owner,
                error_code, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                str(job_id),
                _required(event_type, "event_type"),
                from_state.value if from_state is not None else None,
                to_state.value,
                from_version,
                to_version,
                stage,
                lease_owner,
                error_code,
                error_message,
                created_at,
            ),
        )

    def _retry_delay(self, attempt: int) -> float:
        base = min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** max(0, attempt - 1)),
        )
        jitter = float(self._jitter(base))
        if not math.isfinite(jitter):
            raise ValueError("retry jitter must be finite")
        return min(self._retry_max_seconds, max(0.0, base + jitter))

    def _select_locked(self, tenant_id: str, job_id: JobId) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM document_jobs WHERE tenant_id = ? AND id = ?",
            (tenant_id, str(job_id)),
        ).fetchone()

    def _rollback_if_needed(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")
