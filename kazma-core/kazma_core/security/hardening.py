"""
Security Hardening Runner for Kazma.

Runs a suite of security hardening checks against a Kazma project to
verify that best practices are followed across secrets management,
sandboxing, RBAC, dependency health, skill manifests, encryption,
audit logging, and privilege escalation prevention.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# Regex patterns that suggest hardcoded secrets
_SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"""(?:api[_-]?key|apikey)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
    re.compile(r"""(?:secret|secret[_-]?key)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
    re.compile(r"""(?:token|access[_-]?token|auth[_-]?token)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
    re.compile(r"""(?:password|passwd|pwd)\s*[=:]\s*['"][^'"]{8,}['"]""", re.I),
    re.compile(r"""(?:AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID)\s*[=:]\s*['"][A-Za-z0-9/+]{20,}['"]""", re.I),
    re.compile(r"""(?:PRIVATE[_\s]?KEY)\s*[=:]\s*['"]""", re.I),
]


@dataclass
class HardeningCheck:
    """Result of a single hardening check."""

    name: str
    passed: bool
    severity: str  # critical, high, medium, low, info
    message: str
    recommendation: str


@dataclass
class HardeningReport:
    """Aggregated report from all hardening checks."""

    checks: List[HardeningCheck] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    critical_failures: int = 0
    timestamp: str = ""


