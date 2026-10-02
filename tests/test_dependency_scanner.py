"""The dependency scan asks OSV about what is installed, and never reports
an unasked package as clean.

Live 2026-10-02: the security report said "202 vulnerable dependencies"
because the old scanner asked OSV about each requirement's lowest allowed
version (an unpinned name as ``0``), and a network failure returned "none".
OSV is faked here with ``httpx.MockTransport``; nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from kazma_core.security import dependency_scanner as ds


class FakeOSV:
    """api.osv.dev's querybatch and vulns endpoints, from a table."""

    def __init__(self, affected: dict[tuple[str, str], list[str]], records: dict[str, dict[str, Any]],
                 *, page_size: int = 0, fail: str = "", detail_fails: frozenset[str] = frozenset()) -> None:
        self.affected = affected
        self.records = records
        self.page_size = page_size
        self.fail = fail
        self.detail_fails = detail_fails
        self.queries: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail == "connect":
            raise httpx.ConnectError("no route to api.osv.dev", request=request)
        if self.fail == "500":
            return httpx.Response(500, text="upstream error")
        if request.url.path == "/v1/querybatch":
            results = []
            for query in json.loads(request.content)["queries"]:
                self.queries.append(query)
                key = (query["package"]["name"], query["version"])
                ids = self.affected.get(key, [])
                start = int(query.get("page_token") or 0)
                if self.page_size:
                    page = ids[start:start + self.page_size]
                    result: dict[str, Any] = {"vulns": [{"id": i} for i in page]}
                    if start + self.page_size < len(ids):
                        result["next_page_token"] = str(start + self.page_size)
                else:
                    result = {"vulns": [{"id": i} for i in ids]} if ids else {}
                results.append(result)
            return httpx.Response(200, json={"results": results})
        vuln_id = request.url.path.rsplit("/", 1)[-1]
        if vuln_id in self.detail_fails or vuln_id not in self.records:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=self.records[vuln_id])

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _advisory(vuln_id: str, package: str, fixed: str | None, *, aliases: tuple[str, ...] = ()) -> dict[str, Any]:
    events: list[dict[str, str]] = [{"introduced": "0"}]
    if fixed:
        events.append({"fixed": fixed})
    return {
        "id": vuln_id,
        "summary": f"{package}: something bad",
        "aliases": list(aliases),
        "affected": [{"package": {"name": package, "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": events}]}],
    }


def _audit(osv: FakeOSV, packages: list[tuple[str, str]]) -> ds.DependencyReport:
    async def run() -> ds.DependencyReport:
        async with osv.client() as client:
            return await ds._audit_packages(packages, client=client)

    return asyncio.run(run())


# ── what is asked ────────────────────────────────────────────────────────


def test_the_installed_version_is_asked_not_a_requirement_minimum() -> None:
    osv = FakeOSV({("pyjwt", "2.13.0"): ["GHSA-aaaa"]}, {"GHSA-aaaa": _advisory("GHSA-aaaa", "pyjwt", "2.14.0")})
    report = _audit(osv, [("PyJWT", "2.13.0")])
    assert osv.queries == [{"package": {"name": "pyjwt", "ecosystem": "PyPI"}, "version": "2.13.0"}]
    assert [(v.package, v.version, v.vuln_id, v.fixed_version) for v in report.vulnerabilities] == [
        ("pyjwt", "2.13.0", "GHSA-aaaa", "2.14.0"),
    ]
    assert report.total == 1


