"""Append-only operational audit trail for the document platform.

Phase 9 audit. Complements the per-job event history in :mod:`.jobs`
(``document_job_events`` already records stage transitions): this store
captures **operator- and tenant-facing operational events** that are NOT
job-stage transitions — intake, access reads, indexing, generation,
conversion, mutation/redaction, downloads, cancellation/retry requests,
deletion, garbage collection, and operator actions.

Invariants (mirrors ``document_job_events`` immutability):

* **Immutable content.** A ``BEFORE UPDATE`` trigger aborts every update, so
  a recorded event can never be altered in place.
* **Controlled pruning only.** A ``BEFORE DELETE`` trigger aborts casual
  deletes; the retention sweep flips a one-row control flag inside its own
  transaction to prune whole aged-out rows (log-rotation semantics). No
  other path can delete audit rows.
* **Sanitized detail.** Only an allowlist of safe scalar keys is persisted
  in ``detail_json``; content, filenames, redaction terms, and secrets are
  never stored. Every value is coerced to a short scalar.
* **Tenant-scoped.** Every read/write is bound to a ``tenant_id``; the paged
  query API can never cross tenants.

The store shares the :class:`~kazma_core.documents.repository.DocumentRepository`
connection + lock (like :class:`~kazma_core.documents.jobs.DocumentJobRepository`),
so audit rows live in ``documents.db`` and travel with backup/migration for free.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .repository import DocumentRepository

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_EVENT_TYPES",
    "DocumentAuditEvent",
    "DocumentAuditStore",
]

# Canonical operational event types. Kept a closed set so metrics/label
# cardinality and UI filters stay bounded.
AUDIT_EVENT_TYPES = frozenset(
    {
        "intake",
        "access",
        "index",
        "unindex",
        "generate",
        "convert",
        "mutate",
        "redact",
        "download",
        "cancel",
        "retry",
        "delete",
        "gc",
        "operator",
    }
)

# Outcomes are a closed set for consistent filtering.
_OUTCOMES = frozenset({"success", "failure", "denied"})

# Allowlisted detail keys — safe scalars only. Anything not on this list is
# dropped, so content / filenames / redaction terms can never leak here.
_DETAIL_ALLOWLIST = frozenset(
    {
        "reason",
        "code",
        "library_id",
        "target_format",
        "source_format",
        "page",
        "page_start",
        "page_end",
        "byte_size",
        "chunk_count",
        "field_count",
        "term_count",
        "artifact_type",
        "blob_kind",
        "deleted_blobs",
        "deleted_manifests",
        "deleted_rows",
        "reclaimed_bytes",
        "state",
        "attempt",
        "dry_run",
        "batch",
        "http_status",
        "engine",
        "count",
        "actor_role",
    }
)

_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9_.-]+")
_MAX_STR = 128
_MAX_DETAIL_KEYS = 24

_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
    actor_id TEXT,
    workspace_id TEXT,
    document_id TEXT,
    version_id TEXT,
    job_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_audit_gc_control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    allow INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO document_audit_gc_control (id, allow) VALUES (1, 0);

CREATE INDEX IF NOT EXISTS idx_document_audit_tenant
    ON document_audit_events(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_document_audit_tenant_doc
    ON document_audit_events(tenant_id, document_id, id);
CREATE INDEX IF NOT EXISTS idx_document_audit_created
    ON document_audit_events(created_at);

CREATE TRIGGER IF NOT EXISTS document_audit_no_update
BEFORE UPDATE ON document_audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS document_audit_guard_delete
BEFORE DELETE ON document_audit_events
WHEN (SELECT allow FROM document_audit_gc_control WHERE id = 1) = 0
BEGIN
    SELECT RAISE(ABORT, 'audit events may only be pruned by retention');
END;
"""


@dataclass(frozen=True, slots=True)
class DocumentAuditEvent:
    id: int
    tenant_id: str
    event_type: str
    action: str
    outcome: str
    actor_id: str | None
    workspace_id: str | None
    document_id: str | None
    version_id: str | None
    job_id: str | None
    detail: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "action": self.action,
            "outcome": self.outcome,
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "job_id": self.job_id,
            "detail": self.detail,
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value.strip()


def _opt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_MAX_STR] if text else None


