"""Tests for the SecurityHardeningRunner module."""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.security.hardening import (
    HardeningCheck,
    HardeningReport,
    SecurityHardeningRunner,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner(tmp_path) -> SecurityHardeningRunner:
    """Fresh runner pointing at a temp project directory."""
    return SecurityHardeningRunner(project_root=tmp_path)


@pytest.fixture
def project_with_secrets(tmp_path) -> Path:
    """A project containing a hardcoded API key."""
    (tmp_path / "config.py").write_text(
        'API_KEY = "supersecretkey1234567890"\nDATABASE_URL = "postgres://user:pass@host/db"\n'
    )
    return tmp_path


@pytest.fixture
def project_clean(tmp_path) -> Path:
    """A clean project with no secrets."""
    (tmp_path / "main.py").write_text('import os\napi_key = os.environ.get("API_KEY")\nprint("Hello world")\n')
    return tmp_path


@pytest.fixture
def project_with_eval(tmp_path) -> Path:
    """A project with eval() usage (privilege escalation vector)."""
    (tmp_path / "loader.py").write_text("def load(code):\n    return eval(code)\n")
    return tmp_path


@pytest.fixture
def project_with_sandbox(tmp_path) -> Path:
    """A project with sandbox config."""
    (tmp_path / "kazma.yaml").write_text("sandbox:\n  enabled: true\n  memory_limit: 512MB\n")
    return tmp_path


@pytest.fixture
def project_with_manifest(tmp_path) -> Path:
    """A project with valid skill manifests."""
    skills = tmp_path / "skills"
    skill = skills / "my-skill"
    skill.mkdir(parents=True)
    (skill / "manifest.yaml").write_text("name: my-skill\nversion: 1.0.0\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_hardening_check_fields(self):
        check = HardeningCheck(
            name="test_check",
            passed=True,
            severity="high",
            message="All good",
            recommendation="Keep it up",
        )
        assert check.name == "test_check"
        assert check.passed is True
        assert check.severity == "high"

    def test_hardening_report_fields(self):
        report = HardeningReport(
            total=5,
            passed=3,
            failed=2,
            critical_failures=1,
            timestamp="2025-01-01T00:00:00",
        )
        assert report.total == 5
        assert report.critical_failures == 1
        assert report.checks == []


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    @pytest.mark.asyncio
    async def test_returns_hardening_report(self, runner: SecurityHardeningRunner):
        report = await runner.run_all_checks()
        assert isinstance(report, HardeningReport)
        assert report.total == 8  # 8 checks in CHECKS list
        assert report.timestamp is not None

    @pytest.mark.asyncio
    async def test_report_counts(self, runner: SecurityHardeningRunner):
        report = await runner.run_all_checks()
        assert report.passed + report.failed == report.total
        assert report.critical_failures <= report.failed

    @pytest.mark.asyncio
    async def test_secrets_check_fails_on_secrets(self, project_with_secrets: Path):
        runner = SecurityHardeningRunner(project_root=project_with_secrets)
        check = await runner.run_check("check_no_hardcoded_secrets")
        assert check.passed is False
        assert check.severity == "critical"
        assert "hardcoded secrets" in check.message.lower()

    @pytest.mark.asyncio
    async def test_secrets_check_passes_on_clean(self, project_clean: Path):
        runner = SecurityHardeningRunner(project_root=project_clean)
        check = await runner.run_check("check_no_hardcoded_secrets")
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_secrets_check_skips_venv(self, tmp_path: Path):
        """Secrets in .venv directories should be skipped."""
        venv_dir = tmp_path / ".venv" / "lib"
        venv_dir.mkdir(parents=True)
        (venv_dir / "bad.py").write_text('API_KEY = "shouldbefoundbutvenv"\n')
        runner = SecurityHardeningRunner(project_root=tmp_path)
        check = await runner.run_check("check_no_hardcoded_secrets")
        assert check.passed is True


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


class TestIndividualChecks:
    @pytest.mark.asyncio
    async def test_permission_escalation_detects_eval(self, project_with_eval: Path):
        runner = SecurityHardeningRunner(project_root=project_with_eval)
        check = await runner.run_check("check_permission_escalation")
        assert check.passed is False
        assert check.severity == "critical"
        assert "privilege escalation" in check.message.lower()

    @pytest.mark.asyncio
    async def test_mcp_sandboxing_found(self, project_with_sandbox: Path):
        runner = SecurityHardeningRunner(project_root=project_with_sandbox)
        check = await runner.run_check("check_mcp_sandboxing")
        assert check.passed is True

    @pytest.mark.asyncio
    async def test_mcp_sandboxing_missing(self, tmp_path: Path):
        runner = SecurityHardeningRunner(project_root=tmp_path)
        check = await runner.run_check("check_mcp_sandboxing")
        assert check.passed is False
        assert check.severity == "high"

    @pytest.mark.asyncio
    async def test_skill_manifest_valid(self, project_with_manifest: Path):
        runner = SecurityHardeningRunner(project_root=project_with_manifest)
        check = await runner.run_check("check_skill_manifest_validity")
        assert check.passed is True
        assert "1 skill manifests" in check.message

    @pytest.mark.asyncio
    async def test_unknown_check_raises(self, runner: SecurityHardeningRunner):
        with pytest.raises(ValueError, match="Unknown check"):
            await runner.run_check("nonexistent_check")

    @pytest.mark.asyncio
    async def test_rbac_check(self, runner: SecurityHardeningRunner):
        """RBAC check on an empty project (no .py files with rbac indicators)."""
        check = await runner.run_check("check_rbac_enforcement")
        assert check.severity == "high"
        # May pass or fail depending on existing project files


# ---------------------------------------------------------------------------
# Report generation (markdown)
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_report_before_checks(self, runner: SecurityHardeningRunner):
        """Report before running checks should say no checks run."""
        report = runner.generate_report()
        assert "Security Hardening Report" in report
        assert "No checks have been run yet" in report

    @pytest.mark.asyncio
    async def test_report_after_checks(self, runner: SecurityHardeningRunner):
        """Report after running checks should include results."""
        await runner.run_all_checks()
        report = runner.generate_report()
        assert "## Summary" in report
        assert "## Check Results" in report
        assert "Total checks" in report
        assert "Passed" in report
        assert "Failed" in report

    @pytest.mark.asyncio
    async def test_report_includes_check_names(self, runner: SecurityHardeningRunner):
        await runner.run_all_checks()
        report = runner.generate_report()
        assert "check_no_hardcoded_secrets" in report
        assert "check_mcp_sandboxing" in report
        assert "check_permission_escalation" in report


# ---------------------------------------------------------------------------
# fix_issues
# ---------------------------------------------------------------------------


class TestFixIssues:
    @pytest.mark.asyncio
    async def test_fix_issues_dry_run(self, project_with_secrets: Path):
        runner = SecurityHardeningRunner(project_root=project_with_secrets)
        await runner.run_all_checks()
        fixes = await runner.fix_issues(auto_fix=False)
        assert isinstance(fixes, list)
        # Should have at least the secrets fix
        assert len(fixes) > 0
        secrets_fix = next((f for f in fixes if f["check"] == "check_no_hardcoded_secrets"), None)
        assert secrets_fix is not None
        assert secrets_fix["applied"] is False

    @pytest.mark.asyncio
    async def test_fix_issues_auto_fix(self, project_with_secrets: Path):
        runner = SecurityHardeningRunner(project_root=project_with_secrets)
        await runner.run_all_checks()
        fixes = await runner.fix_issues(auto_fix=True)
        secrets_fix = next((f for f in fixes if f["check"] == "check_no_hardcoded_secrets"), None)
        assert secrets_fix is not None
        assert secrets_fix["applied"] is True
        # Should have created .env and updated .gitignore
        assert (project_with_secrets / ".env").exists()
        assert (project_with_secrets / ".gitignore").exists()
        assert ".env" in (project_with_secrets / ".gitignore").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_fix_issues_no_failures(self, project_clean: Path):
        runner = SecurityHardeningRunner(project_root=project_clean)
        await runner.run_all_checks()
        fixes = await runner.fix_issues(auto_fix=True)
        # If all checks pass, no fixes should be suggested
        # (other checks may still fail, but secrets should pass)
        secrets_fix = next((f for f in fixes if f["check"] == "check_no_hardcoded_secrets"), None)
        assert secrets_fix is None  # No secrets fix needed
