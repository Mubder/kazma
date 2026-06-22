"""Tests for SkillManifest — manifest schema validation and serialization.

TDD: These tests should FAIL before any implementation exists.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml
from kazma_core.hub.manifest_schema import SkillManifest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_MANIFEST_DICT = {
    "name": "test-skill",
    "version": "1.0.0",
    "description": "A test skill for validation",
    "author": "Test Author",
    "license": "MIT",
}


def _write_yaml(data: dict, suffix: str = ".yaml") -> Path:
    """Write a dict as YAML to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    yaml.dump(data, tmp)
    tmp.close()
    return Path(tmp.name)


@pytest.fixture
def valid_manifest_path(tmp_path):
    """Path to a valid manifest YAML file."""
    data = {
        "name": "test-skill",
        "version": "1.0.0",
        "description": "A test skill for validation",
        "author": "Test Author",
        "license": "MIT",
    }
    path = tmp_path / "skill_manifest.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def optional_fields_manifest_path(tmp_path):
    """Path to a manifest with all optional fields populated."""
    data = {
        "name": "full-skill",
        "version": "2.1.0",
        "description": "A fully-specified skill",
        "author": "Full Author",
        "license": "Apache-2.0",
        "capabilities": ["audio", "video"],
        "dependencies": {"core": ">=1.0.0", "optional": ["numpy"]},
        "mcp_servers": [
            {"name": "my-server", "type": "stdio"}
        ],
        "permissions": {"required": ["file_read"]},
        "entry_point": "my_skill.main:run",
        "config_schema": {"type": "object"},
        "min_core_version": "0.5.0",
        "tags": ["testing", "example"],
        "homepage": "https://example.com/skill",
        "repository": "https://github.com/example/skill",
    }
    path = tmp_path / "full_manifest.yaml"
    path.write_text(yaml.dump(data))
    return path


# ---------------------------------------------------------------------------
# Valid manifest loading
# ---------------------------------------------------------------------------

class TestValidManifest:
    def test_loads_successfully(self, valid_manifest_path):
        manifest = SkillManifest(valid_manifest_path)
        assert manifest.data["name"] == "test-skill"
        assert manifest.data["version"] == "1.0.0"

    def test_validate_passes(self, valid_manifest_path):
        manifest = SkillManifest(valid_manifest_path)
        result = manifest.validate()
        assert result.passed is True
        assert result.errors == []
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequiredField:
    @pytest.mark.parametrize("field", ["name", "version", "description", "author", "license"])
    def test_missing_field_raises(self, tmp_path, field):
        data = dict(VALID_MANIFEST_DICT)
        del data[field]
        path = tmp_path / "incomplete.yaml"
        path.write_text(yaml.dump(data))
        manifest = SkillManifest(path)
        result = manifest.validate()
        assert result.passed is False
        assert any(field in e for e in result.errors)


# ---------------------------------------------------------------------------
# Invalid name (not kebab-case)
# ---------------------------------------------------------------------------

class TestInvalidName:
    @pytest.mark.parametrize("bad_name", ["UpperCase", "has spaces", "has_underscores", "123starts-number"])
    def test_invalid_name_rejected(self, tmp_path, bad_name):
        data = dict(VALID_MANIFEST_DICT, name=bad_name)
        path = tmp_path / "bad_name.yaml"
        path.write_text(yaml.dump(data))
        manifest = SkillManifest(path)
        result = manifest.validate()
        assert result.passed is False
        assert any("name" in e.lower() for e in result.errors)

    def test_valid_kebab_case_accepted(self, tmp_path):
        data = dict(VALID_MANIFEST_DICT, name="valid-name-123")
        path = tmp_path / "good.yaml"
        path.write_text(yaml.dump(data))
        manifest = SkillManifest(path)
        result = manifest.validate()
        assert result.passed is True


# ---------------------------------------------------------------------------
# Invalid version (not semver)
# ---------------------------------------------------------------------------

class TestInvalidVersion:
    @pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "1.0.0-beta", "not-a-version"])
    def test_invalid_version_rejected(self, tmp_path, bad_version):
        data = dict(VALID_MANIFEST_DICT, version=bad_version)
        path = tmp_path / "bad_ver.yaml"
        path.write_text(yaml.dump(data))
        manifest = SkillManifest(path)
        result = manifest.validate()
        assert result.passed is False
        assert any("version" in e.lower() for e in result.errors)

    def test_valid_version_accepted(self, tmp_path):
        data = dict(VALID_MANIFEST_DICT, version="0.1.0")
        path = tmp_path / "good.yaml"
        path.write_text(yaml.dump(data))
        manifest = SkillManifest(path)
        result = manifest.validate()
        assert result.passed is True


# ---------------------------------------------------------------------------
# Optional fields
# ---------------------------------------------------------------------------

class TestOptionalFields:
    def test_optional_fields_parse(self, optional_fields_manifest_path):
        manifest = SkillManifest(optional_fields_manifest_path)
        assert manifest.data["capabilities"] == ["audio", "video"]
        assert manifest.data["mcp_servers"][0]["name"] == "my-server"
        assert manifest.data["min_core_version"] == "0.5.0"

    def test_optional_fields_validate(self, optional_fields_manifest_path):
        manifest = SkillManifest(optional_fields_manifest_path)
        result = manifest.validate()
        assert result.passed is True


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_round_trip(self, valid_manifest_path):
        manifest = SkillManifest(valid_manifest_path)
        d = manifest.to_dict()
        assert d["name"] == "test-skill"
        manifest2 = SkillManifest.from_dict(d)
        assert manifest2.to_dict() == d

    def test_from_dict_creates_valid_manifest(self):
        manifest = SkillManifest.from_dict(VALID_MANIFEST_DICT)
        result = manifest.validate()
        assert result.passed is True

    def test_to_yaml_output(self, valid_manifest_path):
        manifest = SkillManifest(valid_manifest_path)
        yaml_str = manifest.to_yaml()
        parsed = yaml.safe_load(yaml_str)
        assert parsed["name"] == "test-skill"

    def test_to_json_output(self, valid_manifest_path):
        manifest = SkillManifest(valid_manifest_path)
        json_str = manifest.to_json()
        parsed = json.loads(json_str)
        assert parsed["name"] == "test-skill"


# ---------------------------------------------------------------------------
# Example manifest validates
# ---------------------------------------------------------------------------

class TestExampleManifest:
    def test_almuhalab_manifest_validates(self):
        example = Path("examples/almuhalab_custom_skills/skill_manifest.yaml")
        if not example.exists():
            pytest.skip("Example manifest not found")
        manifest = SkillManifest(example)
        result = manifest.validate()
        assert result.passed is True