def test_installed_packages_are_the_environment_s_distributions(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def __init__(self, name: str, version: str) -> None:
            self.metadata = {"Name": name}
            self.version = version

    dists = [Dist("PyJWT", "2.13.0"), Dist("torch", "2.5.1+cpu"), Dist("pyjwt", "1.0.0"), Dist("zope.interface", "7.0")]
    monkeypatch.setattr(ds.importlib.metadata, "distributions", lambda: iter(dists))
    # First on sys.path wins, as for import; a local label is dropped; names are PEP 503.
    assert ds._installed_packages() == [("pyjwt", "2.13.0"), ("torch", "2.5.1"), ("zope-interface", "7.0")]


def test_one_advisory_under_two_ids_counts_once() -> None:
    """OSV lists most PyPI advisories twice (a GHSA and a PYSEC id naming
    each other): counted per id, the live install showed 111 for 56."""
    records = {
        "GHSA-bbbb": _advisory("GHSA-bbbb", "urllib3", "2.8.0", aliases=("PYSEC-2026-1",)),
        "PYSEC-2026-1": _advisory("PYSEC-2026-1", "urllib3", "2.8.0", aliases=("GHSA-bbbb",)),
        "GHSA-cccc": _advisory("GHSA-cccc", "urllib3", "2.9.1"),
    }
    osv = FakeOSV({("urllib3", "2.7.0"): ["PYSEC-2026-1", "GHSA-bbbb", "GHSA-cccc"]}, records)
    report = _audit(osv, [("urllib3", "2.7.0")])
    assert sorted((v.vuln_id, v.aliases) for v in report.vulnerabilities) == [
        ("GHSA-bbbb", ("PYSEC-2026-1",)), ("GHSA-cccc", ()),
    ]
    # The upgrade that fixes every fixed advisory is the HIGHEST fix (by
    # release order, not string order: 2.10 is after 2.9).
    records["GHSA-cccc"] = _advisory("GHSA-cccc", "urllib3", "2.10.0")
    report = _audit(FakeOSV({("urllib3", "2.7.0"): ["GHSA-bbbb", "GHSA-cccc"]}, records), [("urllib3", "2.7.0")])
    assert report.upgrades() == {"urllib3 2.7.0": ("2.10.0", 0)}


def test_every_page_of_advisories_is_read() -> None:
    ids = [f"GHSA-{i:04d}" for i in range(5)]
    osv = FakeOSV({("aiohttp", "3.14.1"): ids}, {i: _advisory(i, "aiohttp", "3.14.3") for i in ids}, page_size=2)
    report = _audit(osv, [("aiohttp", "3.14.1")])
    assert sorted(v.vuln_id for v in report.vulnerabilities) == ids
    assert [q.get("page_token") for q in osv.queries] == [None, "2", "4"]


def test_an_advisory_whose_details_cannot_be_read_still_counts() -> None:
    osv = FakeOSV({("pypdf", "6.15.0"): ["GHSA-dddd"]}, {}, detail_fails=frozenset({"GHSA-dddd"}))
    report = _audit(osv, [("pypdf", "6.15.0")])
    assert [(v.vuln_id, v.fixed_version) for v in report.vulnerabilities] == [("GHSA-dddd", None)]
    assert report.upgrades() == {"pypdf 6.15.0": (None, 1)}


# ── a scan that did not run is never clean ──────────────────────────────


@pytest.mark.parametrize("failure", ["connect", "500"])
def test_osv_unreachable_raises(failure: str) -> None:
    with pytest.raises(ds.DependencyQueryError):
        _audit(FakeOSV({}, {}, fail=failure), [("httpx", "0.28.1")])


def test_the_report_says_it_could_not_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr("kazma_core.install_requirements.unmet_requirements", lambda root=None: [])
    async def down() -> ds.DependencyReport:
        raise ds.DependencyQueryError("OSV could not be asked: no route")

    monkeypatch.setattr(ds, "audit_installed", lambda **_: down())
    check = asyncio.run(SecurityHardeningRunner(Path.cwd()).run_check("check_dependency_vulnerabilities"))
    assert check.passed is False
    assert "could not be checked" in check.message

    # Negative control: the old scanner returned [] on any network error,
    # and the same check then passed over a scan that never ran.
    async def silently_clean() -> ds.DependencyReport:
        return ds.DependencyReport(total=258)

    monkeypatch.setattr(ds, "audit_installed", lambda **_: silently_clean())
    check = asyncio.run(SecurityHardeningRunner(Path.cwd()).run_check("check_dependency_vulnerabilities"))
    assert check.passed is True


def test_the_report_names_the_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr("kazma_core.install_requirements.unmet_requirements", lambda root=None: [])
    async def found() -> ds.DependencyReport:
        return ds.DependencyReport(total=3, vulnerabilities=[
            ds.Vulnerability("pyjwt", "2.13.0", "GHSA-1", fixed_version="2.14.0"),
            ds.Vulnerability("pyjwt", "2.13.0", "GHSA-2", fixed_version="2.15.0"),
            ds.Vulnerability("chromadb", "1.5.9", "GHSA-3"),
        ])

    monkeypatch.setattr(ds, "audit_installed", lambda **_: found())
    check = asyncio.run(SecurityHardeningRunner(Path.cwd()).run_check("check_dependency_vulnerabilities"))
    assert check.passed is False and check.severity == "critical"
    assert check.message == "2 of 3 installed packages have 3 known advisories"
    assert "pyjwt 2.13.0 (fixed in 2.15.0)" in check.recommendation
    assert "chromadb 1.5.9 (no fixed release yet)" in check.recommendation


# ── installed hub skills ────────────────────────────────────────────────


def _skill(root: Path, name: str, manifest: str, requirements: str | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(manifest, encoding="utf-8")
    if requirements is not None:
        (folder / "requirements.txt").write_text(requirements, encoding="utf-8")
    return folder


def test_skill_manifests_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import secrets

    key = secrets.token_hex(20)
    _skill(tmp_path, "leaky", f"name: leaky\nenv:\n  API_KEY: {key}\n")
    _skill(tmp_path, "shelly", "name: shelly\nmcp:\n  command: bash\n  args: ['-c', 'curl x | sh']\n")
    # The old pattern matched "system" inside "filesystem": the most common
    # MCP server was "suspicious".
    _skill(tmp_path, "files", "name: files\nmcp:\n  command: npx\n  args: ['@modelcontextprotocol/server-filesystem']\n")
    _skill(tmp_path, "deps", "name: deps\n", "httpx>=0.27\nnot-a-real-package-xyz==1.0\n# comment\n")
    (tmp_path / "no-manifest").mkdir()

    versions = {"httpx": "0.28.1"}

    def version(name: str) -> str:
        if name not in versions:
            raise ds.importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(ds.importlib.metadata, "version", version)
    osv = FakeOSV({("httpx", "0.28.1"): ["GHSA-eeee"]}, {"GHSA-eeee": _advisory("GHSA-eeee", "httpx", "0.29.0")})

    async def run() -> list[ds.SkillScanResult]:
        async with osv.client() as client:
            return await ds.scan_skill_manifests(tmp_path, client=client)

    results = {r.skill_name: r for r in asyncio.run(run())}
    assert sorted(results) == ["deps", "files", "leaky", "shelly"]
    assert results["leaky"].issues == ["line 3: a credential is written in the manifest"]
    assert key not in json.dumps([vars(r) for r in results.values()], default=str), "the value is never echoed"
    assert results["shelly"].issues == ["the manifest runs its command through a shell"]
    assert results["files"].issues == []
    assert results["deps"].issues == ["requirement not-a-real-package-xyz is not installed"]
    assert [(v.package, v.version) for v in results["deps"].vulnerabilities] == [("httpx", "0.28.1")]
    assert osv.queries == [{"package": {"name": "httpx", "ecosystem": "PyPI"}, "version": "0.28.1"}]


def test_the_skill_route_reports_an_osv_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    async def down(*_a: Any, **_k: Any) -> list[ds.SkillScanResult]:
        raise ds.DependencyQueryError("OSV could not be asked: no route")

    async def clean(*_a: Any, **_k: Any) -> list[ds.SkillScanResult]:
        return [ds.SkillScanResult(skill_name="ok", skill_path="/x")]

    (tmp_path / "templates").mkdir()
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), ConfigStore(db_path=str(tmp_path / "s.db")), Jinja2Templates(directory=str(tmp_path / "templates"))))
    client = TestClient(app)

    monkeypatch.setattr(ds, "scan_skill_manifests", down)
    assert client.get("/api/security/deps").json() == {
        "status": "error", "error": "OSV could not be asked: no route", "results": [],
    }
    monkeypatch.setattr(ds, "scan_skill_manifests", clean)
    assert client.get("/api/security/deps").json() == {
        "status": "ok",
        "results": [{"skill_name": "ok", "skill_path": "/x", "issues": [], "vulnerabilities": []}],
    }


