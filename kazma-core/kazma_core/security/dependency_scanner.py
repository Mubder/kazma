"""
Dependency Vulnerability Scanner for Kazma Skills.

Queries the OSV (Open Source Vulnerabilities) API to check skill
dependencies for known CVEs and security advisories.  Includes the
original ``DependencyScanner`` (single-source OSV) and the newer
``DependabotStyleScanner`` (multi-source: OSV, GitHub Advisories, NVD).
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


OSV_API_URL = "https://api.osv.dev/v1/query"


@dataclass
class Vulnerability:
    """A single known vulnerability."""

    package: str
    version: str
    vuln_id: str
    severity: str
    description: str
    fixed_version: Optional[str] = None


@dataclass
class DependencyReport:
    """Aggregated scan report for a skill's dependencies."""

    skill_path: str
    total_deps: int
    vulnerable_deps: int
    results: List[Vulnerability] = field(default_factory=list)


class DependencyScanner:
    """Scan skill dependencies against the OSV vulnerability database."""

    def __init__(self, cache_path: Optional[Path | str] = None) -> None:
        """Initialise the scanner.

        Args:
            cache_path: Path for the local JSON vulnerability cache.
                        Defaults to ``kazma-data/vuln_cache.json``.
        """
        if cache_path is None:
            cache_path = Path("kazma-data/vuln_cache.json")
        self._cache_path = Path(cache_path)
        self._cache: dict[str, List[dict]] = {}
        self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(self, skill_path: Path) -> DependencyReport:
        """Scan all dependencies of a skill for known vulnerabilities.

        Reads ``requirements.txt`` and/or ``pyproject.toml`` from
        *skill_path*, queries the OSV API for each dependency, and
        returns a :class:`DependencyReport`.

        Args:
            skill_path: Root directory of the skill.

        Returns:
            :class:`DependencyReport` with all findings.
        """
        deps = self._parse_dependencies(skill_path)
        total = len(deps)
        all_vulns: List[Vulnerability] = []

        for pkg, ver in deps:
            vulns = await self.check_single(pkg, ver)
            all_vulns.extend(vulns)

        return DependencyReport(
            skill_path=str(skill_path),
            total_deps=total,
            vulnerable_deps=len(all_vulns),
            results=all_vulns,
        )

    async def check_single(self, package: str, version: str) -> List[Vulnerability]:
        """Query the OSV API for vulnerabilities in a single package.

        Args:
            package: Package name (PyPI ecosystem assumed).
            version: Package version string.

        Returns:
            List of :class:`Vulnerability` items (empty if none found).
        """
        cache_key = f"{package}=={version}"

        # Check cache first
        if cache_key in self._cache:
            return self._deserialise_vulns(self._cache[cache_key], package, version)

        if httpx is None:  # pragma: no cover
            return []

        payload = {
            "package": {
                "name": package,
                "ecosystem": "PyPI",
            },
            "version": version,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OSV_API_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # pragma: no cover — network / parse errors
            return []

        vulns_raw: List[dict] = data.get("vulns", [])
        vulns = self._deserialise_vulns(vulns_raw, package, version)

        # Store in cache
        self._cache[cache_key] = vulns_raw
        self._save_cache()

        return vulns

    async def update_database(self) -> None:
        """Refresh the local vulnerability cache.

        This is a stub — a production implementation would re-query
        recently-changed packages or subscribe to OSV change feeds.
        """
        # Clear cache to force fresh queries on next scan
        self._cache.clear()
        self._save_cache()

    # ------------------------------------------------------------------
    # Dependency parsing
    # ------------------------------------------------------------------

    def _parse_dependencies(self, skill_path: Path) -> List[tuple[str, str]]:
        """Parse dependency files from a skill directory.

        Returns:
            List of ``(package_name, version)`` tuples.
        """
        deps: List[tuple[str, str]] = []

        # requirements.txt
        req_file = skill_path / "requirements.txt"
        if req_file.exists():
            deps.extend(self._parse_requirements_txt(req_file))

        # pyproject.toml
        pyproject = skill_path / "pyproject.toml"
        if pyproject.exists():
            deps.extend(self._parse_pyproject_toml(pyproject))

        return deps

    @staticmethod
    def _parse_requirements_txt(path: Path) -> List[tuple[str, str]]:
        """Parse a ``requirements.txt`` file.

        Recognises formats: ``pkg>=1.0``, ``pkg==1.2.3``, ``pkg~=1.0``, ``pkg``.
        """
        deps: List[tuple[str, str]] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return deps

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip extras like pkg[extra]>=1.0
            match = re.match(r"([A-Za-z0-9_.-]+)(?:\[.*?\])?\s*([><=!~]+)\s*([^\s;#]+)", line)
            if match:
                deps.append((match.group(1), match.group(3)))
            else:
                # No version specifier
                pkg_match = re.match(r"([A-Za-z0-9_.-]+)", line)
                if pkg_match:
                    deps.append((pkg_match.group(1), "0"))

        return deps

    @staticmethod
    def _parse_pyproject_toml(path: Path) -> List[tuple[str, str]]:
        """Parse dependencies from ``pyproject.toml`` (basic parsing, no tomllib dependency)."""
        deps: List[tuple[str, str]] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return deps

        in_deps = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "[project.dependencies]" or stripped == 'dependencies = [':
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("["):
                    in_deps = False
                    continue
                if stripped.startswith("]"):
                    in_deps = False
                    continue
                # Lines like: "requests>=2.28.0",  or  'requests>=2.28.0',
                cleaned = stripped.strip(chr(39) + chr(34) + ', ')
                match = re.match(r"([A-Za-z0-9_.-]+)\s*([><=!~]+)\s*(.+)", cleaned)
                if match:
                    deps.append((match.group(1), match.group(3)))
                else:
                    pkg_match = re.match(r"([A-Za-z0-9_.-]+)", cleaned)
                    if pkg_match:
                        deps.append((pkg_match.group(1), "0"))

        return deps

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}
        else:
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialise_vulns(raw: List[dict], package: str, version: str) -> List[Vulnerability]:
        """Convert raw OSV vulnerability dicts into :class:`Vulnerability` objects."""
        results: List[Vulnerability] = []
        for v in raw:
            severity = "UNKNOWN"
            sev_list = v.get("severity", [])
            if isinstance(sev_list, list) and sev_list:
                severity = sev_list[0].get("type", sev_list[0].get("score", "UNKNOWN"))
            elif isinstance(sev_list, str):
                severity = sev_list

            # Determine fixed version from affected ranges
            fixed: Optional[str] = None
            for aff in v.get("affected", []):
                for rng in aff.get("ranges", []):
                    for evt in rng.get("events", []):
                        if "fixed" in evt:
                            fixed = evt["fixed"]
                            break

            results.append(
                Vulnerability(
                    package=package,
                    version=version,
                    vuln_id=v.get("id", "UNKNOWN"),
                    severity=str(severity),
                    description=v.get("summary", v.get("details", "")),
                    fixed_version=fixed,
                )
            )

        return results


