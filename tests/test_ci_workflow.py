"""Tests for CI/CD skill review workflow logic.

Tests the validation steps that a CI pipeline would run when
reviewing a submitted skill for certification.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml

from kazma_core.hub.manifest_schema import SkillManifest, ValidationResult
from kazma_core.hub.validator import SkillValidator


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    """Write a skill_manifest.yaml and return its path."""
    manifest_path = tmp_path / "skill_manifest.yaml"
    manifest_path.write_text(yaml.dump(data))
    return manifest_path


def _write_skill_file(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a file in the skill directory."""
    fpath = tmp_path / filename
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content)
    return fpath


# ---------------------------------------------------------------------------
# Manifest Validation
# ---------------------------------------------------------------------------


class TestManifestValidation:
    """Test manifest validation as run in CI."""

    def test_valid_manifest_passes(self, tmp_path):
        """A valid manifest should pass validation."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "category": "testing",
            "license": "MIT",
        })
        manifest = SkillManifest(tmp_path / "skill_manifest.yaml")
        result = manifest.validate()
        assert result.passed is True
        assert len(result.errors) == 0

    def test_missing_required_field_fails(self, tmp_path):
        """Missing required fields should fail validation."""
        # Missing 'author' and 'license'
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "description": "A test skill",
        })
        manifest = SkillManifest(tmp_path / "skill_manifest.yaml")
        result = manifest.validate()
        assert result.passed is False
        assert any("author" in e for e in result.errors)
        assert any("license" in e for e in result.errors)

    def test_invalid_version_format_fails(self, tmp_path):
        """Non-semver version should fail."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0",  # missing patch
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        manifest = SkillManifest(tmp_path / "skill_manifest.yaml")
        result = manifest.validate()
        assert result.passed is False
        assert any("version" in e.lower() or "semver" in e.lower() for e in result.errors)

    def test_invalid_name_not_kebab_case_fails(self, tmp_path):
        """Non-kebab-case name should fail."""
        _write_manifest(tmp_path, {
            "name": "Test_Skill",  # not kebab-case
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        manifest = SkillManifest(tmp_path / "skill_manifest.yaml")
        result = manifest.validate()
        assert result.passed is False
        assert any("name" in e.lower() or "kebab" in e.lower() for e in result.errors)

    def test_empty_manifest_fails(self, tmp_path):
        """An empty manifest should fail with missing fields."""
        _write_manifest(tmp_path, {})
        manifest = SkillManifest(tmp_path / "skill_manifest.yaml")
        result = manifest.validate()
        assert result.passed is False
        assert len(result.errors) >= 4  # at least name, version, description, author, license


# ---------------------------------------------------------------------------
# Full Directory Validation (SkillValidator)
# ---------------------------------------------------------------------------


class TestDirectoryValidation:
    """Test full skill directory validation."""

    @pytest.mark.asyncio
    async def test_valid_directory_passes(self, tmp_path):
        """A complete valid skill directory should pass all checks."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", "def hello(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is True
        assert result.score > 80

    @pytest.mark.asyncio
    async def test_missing_manifest_fails(self, tmp_path):
        """Directory without manifest should fail."""
        _write_skill_file(tmp_path, "main.py", "def hello(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is False
        assert any("manifest" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_missing_entry_point_warns(self, tmp_path):
        """Missing declared entry point should fail."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
            "entry_point": "main:MyClass",
        })
        # No main.py file created

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is False
        assert any("entry_point" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Security Linter
# ---------------------------------------------------------------------------


class TestSecurityLinter:
    """Test security linting as run in CI."""

    @pytest.mark.asyncio
    async def test_clean_directory_passes(self, tmp_path):
        """Clean skill directory should pass security scan."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", "def hello(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is True
        # No security warnings for clean code
        security_warnings = [w for w in result.warnings if "secret" in w.lower() or "eval" in w.lower()]
        assert len(security_warnings) == 0

    @pytest.mark.asyncio
    async def test_hardcoded_secret_detected(self, tmp_path):
        """Hardcoded secrets should be detected."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", 'api_key = "sk-1234567890abcdef"\n')

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        # Should still pass structurally, but have security warnings
        assert any("secret" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_eval_detected(self, tmp_path):
        """eval() usage should be flagged."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", "eval('print(1)')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert any("eval" in w.lower() for w in result.warnings)
        assert result.score < 100

    @pytest.mark.asyncio
    async def test_exec_detected(self, tmp_path):
        """exec() usage should be flagged."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", "exec('import os')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert any("exec" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_os_system_detected(self, tmp_path):
        """os.system() usage should be flagged."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
        })
        _write_skill_file(tmp_path, "main.py", "import os\nos.system('echo hello')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert any("os.system" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unknown_permission_flagged(self, tmp_path):
        """Unknown permissions should be warned about."""
        _write_manifest(tmp_path, {
            "name": "test-skill",
            "version": "1.0.0",
            "author": "test-author",
            "description": "A test skill",
            "license": "MIT",
            "permissions": ["file_read", "totally_fake_permission"],
        })
        _write_skill_file(tmp_path, "main.py", "def hello(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert any("permission" in w.lower() for w in result.warnings)
