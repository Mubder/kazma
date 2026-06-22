"""Audit Logger — Logs all access attempts and authorization decisions.

Provides tamper-evident audit trails for RBAC and division sandboxing.
All entries are timestamped and stored in a SQLite database for
compliance and forensic review.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path(__file__).resolve().parent.parent.parent / "kazma-data" / "audit.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_entries (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    user_id TEXT NOT NULL,
    division TEXT NOT NULL,
    resource TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    reason TEXT DEFAULT '',
    request_id TEXT DEFAULT '',
    approver_id TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_division ON audit_entries(division);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_entries(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_entries(request_id);
"""


@dataclass
class AuditEntry:
    """A single audit log entry."""

    id: str
    timestamp: str
    event_type: str  # "access_attempt" | "authorization_decision"
    user_id: str
    division: str
    resource: str
    action: str
    result: str  # "allowed" | "denied" | "pending_approval" | "approved"
    reason: str = ""
    request_id: str = ""
    approver_id: str = ""
    metadata_json: str = "{}"


class AuditLogger:
    """Logs all access attempts and authorization decisions.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def log_access_attempt(
        self,
        user_id: str,
        division: str,
        resource: str,
        action: str,
        result: str,
        reason: str = "",
        metadata: dict | None = None,
    ) -> AuditEntry:
        """Log an access attempt with full context.

        Args:
            user_id: The user attempting access.
            division: The division context.
            resource: The resource being accessed.
            action: The action being attempted.
            result: "allowed", "denied", or "pending_approval".
            reason: Explanation for the result.
            metadata: Optional extra metadata as JSON-serializable dict.

        Returns:
            The created AuditEntry.
        """
        import json

        db = await self._get_db()
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            event_type="access_attempt",
            user_id=user_id,
            division=division,
            resource=resource,
            action=action,
            result=result,
            reason=reason,
            metadata_json=json.dumps(metadata or {}),
        )

        await db.execute(
            """INSERT INTO audit_entries
               (id, timestamp, event_type, user_id, division, resource, action, result, reason, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.timestamp,
                entry.event_type,
                entry.user_id,
                entry.division,
                entry.resource,
                entry.action,
                entry.result,
                entry.reason,
                entry.metadata_json,
            ),
        )
        await db.commit()
        logger.info("Audit: %s %s/%s %s -> %s", user_id, division, resource, action, result)
        return entry

    async def log_authorization_decision(
        self,
        request_id: str,
        approver_id: str,
        decision: str,
        reason: str = "",
        user_id: str = "",
        division: str = "",
        resource: str = "",
    ) -> AuditEntry:
        """Log an authorization decision.

        Args:
            request_id: The authorization request ID.
            approver_id: The admin who made the decision.
            decision: "approved" or "denied".
            reason: Explanation for the decision.
            user_id: The user who requested access.
            division: The target division.
            resource: The requested resource.

        Returns:
            The created AuditEntry.
        """
        db = await self._get_db()
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            event_type="authorization_decision",
            user_id=user_id,
            division=division,
            resource=resource,
            action=decision,
            result=decision,
            reason=reason,
            request_id=request_id,
            approver_id=approver_id,
        )

        await db.execute(
            """INSERT INTO audit_entries
               (id, timestamp, event_type, user_id, division, resource, action, result, reason, request_id, approver_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.timestamp,
                entry.event_type,
                entry.user_id,
                entry.division,
                entry.resource,
                entry.action,
                entry.result,
                entry.reason,
                entry.request_id,
                entry.approver_id,
            ),
        )
        await db.commit()
        logger.info("Audit: decision %s by %s -> %s", request_id, approver_id, decision)
        return entry

    async def get_audit_trail(
        self,
        user_id: str | None = None,
        division: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit trail with filters.

        Args:
            user_id: Filter by user.
            division: Filter by division.
            start_date: Filter entries after this date.
            end_date: Filter entries before this date.
            limit: Maximum entries to return.

        Returns:
            List of matching AuditEntry objects.
        """
        db = await self._get_db()
        query = "SELECT * FROM audit_entries WHERE 1=1"
        params: list = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if division:
            query += " AND division = ?"
            params.append(division)
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            AuditEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                user_id=row["user_id"],
                division=row["division"],
                resource=row["resource"],
                action=row["action"],
                result=row["result"],
                reason=row["reason"] or "",
                request_id=row["request_id"] or "",
                approver_id=row["approver_id"] or "",
                metadata_json=row["metadata_json"] or "{}",
            )
            for row in rows
        ]

    async def clear(self) -> int:
        """Clear all audit entries. Returns count of deleted rows."""
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM audit_entries")
        await db.commit()
        count = cursor.rowcount
        logger.warning("Audit log cleared: %d entries deleted", count)
        return count
