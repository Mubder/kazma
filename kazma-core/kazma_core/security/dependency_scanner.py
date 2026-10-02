"""Known vulnerabilities in the packages Kazma runs on, from OSV.

The scanner this replaces asked OSV about the lowest version a requirement
allowed (``httpx>=0.27`` was asked as ``0.27``, an unpinned name as ``0``),
so every advisory ever published against a package counted: the live
security report said "202 vulnerable dependencies" across 35 requirements
(2026-10-02). A network failure returned "none", which made a scan that
never ran look clean, and answers were cached forever. Its multi-source
sibling (GitHub advisories asked without a version, NVD, a scan-history
database, a ``gh issue create`` helper) was reached by nothing.

What runs is what is installed. :func:`_installed_packages` reads the
environment's distributions, and :func:`_audit_packages` asks OSV about those
exact versions (``/v1/querybatch``, then each advisory's summary and fixed
version). A failure to ask raises :class:`DependencyQueryError`: a package
that was not asked about is never reported clean.

:func:`scan_skill_manifests` checks the skills installed from the hub: a
credential written into a manifest, MCP settings that hand a server the
host, and the installed versions of a skill's ``requirements.txt``.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_core.security.secret_scan import find_credentials

__all__ = [
    "DependencyQueryError",
    "DependencyReport",
    "OSV_QUERYBATCH_URL",
    "OSV_VULN_URL",
    "SkillScanResult",
    "Vulnerability",
    "audit_installed",
    "scan_skill_manifests",
]

logger = logging.getLogger(__name__)

OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"

#: Queries per batch request (OSV accepts up to 1,000).
_BATCH_SIZE = 500
#: Advisories whose details (summary, fixed version) are fetched, at most.
_MAX_DETAILS = 200
_DETAIL_CONCURRENCY = 8
_TIMEOUT_S = 30.0


class DependencyQueryError(RuntimeError):
    """OSV could not be asked, so nothing is known about the packages."""


@dataclass(frozen=True)
class Vulnerability:
    """One advisory that affects one installed package version."""

    package: str
    version: str
    vuln_id: str
    summary: str = ""
    fixed_version: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class DependencyReport:
    """The packages asked about and the advisories that affect them."""

    total: int
    vulnerabilities: list[Vulnerability] = field(default_factory=list)

    @property
    def vulnerable_packages(self) -> list[str]:
        return sorted({f"{v.package} {v.version}" for v in self.vulnerabilities})

    def upgrades(self) -> dict[str, tuple[str | None, int]]:
        """Per ``"name version"``: the release that fixes every advisory with a
        fix (the highest), and how many advisories have none."""
        out: dict[str, tuple[str | None, int]] = {}
        for v in self.vulnerabilities:
            key = f"{v.package} {v.version}"
            best, unfixed = out.get(key, (None, 0))
            if v.fixed_version is None:
                unfixed += 1
            elif best is None or _version_key(v.fixed_version) > _version_key(best):
                best = v.fixed_version
            out[key] = (best, unfixed)
        return out


@dataclass
class SkillScanResult:
    """What a scan found in one installed skill."""

    skill_name: str
    skill_path: str
    issues: list[str] = field(default_factory=list)
    vulnerabilities: list[Vulnerability] = field(default_factory=list)


def _canonical_name(name: str) -> str:
    """A distribution name as PyPI and OSV compare it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _installed_packages() -> list[tuple[str, str]]:
    """``(name, version)`` of every distribution in this environment.

    The first distribution of a name on ``sys.path`` wins, as it does for
    ``import``. A local version label (``2.5.1+cpu``) is dropped: OSV knows
    the public release.
    """
    found: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if not name or not dist.version:
            continue
        found.setdefault(_canonical_name(name), dist.version.split("+", 1)[0])
    return sorted(found.items())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT_S, verify=shared_ssl_context())


