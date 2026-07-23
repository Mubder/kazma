"""Skill manifest schema — validates YAML manifests against the hub spec.

Required fields: name (kebab-case), version (semver), description, author, license (SPDX).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["CheckResult", "SkillManifest", "ValidationResult"]


@dataclass
class ValidationResult:
    """Result of manifest validation."""

    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 100.0  # 0-100, security/quality score


@dataclass
class CheckResult:
    """Result of a skill quality/security check."""

    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 100.0  # 0-100, security/quality score


# Validation patterns
_KEBAB_CASE = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class SkillManifest:
    """Skill manifest loaded from a YAML file or dict."""

    REQUIRED_FIELDS = ["name", "version", "description", "author", "license"]
    OPTIONAL_FIELDS = [
        "capabilities",
        "dependencies",
        "mcp_servers",
        "permissions",
        "entry_point",
        "config_schema",
        "min_core_version",
        "tags",
        "homepage",
        "repository",
    ]

    def __init__(self, manifest_path: Path | str):
        self.path = Path(manifest_path)
        with open(self.path) as f:
            self.data: dict[str, Any] = yaml.safe_load(f)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # --- Required fields ---
        for field_name in self.REQUIRED_FIELDS:
            if field_name not in self.data:
                errors.append(f"Missing required field: {field_name}")

        # --- Name: kebab-case ---
        name = self.data.get("name", "")
        if name and not _KEBAB_CASE.match(name):
            errors.append(f"Name must be kebab-case, got: {name!r}")

        # --- Version: simple semver ---
        version = self.data.get("version", "")
        if version and not _SEMVER.match(version):
            errors.append(f"Version must be valid semver (X.Y.Z), got: {version!r}")

        # --- License: non-empty string ---
        license_val = self.data.get("license", "")
        if isinstance(license_val, str) and not license_val.strip():
            errors.append("License must be a non-empty string")

        # --- min_core_version: if present, must be valid semver ---
        min_core = self.data.get("min_core_version")
        if min_core is not None:
            if not _SEMVER.match(str(min_core)):
                errors.append(f"min_core_version must be valid semver, got: {min_core!r}")

        # --- entry_point: warn if looks like relative path ---
        entry_point = self.data.get("entry_point")
        if entry_point is not None:
            ep_str = str(entry_point)
            if "/" in ep_str or ep_str.startswith("."):
                warnings.append(
                    f"entry_point looks like a relative path ({ep_str!r}); use a dotted module path instead"
                )

        # --- mcp_servers: each must have name and type ---
        mcp_servers = self.data.get("mcp_servers")
        if mcp_servers is not None:
            if not isinstance(mcp_servers, list):
                errors.append("mcp_servers must be a list")
            else:
                for i, server in enumerate(mcp_servers):
                    if not isinstance(server, dict):
                        errors.append(f"mcp_servers[{i}] must be a dict")
                        continue
                    for key in ("name", "type"):
                        if key not in server:
                            errors.append(f"mcp_servers[{i}] missing required key: {key}")

        passed = len(errors) == 0
        return ValidationResult(passed=passed, errors=errors, warnings=warnings)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return dict(self.data)

    @classmethod
    def from_dict(cls, data: dict) -> SkillManifest:
        """Create a SkillManifest from a dict without writing to disk."""
        obj = object.__new__(cls)
        obj.path = Path("<from_dict>")
        obj.data = dict(data)
        return obj

    def to_yaml(self) -> str:
        return yaml.dump(self.data, default_flow_style=False, allow_unicode=True)

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2, ensure_ascii=False)
