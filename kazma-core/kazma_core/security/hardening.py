"""The security report: what an operator can check about the install, measured.

``GET /api/security/hardening`` runs it over the install (the folder Kazma
runs from) in a worker thread. Each check measures the property it names,
from the answer the rest of Kazma uses for that property:

* ``check_no_hardcoded_secrets`` -- credentials written in the install's
  product files (git's list of the project; tests, docs and generated
  folders left out), judged by :mod:`kazma_core.security.secret_scan`.
* ``check_mcp_sandboxing`` -- MCP servers keeping a secret as typed instead
  of in the vault, and the per-task workspace guard.
* ``check_rbac_enforcement`` -- who reaches the API: the boot guard's answer
  (bind, declared proxy, the sign-in switches) and the secret's length.
* ``check_dependency_vulnerabilities`` -- OSV advisories for the versions
  installed (:mod:`kazma_core.security.dependency_scanner`).
* ``check_skill_manifest_validity`` -- built-in skills as the loader loads
  them, agent skills as activation checks them.
* ``check_encrypted_communications`` -- whether traffic from other machines
  arrives over TLS (exposure and ``KAZMA_PUBLIC_URL``).
* ``check_audit_logging`` -- the approval registry is on and readable.
* ``check_permission_escalation`` -- calls in product code that run a shell
  or evaluate code, against the reviewed list below.

Until 2026-10-02 four checks passed on a keyword found anywhere (any YAML
file mentioning "sandbox", any Python file mentioning "permission",
"https" in ``kazma.yaml``, an audit class nothing writes to) and four failed
on things that were not true: 48 "secrets" and 576 "escalation vectors" from
a package cache inside the install, 202 "vulnerable dependencies" from
asking OSV about requirement minimums, and "no skill manifests" because
built-in skills name theirs ``skill_manifest.yaml``. Its ``fix_issues``
wrote a ``.env`` into the install; nothing called it, and it is gone.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kazma_core.security.dependency_scanner import DependencyReport

logger = logging.getLogger(__name__)

__all__ = ["HardeningCheck", "HardeningReport", "SecurityHardeningRunner"]


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

    checks: list[HardeningCheck] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    critical_failures: int = 0
    timestamp: str = ""


#: Folders of a project that hold no product code: tests, examples, docs.
_NOT_PRODUCT_DIRS = frozenset({
    "tests", "test", "testing", "examples", "loadtests", "fixtures", "docs", "benchmarks",
})
_CODE_SUFFIXES = frozenset({".py", ".js", ".sh", ".ps1"})
#: Config files: ``name: value`` holds a value without quotes there. A
#: ``.env`` file is where credentials belong and is never read.
_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf"})

#: Calls in product code that run a shell or evaluate code, reviewed: why
#: each is needed and what keeps it safe. Keyed by (file, function, call).
_REVIEWED_SITES: dict[tuple[str, str, str], str] = {
    ("kazma-core/kazma_core/agent/tool_hooks.py", "_run_command_sync", "kazma_core.security.process_budget.run_bounded(shell=True)"): (
        "an operator's tool hook (agent.hooks.*), a command line the operator wrote and "
        "the agent cannot write (safety.protected_config); it runs without the server's "
        "secrets (tool_child_env)"
    ),
}

#: Calls that always go through a shell or evaluate their argument.
_ALWAYS_RISKY = frozenset({
    "os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput",
    "asyncio.create_subprocess_shell", "eval", "exec",
})
#: subprocess functions that use a shell only when asked (``shell=``).
_SUBPROCESS_SHELL_ARG = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    "kazma_core.security.process_budget.run_bounded",
    "kazma_core.security.process_budget.run_bounded_async",
})


def _is_product(parts: tuple[str, ...]) -> bool:
    """A project file that ships as product: not a test, an example or a doc."""
    if any(part in _NOT_PRODUCT_DIRS or part.endswith("_tests") for part in parts[:-1]):
        return False
    name = parts[-1]
    return not (
        name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".spec.js"))
        or name == "conftest.py"
    )


def _is_config(path: Path) -> bool:
    return path.suffix.lower() in _CONFIG_SUFFIXES or path.name.lower().endswith(".env.example")


def _reviewed_note(report: DependencyReport) -> str:
    """``" (2 reviewed as out of reach: chromadb GHSA-...: why)"``, or ``""``."""
    from kazma_core.security.dependency_scanner import review_of

    notes = []
    for vuln in report.vulnerabilities:
        review = review_of(vuln)
        if review is not None:
            notes.append(f"{vuln.package} {vuln.vuln_id}: {review.why}")
    if not notes:
        return ""
    return f" ({len(notes)} reviewed as out of Kazma's reach: " + "; ".join(notes) + ")"


def _upgrade_note(package: str, fixed_in: str | None, unfixed: int) -> str:
    """``pyjwt 2.13.0 (fixed in 2.15.0)``, saying what an upgrade leaves open."""
    if fixed_in is None:
        return f"{package} (no fixed release yet)"
    if unfixed:
        return f"{package} (fixed in {fixed_in}; {unfixed} advisory without a fixed release)"
    return f"{package} (fixed in {fixed_in})"


def _qualified_names(tree: ast.AST) -> dict[str, str]:
    """Local name -> what it was imported as (``sp`` -> ``subprocess``)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, names)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _call_label(call: ast.Call, names: dict[str, str]) -> str:
    """How *call* runs a shell or evaluates code; ``""`` when it does neither."""
    target = _dotted(call.func, names)
    if isinstance(call.func, ast.Attribute) and call.func.attr == "subprocess_shell":
        return "loop.subprocess_shell"
    if target in _ALWAYS_RISKY:
        return target
    if target in _SUBPROCESS_SHELL_ARG:
        for kw in call.keywords:
            if kw.arg != "shell":
                continue
            if isinstance(kw.value, ast.Constant):
                return f"{target}(shell=True)" if kw.value.value else ""
            return f"{target}(shell=<decided at run time>)"
    return ""