# ── reviewed advisories ─────────────────────────────────────────────────


def _no_unmet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kazma_core.install_requirements.unmet_requirements", lambda root=None: [])


def test_a_review_covers_its_advisory_by_id_or_alias_for_its_package() -> None:
    review_id, review = next(iter(ds.REVIEWED_ADVISORIES.items()))
    assert ds.review_of(ds.Vulnerability(review.package, "1.0", review_id)) is review
    assert ds.review_of(ds.Vulnerability(review.package, "1.0", "PYSEC-0000-1", aliases=(review_id,))) is review
    assert ds.review_of(ds.Vulnerability("another-package", "1.0", review_id)) is None
    assert ds.review_of(ds.Vulnerability(review.package, "1.0", "GHSA-not-reviewed")) is None


def test_a_reviewed_advisory_is_named_never_counted_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.security.hardening import SecurityHardeningRunner

    _no_unmet(monkeypatch)
    review_id, review = next(iter(ds.REVIEWED_ADVISORIES.items()))

    async def only_reviewed(**_: Any) -> ds.DependencyReport:
        return ds.DependencyReport(total=2, vulnerabilities=[ds.Vulnerability(review.package, "1.5.9", review_id)])

    monkeypatch.setattr(ds, "audit_installed", only_reviewed)
    check = asyncio.run(SecurityHardeningRunner(Path.cwd()).run_check("check_dependency_vulnerabilities"))
    assert check.passed is True
    assert "1 reviewed as out of Kazma's reach" in check.message and review_id in check.message

    # Negative control: the same advisory with no review fails the check.
    monkeypatch.setattr(ds, "REVIEWED_ADVISORIES", {})
    check = asyncio.run(SecurityHardeningRunner(Path.cwd()).run_check("check_dependency_vulnerabilities"))
    assert check.passed is False and check.severity == "critical"


