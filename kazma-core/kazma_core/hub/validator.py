"""Kazma Hub — skill validator with security scoring."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from kazma_core.hub.manifest_schema import SkillManifest, ValidationResult

# Allowed permission names
_ALLOWED_PERMISSIONS = frozenset(
    {
        "file_read",
        "file_write",
        "network_outbound",
        "network_inbound",
        "camera_access",
        "mqtt_broker",
        "database_read",
        "database_write",
    }
)

# Allowed MCP server types
_ALLOWED_MCP_TYPES = frozenset({"stdio", "sse", "streamable-http"})


def _ok(score: float = 0.0) -> ValidationResult:
    """Shorthand for a passing check with a score delta."""
    return ValidationResult(passed=True, errors=[], warnings=[], score=score)


def _fail(errors: list[str], warnings: list[str] | None = None, score: float = 0.0) -> ValidationResult:
    """Shorthand for a failing check."""
    return ValidationResult(passed=False, errors=errors, warnings=warnings or [], score=score)


class SkillValidator:
    """Validate a skill directory: manifest, entry point, permissions, MCP servers, security."""

    def __init__(self):
        self.checks = [
            self._validate_manifest,
            self._validate_entry_point,
            self._validate_permissions,
            self._validate_mcp_servers,
            self._scan_for_security_issues,
        ]

    async def validate(self, skill_path: Path) -> ValidationResult:
        """Run all checks, aggregate into a single ValidationResult.

        Each check returns a ``ValidationResult`` whose ``score`` field is a
        *delta* (zero or negative).  The base score is 100; deltas are summed
        and clamped to [0, 100].
        """
        errors: list[str] = []
        warnings: list[str] = []
        delta = 0.0

        for check in self.checks:
            result = await check(skill_path)
            errors.extend(result.errors)
            warnings.extend(result.warnings)
            delta += result.score

        score = max(0.0, min(100.0, 100.0 + delta))
        passed = len(errors) == 0
        return ValidationResult(passed=passed, errors=errors, warnings=warnings, score=score)

    # ------------------------------------------------------------------
    # Individual checks (score field = delta, 0 = clean)
    # ------------------------------------------------------------------

    async def _validate_manifest(self, path: Path) -> ValidationResult:
        """Check skill_manifest.yaml exists and is valid."""
        manifest_path = path / "skill_manifest.yaml"

        if not manifest_path.exists():
            return _fail(["Missing skill_manifest.yaml"], score=-30)

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return _fail(["skill_manifest.yaml is not a valid YAML mapping"], score=-30)
        except yaml.YAMLError as exc:
            return _fail([f"Invalid YAML in skill_manifest.yaml: {exc}"], score=-30)

        # Validate via SkillManifest
        try:
            manifest = SkillManifest.from_dict(data)
            vr = manifest.validate()
            if not vr.passed:
                return _fail(vr.errors, vr.warnings, score=-30)
            return _ok(score=0)
        except Exception as exc:
            return _fail([f"Manifest validation error: {exc}"], score=-30)

    async def _validate_entry_point(self, path: Path) -> ValidationResult:
        """If entry_point is declared, ensure the .py file exists."""
        manifest_path = path / "skill_manifest.yaml"
        if not manifest_path.exists():
            return _ok()

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
        except Exception:
            return _ok()

        entry_point = data.get("entry_point") if isinstance(data, dict) else None
        if not entry_point:
            return _ok()

        # Handle 'module:ClassName' format — only check the module part
        module_path = entry_point.split(":")[0] if ":" in entry_point else entry_point
        ep_file = path / f"{module_path}.py"
        if not ep_file.exists():
            return _fail([f"entry_point file not found: {module_path}.py"])

        return _ok()

    async def _validate_permissions(self, path: Path) -> ValidationResult:
        """Check permissions against the known allowlist."""
        manifest_path = path / "skill_manifest.yaml"
        if not manifest_path.exists():
            return _ok()

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
        except Exception:
            return _ok()

        permissions = data.get("permissions") if isinstance(data, dict) else None
        if not permissions or not isinstance(permissions, list):
            return _ok()

        warnings: list[str] = []
        delta = 0.0
        for perm in permissions:
            if perm not in _ALLOWED_PERMISSIONS:
                warnings.append(f"Unknown permission: {perm}")
                delta -= 5.0

        return ValidationResult(passed=True, errors=[], warnings=warnings, score=delta)

    async def _validate_mcp_servers(self, path: Path) -> ValidationResult:
        """Validate MCP server configurations."""
        manifest_path = path / "skill_manifest.yaml"
        if not manifest_path.exists():
            return _ok()

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
        except Exception:
            return _ok()

        mcp_servers = data.get("mcp_servers") if isinstance(data, dict) else None
        if not mcp_servers or not isinstance(mcp_servers, list):
            return _ok()

        errors: list[str] = []
        for i, server in enumerate(mcp_servers):
            if not isinstance(server, dict):
                errors.append(f"mcp_servers[{i}] must be a dict")
                continue
            if "name" not in server:
                errors.append(f"mcp_servers[{i}] missing 'name'")
            if "type" not in server:
                errors.append(f"mcp_servers[{i}] missing 'type'")
            elif server["type"] not in _ALLOWED_MCP_TYPES:
                errors.append(
                    f"mcp_servers[{i}] invalid type: {server['type']!r} "
                    f"(allowed: {', '.join(sorted(_ALLOWED_MCP_TYPES))})"
                )

        if errors:
            return ValidationResult(passed=False, errors=errors, warnings=[], score=0)
        return _ok()

    async def _scan_for_security_issues(self, path: Path) -> ValidationResult:
        """Scan .py files for dangerous patterns."""
        warnings: list[str] = []
        delta = 0.0

        py_files = list(path.glob("**/*.py"))
        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # eval(
            if re.search(r"\beval\s*\(", content):
                delta -= 20.0
                warnings.append(f"{py_file.name}: eval() detected")

            # exec(
            if re.search(r"\bexec\s*\(", content):
                delta -= 20.0
                warnings.append(f"{py_file.name}: exec() detected")

            # __import__  (Unicode-aware word boundary)
            if re.search(r"(?<!\w)__import__(?!\w)", content):
                delta -= 15.0
                warnings.append(f"{py_file.name}: __import__ detected")

            # os.system
            if re.search(r"\bos\.system\s*\(", content):
                delta -= 25.0
                warnings.append(f"{py_file.name}: os.system() detected")

            # Hardcoded secrets patterns
            secret_patterns = [
                r"(?:api_key|api_secret)\s*=\s*['\"]",
                r"(?:password|passwd)\s*=\s*['\"]",
                r"(?:secret|secret_key)\s*=\s*['\"]",
                r"(?:token|access_token)\s*=\s*['\"]",
            ]
            for pat in secret_patterns:
                if re.search(pat, content, re.IGNORECASE):
                    delta -= 10.0
                    warnings.append(f"{py_file.name}: possible hardcoded secret")
                    break  # count once per file

        return ValidationResult(passed=True, errors=[], warnings=warnings, score=delta)