def _risky_calls(source: str) -> list[tuple[int, str, str]]:
    """``(line, function, call)`` for every shell or eval call in *source*."""
    tree = ast.parse(source)
    names = _qualified_names(tree)
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                label = _call_label(child, names)
                if label:
                    found.append((child.lineno, function, label))
            visit(child, function)

    visit(tree, "<module>")
    return found


class SecurityHardeningRunner:
    """Runs the security checks against a Kazma install.

    Each check returns a :class:`HardeningCheck`; the runner aggregates them
    into a :class:`HardeningReport`. A check that cannot run is reported as
    failed with its reason; it never hides the others.
    """

    CHECKS: list[str] = [
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
        """Initialise the runner.

        Args:
            project_root: The install's folder. Defaults to ``Path.cwd()``.
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.results: list[HardeningCheck] = []
        self._files: list[Path] | None = None
        self._files_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_all_checks(self) -> HardeningReport:
        """Run every check and aggregate the results.

        The checks run together, each in a worker thread (they read files
        and stores); the dependency check waits on OSV meanwhile.
        """
        self.results = list(await asyncio.gather(*(self._run_safely(name) for name in self.CHECKS)))
        failed = [c for c in self.results if not c.passed]
        return HardeningReport(
            checks=self.results,
            total=len(self.results),
            passed=sum(1 for c in self.results if c.passed),
            failed=len(failed),
            critical_failures=sum(1 for c in failed if c.severity == "critical"),
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def run_check(self, check_name: str) -> HardeningCheck:
        """Run a single check by name.

        Raises:
            ValueError: If the check name is not recognised.
        """
        if check_name not in self.CHECKS:
            raise ValueError(f"Unknown check: {check_name}")
        return await getattr(self, check_name)()

    async def _run_safely(self, check_name: str) -> HardeningCheck:
        try:
            return await self.run_check(check_name)
        except Exception as exc:  # one broken check must not hide the other seven
            logger.warning("[security] check %s could not run", check_name, exc_info=True)
            return HardeningCheck(
                name=check_name,
                passed=False,
                severity="high",
                message=f"This check could not run: {type(exc).__name__}: {exc}",
                recommendation="Read the server log for the traceback.",
            )

    def generate_report(self) -> str:
        """The last run's results as Markdown."""
        lines = [
            "# Security Hardening Report",
            "",
            f"**Project:** `{self.project_root}`",
            f"**Timestamp:** {datetime.now(UTC).isoformat()}",
            "",
            "## Summary",
            "",
        ]
        if not self.results:
            lines.extend(["*No checks have been run yet.*", ""])
            return "\n".join(lines)

        passed = sum(1 for c in self.results if c.passed)
        crit = sum(1 for c in self.results if not c.passed and c.severity == "critical")
        lines.extend([
            f"- **Total checks:** {len(self.results)}",
            f"- **Passed:** {passed}",
            f"- **Failed:** {len(self.results) - passed}",
            f"- **Critical failures:** {crit}",
            "",
            "## Check Results",
            "",
        ])
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

    # ------------------------------------------------------------------
    # The install's files
    # ------------------------------------------------------------------

    def _product_files(self) -> list[Path]:
        """The install's product files the scans read (cached per run)."""
        with self._files_lock:
            return self._list_product_files()

    def _list_product_files(self) -> list[Path]:
        if self._files is None:
            from kazma_core.workspace.project_files import iter_project_files

            wanted = _CODE_SUFFIXES | _CONFIG_SUFFIXES
            files = []
            for path in iter_project_files(self.project_root):
                if path.suffix.lower() not in wanted and not _is_config(path):
                    continue
                try:
                    parts = path.relative_to(self.project_root).parts
                except ValueError:
                    continue
                if _is_product(parts):
                    files.append(path)
            self._files = files
        return self._files

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.project_root).as_posix()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def check_no_hardcoded_secrets(self) -> HardeningCheck:
        """Credentials written in the install's product files."""
        return await asyncio.to_thread(self._secrets)

    def _secrets(self) -> HardeningCheck:
        from kazma_core.security.secret_scan import find_credentials

        files = self._product_files()
        findings: list[str] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for found in find_credentials(text, bare_values=_is_config(path)):
                findings.append(f"{self._rel(path)}:{found.line} ({found.kind})")

        if findings:
            return HardeningCheck(
                name="check_no_hardcoded_secrets",
                passed=False,
                severity="critical",
                message=f"Found {len(findings)} credential(s) written in the install's files",
                recommendation=(
                    "Move each into Settings (the vault) or .env, then rotate it: whoever "
                    "has read the file has it. Locations: " + ", ".join(findings[:10])
                ),
            )
        return HardeningCheck(
            name="check_no_hardcoded_secrets",
            passed=True,
            severity="critical",
            message=f"No credential written in the install's {len(files)} product files",
            recommendation="Keep credentials in Settings (the vault) or .env",
        )

    async def check_mcp_sandboxing(self) -> HardeningCheck:
        """MCP servers' secrets in the vault, and the per-task workspace guard."""
        return await asyncio.to_thread(self._mcp)

    def _mcp(self) -> HardeningCheck:
        from kazma_core.mcp.manager import mcp_scope_guard_enabled
        from kazma_core.mcp_servers_store import list_mcp_servers, servers_with_plaintext_secrets

        yaml_path = self.project_root / "kazma.yaml"
        servers = list_mcp_servers(yaml_path=yaml_path)
        holders = servers_with_plaintext_secrets(yaml_path=yaml_path)
        problems: list[str] = []
        if holders:
            problems.append(
                f"{len(holders)} MCP server(s) keep a secret as typed instead of in the vault: "
                + ", ".join(holders)
            )
        if not mcp_scope_guard_enabled():
            problems.append(
                "KAZMA_MCP_SCOPE_GUARD=0: a task aimed at another workspace reaches the MCP "
                "servers bound to the active one"
            )
        if problems:
            return HardeningCheck(
                name="check_mcp_sandboxing",
                passed=False,
                severity="high",
                message="; ".join(problems),
                recommendation=(
                    "Kazma moves MCP secrets into the vault when it starts; a secret still "
                    "typed means no vault was available (KAZMA_VAULT_KEY). Unset "
                    "KAZMA_MCP_SCOPE_GUARD."
                ),
            )
        return HardeningCheck(
            name="check_mcp_sandboxing",
            passed=True,
            severity="high",
            message=(
                f"{len(servers)} MCP server(s): every secret is a vault pointer, each server "
                "starts with an allowlisted environment, and the per-task workspace guard is on"
            ),
            recommendation="Add MCP servers through Settings, which stores their secrets in the vault",
        )

    async def check_rbac_enforcement(self) -> HardeningCheck:
        """Who reaches the API: exposure, the sign-in switches, the secret."""
        return await asyncio.to_thread(self._access)

    def _access(self) -> HardeningCheck:
        from kazma_core.security.boot_guard import (
            WEAK_SECRET_CHARS,
            check_exposure_posture,
            env_flag,
            exposure,
        )

        host = os.environ.get("KAZMA_HOST", "127.0.0.1").strip() or "127.0.0.1"
        reached = exposure(host)
        ok, warning = check_exposure_posture(host)
        if not ok:
            switch = "KAZMA_AUTH_DISABLED" if env_flag("KAZMA_AUTH_DISABLED") else "KAZMA_DEV_WS_BYPASS"
            return HardeningCheck(
                name="check_rbac_enforcement",
                passed=False,
                severity="critical",
                message=f"Sign-in is switched off ({switch}) while Kazma is reachable through {reached}",
                recommendation=f"Unset {switch}: Kazma refuses to start this way, and the next restart will fail.",
            )
        if warning:
            return HardeningCheck(
                name="check_rbac_enforcement",
                passed=False,
                severity="high",
                message=f"KAZMA_DEMO_MODE: sign-in is off, and Kazma is reachable through {reached}",
                recommendation="Demo mode is for a throwaway instance with no real data, keys or vault.",
            )
        secret = os.environ.get("KAZMA_SECRET", "").strip()
        if secret and len(secret) < WEAK_SECRET_CHARS:
            return HardeningCheck(
                name="check_rbac_enforcement",
                passed=False,
                severity="medium",
                message=f"KAZMA_SECRET is {len(secret)} characters long",
                recommendation=(
                    f"Use at least {WEAK_SECRET_CHARS} random characters: the sign-in throttle "
                    "limits each address, not every address at once."
                ),
            )
        if env_flag("KAZMA_AUTH_DISABLED"):
            message = "Sign-in is off (KAZMA_AUTH_DISABLED); Kazma is reachable from this machine only"
        elif reached:
            message = f"Every API request from another machine needs a credential (reached through {reached})"
        else:
            message = "Kazma is reachable from this machine only; other machines need a credential if it is exposed"
        return HardeningCheck(
            name="check_rbac_enforcement",
            passed=True,
            severity="high",
            message=message,
            recommendation="Declare any reverse proxy in KAZMA_TRUSTED_PROXIES",
        )

    async def check_dependency_vulnerabilities(self) -> HardeningCheck:
        """OSV advisories for the installed versions, and this build's own minimums."""
        from kazma_core.install_requirements import unmet_requirements
        from kazma_core.security.dependency_scanner import DependencyQueryError, audit_installed

        unmet = await asyncio.to_thread(unmet_requirements, self.project_root)
        behind = (
            f"{len(unmet)} installed package(s) are older than this build requires: "
            + "; ".join(u.describe() for u in unmet) + ". "
        ) if unmet else ""
        update = "Run `kazma update` on the server: it installs this build's minimums. "
        try:
            report = await audit_installed()
        except DependencyQueryError as exc:
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=False,
                severity="high" if unmet else "medium",
                message=f"The installed packages could not be checked against OSV: {exc}",
                recommendation=behind + (update if unmet else "")
                + "Run the report again when api.osv.dev is reachable.",
            )
        reviewed = _reviewed_note(report)
        open_vulns = report.open_vulnerabilities
        if open_vulns:
            upgrades = report.upgrades()
            packages = report.vulnerable_packages
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=False,
                severity="critical",
                message=(
                    f"{len(packages)} of {report.total} installed packages have "
                    f"{len(open_vulns)} known advisories" + reviewed
                ),
                recommendation=behind + update + "Fixed releases: " + ", ".join(
                    _upgrade_note(pkg, *upgrades[pkg]) for pkg in packages
                ),
            )
        if unmet:
            return HardeningCheck(
                name="check_dependency_vulnerabilities",
                passed=False,
                severity="high",
                message=behind.strip() + reviewed,
                recommendation=update.strip(),
            )
        return HardeningCheck(
            name="check_dependency_vulnerabilities",
            passed=True,
            severity="critical",
            message=(
                f"No open advisory affects the {report.total} installed packages, and they "
                f"meet this build's minimums" + reviewed
            ),
            recommendation="Run the report after each upgrade",
        )

    async def check_skill_manifest_validity(self) -> HardeningCheck:
        """Built-in skills as the loader loads them; agent skills as activation checks them."""
        return await asyncio.to_thread(self._skills)

    def _skills(self) -> HardeningCheck:
        from kazma_skills.native_loader import NativeSkillLoader

        from kazma_core.agent_skills.catalog import activation_integrity
        from kazma_core.agent_skills.discovery import discover_skills, skill_files
        from kazma_core.agent_skills.parser import parse_skill_md

        loader = NativeSkillLoader(registry=None)
        native = loader.skill_dirs()
        problems = [f"{name}: {why}" for name, whys in loader.problems().items() for why in whys]

        for _scope, skill_md in skill_files(project_root=self.project_root):
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{skill_md}: cannot be read ({exc})")
                continue
            if parse_skill_md(text, path=skill_md, directory_name=skill_md.parent.name) is None:
                problems.append(f"{skill_md}: not a usable SKILL.md (frontmatter, description or name)")

        skills = discover_skills(project_root=self.project_root, include_disabled=True)
        unsigned = 0
        for skill in skills.values():
            verdict = activation_integrity(skill, warn=False)
            if not verdict.ok:
                problems.append(f"agent skill {skill.name}: refused at activation ({verdict.reason})")
            elif not verdict.signed:
                unsigned += 1

        if problems:
            return HardeningCheck(
                name="check_skill_manifest_validity",
                passed=False,
                severity="medium",
                message=f"{len(problems)} skill problem(s)",
                recommendation="Fix or reinstall: " + "; ".join(problems[:8]),
            )
        return HardeningCheck(
            name="check_skill_manifest_validity",
            passed=True,
            severity="medium",
            message=(
                f"All {len(native)} built-in skills load; {len(skills)} agent skill(s) pass the "
                f"activation check" + (f" ({unsigned} unsigned: they load with a warning)" if unsigned else "")
            ),
            recommendation="Install agent skills through Kazma so they are signed",
        )

    async def check_encrypted_communications(self) -> HardeningCheck:
        """Whether traffic from other machines reaches Kazma over TLS."""
        return await asyncio.to_thread(self._tls)

    def _tls(self) -> HardeningCheck:
        from kazma_core.security.boot_guard import exposure, is_loopback

        host = os.environ.get("KAZMA_HOST", "127.0.0.1").strip() or "127.0.0.1"
        reached = exposure(host)
        public = os.environ.get("KAZMA_PUBLIC_URL", "").strip()
        if not reached:
            return HardeningCheck(
                name="check_encrypted_communications",
                passed=True,
                severity="high",
                message="Kazma is reachable from this machine only: nothing it serves crosses a network",
                recommendation="If you expose it, put a TLS proxy in front and set KAZMA_PUBLIC_URL",
            )
        if public.lower().startswith("https://"):
            return HardeningCheck(
                name="check_encrypted_communications",
                passed=True,
                severity="high",
                message=f"Other machines reach Kazma over TLS, ended in front of it ({public})",
                recommendation="Keep the plain-HTTP port bound to loopback or a private network",
            )
        direct = not is_loopback(host)
        return HardeningCheck(
            name="check_encrypted_communications",
            passed=False,
            severity="high" if direct else "medium",
            message=(
                f"Kazma serves plain HTTP and is reachable through {reached}; "
                + ("KAZMA_PUBLIC_URL is not an https address" if public
                   else "no https address is declared (KAZMA_PUBLIC_URL)")
            ),
            recommendation=(
                "Put Kazma behind a proxy that serves TLS (Caddy, nginx, a Cloudflare tunnel) "
                "and set KAZMA_PUBLIC_URL to its https address."
            ),
        )

    async def check_audit_logging(self) -> HardeningCheck:
        """The approval registry, where each decision is recorded, is on and readable."""
        return await asyncio.to_thread(self._audit)

    def _audit(self) -> HardeningCheck:
        from kazma_core.safety import hitl_gates

        if not hitl_gates.gate_registry_enabled():
            return HardeningCheck(
                name="check_audit_logging",
                passed=False,
                severity="high",
                message="Approval decisions are not recorded (KAZMA_GATE_REGISTRY=0)",
                recommendation="Unset KAZMA_GATE_REGISTRY: the registry records who approved what, and when.",
            )
        try:
            decisions = hitl_gates.recorded_decision_count()
        except sqlite3.Error as exc:
            return HardeningCheck(
                name="check_audit_logging",
                passed=False,
                severity="high",
                message=f"The approval registry cannot be read: {exc}",
                recommendation="Check hitl_gates.db in the data folder (disk space, permissions).",
            )
        return HardeningCheck(
            name="check_audit_logging",
            passed=True,
            severity="high",
            message=f"Approval decisions are recorded ({decisions} in hitl_gates.db)",
            recommendation="Keep KAZMA_GATE_REGISTRY on",
        )

    async def check_permission_escalation(self) -> HardeningCheck:
        """Calls in product code that run a shell or evaluate code."""
        return await asyncio.to_thread(self._escalation)

    def _escalation(self) -> HardeningCheck:
        unreviewed: list[str] = []
        reviewed: list[str] = []
        files = [p for p in self._product_files() if p.suffix == ".py"]
        for path in files:
            rel = self._rel(path)
            try:
                calls = _risky_calls(path.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, SyntaxError, ValueError, RecursionError):
                continue
            for line, function, label in calls:
                reason = _REVIEWED_SITES.get((rel, function, label))
                if reason:
                    reviewed.append(f"{rel} {function}: {reason}")
                else:
                    unreviewed.append(f"{rel}:{line} {label} in {function}")

        if unreviewed:
            return HardeningCheck(
                name="check_permission_escalation",
                passed=False,
                severity="critical",
                message=f"Found {len(unreviewed)} unreviewed call(s) that run a shell or evaluate code",
                recommendation=(
                    "Pass an argument list with no shell, or review the call and add it to "
                    "_REVIEWED_SITES in security/hardening.py with why it is safe: "
                    + ", ".join(unreviewed[:10])
                ),
            )
        return HardeningCheck(
            name="check_permission_escalation",
            passed=True,
            severity="critical",
            message=(
                f"No unreviewed shell or eval call in {len(files)} product files"
                + (f" ({len(reviewed)} reviewed: {'; '.join(reviewed)})" if reviewed else "")
            ),
            recommendation="Run programs with an argument list, never through a shell",
        )
