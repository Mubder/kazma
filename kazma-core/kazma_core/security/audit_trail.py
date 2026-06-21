"""
Security Audit Trail for Kazma.

Logs security-relevant events (certifications, vulnerability discoveries,
permission changes, etc.) to a SQLite store and generates aggregate reports.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
import threading


@dataclass
class SecurityEvent:
    """A single security-relevant event."""

    id: str
    event_type: str
    skill_id: str
    details: str
    severity: str
    timestamp: str


@dataclass
class SecurityReport:
    """Aggregate security report for a time period."""

    period_days: int
    total_events: int
    by_severity: Dict[str, int] = field(default_factory=dict)
    by_type: Dict[str, int] = field(default_factory=dict)
    events: List[SecurityEvent] = field(default_factory=list)


class SecurityAuditTrail:
    """SQLite-backed security event logger and reporter."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Initialise the audit trail.

        Args:
            db_path: Path to the SQLite database.  Defaults to
                     ``kazma-data/security_audit.db``.
        """
        if db_path is None:
            db_path = Path("kazma-data/security_audit.db")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __del__(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _init_db(self) -> None:
        """Create the security_events table if it does not exist."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    details TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_skill ON security_events(skill_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_type ON security_events(event_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_severity ON security_events(severity)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_se_timestamp ON security_events(timestamp)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def log_event(
        self,
        event_type: str,
        skill_id: str,
        details: str,
        severity: str = "info",
    ) -> SecurityEvent:
        """Log a security event.

        Args:
            event_type: Category of event (e.g. ``certification_issued``,
                        ``vulnerability_found``).
            skill_id: Identifier of the affected skill.
            details: Human-readable event description.
            severity: One of ``info``, ``warning``, ``critical``.

        Returns:
            The recorded :class:`SecurityEvent`.
        """
        await self._init_db()

        event = SecurityEvent(
            id=uuid.uuid4().hex,
            event_type=event_type,
            skill_id=skill_id,
            details=details,
            severity=severity,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO security_events (id, event_type, skill_id, details, severity, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event.id, event.event_type, event.skill_id, event.details, event.severity, event.timestamp),
            )
            conn.commit()

        return event

    async def get_events(
        self,
        skill_id: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[SecurityEvent]:
        """Query events with optional filters.

        Args:
            skill_id: Filter by skill identifier.
            event_type: Filter by event type.
            severity: Filter by severity level.
            since: ISO timestamp — only return events at or after this time.

        Returns:
            Matching :class:`SecurityEvent` items, newest first.
        """
        await self._init_db()

        clauses: List[str] = []
        params: List[str] = []

        if skill_id is not None:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM security_events WHERE {where} ORDER BY timestamp DESC"

        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(sql, params).fetchall()

        return [
            SecurityEvent(
                id=r["id"],
                event_type=r["event_type"],
                skill_id=r["skill_id"],
                details=r["details"],
                severity=r["severity"],
                timestamp=r["timestamp"],
            )
            for r in rows
        ]

    async def generate_report(self, period_days: int = 30) -> SecurityReport:
        """Generate a summary report for the last *period_days* days.

        Args:
            period_days: Number of days to include in the report.

        Returns:
            :class:`SecurityReport` with aggregated statistics.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat()
        events = await self.get_events(since=since)

        by_severity: Dict[str, int] = defaultdict(int)
        by_type: Dict[str, int] = defaultdict(int)

        for ev in events:
            by_severity[ev.severity] += 1
            by_type[ev.event_type] += 1

        return SecurityReport(
            period_days=period_days,
            total_events=len(events),
            by_severity=dict(by_severity),
            by_type=dict(by_type),
            events=events,
        )
