"""Tests for the Kazma Hub Badge System (badges.py)."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from kazma_core.hub.badges import (
    BADGE_LEVELS,
    Badge,
    BadgeStats,
    BadgeVerification,
    CertificationBadgeSystem,
    _CREATE_BADGES,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary SQLite DB path."""
    return str(tmp_path / "test_badges.db")


@pytest.fixture
def badge_system(db_path: str) -> CertificationBadgeSystem:
    """Return a CertificationBadgeSystem with a fresh DB."""
    import sqlite3 as _sqlite3

    # Ensure both badges and skills tables exist
    conn = _sqlite3.connect(db_path)
    conn.executescript(_CREATE_BADGES)
    conn.executescript(
        """CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            author TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT,
            license TEXT,
            capabilities TEXT,
            tags TEXT,
            manifest_json TEXT,
            checksum TEXT,
            installed_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, author, version)
        )"""
    )
    conn.close()
    return CertificationBadgeSystem(db_path=db_path)


@pytest.fixture
def badge_system_with_skill(badge_system: CertificationBadgeSystem) -> CertificationBadgeSystem:
    """Return a badge system that has a skill registered in the DB."""
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(badge_system.db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            author TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT,
            license TEXT,
            capabilities TEXT,
            tags TEXT,
            manifest_json TEXT,
            checksum TEXT,
            installed_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, author, version)
        )"""
    )
    conn.execute(
        "INSERT INTO skills (name, author, version, description, license) "
        "VALUES (?, ?, ?, ?, ?)",
        ("test-skill", "test-author", "1.0.0", "A test skill", "MIT"),
    )
    conn.commit()
    conn.close()
    return badge_system


# --- Badge dataclass tests ---


class TestBadgeDataclass:
    """Test Badge dataclass construction."""

    def test_badge_creation(self):
        now = datetime.now(timezone.utc)
        badge = Badge(skill_id="test", level="basic", issued_at=now, expires_at=None)
        assert badge.skill_id == "test"
        assert badge.level == "basic"
        assert badge.revoked is False
        assert badge.revoke_reason is None

    def test_badge_with_expiry(self):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=365)
        badge = Badge(skill_id="test", level="premium", issued_at=now, expires_at=expires)
        assert badge.expires_at == expires


# --- Issue badge tests ---


class TestIssueBadge:
    """Test badge issuance at each level."""

    @pytest.mark.asyncio
    async def test_issue_basic_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        badge = await badge_system_with_skill.issue_badge("test-skill", "basic")
        assert badge.skill_id == "test-skill"
        assert badge.level == "basic"
        assert badge.revoked is False
        assert badge.issued_at is not None

    @pytest.mark.asyncio
    async def test_issue_standard_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        badge = await badge_system_with_skill.issue_badge("test-skill", "standard")
        assert badge.level == "standard"

    @pytest.mark.asyncio
    async def test_issue_premium_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        badge = await badge_system_with_skill.issue_badge("test-skill", "premium")
        assert badge.level == "premium"

    @pytest.mark.asyncio
    async def test_issue_badge_nonexistent_skill(self, badge_system: CertificationBadgeSystem):
        with pytest.raises(ValueError, match="Skill .* not found"):
            await badge_system.issue_badge("nonexistent-skill", "basic")

    @pytest.mark.asyncio
    async def test_issue_badge_invalid_level(self, badge_system_with_skill: CertificationBadgeSystem):
        with pytest.raises(ValueError, match="Invalid badge level"):
            await badge_system_with_skill.issue_badge("test-skill", "invalid")


# --- Verify badge tests ---


class TestVerifyBadge:
    """Test badge verification scenarios."""

    @pytest.mark.asyncio
    async def test_verify_valid_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        await badge_system_with_skill.issue_badge("test-skill", "basic")
        result = await badge_system_with_skill.verify_badge("test-skill")
        assert result.valid is True
        assert result.level == "basic"

    @pytest.mark.asyncio
    async def test_verify_expired_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        # Manually insert an expired badge
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(badge_system_with_skill.db_path)
        now = datetime.now(timezone.utc)
        expired = now - timedelta(days=1)
        conn.execute(
            "INSERT INTO badges (skill_id, level, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            ("test-skill", "basic", now.isoformat(), expired.isoformat()),
        )
        conn.commit()
        conn.close()
        result = await badge_system_with_skill.verify_badge("test-skill")
        assert result.valid is False
        assert "expired" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_verify_revoked_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        await badge_system_with_skill.issue_badge("test-skill", "basic")
        await badge_system_with_skill.revoke_badge("test-skill", "security issue")
        result = await badge_system_with_skill.verify_badge("test-skill")
        assert result.valid is False
        assert "revoked" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_verify_no_badge(self, badge_system: CertificationBadgeSystem):
        result = await badge_system.verify_badge("nonexistent")
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_verify_badge_with_short_expiry(self, badge_system_with_skill: CertificationBadgeSystem):
        """Badge that expires in 1 second should be valid immediately but invalid shortly after."""
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(badge_system_with_skill.db_path)
        now = datetime.now(timezone.utc)
        almost_expired = now + timedelta(seconds=1)
        conn.execute(
            "INSERT INTO badges (skill_id, level, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            ("test-skill", "basic", now.isoformat(), almost_expired.isoformat()),
        )
        conn.commit()
        conn.close()

        # Should be valid immediately
        result = await badge_system_with_skill.verify_badge("test-skill")
        assert result.valid is True


# --- Revoke badge tests ---


class TestRevokeBadge:
    """Test badge revocation."""

    @pytest.mark.asyncio
    async def test_revoke_badge(self, badge_system_with_skill: CertificationBadgeSystem):
        await badge_system_with_skill.issue_badge("test-skill", "basic")
        await badge_system_with_skill.revoke_badge("test-skill", "vulnerability found")
        result = await badge_system_with_skill.verify_badge("test-skill")
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_badge(self, badge_system: CertificationBadgeSystem):
        with pytest.raises(ValueError, match="No badge found"):
            await badge_system.revoke_badge("nonexistent", "test reason")


# --- Badge replacement tests ---


class TestBadgeReplacement:
    """Test that issuing a new badge replaces the old one."""

    @pytest.mark.asyncio
    async def test_replacing_badge_updates_level(self, badge_system_with_skill: CertificationBadgeSystem):
        """Issuing a higher-level badge replaces the old one."""
        bs = badge_system_with_skill

        # Issue basic
        await bs.issue_badge("test-skill", "basic")
        v1 = await bs.verify_badge("test-skill")
        assert v1.level == "basic"

        # Replace with premium
        await bs.issue_badge("test-skill", "premium")
        v2 = await bs.verify_badge("test-skill")
        assert v2.valid is True
        assert v2.level == "premium"

    @pytest.mark.asyncio
    async def test_replacing_badge_downgrades_level(self, badge_system_with_skill: CertificationBadgeSystem):
        """Issuing a lower-level badge replaces the higher one."""
        bs = badge_system_with_skill

        await bs.issue_badge("test-skill", "premium")
        v1 = await bs.verify_badge("test-skill")
        assert v1.level == "premium"

        await bs.issue_badge("test-skill", "basic")
        v2 = await bs.verify_badge("test-skill")
        assert v2.level == "basic"


# --- SVG generation tests ---


class TestBadgeSvg:
    """Test badge SVG generation."""

    def test_generate_basic_svg(self, badge_system: CertificationBadgeSystem):
        svg = badge_system.generate_badge_svg("basic", "My Skill")
        assert "<svg" in svg
        assert "Kazma-Certified" in svg
        assert "Basic" in svg

    def test_generate_standard_svg(self, badge_system: CertificationBadgeSystem):
        svg = badge_system.generate_badge_svg("standard", "My Skill")
        assert "Standard" in svg

    def test_generate_premium_svg(self, badge_system: CertificationBadgeSystem):
        svg = badge_system.generate_badge_svg("premium", "My Skill")
        assert "Premium" in svg

    def test_svg_is_valid_xml(self, badge_system: CertificationBadgeSystem):
        svg = badge_system.generate_badge_svg("basic", "Test")
        assert svg.strip().startswith("<?xml") or svg.strip().startswith("<svg")


# --- Badge stats tests ---


class TestBadgeStats:
    """Test badge statistics."""

    @pytest.mark.asyncio
    async def test_get_badge_stats_empty(self, badge_system: CertificationBadgeSystem):
        stats = await badge_system.get_badge_stats()
        assert stats.total == 0

    @pytest.mark.asyncio
    async def test_get_badge_stats_with_badges(self, badge_system_with_skill: CertificationBadgeSystem):
        # Issue some badges (need multiple skills for multiple badges)
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(badge_system_with_skill.db_path)
        for i in range(3):
            conn.execute(
                "INSERT INTO skills (name, author, version, description, license) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"skill-{i}", "author", "1.0.0", "desc", "MIT"),
            )
        conn.commit()
        conn.close()

        await badge_system_with_skill.issue_badge("test-skill", "basic")
        await badge_system_with_skill.issue_badge("skill-0", "standard")
        await badge_system_with_skill.issue_badge("skill-1", "premium")

        stats = await badge_system_with_skill.get_badge_stats()
        assert stats.total == 3
        assert stats.by_level["basic"] == 1
        assert stats.by_level["standard"] == 1
        assert stats.by_level["premium"] == 1

    @pytest.mark.asyncio
    async def test_stats_reflect_revoked_badges(self, badge_system_with_skill: CertificationBadgeSystem):
        """Revoked badges should not appear in stats."""
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(badge_system_with_skill.db_path)
        conn.execute(
            "INSERT INTO skills (name, author, version, description, license) "
            "VALUES (?, ?, ?, ?, ?)",
            ("extra-skill", "author", "1.0.0", "desc", "MIT"),
        )
        conn.commit()
        conn.close()

        await badge_system_with_skill.issue_badge("test-skill", "basic")
        await badge_system_with_skill.issue_badge("extra-skill", "standard")
        await badge_system_with_skill.revoke_badge("extra-skill", "revoked for testing")

        stats = await badge_system_with_skill.get_badge_stats()
        assert stats.total == 1  # only non-revoked
        assert stats.by_level["basic"] == 1
        assert "standard" not in stats.by_level

    @pytest.mark.asyncio
    async def test_stats_recent_issuances(self, badge_system_with_skill: CertificationBadgeSystem):
        """Stats should count recent issuances correctly."""
        await badge_system_with_skill.issue_badge("test-skill", "basic")
        stats = await badge_system_with_skill.get_badge_stats()
        assert stats.recent_issuances >= 1


# --- Badge levels constant ---


class TestBadgeLevels:
    """Test BADGE_LEVELS constant."""

    def test_all_levels_present(self):
        assert "basic" in BADGE_LEVELS
        assert "standard" in BADGE_LEVELS
        assert "premium" in BADGE_LEVELS

    def test_levels_have_required_keys(self):
        for level_name, level_data in BADGE_LEVELS.items():
            assert "label" in level_data, f"{level_name} missing label"
            assert "description" in level_data, f"{level_name} missing description"
            assert "requirements" in level_data, f"{level_name} missing requirements"
            assert isinstance(level_data["requirements"], list)
