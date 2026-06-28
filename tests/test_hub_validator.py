"""Tests for the Kazma Hub skill validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_manifest_dict(
    name: str = "valid-skill",
    author: str = "tester",
    version: str = "1.0.0",
    description: str = "A valid test skill",
    license: str = "MIT",
    entry_point: str | None = None,
    capabilities: list | None = None,
    permissions: list | None = None,
    mcp_servers: list | None = None,
) -> dict:
    data: dict = {
        "name": name,
        "author": author,
        "version": version,
        "description": description,
        "license": license,
    }
    if entry_point:
        data["entry_point"] = entry_point
    if capabilities:
        data["capabilities"] = capabilities
    if permissions:
        data["permissions"] = permissions
    if mcp_servers:
        data["mcp_servers"] = mcp_servers
    return data


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "skill_manifest.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


def _write_py(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Tests — Valid skill passes validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestValidSkill:
    async def test_valid_skill_passes(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict(entry_point="main"))
        _write_py(tmp_path, "main.py", "def run():\n    pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is True
        assert len(result.errors) == 0
        assert result.score == 100.0


# ---------------------------------------------------------------------------
# Tests — Missing manifest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMissingManifest:
    async def test_missing_manifest_fails(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_py(tmp_path, "main.py", "pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is False
        assert any("manifest" in e.lower() for e in result.errors)
        # Score should be deducted
        assert result.score < 100.0


# ---------------------------------------------------------------------------
# Tests — Invalid manifest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInvalidManifest:
    async def test_missing_required_fields_fails(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        # Manifest with only name, missing version, description, author, license
        incomplete = {"name": "test-skill"}
        _write_manifest(tmp_path, incomplete)

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is False
        assert any("version" in e.lower() or "required" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Tests — Security issues detected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSecurityScan:
    async def test_eval_detected(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        _write_py(tmp_path, "main.py", "eval('x + 1')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.score < 100.0
        # Should flag eval
        assert (
            any("eval" in (e + w).lower() for e in result.errors for w in result.warnings)
            or any("eval" in w.lower() for w in result.warnings)
            or any("eval" in e.lower() for e in result.errors)
        )

    async def test_exec_detected(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        _write_py(tmp_path, "main.py", "exec('code')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.score < 100.0

    async def test_os_system_detected(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        _write_py(tmp_path, "main.py", "import os\nos.system('rm -rf /')\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        # os.system is a heavy deduction
        assert result.score <= 75.0

    async def test_hardcoded_secret_detected(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        _write_py(tmp_path, "main.py", 'api_key = "sk_live_abc123"\n')

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.score < 100.0


# ---------------------------------------------------------------------------
# Tests — Unknown permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPermissions:
    async def test_unknown_permission_warns(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict(permissions=["file_read", "unknown_perm_xyz"]))
        _write_py(tmp_path, "main.py", "pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        # Unknown permission should generate a warning
        assert any("unknown_perm_xyz" in w for w in result.warnings)
        # Score should be deducted
        assert result.score < 100.0

    async def test_known_permissions_no_warning(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict(permissions=["file_read", "network_outbound"]))
        _write_py(tmp_path, "main.py", "pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        # No unknown permission warnings
        assert not any("permission" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Tests — Entry point validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEntryPoint:
    async def test_missing_entry_point_fails(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict(entry_point="nonexistent"))
        # Don't create main.py

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is False
        assert any("entry_point" in e.lower() or "missing" in e.lower() for e in result.errors)

    async def test_valid_entry_point_passes(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict(entry_point="main"))
        _write_py(tmp_path, "main.py", "def run(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Tests — Score calculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestScoreCalculation:
    async def test_clean_skill_full_score(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        _write_py(tmp_path, "main.py", "def run(): pass\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        assert result.score == 100.0

    async def test_multiple_issues_compound(self, tmp_path):
        from kazma_core.hub.validator import SkillValidator

        _write_manifest(tmp_path, _valid_manifest_dict())
        # Multiple security issues
        _write_py(tmp_path, "main.py", "eval('bad')\nexec('worse')\nos.system('bad')\napi_key = 'secret'\n")

        validator = SkillValidator()
        result = await validator.validate(tmp_path)
        # Should have significant deductions
        assert result.score < 50.0
