"""Tests for DependencyScanner and DependabotStyleScanner."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.security.dependency_scanner import (
    DependabotStyleScanner,
    DependencyReport,
    DependencyScanner,
    ScanReport,
    ScanResult,
    SkillScanResult,
    Vulnerability,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scanner(tmp_path: Path) -> DependencyScanner:
    return DependencyScanner(cache_path=tmp_path / "vuln_cache.json")


@pytest.fixture
def dep_scanner(tmp_path: Path) -> DependabotStyleScanner:
    return DependabotStyleScanner(db_path=str(tmp_path / "scan.db"))


@pytest.fixture
def skill_with_reqs(tmp_path: Path) -> Path:
    skill = tmp_path / "req-skill"
    skill.mkdir()
    (skill / "requirements.txt").write_text(
        "# Core deps\nrequests>=2.28.0\nflask==2.3.2\nurllib3~=1.26\n\n# Dev\ntest-dep\n"
    )
    return skill


@pytest.fixture
def skill_with_pyproject(tmp_path: Path) -> Path:
    skill = tmp_path / "py-skill"
    skill.mkdir()
    (skill / "pyproject.toml").write_text(
        "[project]\n"
        'name = "my-project"\n'
        'version = "1.0.0"\n\n'
        "[project.dependencies]\n"
        '"requests>=2.28.0",\n'
        '"numpy>=1.24.0",\n'
    )
    return skill


@pytest.fixture
def skill_with_both(tmp_path: Path) -> Path:
    skill = tmp_path / "both-skill"
    skill.mkdir()
    (skill / "requirements.txt").write_text("requests>=2.28.0\n")
    (skill / "pyproject.toml").write_text(
        "[project]\nname = 'both'\nversion = '1.0.0'\n\n[project.dependencies]\n'numpy>=1.24.0',\n"
    )
    return skill


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a skills directory with multiple skills for manifest scanning."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Skill 1: clean manifest
    s1 = skills / "clean-skill"
    s1.mkdir()
    (s1 / "manifest.yaml").write_text("name: clean-skill\nversion: 1.0.0\n")

    # Skill 2: suspicious manifest
    s2 = skills / "suspicious-skill"
    s2.mkdir()
    (s2 / "manifest.yaml").write_text(
        "name: suspicious-skill\nversion: 0.1.0\n"
        "mcp_server:\n  command: eval(user_input)\n  env:\n    TOKEN: abc123\n"
    )

    # Skill 3: escalation manifest
    s3 = skills / "escalation-skill"
    s3.mkdir()
    (s3 / "manifest.yaml").write_text(
        "name: escalation-skill\nversion: 2.0.0\n"
        "run: sudo apt-get install something\n"
    )

    # Skill 4: no manifest, should be skipped
    s4 = skills / "no-manifest-skill"
    s4.mkdir()

    return skills


# ---------------------------------------------------------------------------
# DependencyScanner dataclass tests
# ---------------------------------------------------------------------------

class TestDependencyReport:
    def test_report_dataclass(self):
        report = DependencyReport(skill_path="/tmp/skill", total_deps=5, vulnerable_deps=2)
        assert report.skill_path == "/tmp/skill"
        assert report.total_deps == 5
        assert report.vulnerable_deps == 2
        assert report.results == []

    def test_vulnerability_dataclass(self):
        vuln = Vulnerability(
            package="requests",
            version="2.28.0",
            vuln_id="GHSA-abc",
            severity="HIGH",
            description="Test vuln",
            fixed_version="2.28.1",
        )
        assert vuln.package == "requests"
        assert vuln.fixed_version == "2.28.1"


# ---------------------------------------------------------------------------
# DependencyScanner check_single
# ---------------------------------------------------------------------------

class TestCheckSingle:
    @pytest.mark.asyncio
    async def test_check_single_clean(self, scanner: DependencyScanner):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"vulns": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("kazma_core.security.dependency_scanner.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            vulns = await scanner.check_single("requests", "2.28.0")
            assert vulns == []

    @pytest.mark.asyncio
    async def test_check_single_vulnerable(self, scanner: DependencyScanner):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "vulns": [
                {
                    "id": "GHSA-test-123",
                    "summary": "Critical RCE in requests",
                    "severity": [{"type": "CVSS_V3", "score": "9.8"}],
                    "affected": [
                        {
                            "ranges": [
                                {
                                    "events": [{"introduced": "0"}, {"fixed": "2.28.1"}],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("kazma_core.security.dependency_scanner.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            vulns = await scanner.check_single("requests", "2.28.0")
            assert len(vulns) == 1
            assert vulns[0].vuln_id == "GHSA-test-123"
            assert vulns[0].severity == "CVSS_V3"
            assert vulns[0].fixed_version == "2.28.1"
            assert vulns[0].package == "requests"

    @pytest.mark.asyncio
    async def test_check_single_network_error(self, scanner: DependencyScanner):
        with patch("kazma_core.security.dependency_scanner.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            vulns = await scanner.check_single("requests", "2.28.0")
            assert vulns == []

    @pytest.mark.asyncio
    async def test_check_single_uses_cache(self, scanner: DependencyScanner):
        # Manually populate cache
        cache_key = "requests==2.28.0"
        scanner._cache[cache_key] = [{"id": "CACHED-001", "summary": "cached vuln"}]
        scanner._save_cache()

        vulns = await scanner.check_single("requests", "2.28.0")
        assert len(vulns) == 1
        assert vulns[0].vuln_id == "CACHED-001"


# ---------------------------------------------------------------------------
# DependencyScanner scan
# ---------------------------------------------------------------------------

class TestScan:
    @pytest.mark.asyncio
    async def test_scan_requirements_txt(self, scanner: DependencyScanner, skill_with_reqs: Path):
        with patch.object(scanner, "check_single", new_callable=AsyncMock, return_value=[]):
            report = await scanner.scan(skill_with_reqs)
        assert isinstance(report, DependencyReport)
        assert report.total_deps == 4  # requests, flask, urllib3, test-dep
        assert report.vulnerable_deps == 0

    @pytest.mark.asyncio
    async def test_scan_pyproject_toml(self, scanner: DependencyScanner, skill_with_pyproject: Path):
        with patch.object(scanner, "check_single", new_callable=AsyncMock, return_value=[]):
            report = await scanner.scan(skill_with_pyproject)
        assert report.total_deps >= 2

    @pytest.mark.asyncio
    async def test_scan_no_deps(self, scanner: DependencyScanner, tmp_path: Path):
        skill = tmp_path / "no-deps"
        skill.mkdir()
        report = await scanner.scan(skill)
        assert report.total_deps == 0
        assert report.vulnerable_deps == 0

    @pytest.mark.asyncio
    async def test_scan_mixed_deps(self, scanner: DependencyScanner, skill_with_both: Path):
        with patch.object(scanner, "check_single", new_callable=AsyncMock, return_value=[]):
            report = await scanner.scan(skill_with_both)
        # requests from requirements.txt + numpy from pyproject.toml
        assert report.total_deps == 2


# ---------------------------------------------------------------------------
# DependencyScanner parse dependencies
# ---------------------------------------------------------------------------

class TestParseDependencies:
    def test_parse_requirements_txt(self, scanner: DependencyScanner, tmp_path: Path):
        req = tmp_path / "requirements.txt"
        req.write_text("requests>=2.28.0\nflask==2.3.2\nnumpy~=1.24\n")
        deps = scanner._parse_requirements_txt(req)
        assert len(deps) == 3
        assert ("requests", "2.28.0") in deps
        assert ("flask", "2.3.2") in deps
        assert ("numpy", "1.24") in deps

    def test_parse_requirements_txt_comments(self, scanner: DependencyScanner, tmp_path: Path):
        req = tmp_path / "requirements.txt"
        req.write_text("# This is a comment\nrequests>=2.28.0\n# another comment\n")
        deps = scanner._parse_requirements_txt(req)
        assert len(deps) == 1

    def test_parse_requirements_txt_empty(self, scanner: DependencyScanner, tmp_path: Path):
        req = tmp_path / "requirements.txt"
        req.write_text("")
        deps = scanner._parse_requirements_txt(req)
        assert deps == []

    def test_parse_requirements_txt_no_version(self, scanner: DependencyScanner, tmp_path: Path):
        req = tmp_path / "requirements.txt"
        req.write_text("requests\nflask\n")
        deps = scanner._parse_requirements_txt(req)
        assert len(deps) == 2
        # Should default to version "0"
        assert ("requests", "0") in deps

    def test_parse_pyproject_toml(self, scanner: DependencyScanner, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "test"\n\n'
            "[project.dependencies]\n"
            '"requests>=2.28.0",\n'
            '"flask==2.3.2",\n'
        )
        deps = scanner._parse_pyproject_toml(pyproject)
        assert len(deps) == 2

    def test_parse_pyproject_toml_empty(self, scanner: DependencyScanner, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        deps = scanner._parse_pyproject_toml(pyproject)
        assert deps == []


# ---------------------------------------------------------------------------
# DependencyScanner cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_cache_stores_results(self, scanner: DependencyScanner):
        scanner._cache["test==1.0"] = [{"id": "VULN-1"}]
        scanner._save_cache()
        # Reload from disk
        scanner._load_cache()
        assert "test==1.0" in scanner._cache

    def test_cache_returns_cached(self, scanner: DependencyScanner):
        scanner._cache["cached==2.0"] = [{"id": "CACHED-VULN"}]
        scanner._save_cache()
        scanner._load_cache()
        assert scanner._cache["cached==2.0"][0]["id"] == "CACHED-VULN"

    @pytest.mark.asyncio
    async def test_update_database(self, scanner: DependencyScanner):
        scanner._cache["old==1.0"] = [{"id": "old"}]
        await scanner.update_database()
        assert scanner._cache == {}

    def test_cache_load_nonexistent(self, tmp_path: Path):
        scanner = DependencyScanner(cache_path=tmp_path / "no_cache.json")
        assert scanner._cache == {}


# ===================================================================
# DependabotStyleScanner tests
# ===================================================================


class TestDependabotStyleDataclasses:
    """Test the new dataclasses."""

    def test_scan_result_dataclass(self):
        vuln = Vulnerability("requests", "2.28.0", "CVE-1", "HIGH", "Test")
        result = ScanResult(
            package="requests",
            current_version="2.28.0",
            vulnerability=vuln,
            fix_available=True,
            fix_version="2.28.1",
            source="osv",
        )
        assert result.package == "requests"
        assert result.fix_available is True
        assert result.source == "osv"

    def test_scan_report_dataclass(self):
        report = ScanReport(
            scan_time="2025-01-01T00:00:00",
            total_packages=10,
            vulnerable_packages=2,
        )
        assert report.total_packages == 10
        assert report.results == []
        assert report.sources_checked == []

    def test_skill_scan_result_dataclass(self):
        result = SkillScanResult(skill_name="test", skill_path="/tmp/skill")
        assert result.skill_name == "test"
        assert result.issues == []
        assert result.vulnerable_deps == []


class TestDependabotStyleScanner:
    """Test the DependabotStyleScanner class."""

    def test_init_creates_db(self, dep_scanner: DependabotStyleScanner):
        """Test that init creates the SQLite database."""
        assert dep_scanner._db_path.exists()
        conn = dep_scanner._get_conn()
        # Verify tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "scan_history" in table_names
        assert "scan_results" in table_names

    def test_parse_dependencies(self, dep_scanner: DependabotStyleScanner, skill_with_both: Path):
        """Test dependency parsing."""
        deps = dep_scanner._parse_dependencies(skill_with_both)
        assert len(deps) == 2
        pkg_names = {d[0] for d in deps}
        assert "requests" in pkg_names
        assert "numpy" in pkg_names

    @pytest.mark.asyncio
    async def test_scan_all_dependencies_clean(self, dep_scanner: DependabotStyleScanner, tmp_path: Path):
        """Test scanning a project with no vulnerabilities."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("requests>=2.28.0\n")

        # Mock all sources to return empty
        with patch.object(dep_scanner, "_query_osv", new_callable=AsyncMock, return_value=[]), \
             patch.object(dep_scanner, "_query_github_advisories", new_callable=AsyncMock, return_value=[]), \
             patch.object(dep_scanner, "_query_nvd", new_callable=AsyncMock, return_value=[]):
            report = await dep_scanner.scan_all_dependencies(project)

        assert isinstance(report, ScanReport)
        assert report.total_packages == 1
        assert report.vulnerable_packages == 0
        assert report.results == []
        assert report.sources_checked == []

    @pytest.mark.asyncio
    async def test_scan_all_dependencies_with_vulns(self, dep_scanner: DependabotStyleScanner, tmp_path: Path):
        """Test scanning with mock vulnerabilities from multiple sources."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("requests==2.28.0\n")

        osv_vuln = ScanResult(
            package="requests",
            current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "GHSA-111", "HIGH", "RCE"),
            fix_available=True,
            fix_version="2.28.1",
            source="osv",
        )
        nvd_vuln = ScanResult(
            package="requests",
            current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "CVE-2024-1234", "HIGH", "RCE detail"),
            fix_available=False,
            source="nvd",
        )

        with patch.object(dep_scanner, "_query_osv", new_callable=AsyncMock, return_value=[osv_vuln]), \
             patch.object(dep_scanner, "_query_github_advisories", new_callable=AsyncMock, return_value=[]), \
             patch.object(dep_scanner, "_query_nvd", new_callable=AsyncMock, return_value=[nvd_vuln]):
            report = await dep_scanner.scan_all_dependencies(project)

        assert report.vulnerable_packages == 1
        assert len(report.results) == 2
        assert "osv" in report.sources_checked
        assert "nvd" in report.sources_checked

    @pytest.mark.asyncio
    async def test_scan_deduplicates_results(self, dep_scanner: DependabotStyleScanner, tmp_path: Path):
        """Test that duplicate vulns from different sources are deduplicated."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("requests==2.28.0\n")

        same_vuln_osv = ScanResult(
            package="requests", current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "GHSA-shared", "HIGH", "dup"),
            fix_available=True, source="osv",
        )
        same_vuln_gh = ScanResult(
            package="requests", current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "GHSA-shared", "HIGH", "dup"),
            fix_available=True, source="github_advisories",
        )
        diff_vuln = ScanResult(
            package="requests", current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "CVE-diff", "MEDIUM", "other"),
            fix_available=False, source="nvd",
        )

        with patch.object(dep_scanner, "_query_osv", new_callable=AsyncMock, return_value=[same_vuln_osv]), \
             patch.object(dep_scanner, "_query_github_advisories", new_callable=AsyncMock, return_value=[same_vuln_gh]), \
             patch.object(dep_scanner, "_query_nvd", new_callable=AsyncMock, return_value=[diff_vuln]):
            report = await dep_scanner.scan_all_dependencies(project)

        # GHSA-shared should appear only once, CVE-diff once
        vuln_ids = [r.vulnerability.vuln_id for r in report.results]
        assert vuln_ids.count("GHSA-shared") == 1
        assert "CVE-diff" in vuln_ids
        assert len(report.results) == 2

    @pytest.mark.asyncio
    async def test_scan_persists_to_sqlite(self, dep_scanner: DependabotStyleScanner, tmp_path: Path):
        """Test that scan results are stored in SQLite."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("flask==2.0.0\n")

        vuln = ScanResult(
            package="flask", current_version="2.0.0",
            vulnerability=Vulnerability("flask", "2.0.0", "CVE-persist", "CRITICAL", "XSS"),
            fix_available=True, fix_version="2.0.1", source="osv",
        )

        with patch.object(dep_scanner, "_query_osv", new_callable=AsyncMock, return_value=[vuln]), \
             patch.object(dep_scanner, "_query_github_advisories", new_callable=AsyncMock, return_value=[]), \
             patch.object(dep_scanner, "_query_nvd", new_callable=AsyncMock, return_value=[]):
            await dep_scanner.scan_all_dependencies(project)

        history = dep_scanner.get_scan_history()
        assert len(history) == 1
        assert history[0]["total_packages"] == 1
        assert history[0]["vulnerable_packages"] == 1

    def test_get_scan_history(self, dep_scanner: DependabotStyleScanner):
        """Test retrieving scan history."""
        # No history yet
        assert dep_scanner.get_scan_history() == []

        # Add some history manually
        from datetime import datetime
        now = datetime.now(UTC).isoformat()
        conn = dep_scanner._get_conn()
        conn.execute(
            "INSERT INTO scan_history (scan_time, total_packages, vulnerable_packages, sources_checked, results_json) VALUES (?, ?, ?, ?, ?)",
            (now, 5, 2, '["osv"]', "[]"),
        )
        conn.commit()

        history = dep_scanner.get_scan_history(limit=5)
        assert len(history) == 1
        assert history[0]["total_packages"] == 5

    @pytest.mark.asyncio
    async def test_scan_skill_manifests(self, dep_scanner: DependabotStyleScanner, skills_dir: Path):
        """Test skill manifest scanning."""
        with patch.object(dep_scanner, "_query_osv", new_callable=AsyncMock, return_value=[]):
            results = await dep_scanner.scan_skill_manifests(str(skills_dir))

        # Should find 3 skills (clean, suspicious, escalation), skip no-manifest
        assert len(results) == 3
        names = {r.skill_name for r in results}
        assert "clean-skill" in names
        assert "suspicious-skill" in names
        assert "escalation-skill" in names

        # Check that suspicious skill has issues
        suspicious = next(r for r in results if r.skill_name == "suspicious-skill")
        assert len(suspicious.issues) > 0
        assert any("suspicious" in i.lower() for i in suspicious.issues)

        # Check that escalation skill has issues
        esc = next(r for r in results if r.skill_name == "escalation-skill")
        assert len(esc.issues) > 0
        assert any("sudo" in i.lower() for i in esc.issues)

    @pytest.mark.asyncio
    async def test_scan_skill_manifests_nonexistent_dir(self, dep_scanner: DependabotStyleScanner):
        """Test scanning a nonexistent skills directory."""
        results = await dep_scanner.scan_skill_manifests("/nonexistent/path")
        assert results == []

    @pytest.mark.asyncio
    async def test_create_github_issue_success(self, dep_scanner: DependabotStyleScanner):
        """Test creating a GitHub issue (mocked)."""
        vuln = Vulnerability("requests", "2.28.0", "GHSA-test", "HIGH", "Test vuln", "2.28.1")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "https://github.com/test/repo/issues/42\n"

        with patch("kazma_core.security.dependency_scanner.asyncio.to_thread", new_callable=AsyncMock, return_value=mock_proc):
            url = await dep_scanner.create_github_issue(vuln)
            assert url == "https://github.com/test/repo/issues/42"

    @pytest.mark.asyncio
    async def test_create_github_issue_gh_not_installed(self, dep_scanner: DependabotStyleScanner):
        """Test creating issue when gh CLI is not installed."""
        vuln = Vulnerability("requests", "2.28.0", "GHSA-test", "HIGH", "Test")

        with patch("kazma_core.security.dependency_scanner.asyncio.to_thread", new_callable=AsyncMock, side_effect=FileNotFoundError):
            result = await dep_scanner.create_github_issue(vuln)
            assert "gh CLI not installed" in result

    @pytest.mark.asyncio
    async def test_generate_advisory(self, dep_scanner: DependabotStyleScanner):
        """Test advisory generation."""
        vuln = Vulnerability("flask", "2.0.0", "GHSA-advisory", "CRITICAL", "XSS in templates")
        advisory = await dep_scanner.generate_advisory(vuln)

        assert "cve_id" in advisory
        assert advisory["cve_id"].startswith("CVE-")
        assert advisory["package"] == "flask"
        assert advisory["severity"] == "CRITICAL"
        assert "advisory_content" in advisory
        assert "flask" in advisory["advisory_content"]

    @pytest.mark.asyncio
    async def test_check_for_updates(self, dep_scanner: DependabotStyleScanner, tmp_path: Path):
        """Test checking for security updates."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("requests==2.28.0\n")

        fix_vuln = ScanResult(
            package="requests", current_version="2.28.0",
            vulnerability=Vulnerability("requests", "2.28.0", "GHSA-fix", "HIGH", "Fix available"),
            fix_available=True, fix_version="2.28.1", source="osv",
        )

        with patch.object(dep_scanner, "_query_osv_single", new_callable=AsyncMock, return_value=[fix_vuln]):
            updates = await dep_scanner.check_for_updates(project)

        assert len(updates) == 1
        assert updates[0]["package"] == "requests"
        assert updates[0]["fix_version"] == "2.28.1"

    @pytest.mark.asyncio
    async def test_query_osv_mocked(self, dep_scanner: DependabotStyleScanner):
        """Test OSV query with mocked HTTP."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "vulns": [
                {
                    "id": "GHSA-mock",
                    "summary": "Mock vuln",
                    "severity": [{"type": "CVSS_V3", "score": "7.5"}],
                    "affected": [
                        {"ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.0.1"}]}]}
                    ],
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("kazma_core.security.dependency_scanner.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            results = await dep_scanner._query_osv([("requests", "1.0.0")])
            assert len(results) == 1
            assert results[0].source == "osv"
            assert results[0].fix_version == "1.0.1"