# ======================================================================
# Dependabot-style multi-source scanner
# ======================================================================


@dataclass
class ScanResult:
    """A single vulnerability finding from any source."""

    package: str
    current_version: str
    vulnerability: Vulnerability
    fix_available: bool
    fix_version: Optional[str] = None
    source: str = "osv"  # "osv", "github_advisories", "nvd"


@dataclass
class ScanReport:
    """Aggregated multi-source scan report."""

    scan_time: str
    total_packages: int
    vulnerable_packages: int
    results: List[ScanResult] = field(default_factory=list)
    sources_checked: List[str] = field(default_factory=list)


@dataclass
class SkillScanResult:
    """Result of scanning an installed skill manifest."""

    skill_name: str
    skill_path: str
    issues: List[str] = field(default_factory=list)
    vulnerable_deps: List[ScanResult] = field(default_factory=list)


class DependabotStyleScanner:
    """Automated dependency vulnerability scanning with multi-source support.

    Extends the basic ``DependencyScanner`` with:
    * Multi-source queries (OSV, GitHub Advisories, NVD)
    * SQLite scan history tracking
    * Skill manifest scanning
    * GitHub issue auto-creation
    * Security advisory generation
    """

    GITHUB_ADVISORIES_URL = "https://api.github.com/advisories"
    NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    def __init__(self, db_path: str = "kazma-data/security_scan.db") -> None:
        self.db_path = db_path
        self.scan_interval_hours = 24
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT NOT NULL,
                total_packages INTEGER NOT NULL,
                vulnerable_packages INTEGER NOT NULL,
                sources_checked TEXT NOT NULL,
                results_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                package TEXT NOT NULL,
                current_version TEXT NOT NULL,
                vuln_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                fix_available INTEGER NOT NULL,
                fix_version TEXT,
                source TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scan_history(id)
            );
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Dependency parsing (reuses DependencyScanner logic)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_requirements_txt(path: Path) -> List[tuple[str, str]]:
        """Parse a ``requirements.txt`` file."""
        return DependencyScanner._parse_requirements_txt(path)

    @staticmethod
    def _parse_pyproject_toml(path: Path) -> List[tuple[str, str]]:
        """Parse dependencies from ``pyproject.toml``."""
        return DependencyScanner._parse_pyproject_toml(path)

    def _parse_dependencies(self, project_root: Path) -> List[tuple[str, str]]:
        """Parse dependency files from a project directory."""
        deps: List[tuple[str, str]] = []
        req_file = project_root / "requirements.txt"
        if req_file.exists():
            deps.extend(self._parse_requirements_txt(req_file))
        pyproject = project_root / "pyproject.toml"
        if pyproject.exists():
            deps.extend(self._parse_pyproject_toml(pyproject))
        return deps

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_all_dependencies(self, project_root: Path) -> ScanReport:
        """Scan project dependencies against OSV, GitHub Advisories, and NVD.

        1. Parse dependencies from project files
        2. Query each source (OSV, GitHub, NVD)
        3. Deduplicate and merge results
        4. Store scan history in SQLite
        5. Return aggregated ScanReport
        """
        deps = self._parse_dependencies(project_root)
        total = len(deps)
        all_results: List[ScanResult] = []
        sources_checked: List[str] = []

        # Query OSV
        osv_results = await self._query_osv(deps)
        if osv_results:
            all_results.extend(osv_results)
            sources_checked.append("osv")

        # Query GitHub Advisories
        gh_results = await self._query_github_advisories(deps)
        if gh_results:
            all_results.extend(gh_results)
            sources_checked.append("github_advisories")

        # Query NVD
        nvd_results = await self._query_nvd(deps)
        if nvd_results:
            all_results.extend(nvd_results)
            sources_checked.append("nvd")

        # Deduplicate by (package, vuln_id)
        seen: set[tuple[str, str]] = set()
        deduped: List[ScanResult] = []
        for r in all_results:
            key = (r.package, r.vulnerability.vuln_id)
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        vulnerable_packages = len({r.package for r in deduped})

        report = ScanReport(
            scan_time=datetime.now(timezone.utc).isoformat(),
            total_packages=total,
            vulnerable_packages=vulnerable_packages,
            results=deduped,
            sources_checked=sources_checked,
        )

        # Persist to SQLite
        self._store_scan_report(report)

        return report

    async def scan_skill_manifests(
        self, skills_dir: str = "~/.kazma/skills"
    ) -> List[SkillScanResult]:
        """Scan installed skill manifests for vulnerabilities and suspicious configs.

        1. Find all skill directories
        2. Parse manifest.yaml from each
        3. Check for known vulnerable deps
        4. Flag suspicious MCP server configs
        5. Detect permission escalation attempts
        """
        resolved = Path(skills_dir).expanduser()
        results: List[SkillScanResult] = []

        if not resolved.exists():
            return results

        for skill_dir in resolved.iterdir():
            if not skill_dir.is_dir():
                continue

            manifest_path = None
            for name in ("manifest.yaml", "manifest.yml"):
                candidate = skill_dir / name
                if candidate.exists():
                    manifest_path = candidate
                    break

            if manifest_path is None:
                continue

            try:
                content = manifest_path.read_text(encoding="utf-8")
            except OSError:
                continue

            issues: List[str] = []

            # Check for suspicious MCP server configs
            suspicious_patterns = [
                (r"command\s*:\s*.*(?:eval|exec|system|shell)", "Suspicious MCP command detected"),
                (r"env\s*:.*(?:TOKEN|SECRET|KEY|PASSWORD)", "MCP env may leak secrets"),
                (r"args\s*:.*--privileged", "Privileged MCP container detected"),
                (r"network\s*:\s*host", "Host network access in MCP server"),
            ]
            for pattern, msg in suspicious_patterns:
                if re.search(pattern, content, re.I):
                    issues.append(msg)

            # Check for permission escalation indicators
            escalation_patterns = [
                (r"sudo\s", "sudo usage in skill"),
                (r"chmod\s+777", "World-writable permissions"),
                (r"setuid|setgid", "SUID/SGID bit usage"),
            ]
            for pattern, msg in escalation_patterns:
                if re.search(pattern, content, re.I):
                    issues.append(msg)

            # Check for vulnerable deps if skill has requirements.txt
            vuln_deps: List[ScanResult] = []
            req_file = skill_dir / "requirements.txt"
            if req_file.exists():
                deps = self._parse_requirements_txt(req_file)
                osv_results = await self._query_osv(deps)
                vuln_deps.extend(osv_results)

            results.append(SkillScanResult(
                skill_name=skill_dir.name,
                skill_path=str(skill_dir),
                issues=issues,
                vulnerable_deps=vuln_deps,
            ))

        return results

    async def create_github_issue(self, vulnerability: Vulnerability) -> str:
        """Auto-create GitHub issue for a new vulnerability.

        Uses ``gh`` CLI if available.  Returns the issue URL on success,
        or an error message string on failure.
        """
        title = f"Security: {vulnerability.vuln_id} in {vulnerability.package}"
        body = (
            f"## Vulnerability Report\n\n"
            f"**Package:** {vulnerability.package}\n"
            f"**Version:** {vulnerability.version}\n"
            f"**Vuln ID:** {vulnerability.vuln_id}\n"
            f"**Severity:** {vulnerability.severity}\n"
            f"**Fixed in:** {vulnerability.fixed_version or 'N/A'}\n\n"
            f"### Description\n\n{vulnerability.description}\n\n"
            f"### Recommendation\n\n"
            f"Update `{vulnerability.package}` to version "
            f"`{vulnerability.fixed_version or 'latest'}` or later.\n"
        )

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    "gh", "issue", "create",
                    "--title", title,
                    "--body", body,
                    "--label", "security",
                    "--label", "vulnerability",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
            return f"gh issue create failed: {proc.stderr.strip()}"
        except FileNotFoundError:
            return "gh CLI not installed"
        except Exception as exc:
            return f"Failed to create issue: {exc}"

    async def generate_advisory(self, vulnerability: Vulnerability) -> dict:
        """Generate a security advisory document for publication."""
        now = datetime.now(timezone.utc).isoformat()
        cve_id = f"CVE-{now[:4]}-{abs(hash(vulnerability.vuln_id)) % 10**7:07d}"
        return {
            "cve_id": cve_id,
            "package": vulnerability.package,
            "affected_version": vulnerability.version,
            "fixed_version": vulnerability.fixed_version or "N/A",
            "severity": vulnerability.severity,
            "description": vulnerability.description,
            "published_at": now,
            "advisory_content": (
                f"# Security Advisory: {vulnerability.vuln_id}\n\n"
                f"**Package:** {vulnerability.package}\n"
                f"**Affected:** {vulnerability.version}\n"
                f"**Fixed in:** {vulnerability.fixed_version or 'N/A'}\n"
                f"**Severity:** {vulnerability.severity}\n\n"
                f"{vulnerability.description}\n"
            ),
        }

    async def check_for_updates(self, project_root: Path) -> list:
        """Check if any dependencies have security updates available."""
        deps = self._parse_dependencies(project_root)
        updates: list = []
        for pkg, ver in deps:
            vulns = await self._query_osv_single(pkg, ver)
            for sr in vulns:
                if sr.fix_available and sr.fix_version:
                    updates.append({
                        "package": pkg,
                        "current_version": ver,
                        "fix_version": sr.fix_version,
                        "vuln_id": sr.vulnerability.vuln_id,
                        "severity": sr.vulnerability.severity,
                    })
        return updates

    def get_scan_history(self, limit: int = 10) -> list:
        """Get recent scan history from SQLite."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM scan_history ORDER BY scan_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Source queries
    # ------------------------------------------------------------------

    async def _query_osv(
        self, deps: List[tuple[str, str]]
    ) -> List[ScanResult]:
        """Query OSV for all deps."""
        results: List[ScanResult] = []
        for pkg, ver in deps:
            results.extend(await self._query_osv_single(pkg, ver))
        return results

    async def _query_osv_single(self, pkg: str, ver: str) -> List[ScanResult]:
        """Query OSV API for a single package."""
        if httpx is None:  # pragma: no cover
            return []

        payload = {
            "package": {"name": pkg, "ecosystem": "PyPI"},
            "version": ver,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OSV_API_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # pragma: no cover
            return []

        results: List[ScanResult] = []
        for v in data.get("vulns", []):
            vuln = DependencyScanner._deserialise_vulns([v], pkg, ver)
            if vuln:
                fixed = vuln[0].fixed_version
                results.append(ScanResult(
                    package=pkg,
                    current_version=ver,
                    vulnerability=vuln[0],
                    fix_available=fixed is not None,
                    fix_version=fixed,
                    source="osv",
                ))
        return results

    async def _query_github_advisories(
        self, deps: List[tuple[str, str]]
    ) -> List[ScanResult]:
        """Query GitHub Security Advisories API for all deps."""
        if httpx is None:  # pragma: no cover
            return []

        results: List[ScanResult] = []
        for pkg, ver in deps:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        self.GITHUB_ADVISORIES_URL,
                        params={"ecosystem": "pip", "package": pkg},
                    )
                    if resp.status_code == 403:  # Rate limited
                        continue
                    resp.raise_for_status()
                    advisories = resp.json()
            except Exception:  # pragma: no cover
                continue

            for adv in advisories:
                cve_id = adv.get("cve_id", "N/A")
                severity = adv.get("severity", "unknown")
                summary = adv.get("summary", "")
                # Determine fix version from vulnerabilities
                fix_ver = None
                for vuln in adv.get("vulnerabilities", []):
                    patched = vuln.get("patched_versions")
                    if patched:
                        fix_ver = patched
                        break
                results.append(ScanResult(
                    package=pkg,
                    current_version=ver,
                    vulnerability=Vulnerability(
                        package=pkg,
                        version=ver,
                        vuln_id=cve_id,
                        severity=str(severity),
                        description=summary,
                        fixed_version=fix_ver,
                    ),
                    fix_available=fix_ver is not None,
                    fix_version=fix_ver,
                    source="github_advisories",
                ))
        return results

    async def _query_nvd(
        self, deps: List[tuple[str, str]]
    ) -> List[ScanResult]:
        """Query NVD API for all deps."""
        if httpx is None:  # pragma: no cover
            return []

        results: List[ScanResult] = []
        for pkg, ver in deps:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        self.NVD_API_URL,
                        params={"keywordSearch": pkg, "resultsPerPage": 5},
                    )
                    if resp.status_code == 403:  # Rate limited
                        continue
                    resp.raise_for_status()
                    data = resp.json()
            except Exception:  # pragma: no cover
                continue

            for item in data.get("vulnerabilities", []):
                cve = item.get("cve", {})
                cve_id = cve.get("id", "N/A")
                descriptions = cve.get("descriptions", [])
                desc = next(
                    (d["value"] for d in descriptions if d.get("lang") == "en"),
                    "",
                )
                # Determine severity from CVSS metrics
                severity = "unknown"
                metrics = cve.get("metrics", {})
                for metric_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                    metric_list = metrics.get(metric_key, [])
                    if metric_list:
                        cvss = metric_list[0].get("cvssData", {})
                        severity = cvss.get("baseSeverity", "unknown")
                        break

                results.append(ScanResult(
                    package=pkg,
                    current_version=ver,
                    vulnerability=Vulnerability(
                        package=pkg,
                        version=ver,
                        vuln_id=cve_id,
                        severity=str(severity),
                        description=desc,
                    ),
                    fix_available=False,
                    source="nvd",
                ))
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _store_scan_report(self, report: ScanReport) -> int:
        """Store a scan report in SQLite. Returns the scan_id."""
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO scan_history (scan_time, total_packages, vulnerable_packages, sources_checked, results_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report.scan_time,
                report.total_packages,
                report.vulnerable_packages,
                json.dumps(report.sources_checked),
                json.dumps([
                    {
                        "package": r.package,
                        "current_version": r.current_version,
                        "vuln_id": r.vulnerability.vuln_id,
                        "severity": r.vulnerability.severity,
                        "description": r.vulnerability.description,
                        "fix_available": r.fix_available,
                        "fix_version": r.fix_version,
                        "source": r.source,
                    }
                    for r in report.results
                ]),
            ),
        )
        scan_id: int = cur.lastrowid or 0  # type: ignore[assignment]
        for r in report.results:
            conn.execute(
                """
                INSERT INTO scan_results
                    (scan_id, package, current_version, vuln_id, severity, description, fix_available, fix_version, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    r.package,
                    r.current_version,
                    r.vulnerability.vuln_id,
                    r.vulnerability.severity,
                    r.vulnerability.description,
                    1 if r.fix_available else 0,
                    r.fix_version,
                    r.source,
                ),
            )
        conn.commit()
        return scan_id
