"""
Security Linter for Kazma Skills.

AST-based static analysis that detects hardcoded secrets, dangerous function
calls, insecure configurations, and policy violations in skill source code,
manifests, and MCP server configurations.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["LintReport", "LintResult", "Rule", "SECURITY_RULES", "SecurityLinter"]


@dataclass(frozen=True)
class Rule:
    """A single security linting rule."""

    id: str
    description: str
    severity: str  # critical | high | medium | low


@dataclass
class LintResult:
    """Result of a single lint check."""

    rule_id: str
    severity: str
    message: str
    file_path: str | None = None
    line_number: int | None = None
    passed: bool = True


@dataclass
class LintReport:
    """Aggregated lint report for a skill."""

    passed: bool = True
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    results: list[LintResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Security rules catalogue — 13 rules across 4 severity levels
# ---------------------------------------------------------------------------

SECURITY_RULES: list[Rule] = [
    # Critical
    Rule("SEC001", "Hardcoded secrets or passwords detected", "critical"),
    Rule("SEC002", "Hardcoded API keys or tokens detected", "critical"),
    # High
    Rule("SEC003", "Use of eval() — potential code injection", "high"),
    Rule("SEC004", "Use of exec() — potential code injection", "high"),
    Rule("SEC005", "Use of os.system() — command injection risk", "high"),
    Rule("SEC006", "subprocess with shell=True — command injection risk", "high"),
    # Medium
    Rule("SEC007", "Unrestricted file access via open()", "medium"),
    Rule("SEC008", "Insecure HTTP URL detected", "medium"),
    Rule("SEC009", "Missing permission declarations in manifest", "medium"),
    Rule("SEC010", "Suspicious entry_point path in manifest", "medium"),
    # Low
    Rule("SEC011", "Overly broad or wildcard permissions", "low"),
    Rule("SEC012", "Deprecated function usage", "low"),
    Rule("SEC013", "Missing input validation", "low"),
]

_SECRET_PATTERNS = re.compile(
    r"(?i)(password|passwd|secret|api_?key|token|auth_?token|access_?key|private_?key)\s*=\s*['\"]",
)
_TOKEN_LITERAL_RE = re.compile(
    r"(?i)(ghp_[a-zA-Z0-9]{36}|sk-[a-zA-Z0-9]{32,}|AKIA[0-9A-Z]{16}|eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+)",
)
_INSECURE_URL_RE = re.compile(r"""(?i)http://[^\s'"'\\]+""")
_DANGEROUS_PATH_RE = re.compile(r"(?i)(\.\./|/etc/passwd|/etc/shadow|/proc/)")
_ENTRY_POINT_RE = re.compile(r"(?i)(rm\s+-rf|wget|curl|nc\s+-|netcat|mkfifo)")
_BROAD_PERM_RE = re.compile(r"(?i)(admin|root|superuser|\*|all)")
_DEPRECATED_FUNCS = {"has_key", "dict.iteritems", "dict.itervalues", "dict.iterkeys", "apply", "execfile", "reduce"}


