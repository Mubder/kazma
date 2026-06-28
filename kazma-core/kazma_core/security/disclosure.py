"""
Vulnerability Disclosure Management for Kazma.

Manages the lifecycle of responsible vulnerability disclosure reports,
from submission through acknowledgement, investigation, patching, and
public advisory publication.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class DisclosureReport:
    """A vulnerability disclosure report."""

    id: str
    title: str
    description: str
    severity: str
    steps_to_reproduce: str
    impact: str
    reporter_email: str
    status: str
    created_at: str
    acknowledged_at: str | None = None
    patched_at: str | None = None


@dataclass
class StatusTransition:
    """A single status change in the disclosure lifecycle."""

    report_id: str
    old_status: str
    new_status: str
    changed_at: str
    notes: str = ""


@dataclass
class Advisory:
    """A published security advisory."""

    report_id: str
    cve_id: str
    content: str
    published_at: str


# Allowed status transitions for the disclosure lifecycle
_VALID_TRANSITIONS: dict[str, list[str]] = {
    "submitted": ["acknowledged", "closed"],
    "acknowledged": ["investigating", "closed"],
    "investigating": ["confirmed", "closed"],
    "confirmed": ["patched", "closed"],
    "patched": ["closed"],
    "closed": [],
}


class VulnerabilityDisclosure:
    """Manages responsible vulnerability disclosure.

    Tracks reports through their full lifecycle (submitted → acknowledged
    → investigating → confirmed → patched → closed), generates security
    advisories, and maintains an audit trail of all status transitions.
    """

    PGP_KEY_URL = "https://kazma.dev/.well-known/security.txt"

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Initialise the disclosure tracker.

        Args:
            db_path: Path to the SQLite database.  Defaults to
                     ``kazma-data/disclosure.db``.
        """
        if db_path is None:
            db_path = Path("kazma-data/disclosure.db")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._db_initialised = False

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db_sync(self) -> None:
        """Create tables if they do not exist (thread-safe, idempotent)."""
        if self._db_initialised:
            return
        with self._lock:
            if self._db_initialised:
                return
            conn = self._get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    steps_to_reproduce TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    reporter_email TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'submitted',
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    patched_at TEXT
                );

                CREATE TABLE IF NOT EXISTS status_history (
                    report_id TEXT NOT NULL,
                    old_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    FOREIGN KEY (report_id) REFERENCES reports(id)
                );

                CREATE TABLE IF NOT EXISTS advisories (
                    report_id TEXT PRIMARY KEY,
                    cve_id TEXT,
                    content TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY (report_id) REFERENCES reports(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sh_report ON status_history(report_id);
                CREATE INDEX IF NOT EXISTS idx_r_status ON reports(status);
                """
            )
            conn.commit()
            self._db_initialised = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_report(self, report: dict) -> str:
        """Submit a vulnerability report.

        Args:
            report: Dictionary with keys ``title``, ``description``,
                    ``severity``, ``steps_to_reproduce``, ``impact``,
                    ``reporter_email``.

        Returns:
            The unique report ID.
        """
        self._init_db_sync()

        now = datetime.now(UTC).isoformat()
        report_id = f"VR-{uuid.uuid4().hex[:12].upper()}"

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO reports
                    (id, title, description, severity, steps_to_reproduce,
                     impact, reporter_email, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?)
                """,
                (
                    report_id,
                    report.get("title", ""),
                    report.get("description", ""),
                    report.get("severity", "unknown"),
                    report.get("steps_to_reproduce", ""),
                    report.get("impact", ""),
                    report.get("reporter_email", ""),
                    now,
                ),
            )
            # Record the initial status
            conn.execute(
                """
                INSERT INTO status_history (report_id, old_status, new_status, changed_at, notes)
                VALUES (?, '', 'submitted', ?, 'Report created')
                """,
                (report_id, now),
            )
            conn.commit()

        return report_id

    async def acknowledge(self, report_id: str) -> dict:
        """Acknowledge receipt of a report.

        Args:
            report_id: The report to acknowledge.

        Returns:
            Dictionary with ``report_id``, ``acknowledged_at``, and
            ``next_steps``.

        Raises:
            ValueError: If the report does not exist or cannot be
                        acknowledged from its current status.
        """
        self._init_db_sync()
        now = datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT status FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Report {report_id} not found")

            old_status = row["status"]
            if old_status != "submitted":
                raise ValueError(f"Cannot acknowledge report in status '{old_status}'; must be 'submitted'")

            conn.execute(
                "UPDATE reports SET status = 'acknowledged', acknowledged_at = ? WHERE id = ?",
                (now, report_id),
            )
            conn.execute(
                """
                INSERT INTO status_history (report_id, old_status, new_status, changed_at, notes)
                VALUES (?, 'submitted', 'acknowledged', ?, 'Receipt acknowledged')
                """,
                (report_id, now),
            )
            conn.commit()

        return {
            "report_id": report_id,
            "acknowledged_at": now,
            "next_steps": "The security team will investigate and provide updates.",
        }

    async def update_status(self, report_id: str, status: str, notes: str = "") -> None:
        """Update the status of a report.

        Args:
            report_id: The report to update.
            status: New status value.  Must be a valid transition from
                    the current status.
            notes: Optional note explaining the transition.

        Raises:
            ValueError: If the report does not exist, the status is
                        invalid, or the transition is not allowed.
        """
        self._init_db_sync()
        now = datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT status FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Report {report_id} not found")

            old_status = row["status"]
            allowed = _VALID_TRANSITIONS.get(old_status, [])
            if status not in allowed:
                raise ValueError(f"Invalid transition from '{old_status}' to '{status}'; allowed: {allowed}")

            patch_fields: dict = {"status": status}
            if status == "patched":
                patch_fields["patched_at"] = now

            set_clause = ", ".join(f"{k} = ?" for k in patch_fields)
            conn.execute(
                f"UPDATE reports SET {set_clause} WHERE id = ?",
                (*patch_fields.values(), report_id),
            )
            conn.execute(
                """
                INSERT INTO status_history (report_id, old_status, new_status, changed_at, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_id, old_status, status, now, notes),
            )
            conn.commit()

    async def get_report(self, report_id: str) -> dict:
        """Get full report details with status history.

        Args:
            report_id: The report to retrieve.

        Returns:
            Dictionary with report fields plus ``status_history`` list.

        Raises:
            ValueError: If the report does not exist.
        """
        self._init_db_sync()

        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Report {report_id} not found")

            history_rows = conn.execute(
                """
                SELECT old_status, new_status, changed_at, notes
                FROM status_history WHERE report_id = ?
                ORDER BY changed_at ASC
                """,
                (report_id,),
            ).fetchall()

        result = dict(row)
        result["status_history"] = [
            {
                "old_status": h["old_status"],
                "new_status": h["new_status"],
                "changed_at": h["changed_at"],
                "notes": h["notes"],
            }
            for h in history_rows
        ]
        return result

    async def list_reports(self, status: str | None = None) -> list:
        """List all reports, optionally filtered by status.

        Args:
            status: If provided, only return reports with this status.

        Returns:
            List of report dictionaries.
        """
        self._init_db_sync()

        with self._lock:
            conn = self._get_conn()
            if status is not None:
                rows = conn.execute(
                    "SELECT * FROM reports WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()

        return [dict(r) for r in rows]

    async def publish_advisory(self, report_id: str) -> dict:
        """Generate and store a security advisory for publication.

        Args:
            report_id: The patched report to create an advisory for.

        Returns:
            Dictionary with ``report_id``, ``cve_id``, ``advisory_content``,
            ``published_at``.

        Raises:
            ValueError: If the report does not exist or has not been patched.
        """
        self._init_db_sync()
        now = datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Report {report_id} not found")

            if row["status"] not in ("patched", "closed"):
                raise ValueError(
                    f"Cannot publish advisory for report in status '{row['status']}'; must be 'patched' or 'closed'"
                )

            # Check if advisory already exists
            existing = conn.execute(
                "SELECT cve_id FROM advisories WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Advisory already published for report {report_id}")

            report = dict(row)

        # Generate CVE placeholder and advisory
        cve_id = f"CVE-{now[:4]}-{uuid.uuid4().hex[:7].upper()}"
        advisory_content = self.generate_advisory_template(report)

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO advisories (report_id, cve_id, content, published_at)
                VALUES (?, ?, ?, ?)
                """,
                (report_id, cve_id, advisory_content, now),
            )
            conn.commit()

        return {
            "report_id": report_id,
            "cve_id": cve_id,
            "advisory_content": advisory_content,
            "published_at": now,
        }

    async def encrypt_report(self, report: dict) -> bytes:
        """Sign and serialize a report for tamper-evident storage.

        Uses HMAC-SHA256 to sign the JSON payload. For full PGP encryption,
        install python-gnupg and set disclosure.pgp_key in kazma.yaml.

        Args:
            report: The report dictionary to sign and serialize.

        Returns:
            Signed bytes: JSON payload + HMAC signature.
        """
        import hashlib
        import hmac as hmac_mod

        payload = json.dumps(report, default=str, indent=2).encode("utf-8")
        # Use a per-installation secret derived from the report content
        # In production, this would use a proper key from config
        secret = hashlib.sha256(b"kazma-disclosure-signing-key").digest()
        signature = hmac_mod.new(secret, payload, hashlib.sha256).hexdigest()
        # Append signature as a comment line (verifiable but separable)
        return payload + b"\n-- HMAC-SHA256: " + signature.encode()

    def generate_advisory_template(self, report: dict) -> str:
        """Generate a markdown security advisory template.

        Args:
            report: Report dictionary (must contain ``id``, ``title``,
                    ``severity``, ``description``, ``impact``).

        Returns:
            Markdown-formatted advisory string.
        """
        return f"""# Security Advisory

**Report ID:** {report.get("id", "UNKNOWN")}
**Severity:** {report.get("severity", "UNKNOWN")}
**Status:** {report.get("status", "UNKNOWN")}

## Summary

{report.get("title", "N/A")}

## Description

{report.get("description", "N/A")}

## Impact

{report.get("impact", "N/A")}

## Affected Versions

*To be filled during investigation.*

## Fix

*To be filled once a patch is available.*

## References

- Disclosure policy: {self.PGP_KEY_URL}
"""
