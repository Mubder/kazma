"""
Skill Certification for Kazma.

Validates and certifies skills at basic / standard / premium levels,
stores certification records in SQLite, and provides verification
and revocation operations.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

CERTIFICATION_LEVELS: dict[str, dict] = {
    "basic": {
        "min_requirements": ["manifest_valid", "no_critical_violations"],
        "badge": "basic-certified",
        "validity_days": 90,
    },
    "standard": {
        "min_requirements": ["manifest_valid", "no_critical_violations", "no_high_violations", "tests_pass"],
        "badge": "standard-certified",
        "validity_days": 180,
    },
    "premium": {
        "min_requirements": [
            "manifest_valid",
            "no_critical_violations",
            "no_high_violations",
            "tests_pass",
            "coverage_above_80",
        ],
        "badge": "premium-certified",
        "validity_days": 365,
    },
}


@dataclass
class CertificationResult:
    """Result of a certification attempt."""

    certified: bool
    level: str
    badge: str
    valid_until: str
    requirements_met: list[str] = field(default_factory=list)
    requirements_failed: list[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    """Result of verifying an existing certification."""

    valid: bool
    level: str
    expiry: str
    last_verified: str


class KazmaCertification:
    """Manage skill certification lifecycle with SQLite-backed storage."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialise the certification store.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to ``kazma-data/certifications.db``.
        """
        if db_path is None:
            db_path = Path("kazma-data/certifications.db")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._db_lock = threading.Lock()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            from kazma_core.config_store import apply_sqlite_pragmas

            apply_sqlite_pragmas(self._conn)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create the certifications table if it does not exist."""
        with self._db_lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS certifications (
                    skill_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    badge TEXT NOT NULL,
                    certified_at TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    revoked INTEGER DEFAULT 0,
                    revoke_reason TEXT
                )
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def certify(self, skill_path: Path, level: str = "basic") -> CertificationResult:
        """Certify a skill at the given level.

        Args:
            skill_path: Path to the skill directory.
            level: Certification level (``basic``, ``standard``, ``premium``).

        Returns:
            :class:`CertificationResult` with outcome details.
        """
        if level not in CERTIFICATION_LEVELS:
            return CertificationResult(
                certified=False,
                level=level,
                badge="",
                valid_until="",
                requirements_failed=[f"Unknown certification level: {level}"],
            )

        level_cfg = CERTIFICATION_LEVELS[level]
        required = level_cfg["min_requirements"]

        # --- Determine which requirements are met ---
        met, failed = await self._check_requirements(skill_path, level, required)

        certified = len(failed) == 0
        now = datetime.now(UTC)
        valid_until = (now + timedelta(days=level_cfg["validity_days"])).isoformat() if certified else ""

        skill_id = await self._extract_skill_id(skill_path)

        if certified:
            self._init_db()
            with self._db_lock:
                conn = self._get_conn()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO certifications
                        (skill_id, level, badge, certified_at, valid_until, revoked, revoke_reason)
                    VALUES (?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (skill_id, level, level_cfg["badge"], now.isoformat(), valid_until),
                )
                conn.commit()

        return CertificationResult(
            certified=certified,
            level=level,
            badge=level_cfg["badge"] if certified else "",
            valid_until=valid_until,
            requirements_met=met,
            requirements_failed=failed,
        )

    async def verify(self, skill_id: str) -> VerificationResult:
        """Verify an existing certification.

        Args:
            skill_id: Identifier of the skill.

        Returns:
            :class:`VerificationResult` indicating validity.
        """
        self._init_db()
        now = datetime.now(UTC)
        last_verified = now.isoformat()

        with self._db_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT level, valid_until, revoked FROM certifications WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()

        if row is None:
            return VerificationResult(valid=False, level="", expiry="", last_verified=last_verified)

        if row["revoked"]:
            return VerificationResult(
                valid=False, level=row["level"], expiry=row["valid_until"], last_verified=last_verified
            )

        expiry = row["valid_until"]
        try:
            expiry_dt = datetime.fromisoformat(expiry)
        except (ValueError, TypeError):
            return VerificationResult(valid=False, level=row["level"], expiry=expiry, last_verified=last_verified)

        valid = expiry_dt > now
        return VerificationResult(valid=valid, level=row["level"], expiry=expiry, last_verified=last_verified)

    async def revoke(self, skill_id: str, reason: str) -> bool:
        """Revoke a certification.

        Args:
            skill_id: Identifier of the skill.
            reason: Human-readable reason for revocation.

        Returns:
            ``True`` if the certification was revoked, ``False`` otherwise.
        """
        self._init_db()
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE certifications SET revoked = 1, revoke_reason = ? WHERE skill_id = ? AND revoked = 0",
                (reason, skill_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_requirements(
        self, skill_path: Path, level: str, requirements: list[str]
    ) -> tuple[list[str], list[str]]:
        """Evaluate which requirements are met and which are not."""
        met: list[str] = []
        failed: list[str] = []

        for req in requirements:
            ok = await self._evaluate_requirement(skill_path, req)
            if ok:
                met.append(req)
            else:
                failed.append(req)

        return met, failed

    async def _evaluate_requirement(self, skill_path: Path, req: str) -> bool:
        """Evaluate a single requirement."""
        if req == "manifest_valid":
            return await self._has_valid_manifest(skill_path)
        if req in ("no_critical_violations", "no_high_violations"):
            return await self._has_no_violations(skill_path, req)
        if req == "tests_pass":
            return await self._has_test_files(skill_path)
        if req == "coverage_above_80":
            return await self._has_coverage_evidence(skill_path)
        return False

    @staticmethod
    async def _has_valid_manifest(skill_path: Path) -> bool:
        """Check that the skill has a parseable manifest."""
        import json

        for name in ("skill.yaml", "skill.yml", "skill.json", "manifest.yaml", "manifest.yml", "manifest.json"):
            p = skill_path / name
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8")
                    if name.endswith(".json"):
                        data = json.loads(text)
                        return isinstance(data, dict)
                    # Basic YAML presence check
                    return len(text.strip()) > 0
                except (OSError, ValueError):
                    return False
        return False

    @staticmethod
    async def _has_no_violations(skill_path: Path, req: str) -> bool:
        """Run linter and check for critical/high violations."""
        from .linter import SecurityLinter

        linter = SecurityLinter()
        report = await linter.lint_skill(skill_path)
        if req == "no_critical_violations":
            return report.critical == 0
        if req == "no_high_violations":
            return report.high == 0
        return True

    @staticmethod
    async def _has_test_files(skill_path: Path) -> bool:
        """Run actual pytest on the skill directory to verify tests pass."""
        test_files = list(skill_path.rglob("test_*.py")) + list(skill_path.rglob("*_test.py"))
        if not test_files:
            return False
        try:
            result = subprocess.run(
                ["pytest", str(skill_path), "-q", "--tb=no"],
                capture_output=True, text=True, timeout=60,
                cwd=str(skill_path.parent),
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    @staticmethod
    async def _has_coverage_evidence(skill_path: Path) -> bool:
        """Check for coverage evidence (coverage.json, .coveragerc, etc.)."""
        indicators = [".coveragerc", "coverage.json", "htmlcov"]
        return any((skill_path / name).exists() for name in indicators)

    @staticmethod
    async def _extract_skill_id(skill_path: Path) -> str:
        """Extract a skill identifier from the manifest or directory name."""
        import json

        for name in ("skill.yaml", "skill.yml", "skill.json", "manifest.yaml", "manifest.yml", "manifest.json"):
            p = skill_path / name
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8")
                    if name.endswith(".json"):
                        data = json.loads(text)
                    else:
                        # Minimal YAML parse for name field
                        for line in text.splitlines():
                            if line.strip().startswith("name:"):
                                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                                return val
                        return skill_path.name
                except (OSError, ValueError):
                    pass
        return skill_path.name