def _product_python() -> dict[str, str]:
    import subprocess

    from kazma_core.security.child_env import tool_child_env

    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "ls-files", "-z", "--", "kazma-*/kazma_*/*.py"],
        cwd=root, capture_output=True, check=True, env=tool_child_env(),
    ).stdout.decode("utf-8").split("\0")
    scanner = "kazma-core/kazma_core/security/dependency_scanner.py"  # names the patterns
    return {
        rel: (root / rel).read_text(encoding="utf-8", errors="replace")
        for rel in listed
        if rel and rel != scanner and "/tests/" not in rel and not rel.split("/")[0].endswith("_tests")
        and (root / rel).is_file()
    }


def test_no_reviewed_advisory_is_reachable_from_product_code() -> None:
    """Each review says why its advisory cannot be reached; the code that
    would make it reachable must not appear (re-read the advisory if it does)."""
    import re

    sources = _product_python()
    assert len(sources) > 500
    for vuln_id, review in ds.REVIEWED_ADVISORIES.items():
        pattern = re.compile(review.reachable_if)
        hits = sorted(rel for rel, text in sources.items() if pattern.search(text))
        assert not hits, f"{vuln_id} ({review.package}) may be reachable now: {hits} -- re-read it"
    # Negative controls: each premise catches the use it guards against.
    assert re.search(ds._CHROMA_SERVER, "client = chromadb.HttpClient(host='chroma')")
    assert re.search(ds._CHROMA_SERVER, "client = chromadb.AsyncHttpClient()")
    assert re.search(
        ds.REVIEWED_ADVISORIES["GHSA-4j2p-28q2-5m79"].reachable_if,
        "model = load_checkpoint_and_dispatch(model, checkpoint)",
    )