def _sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only allowlisted keys and coerce to safe scalars."""
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in _DETAIL_ALLOWLIST:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            out[key] = int(value)
        elif isinstance(value, float):
            out[key] = float(value)
        else:
            text = "".join(
                ch if ch.isprintable() and ch not in "\r\n" else " "
                for ch in str(value)
            )
            out[key] = " ".join(text.split())[:_MAX_STR]
        if len(out) >= _MAX_DETAIL_KEYS:
            break
    return out


def _event(row: Any) -> DocumentAuditEvent:
    try:
        detail = json.loads(row["detail_json"])
        if not isinstance(detail, dict):
            detail = {}
    except (ValueError, TypeError):
        detail = {}
    return DocumentAuditEvent(
        id=int(row["id"]),
        tenant_id=row["tenant_id"],
        event_type=row["event_type"],
        action=row["action"],
        outcome=row["outcome"],
        actor_id=row["actor_id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        version_id=row["version_id"],
        job_id=row["job_id"],
        detail=detail,
        created_at=row["created_at"],
    )


class DocumentAuditStore:
    """Append-only operational audit sharing the repository connection/lock."""

    def __init__(self, repository: DocumentRepository) -> None:
        if not isinstance(repository, DocumentRepository):
            raise TypeError("repository must be a DocumentRepository")
        self._conn = repository._conn  # noqa: SLF001 - shared connection by design
        self._lock = repository._lock  # noqa: SLF001
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def record(
        self,
        *,
        tenant_id: str,
        event_type: str,
        action: str,
        outcome: str = "success",
        actor_id: str | None = None,
        workspace_id: str | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one immutable operational audit event.

        Best-effort: never raises into the caller's critical path. An audit
        write failure is logged but must not break a document operation.
        """
        try:
            tenant = _clean(tenant_id)
            etype = _clean(event_type).lower()
            if etype not in AUDIT_EVENT_TYPES:
                etype = "operator"
            act = _SAFE_TOKEN_RE.sub("_", _clean(action).lower())[:64].strip("_") or "action"
            result = outcome if outcome in _OUTCOMES else "success"
            payload = json.dumps(
                _sanitize_detail(detail),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO document_audit_events (
                        tenant_id, event_type, action, outcome, actor_id,
                        workspace_id, document_id, version_id, job_id,
                        detail_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant,
                        etype,
                        act,
                        result,
                        _opt(actor_id),
                        _opt(workspace_id),
                        _opt(document_id),
                        _opt(version_id),
                        _opt(job_id),
                        payload,
                        _now(),
                    ),
                )
        except Exception:  # noqa: BLE001 - audit must never break operations
            logger.debug("[documents.audit] failed to record %s/%s", event_type, action, exc_info=True)

    def list_events(
        self,
        *,
        tenant_id: str,
        document_id: Any = None,
        event_type: str | None = None,
        limit: int = 50,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """Return a tenant-scoped page of audit events (newest first).

        Keyset pagination on the autoincrement id: pass the returned
        ``next_before_id`` to fetch the next older page.
        """
        tenant = _clean(tenant_id)
        page_size = max(1, min(int(limit), 200))
        clauses = ["tenant_id = ?"]
        params: list[Any] = [tenant]
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(str(document_id))
        if event_type is not None:
            etype = str(event_type).strip().lower()
            if etype in AUDIT_EVENT_TYPES:
                clauses.append("event_type = ?")
                params.append(etype)
        if before_id is not None:
            clauses.append("id < ?")
            params.append(int(before_id))
        where = " AND ".join(clauses)
        # Fetch one extra row to know whether another page exists.
        params.append(page_size + 1)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM document_audit_events
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        events = [_event(row) for row in rows[:page_size]]
        has_more = len(rows) > page_size
        return {
            "events": [e.to_dict() for e in events],
            "has_more": has_more,
            "next_before_id": events[-1].id if (has_more and events) else None,
        }

    def count(self, *, tenant_id: str) -> int:
        tenant = _clean(tenant_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM document_audit_events WHERE tenant_id = ?",
                (tenant,),
            ).fetchone()
        return int(row["c"])

    def prune_older_than(self, *, cutoff_iso: str, max_rows: int) -> int:
        """Delete whole aged-out audit rows in one bounded batch.

        This is the ONLY path allowed to delete audit rows: it flips the
        ``document_audit_gc_control`` flag inside the transaction so the
        delete-guard trigger permits the prune, then resets it. Returns the
        number of rows deleted.
        """
        limit = max(1, int(max_rows))
        deleted = 0
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "UPDATE document_audit_gc_control SET allow = 1 WHERE id = 1"
                )
                cursor = self._conn.execute(
                    """
                    DELETE FROM document_audit_events
                    WHERE id IN (
                        SELECT id FROM document_audit_events
                        WHERE created_at < ?
                        ORDER BY id ASC
                        LIMIT ?
                    )
                    """,
                    (str(cutoff_iso), limit),
                )
                deleted = int(cursor.rowcount)
                self._conn.execute(
                    "UPDATE document_audit_gc_control SET allow = 0 WHERE id = 1"
                )
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
        return deleted
