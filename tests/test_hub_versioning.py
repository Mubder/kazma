"""Tests for SkillVersioning — version parsing, compatibility, and conflict resolution.

TDD: These tests should FAIL before any implementation exists.
"""

from __future__ import annotations

import pytest

from kazma_core.hub.manifest_schema import SkillManifest
from kazma_core.hub.versioning import ConflictResolution, SkillVersioning


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------

class TestParseVersion:
    def test_parse_simple_version(self):
        assert SkillVersioning.parse_version("1.2.3") == (1, 2, 3)

    def test_parse_zero_version(self):
        assert SkillVersioning.parse_version("0.0.0") == (0, 0, 0)

    def test_parse_large_numbers(self):
        assert SkillVersioning.parse_version("100.200.300") == (100, 200, 300)

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError):
            SkillVersioning.parse_version("1.2")

    def test_invalid_version_non_numeric(self):
        with pytest.raises(ValueError):
            SkillVersioning.parse_version("a.b.c")


# ---------------------------------------------------------------------------
# is_compatible
# ---------------------------------------------------------------------------

class TestIsCompatible:
    def test_equal_versions_compatible(self):
        assert SkillVersioning.is_compatible("1.0.0", "1.0.0") is True

    def test_newer_version_compatible(self):
        assert SkillVersioning.is_compatible("2.0.0", "1.0.0") is True

    def test_minor_newer_compatible(self):
        assert SkillVersioning.is_compatible("1.5.0", "1.0.0") is True

    def test_patch_newer_compatible(self):
        assert SkillVersioning.is_compatible("1.0.5", "1.0.0") is True

    def test_older_version_incompatible(self):
        assert SkillVersioning.is_compatible("0.9.0", "1.0.0") is False

    def test_minor_older_incompatible(self):
        assert SkillVersioning.is_compatible("1.0.0", "1.5.0") is False


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------

class TestGetLatest:
    def test_single_version(self):
        assert SkillVersioning.get_latest(["1.0.0"]) == "1.0.0"

    def test_mixed_versions(self):
        assert SkillVersioning.get_latest(["1.0.0", "2.3.1", "0.9.0"]) == "2.3.1"

    def test_equal_versions(self):
        assert SkillVersioning.get_latest(["1.0.0", "1.0.0"]) == "1.0.0"

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            SkillVersioning.get_latest([])


# ---------------------------------------------------------------------------
# resolve_conflicts
# ---------------------------------------------------------------------------

class TestResolveConflicts:
    def _make_manifest(self, name: str, capabilities: list[str] = None) -> SkillManifest:
        data = {
            "name": name,
            "version": "1.0.0",
            "description": f"Skill {name}",
            "author": "Test",
            "license": "MIT",
        }
        if capabilities:
            data["capabilities"] = capabilities
        return SkillManifest.from_dict(data)

    def test_no_conflicts(self):
        installed = [self._make_manifest("alpha-skill")]
        new = self._make_manifest("beta-skill")
        result = SkillVersioning.resolve_conflicts(installed, new)
        assert result.has_conflicts is False
        assert result.can_proceed is True

    def test_same_name_replacement_ok(self):
        installed = [self._make_manifest("my-skill")]
        new = self._make_manifest("my-skill")
        result = SkillVersioning.resolve_conflicts(installed, new)
        assert result.has_conflicts is False
        assert result.can_proceed is True
        assert len(result.warnings) > 0  # should warn about replacement

    def test_same_capabilities_warns(self):
        installed = [self._make_manifest("skill-a", capabilities=["audio"])]
        new = self._make_manifest("skill-b", capabilities=["audio"])
        result = SkillVersioning.resolve_conflicts(installed, new)
        assert result.has_conflicts is False
        assert result.can_proceed is True
        assert any("audio" in w.lower() or "capabilit" in w.lower() for w in result.warnings)

    def test_empty_installed(self):
        new = self._make_manifest("fresh-skill")
        result = SkillVersioning.resolve_conflicts([], new)
        assert result.has_conflicts is False
        assert result.can_proceed is True