async def _post_batch(client: httpx.AsyncClient, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        resp = await client.post(OSV_QUERYBATCH_URL, json={"queries": queries})
        resp.raise_for_status()
        results = resp.json().get("results")
    except (httpx.HTTPError, ValueError) as exc:
        raise DependencyQueryError(f"OSV could not be asked: {exc}") from exc
    if not isinstance(results, list) or len(results) != len(queries):
        raise DependencyQueryError("OSV answered a batch with the wrong number of results")
    return results


async def _advisory_ids(
    client: httpx.AsyncClient, packages: list[tuple[str, str]],
) -> dict[tuple[str, str], list[str]]:
    """Every advisory id OSV lists for each ``(name, version)``, all pages."""
    ids: dict[tuple[str, str], list[str]] = {pkg: [] for pkg in packages}
    pending: list[tuple[tuple[str, str], str | None]] = [(pkg, None) for pkg in packages]
    while pending:
        batch, pending = pending[:_BATCH_SIZE], pending[_BATCH_SIZE:]
        queries = []
        for (name, version), token in batch:
            query: dict[str, Any] = {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
            if token:
                query["page_token"] = token
            queries.append(query)
        for (pkg, _token), result in zip(batch, await _post_batch(client, queries), strict=True):
            for vuln in result.get("vulns") or ():
                if vuln.get("id"):
                    ids[pkg].append(str(vuln["id"]))
            if result.get("next_page_token"):
                pending.append((pkg, str(result["next_page_token"])))
    return ids


def _fixed_version(advisory: dict[str, Any], package: str) -> str | None:
    """The first version that fixes *advisory* for *package*, if OSV says."""
    for affected in advisory.get("affected") or ():
        pkg = affected.get("package") or {}
        if _canonical_name(str(pkg.get("name", ""))) != package:
            continue
        for rng in affected.get("ranges") or ():
            for event in rng.get("events") or ():
                if event.get("fixed"):
                    return str(event["fixed"])
    return None


async def _details(client: httpx.AsyncClient, vuln_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Each advisory's record; one that cannot be fetched is left out (its id still counts)."""
    gate = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch(vuln_id: str) -> tuple[str, dict[str, Any] | None]:
        async with gate:
            try:
                resp = await client.get(OSV_VULN_URL.format(id=vuln_id))
                resp.raise_for_status()
                return vuln_id, resp.json()
            except (httpx.HTTPError, ValueError):
                logger.info("[security] OSV advisory %s could not be read; reported by id", vuln_id)
                return vuln_id, None

    pairs = await asyncio.gather(*(fetch(v) for v in vuln_ids[:_MAX_DETAILS]))
    return {vuln_id: record for vuln_id, record in pairs if record}


async def _audit_packages(
    packages: list[tuple[str, str]], *, client: httpx.AsyncClient | None = None,
) -> DependencyReport:
    """The OSV advisories affecting exactly these ``(name, version)`` pairs.

    Raises :class:`DependencyQueryError` when OSV cannot be asked.
    """
    packages = sorted({(_canonical_name(n), v) for n, v in packages if n and v})
    if not packages:
        return DependencyReport(total=0)
    own = client is None
    http = client or _client()
    try:
        ids = await _advisory_ids(http, packages)
        unique = sorted({i for found in ids.values() for i in found})
        records = await _details(http, unique) if unique else {}
    finally:
        if own:
            await http.aclose()
    vulns = []
    for (name, version), found in ids.items():
        for group in _same_advisories(sorted(set(found)), records):
            vuln_id = min(group, key=_id_preference)
            record = records.get(vuln_id) or next((records[i] for i in group if i in records), {})
            fixes = [f for f in (_fixed_version(records.get(i) or {}, name) for i in group) if f]
            vulns.append(Vulnerability(
                package=name,
                version=version,
                vuln_id=vuln_id,
                summary=str(record.get("summary") or "")[:300],
                fixed_version=max(fixes, key=_version_key) if fixes else None,
                aliases=tuple(sorted(set(group) - {vuln_id})),
            ))
    return DependencyReport(total=len(packages), vulnerabilities=vulns)


def _same_advisories(ids: list[str], records: dict[str, dict[str, Any]]) -> list[list[str]]:
    """*ids* grouped by advisory: OSV lists one issue under several ids
    (a GHSA and a PYSEC id name each other as aliases)."""
    parent = {i: i for i in ids}

    def root(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in ids:
        for alias in (records.get(i) or {}).get("aliases") or ():
            if alias in parent:
                parent[root(str(alias))] = root(i)
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())


def _id_preference(vuln_id: str) -> tuple[int, str]:
    """The id a merged advisory is shown under: GHSA (it has a summary), then PYSEC."""
    return (0 if vuln_id.startswith("GHSA-") else 1 if vuln_id.startswith("PYSEC-") else 2, vuln_id)


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """A sort key for release strings (``2.10.0`` after ``2.9.1``)."""
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[.+-]", version))


async def audit_installed(*, client: httpx.AsyncClient | None = None) -> DependencyReport:
    """:func:`_audit_packages` over every installed distribution."""
    packages = await asyncio.to_thread(_installed_packages)
    return await _audit_packages(packages, client=client)


# ---------------------------------------------------------------------------
# Installed hub skills
# ---------------------------------------------------------------------------

#: A ``command:`` whose program is a shell (``command: bash``,
#: ``command: ["/bin/sh", ...]``), and the flag that hands it a script.
_SHELL_PROGRAM = re.compile(
    r"(?im)\bcommand\s*:\s*\[?\s*[\"']?(?:[^\s\"'\],]*[/\\])?(?:sh|bash|zsh|dash|cmd|powershell|pwsh)(?:\.exe)?[\"']?(?=[\s,\]]|$)"
)
_SCRIPT_FLAG = re.compile(r"(?i)(?:^|[\s\"',\[])(?:-c|/c|-command)(?=[\s\"',\]]|$)")

#: Manifest settings that hand an MCP server more than a program needs.
_MANIFEST_FLAGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)--privileged\b"), "asks for a privileged container"),
    (re.compile(r"(?i)\bnetwork(?:_mode)?\s*:\s*['\"]?host\b"), "shares the host network"),
    (re.compile(r"(?i)\bsudo\s"), "uses sudo"),
    (re.compile(r"(?i)\bchmod\s+(?:-R\s+)?777\b"), "makes files writable by everyone"),
    (re.compile(r"(?i)\bset[ug]id\b"), "sets a set-user-ID or set-group-ID bit"),
)

_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _requirement_names(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            names.append(_canonical_name(match.group(1)))
    return names


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name).split("+", 1)[0]
    except importlib.metadata.PackageNotFoundError:
        return None


def _manifest_issues(text: str) -> list[str]:
    issues = [
        f"line {f.line}: a {f.kind} is written in the manifest"
        for f in find_credentials(text, bare_values=True)
    ]
    if _SHELL_PROGRAM.search(text) and _SCRIPT_FLAG.search(text):
        issues.append("the manifest runs its command through a shell")
    issues.extend(f"the manifest {what}" for pattern, what in _MANIFEST_FLAGS if pattern.search(text))
    return issues


def _default_skills_dir() -> Path:
    from kazma_core.paths import installed_skills_dir

    return Path(installed_skills_dir())


async def scan_skill_manifests(
    skills_dir: str | Path | None = None, *, client: httpx.AsyncClient | None = None,
) -> list[SkillScanResult]:
    """Every installed skill with a manifest, and what is wrong with it.

    Raises :class:`DependencyQueryError` when a skill's requirements could
    not be checked against OSV.
    """
    root = Path(skills_dir).expanduser() if skills_dir else _default_skills_dir()
    results: list[SkillScanResult] = []
    asked: list[tuple[SkillScanResult, list[tuple[str, str]]]] = []
    if not root.is_dir():
        return results
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = next((skill_dir / n for n in ("manifest.yaml", "manifest.yml") if (skill_dir / n).is_file()), None)
        if manifest is None:
            continue
        result = SkillScanResult(skill_name=skill_dir.name, skill_path=str(skill_dir))
        try:
            result.issues.extend(_manifest_issues(manifest.read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            result.issues.append(f"the manifest cannot be read: {exc}")
        requirements = skill_dir / "requirements.txt"
        if requirements.is_file():
            pinned: list[tuple[str, str]] = []
            for name in _requirement_names(requirements):
                version = _installed_version(name)
                if version is None:
                    result.issues.append(f"requirement {name} is not installed")
                else:
                    pinned.append((name, version))
            asked.append((result, pinned))
        results.append(result)
    wanted = sorted({pkg for _result, pkgs in asked for pkg in pkgs})
    if wanted:
        report = await _audit_packages(wanted, client=client)
        for result, pkgs in asked:
            mine = set(pkgs)
            result.vulnerabilities = [v for v in report.vulnerabilities if (v.package, v.version) in mine]
    return results
