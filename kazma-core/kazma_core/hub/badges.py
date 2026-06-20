"""Kazma Hub — Certification Badge System.

Manages badge issuance, verification, revocation, and SVG generation
for the Kazma-Certified badge program.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BADGE_LEVELS: dict[str, dict[str, Any]] = {
    "basic": {
        "label": "Kazma-Certified Basic",
        "description": "Passed automated security scan",
        "requirements": [
            "manifest_valid",
            "security_lint_pass",
            "no_critical_vulnerabilities",
        ],
    },
    "standard": {
        "label": "Kazma-Certified Standard",
        "description": "Passed automated + manual review",
        "requirements": [
            "basic_requirements",
            "code_review_approved",
            "test_coverage_80",
            "documentation_complete",
        ],
    },
    "premium": {
        "label": "Kazma-Certified Premium",
        "description": "Full audit + penetration test",
        "requirements": [
            "standard_requirements",
            "penetration_test_passed",
            "dependency_audit_clean",
            "performance_benchmark_met",
        ],
    },
}

_CREATE_BADGES = """\
CREATE TABLE IF NOT EXISTS badges (
    skill_id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT,
    revoked INTEGER DEFAULT 0,
    revoke_reason TEXT,
    FOREIGN KEY (skill_id) REFERENCES skills(id)
);
"""


@dataclass
class Badge:
    """Represents an issued certification badge."""

    skill_id: str
    level: str  # basic | standard | premium
    issued_at: datetime
    expires_at: datetime | None
    revoked: bool = False
    revoke_reason: str | None = None


@dataclass
class BadgeVerification:
    """Result of verifying a badge."""

    valid: bool
    level: str | None = None
    reason: str = ""


@dataclass
class BadgeStats:
    """Aggregate badge statistics."""

    total: int = 0
    by_level: dict[str, int] = field(default_factory=dict)
    recent_issuances: int = 0


class CertificationBadgeSystem:
    """Manages Kazma-Certified badges in the hub SQLite database."""

    def __init__(self, db_path: str = "~/.kazma/hub/registry.db"):
        self.db_path = str(Path(db_path).expanduser())
        # Ensure the badges table exists
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_CREATE_BADGES)
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a synchronous SQLite connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def issue_badge(self, skill_id: str, level: str) -> Badge:
        """Issue a badge for a skill at the given level.

        Validates the skill exists and the level is valid, then records
        the badge in the database.

        Args:
            skill_id: The skill identifier.
            level: Badge level (basic, standard, premium).

        Returns:
            Badge dataclass with issuance details.

        Raises:
            ValueError: If the skill doesn't exist or level is invalid.
        """
        if level not in BADGE_LEVELS:
            raise ValueError(
                f"Invalid badge level: {level!r}. "
                f"Must be one of: {', '.join(sorted(BADGE_LEVELS))}"
            )

        conn = self._get_conn()
        try:
            # Check skill exists
            cursor = conn.execute("SELECT 1 FROM skills WHERE name = ?", (skill_id,))
            if cursor.fetchone() is None:
                raise ValueError(f"Skill '{skill_id}' not found in registry")

            now = datetime.now(timezone.utc)
            expires = now + timedelta(days=365)

            conn.execute(
                """INSERT OR REPLACE INTO badges
                   (skill_id, level, issued_at, expires_at, revoked, revoke_reason)
                   VALUES (?, ?, ?, ?, 0, NULL)""",
                (skill_id, level, now.isoformat(), expires.isoformat()),
            )
            conn.commit()

            return Badge(
                skill_id=skill_id,
                level=level,
                issued_at=now,
                expires_at=expires,
            )
        finally:
            conn.close()

    def verify_badge(self, skill_id: str) -> BadgeVerification:
        """Verify a badge is valid (exists, not revoked, not expired).

        Args:
            skill_id: The skill identifier to check.

        Returns:
            BadgeVerification with validity status.
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT level, expires_at, revoked, revoke_reason FROM badges WHERE skill_id = ?",
                (skill_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return BadgeVerification(valid=False, reason="No badge found")

            if row["revoked"]:
                return BadgeVerification(
                    valid=False,
                    level=row["level"],
                    reason=f"Badge revoked: {row['revoke_reason'] or 'unknown reason'}",
                )

            if row["expires_at"]:
                try:
                    expires = datetime.fromisoformat(row["expires_at"])
                except (ValueError, TypeError):
                    return BadgeVerification(valid=False, level=row["level"], reason="Invalid expiry date")
                if datetime.now(timezone.utc) > expires:
                    return BadgeVerification(
                        valid=False,
                        level=row["level"],
                        reason="Badge expired",
                    )

            return BadgeVerification(valid=True, level=row["level"], reason="Badge is valid")
        finally:
            conn.close()

    def revoke_badge(self, skill_id: str, reason: str) -> None:
        """Revoke a badge with a reason.

        Args:
            skill_id: The skill identifier.
            reason: Why the badge is being revoked.

        Raises:
            ValueError: If no badge exists for the skill.
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE badges SET revoked = 1, revoke_reason = ? WHERE skill_id = ?",
                (reason, skill_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"No badge found for skill '{skill_id}'")
        finally:
            conn.close()

    def generate_badge_svg(self, level: str, skill_name: str) -> str:
        """Generate an SVG badge string for embedding.

        Args:
            level: Badge level (basic, standard, premium).
            skill_name: Name of the skill for display.

        Returns:
            SVG string.
        """
        colors = {
            "basic": "#22c55e",
            "standard": "#3b82f6",
            "premium": "#eab308",
        }
        color = colors.get(level, "#6b7280")
        label = BADGE_LEVELS.get(level, {}).get("label", f"Kazma-Certified {level.title()}")

        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60" width="200" height="60">
  <rect x="0" y="0" width="200" height="60" rx="8" fill="{color}" />
  <text x="100" y="25" text-anchor="middle" fill="white" font-family="Arial, sans-serif" font-size="12" font-weight="bold">Kazma-Certified</text>
  <text x="100" y="45" text-anchor="middle" fill="white" font-family="Arial, sans-serif" font-size="14" font-weight="bold">{level.title()}</text>
</svg>"""

    async def get_badge_stats(self) -> BadgeStats:
        """Get aggregate badge statistics.

        Returns:
            BadgeStats with totals and breakdowns.
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT level, COUNT(*) as cnt FROM badges WHERE revoked = 0 GROUP BY level")
            rows = cursor.fetchall()
            by_level = {row["level"]: row["cnt"] for row in rows}
            total = sum(by_level.values())

            # Recent issuances (last 30 days)
            thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM badges WHERE issued_at >= ? AND revoked = 0",
                (thirty_days_ago,),
            )
            recent = cursor.fetchone()["cnt"]

            return BadgeStats(total=total, by_level=by_level, recent_issuances=recent)
        finally:
            conn.close()