class SecurityLinter:
    """AST-based security linter for Kazma skills."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lint_skill(self, skill_path: Path) -> LintReport:
        """Orchestrate all lint checks on a skill directory.

        Args:
            skill_path: Root directory of the skill to lint.

        Returns:
            Aggregated :class:`LintReport`.
        """
        results: list[LintResult] = []

        # Lint manifest
        manifest = await self._load_manifest(skill_path)
        if manifest is not None:
            results.extend(await self.lint_manifest(manifest))

        # Lint source code
        results.extend(await self.lint_code(skill_path))

        # Lint MCP servers
        servers: list[dict] = manifest.get("mcp_servers", []) if manifest else []
        results.extend(await self.lint_mcp_servers(servers))

        return self._build_report(results)

    async def lint_manifest(self, manifest: dict) -> list[LintResult]:
        """Check a manifest dict for security issues.

        Args:
            manifest: Parsed manifest (YAML/JSON dict).

        Returns:
            List of :class:`LintResult` items.
        """
        results: list[LintResult] = []

        # SEC001 / SEC002 — secrets in env vars
        env: dict = manifest.get("env", {}) or manifest.get("environment", {})
        for key, value in env.items():
            key_lower = key.lower()
            if any(kw in key_lower for kw in ("password", "passwd", "secret")):
                results.append(
                    LintResult(
                        rule_id="SEC001",
                        severity="critical",
                        message=f"Secret-like env var '{key}' in manifest",
                        file_path="manifest",
                        passed=False,
                    )
                )
            if any(kw in key_lower for kw in ("api_key", "token", "access_key", "auth_token")):
                results.append(
                    LintResult(
                        rule_id="SEC002",
                        severity="critical",
                        message=f"API key/token env var '{key}' in manifest",
                        file_path="manifest",
                        passed=False,
                    )
                )

        # SEC010 — suspicious entry_point
        ep = manifest.get("entry_point", "")
        if ep and _ENTRY_POINT_RE.search(ep):
            results.append(
                LintResult(
                    rule_id="SEC010",
                    severity="medium",
                    message=f"Suspicious entry_point: {ep}",
                    file_path="manifest",
                    passed=False,
                )
            )

        # SEC011 — overly broad permissions
        permissions = manifest.get("permissions", []) or []
        for perm in permissions:
            if isinstance(perm, str) and _BROAD_PERM_RE.search(perm):
                results.append(
                    LintResult(
                        rule_id="SEC011",
                        severity="low",
                        message=f"Overly broad permission: {perm}",
                        file_path="manifest",
                        passed=False,
                    )
                )

        return results

    async def lint_code(self, code_path: Path) -> list[LintResult]:
        """AST-based static analysis on Python source files under *code_path*.

        Checks for eval/exec, os.system, subprocess shell=True, hardcoded
        secrets, unrestricted file access, and deprecated builtins.

        Args:
            code_path: Directory containing Python source files.

        Returns:
            List of :class:`LintResult` items.
        """
        results: list[LintResult] = []
        py_files = list(code_path.rglob("*.py"))

        for py_file in py_files:
            try:
                source = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel = str(py_file.relative_to(code_path)) if code_path in py_file.parents else str(py_file)
            lines = source.splitlines()

            # --- Regex-based quick checks (work on non-parseable files too) ---
            for idx, line in enumerate(lines, start=1):
                # SEC001 — hardcoded password/secret
                if _SECRET_PATTERNS.search(line):
                    results.append(
                        LintResult(
                            rule_id="SEC001",
                            severity="critical",
                            message="Possible hardcoded secret or password",
                            file_path=rel,
                            line_number=idx,
                            passed=False,
                        )
                    )
                # SEC002 — token literal
                if _TOKEN_LITERAL_RE.search(line):
                    results.append(
                        LintResult(
                            rule_id="SEC002",
                            severity="critical",
                            message="Possible hardcoded API token",
                            file_path=rel,
                            line_number=idx,
                            passed=False,
                        )
                    )
                # SEC008 — insecure URL
                if _INSECURE_URL_RE.search(line) and "http://localhost" not in line and "http://127.0.0.1" not in line:
                    results.append(
                        LintResult(
                            rule_id="SEC008",
                            severity="medium",
                            message="Insecure HTTP URL detected",
                            file_path=rel,
                            line_number=idx,
                            passed=False,
                        )
                    )

            # --- AST-based checks ---
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                # SEC003 — eval()
                if isinstance(node.func, ast.Name) and node.func.id == "eval":
                    results.append(
                        LintResult(
                            rule_id="SEC003",
                            severity="high",
                            message="Use of eval()",
                            file_path=rel,
                            line_number=node.lineno,
                            passed=False,
                        )
                    )

                # SEC004 — exec()
                if isinstance(node.func, ast.Name) and node.func.id == "exec":
                    results.append(
                        LintResult(
                            rule_id="SEC004",
                            severity="high",
                            message="Use of exec()",
                            file_path=rel,
                            line_number=node.lineno,
                            passed=False,
                        )
                    )

                # SEC005 — os.system()
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "system"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    results.append(
                        LintResult(
                            rule_id="SEC005",
                            severity="high",
                            message="Use of os.system()",
                            file_path=rel,
                            line_number=node.lineno,
                            passed=False,
                        )
                    )

                # SEC006 — subprocess.run(..., shell=True)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("run", "call", "Popen")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                ):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            results.append(
                                LintResult(
                                    rule_id="SEC006",
                                    severity="high",
                                    message=f"subprocess.{node.func.attr}() with shell=True",
                                    file_path=rel,
                                    line_number=node.lineno,
                                    passed=False,
                                )
                            )

                # SEC007 — open() without restricted mode (heuristic: bare open with no mode or text mode)
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    has_mode = any(kw.arg == "mode" for kw in node.keywords)
                    if not has_mode and len(node.args) < 2:
                        results.append(
                            LintResult(
                                rule_id="SEC007",
                                severity="medium",
                                message="Unrestricted file access via open()",
                                file_path=rel,
                                line_number=node.lineno,
                                passed=False,
                            )
                        )

            # SEC012 — deprecated functions
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in _DEPRECATED_FUNCS:
                    results.append(
                        LintResult(
                            rule_id="SEC012",
                            severity="low",
                            message=f"Deprecated function: {node.id}()",
                            file_path=rel,
                            line_number=getattr(node, "lineno", None),
                            passed=False,
                        )
                    )

        return results

    async def lint_mcp_servers(self, servers: list[dict]) -> list[LintResult]:
        """Validate MCP server configurations for security issues.

        Args:
            servers: List of MCP server config dicts.

        Returns:
            List of :class:`LintResult` items.
        """
        results: list[LintResult] = []

        for idx, server in enumerate(servers):
            name = server.get("name", f"server-{idx}")
            cmd = server.get("command", "") or ""
            args = server.get("args", []) or []
            full_cmd = f"{cmd} {' '.join(str(a) for a in args)}"

            # SEC006 — dangerous commands in server config
            dangerous_patterns = ["rm -rf", "rm -r /", "mkfs", "dd if=", "wget ", "curl ", "nc ", "netcat"]
            for pat in dangerous_patterns:
                if pat in full_cmd.lower():
                    results.append(
                        LintResult(
                            rule_id="SEC006",
                            severity="high",
                            message=f"dangerous command pattern '{pat}' in MCP server '{name}'",
                            file_path="mcp_servers",
                            passed=False,
                        )
                    )

            # SEC010 — suspicious entry_point / command path
            if _DANGEROUS_PATH_RE.search(full_cmd):
                results.append(
                    LintResult(
                        rule_id="SEC010",
                        severity="medium",
                        message=f"Suspicious path in MCP server '{name}' command",
                        file_path="mcp_servers",
                        passed=False,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_manifest(self, skill_path: Path) -> dict | None:
        """Attempt to load a manifest from common locations."""
        import json

        for name in ("skill.yaml", "skill.yml", "skill.json", "manifest.yaml", "manifest.yml", "manifest.json"):
            p = skill_path / name
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8")
                    if name.endswith(".json"):
                        return json.loads(text)
                    # Simple YAML-like parsing for basic key: value pairs
                    return self._simple_yaml(text)
                except (OSError, ValueError):
                    return None
        return None

    @staticmethod
    def _simple_yaml(text: str) -> dict:
        """Minimal YAML-like parser for manifests (top-level scalars and dicts)."""
        result: dict = {}
        current_key: str | None = None
        current_sub: dict = {}
        in_sub = False

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            if indent == 0 and ":" in stripped:
                # flush previous sub-dict
                if current_key and in_sub and current_sub:
                    result[current_key] = current_sub
                    current_sub = {}
                    in_sub = False

                parts = stripped.split(":", 1)
                key = parts[0].strip()
                val = parts[1].strip() if len(parts) > 1 else ""
                if val:
                    result[key] = val
                    current_key = key
                else:
                    current_key = key
            elif indent > 0 and current_key:
                in_sub = True
                if ":" in stripped:
                    sub_parts = stripped.split(":", 1)
                    sub_key = sub_parts[0].strip("- ").strip()
                    sub_val = sub_parts[1].strip().strip('"').strip("'") if len(sub_parts) > 1 else ""
                    current_sub[sub_key] = sub_val

        if current_key and in_sub and current_sub:
            result[current_key] = current_sub

        return result

    @staticmethod
    def _build_report(results: list[LintResult]) -> LintReport:
        """Build an aggregated report from individual results."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for r in results:
            if not r.passed:
                counts[r.severity] = counts.get(r.severity, 0) + 1

        passed = counts["critical"] == 0 and counts["high"] == 0
        return LintReport(
            passed=passed,
            critical=counts["critical"],
            high=counts["high"],
            medium=counts["medium"],
            low=counts["low"],
            results=results,
        )