class SecurityHardeningRunner:
    """Runs all security hardening checks against a Kazma project.

    Each check inspects a specific security concern and returns a
    :class:`HardeningCheck` indicating pass/fail with a severity and
    recommendation.  The runner aggregates results into a
    :class:`HardeningReport`.
    """

    CHECKS: List[str] = [
        "check_no_hardcoded_secrets",
        "check_mcp_sandboxing",
        "check_rbac_enforcement",
        "check_dependency_vulnerabilities",
        "check_skill_manifest_validity",
        "check_encrypted_communications",
        "check_audit_logging",
        "check_permission_escalation",
    ]

    def __init__(self, project_root: str | Path | None = None) -> None:
        """Initialise the hardening runner.

        Args:
            project_root: Root directory of the Kazma project.
                          Defaults to ``Path.cwd()``.
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.results: List[HardeningCheck] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_all_checks(self) -> HardeningReport:
        """Run all 8 hardening checks and return an aggregated report.

        Returns:
            :class:`HardeningReport` with per-check results and summary.
        """
        self.results = []

        for check_name in self.CHECKS:
            result = await self.run_check(check_name)
            self.results.append(result)

        failed = [c for c in self.results if not c.passed]
        return HardeningReport(
            checks=self.results,
            total=len(self.results),
            passed=sum(1 for c in self.results if c.passed),
            failed=len(failed),
            critical_failures=sum(1 for c in failed if c.severity == "critical"),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def run_check(self, check_name: str) -> HardeningCheck:
        """Run a single hardening check by name.

        Args:
            check_name: Name of the check method to invoke.

        Returns:
            :class:`HardeningCheck` with the result.

        Raises:
            ValueError: If the check name is not recognised.
        """
        method = getattr(self, check_name, None)
        if method is None or check_name.startswith("_"):
            raise ValueError(f"Unknown check: {check_name}")
        return await method()

    def generate_report(self) -> str:
        """Generate a human-readable markdown report.

        Returns:
            Markdown-formatted report string.
        """
        lines = [
            "# Security Hardening Report",
            "",
            f"**Project:** `{self.project_root}`",
            f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Summary",
            "",
        ]

        if self.results:
            passed = sum(1 for c in self.results if c.passed)
            failed = len(self.results) - passed
            crit = sum(1 for c in self.results if not c.passed and c.severity == "critical")
            lines.extend([
                f"- **Total checks:** {len(self.results)}",
                f"- **Passed:** {passed}",
                f"- **Failed:** {failed}",
                f"- **Critical failures:** {crit}",
                "",
            ])
        else:
            lines.append("*No checks have been run yet.*")
            lines.append("")
            return "\n".join(lines)

        lines.append("## Check Results")
        lines.append("")

        for check in self.results:
            status = "✅ PASS" if check.passed else "❌ FAIL"
            lines.extend([
                f"### {check.name} — {status}",
                "",
                f"**Severity:** {check.severity}",
                f"**Message:** {check.message}",
                f"**Recommendation:** {check.recommendation}",
                "",
            ])

        return "\n".join(lines)

    async def fix_issues(self, auto_fix: bool = False) -> list:
        """Attempt to auto-fix issues where possible.

        Currently only a few checks support auto-fix.  Others return
        an empty fix list.

        Args:
            auto_fix: If ``True``, actually apply fixes.  If ``False``,
                      return the list of *would-be* fixes without
                      modifying anything.

        Returns:
            List of dicts describing applied or proposed fixes.
        """
        fixes: list = []

        for check in self.results:
            if check.passed:
                continue

            if check.name == "check_no_hardcoded_secrets":
                fix = {
                    "check": check.name,
                    "action": "Create .env file and add to .gitignore",
                    "applied": False,
                }
                if auto_fix:
                    env_file = self.project_root / ".env"
                    gitignore = self.project_root / ".gitignore"
                    try:
                        if not env_file.exists():
                            env_file.write_text(
                                "# Move hardcoded secrets here\n"
                                "# API_KEY=\n# SECRET_KEY=\n",
                                encoding="utf-8",
                            )
                        if gitignore.exists():
                            content = gitignore.read_text(encoding="utf-8")
                            if ".env" not in content:
                                gitignore.write_text(
                                    content.rstrip() + "\n.env\n",
                                    encoding="utf-8",
                                )
                        else:
                            gitignore.write_text(".env\n", encoding="utf-8")
                        fix["applied"] = True
                    except OSError:
                        fix["action"] = "Failed to create .env — manual intervention needed"
                fixes.append(fix)

            elif check.name == "check_skill_manifest_validity":
                fixes.append({
                    "check": check.name,
                    "action": "Manually validate and fix skill manifest files under skills/",
                    "applied": False,
                })

            else:
                fixes.append({
                    "check": check.name,
                    "action": f"Manual review required for {check.name}",
                    "applied": False,
                })

        return fixes

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def check_no_hardcoded_secrets(self) -> HardeningCheck:
        """Scan .py files for hardcoded API keys, tokens, and passwords."""
        findings: list[str] = []

        for py_file in self.project_root.rglob("*.py"):
            # Skip virtualenvs and cache dirs
            parts = py_file.parts
            if any(d in parts for d in (".venv", "venv", "__pycache__", ".git", "node_modules")):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"{py_file.relative_to(self.project_root)}:{i}")
                        break

        if findings:
            return HardeningCheck(
                name="check_no_hardcoded_secrets",
                passed=False,
                severity="critical",
                message=f"Found {len(findings)} potential hardcoded secrets in Python files",
                recommendation=(
                    "Move secrets to environment variables or a .env file. "
                    "Affected locations: " + ", ".join(findings[:10])
                ),
            )
        return HardeningCheck(
            name="check_no_hardcoded_secrets",
            passed=True,
            severity="critical",
            message="No hardcoded secrets detected in Python source files",
            recommendation="Continue using environment variables for all secrets",
        )

    async def check_mcp_sandboxing(self) -> HardeningCheck:
        """Verify that MCP server sandbox configuration exists."""
        sandbox_paths = [
            self.project_root / "sandbox.yaml",
            self.project_root / "kazma.yaml",
            self.project_root / "config" / "sandbox.yaml",
            self.project_root / "kazma-core" / "kazma.yaml",
        ]

        # Also scan for sandbox key in any YAML config
        found = any(p.exists() for p in sandbox_paths)
        if not found:
            # Check if any YAML file contains sandbox config
            for yaml_file in self.project_root.rglob("*.yaml"):
                try:
                    content = yaml_file.read_text(encoding="utf-8", errors="ignore")
                    if "sandbox" in content.lower():
                        found = True
                        break
                except OSError:
                    continue

        if found:
            return HardeningCheck(
                name="check_mcp_sandboxing",
                passed=True,
                severity="high",
                message="MCP sandbox configuration found",
                recommendation="Review sandbox config regularly for restrictive policies",
            )
        return HardeningCheck(
            name="check_mcp_sandboxing",
            passed=False,
            severity="high",
            message="No MCP sandbox configuration detected",
            recommendation=(
                "Create a sandbox.yaml defining resource limits, filesystem "
                "access restrictions, and network policies for MCP servers"
            ),
        )

    async def check_rbac_enforcement(self) -> HardeningCheck:
        """Check that RBAC middleware is configured."""
        rbac_indicators = ["rbac", "role_based", "permission", "authorization"]
        found = False

        for py_file in self.project_root.rglob("*.py"):
            parts = py_file.parts
            if any(d in parts for d in (".venv", "venv", "__pycache__", ".git", "node_modules")):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            for indicator in rbac_indicators:
                if indicator in content:
                    found = True
                    break
            if found:
                break

        if found:
            return HardeningCheck(
                name="check_rbac_enforcement",
                passed=True,
                severity="high",
                message="RBAC-related configuration detected in project",
                recommendation="Ensure all API endpoints enforce role-based access control",
            )
        return HardeningCheck(
            name="check_rbac_enforcement",
            passed=False,
            severity="high",
            message="No RBAC enforcement configuration detected",
            recommendation=(
                "Implement role-based access control middleware to restrict "
                "API endpoint access based on user roles"
            ),
        )

    async def check_dependency_vulnerabilities(self) -> HardeningCheck:
        """Run DependencyScanner on project dependencies."""
        try:
            from .dependency_scanner import DependencyScanner
            scanner = DependencyScanner()
            report = await scanner.scan(self.project_root)
            if report.vulnerable_deps > 0:
                return HardeningCheck(
                    name="check_dependency_vulnerabilities",
                    passed=False,
                    severity="critical",
                    message=f"Found {report.vulnerable_deps} vulnerable dependencies",
                    recommendation=(
                        f"Update vulnerable packages. Found {report.vulnerable_deps} "
                        f"issues across {report.total_deps} dependencies"
                    ),
                )
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=True,
                severity="critical",
                message=f"No known vulnerabilities in {report.total_deps} dependencies",
                recommendation="Continue monitoring dependencies with regular scans",
            )
        except ImportError:
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=False,
                severity="critical",
                message="DependencyScanner module not available",
                recommendation="Ensure dependency_scanner module is importable",
            )
        except Exception as exc:
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=False,
                severity="medium",
                message=f"Dependency scan failed: {exc}",
                recommendation="Verify dependency files are well-formed and network is available",
            )

    async def check_skill_manifest_validity(self) -> HardeningCheck:
        """Validate installed skill manifests."""
        skills_dir = self.project_root / "skills"
        if not skills_dir.exists():
            # Try common alternative locations
            alt_paths = [
                self.project_root / "kazma-core" / "skills",
                Path.home() / ".kazma" / "skills",
            ]
            for alt in alt_paths:
                if alt.exists():
                    skills_dir = alt
                    break

        if not skills_dir.exists():
            return HardeningCheck(
                name="check_skill_manifest_validity",
                passed=False,
                severity="medium",
                message="No skills directory found",
                recommendation="Create a skills directory with valid manifest files",
            )

        invalid: list[str] = []
        total = 0
        for manifest in skills_dir.rglob("manifest.yaml"):
            total += 1
            try:
                content = manifest.read_text(encoding="utf-8")
                # Basic validation: must contain at least name and version
                if "name:" not in content or "version:" not in content:
                    invalid.append(str(manifest.relative_to(skills_dir)))
            except OSError:
                invalid.append(str(manifest.relative_to(skills_dir)))

        for manifest in skills_dir.rglob("manifest.yml"):
            total += 1
            try:
                content = manifest.read_text(encoding="utf-8")
                if "name:" not in content or "version:" not in content:
                    invalid.append(str(manifest.relative_to(skills_dir)))
            except OSError:
                invalid.append(str(manifest.relative_to(skills_dir)))

        if invalid:
            return HardeningCheck(
                name="check_skill_manifest_validity",
                passed=False,
                severity="medium",
                message=f"{len(invalid)}/{total} skill manifests are invalid",
                recommendation="Fix manifests: " + ", ".join(invalid[:5]),
            )
        if total == 0:
            return HardeningCheck(
                name="check_skill_manifest_validity",
                passed=False,
                severity="low",
                message="No skill manifests found to validate",
                recommendation="Install skills with valid manifest.yaml files",
            )
        return HardeningCheck(
            name="check_skill_manifest_validity",
            passed=True,
            severity="medium",
            message=f"All {total} skill manifests are valid",
            recommendation="Continue validating manifests on skill installation",
        )

    async def check_encrypted_communications(self) -> HardeningCheck:
        """Verify TLS/mTLS configuration."""
        config_files = [
            self.project_root / "kazma.yaml",
            self.project_root / "config" / "tls.yaml",
            self.project_root / "kazma-core" / "kazma.yaml",
        ]

        tls_found = False
        for cfg in config_files:
            if cfg.exists():
                try:
                    content = cfg.read_text(encoding="utf-8", errors="ignore").lower()
                    if any(term in content for term in ("tls", "ssl", "https", "mtls", "certificate")):
                        tls_found = True
                        break
                except OSError:
                    continue

        if tls_found:
            return HardeningCheck(
                name="check_encrypted_communications",
                passed=True,
                severity="high",
                message="TLS/encrypted communications configuration detected",
                recommendation="Ensure TLS 1.3+ is enforced and certificates are valid",
            )
        return HardeningCheck(
            name="check_encrypted_communications",
            passed=False,
            severity="high",
            message="No TLS/mTLS configuration detected",
            recommendation=(
                "Configure TLS for all external communications. "
                "Use mTLS for inter-service communication where possible"
            ),
        )

    async def check_audit_logging(self) -> HardeningCheck:
        """Verify audit trail is configured and functional."""
        try:
            from .audit_trail import SecurityAuditTrail
            trail = SecurityAuditTrail()
            # Verify the module is functional by checking it has expected methods
            assert hasattr(trail, "log_event")
            assert hasattr(trail, "get_events")
            return HardeningCheck(
                name="check_audit_logging",
                passed=True,
                severity="high",
                message="Security audit trail module is available and initialised",
                recommendation="Review audit logs regularly for suspicious activity",
            )
        except ImportError:
            return HardeningCheck(
                name="check_audit_logging",
                passed=False,
                severity="high",
                message="SecurityAuditTrail module not available",
                recommendation="Ensure audit_trail module is importable",
            )
        except Exception as exc:
            return HardeningCheck(
                name="check_audit_logging",
                passed=False,
                severity="high",
                message=f"Audit trail initialisation failed: {exc}",
                recommendation="Verify SQLite is available and the data directory is writable",
            )

    async def check_permission_escalation(self) -> HardeningCheck:
        """Check for privilege escalation vectors in delegation."""
        escalation_patterns = [
            re.compile(r"os\.system\s*\(", re.I),
            re.compile(r"subprocess\.(run|call|Popen)\s*\([^)]*shell\s*=\s*True", re.I),
            re.compile(r"eval\s*\(", re.I),
            re.compile(r"exec\s*\(", re.I),
            re.compile(r"__import__\s*\(", re.I),
            re.compile(r"setattr\s*\([^)]*__", re.I),
        ]

        findings: list[str] = []
        for py_file in self.project_root.rglob("*.py"):
            parts = py_file.parts
            if any(d in parts for d in (".venv", "venv", "__pycache__", ".git", "node_modules")):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for pattern in escalation_patterns:
                    if pattern.search(line):
                        findings.append(f"{py_file.relative_to(self.project_root)}:{i}")
                        break

        if findings:
            return HardeningCheck(
                name="check_permission_escalation",
                passed=False,
                severity="critical",
                message=f"Found {len(findings)} potential privilege escalation vectors",
                recommendation=(
                    "Review and restrict use of eval/exec/os.system. "
                    "Affected locations: " + ", ".join(findings[:10])
                ),
            )
        return HardeningCheck(
            name="check_permission_escalation",
            passed=True,
            severity="critical",
            message="No obvious privilege escalation vectors detected",
            recommendation="Continue reviewing code for unsafe dynamic execution patterns",
        )
