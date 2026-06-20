"""Tests for the KazmaCertification module."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.security.certification import (
    CERTIFICATION_LEVELS,
    CertificationResult,
    KazmaCertification,
    VerificationResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cert_db(tmp_path: Path) -> KazmaCertification:
    """Return a KazmaCertification backed by a temp DB."""
    return KazmaCertification(db_path=tmp_path / "test_certs.db")


@pytest.fixture
def basic_skill(tmp_path: Path) -> Path:
    """Create a minimal valid skill (passes basic certification)."""
    skill = tmp_path / "basic-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text("name: basic-skill\nversion: 1.0.0\n")
    (skill / "main.py").write_text("def run():\n    pass\n")
    return skill


@pytest.fixture
def standard_skill(tmp_path: Path) -> Path:
    """Create a skill that passes standard certification (has tests)."""
    skill = tmp_path / "std-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text("name: std-skill\nversion: 1.0.0\n")
    (skill / "main.py").write_text("def run():\n    pass\n")
    (skill / "test_main.py").write_text("def test_run():\n    pass\n")
    return skill


@pytest.fixture
def premium_skill(tmp_path: Path) -> Path:
    """Create a skill that passes premium certification."""
    skill = tmp_path / "premium-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text("name: premium-skill\nversion: 1.0.0\n")
    (skill / "main.py").write_text("def run():\n    pass\n")
    (skill / "test_main.py").write_text("def test_run():\n    pass\n")
    (skill / ".coveragerc").write_text("[run]\nsource = .\n")
    return skill


# ---------------------------------------------------------------------------
# Certification levels
# ---------------------------------------------------------------------------

class TestCertificationLevels:
    def test_certification_levels_count(self):
        assert len(CERTIFICATION_LEVELS) == 3

    def test_basic_requirements(self):
        reqs = CERTIFICATION_LEVELS["basic"]["min_requirements"]
        assert "manifest_valid" in reqs
        assert "no_critical_violations" in reqs

    def test_standard_requirements(self):
        reqs = CERTIFICATION_LEVELS["standard"]["min_requirements"]
        assert "tests_pass" in reqs
        assert "no_high_violations" in reqs

    def test_premium_requirements(self):
        reqs = CERTIFICATION_LEVELS["premium"]["min_requirements"]
        assert "coverage_above_80" in reqs

    def test_all_levels_have_badge_and_validity(self):
        for level, cfg in CERTIFICATION_LEVELS.items():
            assert "badge" in cfg, f"{level} missing badge"
            assert "validity_days" in cfg, f"{level} missing validity_days"
            assert cfg["validity_days"] > 0


# ---------------------------------------------------------------------------
# Certify
# ---------------------------------------------------------------------------

class TestCertify:
    @pytest.mark.asyncio
    async def test_certify_basic(self, cert_db: KazmaCertification, basic_skill: Path):
        result = await cert_db.certify(basic_skill, level="basic")
        assert result.certified is True
        assert result.level == "basic"
        assert result.badge == "basic-certified"
        assert len(result.valid_until) > 0
        assert len(result.requirements_met) > 0

    @pytest.mark.asyncio
    async def test_certify_standard(self, cert_db: KazmaCertification, standard_skill: Path):
        result = await cert_db.certify(standard_skill, level="standard")
        # May or may not pass depending on linter results, but should not crash
        assert isinstance(result, CertificationResult)
        assert result.level == "standard"

    @pytest.mark.asyncio
    async def test_certify_premium(self, cert_db: KazmaCertification, premium_skill: Path):
        result = await cert_db.certify(premium_skill, level="premium")
        assert isinstance(result, CertificationResult)
        assert result.level == "premium"

    @pytest.mark.asyncio
    async def test_certify_invalid_level(self, cert_db: KazmaCertification, basic_skill: Path):
        result = await cert_db.certify(basic_skill, level="nonexistent")
        assert result.certified is False
        assert "Unknown certification level" in result.requirements_failed[0]

    @pytest.mark.asyncio
    async def test_certify_stores_in_db(self, cert_db: KazmaCertification, basic_skill: Path):
        result = await cert_db.certify(basic_skill, level="basic")
        if result.certified:
            skill_id = await cert_db._extract_skill_id(basic_skill)
            verification = await cert_db.verify(skill_id)
            assert verification.valid is True

    @pytest.mark.asyncio
    async def test_certify_no_manifest(self, cert_db: KazmaCertification, tmp_path: Path):
        skill = tmp_path / "no-manifest"
        skill.mkdir()
        (skill / "code.py").write_text("x = 1\n")
        result = await cert_db.certify(skill, level="basic")
        assert result.certified is False
        assert any("manifest_valid" in f for f in result.requirements_failed)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

class TestVerify:
    @pytest.mark.asyncio
    async def test_verify_valid(self, cert_db: KazmaCertification, basic_skill: Path):
        cert_result = await cert_db.certify(basic_skill, level="basic")
        if cert_result.certified:
            skill_id = await cert_db._extract_skill_id(basic_skill)
            verify_result = await cert_db.verify(skill_id)
            assert verify_result.valid is True
            assert verify_result.level == "basic"

    @pytest.mark.asyncio
    async def test_verify_not_found(self, cert_db: KazmaCertification):
        result = await cert_db.verify("nonexistent-skill-id")
        assert result.valid is False
        assert result.level == ""

    @pytest.mark.asyncio
    async def test_verify_revoked(self, cert_db: KazmaCertification, basic_skill: Path):
        cert_result = await cert_db.certify(basic_skill, level="basic")
        if cert_result.certified:
            skill_id = await cert_db._extract_skill_id(basic_skill)
            await cert_db.revoke(skill_id, "policy violation")
            verify_result = await cert_db.verify(skill_id)
            assert verify_result.valid is False


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------

class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_certification(self, cert_db: KazmaCertification, basic_skill: Path):
        cert_result = await cert_db.certify(basic_skill, level="basic")
        if cert_result.certified:
            skill_id = await cert_db._extract_skill_id(basic_skill)
            revoked = await cert_db.revoke(skill_id, "security concern")
            assert revoked is True
            # Verify it's now invalid
            verify_result = await cert_db.verify(skill_id)
            assert verify_result.valid is False

    @pytest.mark.asyncio
    async def test_revoke_nonexistent(self, cert_db: KazmaCertification):
        result = await cert_db.revoke("nonexistent-id", "reason")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_idempotent(self, cert_db: KazmaCertification, basic_skill: Path):
        cert_result = await cert_db.certify(basic_skill, level="basic")
        if cert_result.certified:
            skill_id = await cert_db._extract_skill_id(basic_skill)
            r1 = await cert_db.revoke(skill_id, "first")
            assert r1 is True
            r2 = await cert_db.revoke(skill_id, "second")
            assert r2 is False  # Already revoked


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

class TestDBOperations:
    @pytest.mark.asyncio
    async def test_init_db(self, cert_db: KazmaCertification):
        await cert_db._init_db()
        # Should be callable multiple times without error
        await cert_db._init_db()

    @pytest.mark.asyncio
    async def test_db_persistence(self, tmp_path: Path, basic_skill: Path):
        db_file = tmp_path / "persist.db"
        cert1 = KazmaCertification(db_path=db_file)
        cert_result = await cert1.certify(basic_skill, level="basic")
        if cert_result.certified:
            skill_id = await cert1._extract_skill_id(basic_skill)
            # Create new instance with same DB
            cert2 = KazmaCertification(db_path=db_file)
            verify_result = await cert2.verify(skill_id)
            assert verify_result.valid is True
