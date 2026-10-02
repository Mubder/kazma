"""The security report measures what each check names.

Live 2026-10-02 the report said 4 of 8 failed, 3 critical, and none of it
was true: 48 "secrets" and 576 "escalation vectors" came from a package
cache inside the install, 202 "vulnerable dependencies" from asking OSV about
requirement minimums, and "no skill manifests" from looking for the wrong
file name. The four that passed did so on a keyword found anywhere.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import pytest
from kazma_core.security import dependency_scanner, hardening
from kazma_core.security.hardening import HardeningCheck, HardeningReport, SecurityHardeningRunner

_ENV = (
    "KAZMA_HOST", "KAZMA_TRUSTED_PROXIES", "KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS",
    "KAZMA_DEMO_MODE", "KAZMA_SECRET", "KAZMA_PUBLIC_URL", "KAZMA_MCP_SCOPE_GUARD",
    "KAZMA_GATE_REGISTRY", "KAZMA_PRODUCTION",
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No network, a private gate registry, and none of the posture switches."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)

    async def nothing_installed(**_: Any) -> dependency_scanner.DependencyReport:
        return dependency_scanner.DependencyReport(total=0)

    monkeypatch.setattr(dependency_scanner, "audit_installed", nothing_installed)
    monkeypatch.setattr("kazma_core.install_requirements.unmet_requirements", lambda root=None: [])
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(str(tmp_path / "gates.db"))
    yield
    hitl_gates.set_db_path_for_tests(None)


def _run(root: Path, check: str) -> HardeningCheck:
    return asyncio.run(SecurityHardeningRunner(root).run_check(check))


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _key() -> str:
    return secrets.token_hex(20)


# ── the report ──────────────────────────────────────────────────────────


def test_dataclasses_keep_their_fields() -> None:
    check = HardeningCheck(name="c", passed=True, severity="high", message="m", recommendation="r")
    assert (check.name, check.passed, check.severity) == ("c", True, "high")
    report = HardeningReport(total=5, passed=3, failed=2, critical_failures=1, timestamp="t")
    assert (report.total, report.critical_failures, report.checks) == (5, 1, [])


def test_every_check_runs_and_is_counted(tmp_path: Path) -> None:
    runner = SecurityHardeningRunner(tmp_path)
    report = asyncio.run(runner.run_all_checks())
    assert [c.name for c in report.checks] == SecurityHardeningRunner.CHECKS
    assert report.total == 8 and report.passed + report.failed == 8
    assert report.critical_failures <= report.failed
    markdown = runner.generate_report()
    assert "## Check Results" in markdown and "check_permission_escalation" in markdown


def test_a_check_that_cannot_run_is_a_failure_not_a_missing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(self: SecurityHardeningRunner) -> HardeningCheck:
        raise RuntimeError("the registry is on fire")

    monkeypatch.setattr(SecurityHardeningRunner, "check_audit_logging", broken)
    report = asyncio.run(SecurityHardeningRunner(tmp_path).run_all_checks())
    audit = next(c for c in report.checks if c.name == "check_audit_logging")
    assert audit.passed is False and "the registry is on fire" in audit.message
    assert report.total == 8


def test_an_unknown_check_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown check"):
        _run(tmp_path, "check_nothing")
    with pytest.raises(ValueError, match="Unknown check"):
        _run(tmp_path, "_product_files")


def test_nothing_writes_into_the_install() -> None:
    """``fix_issues`` wrote a ``.env`` and edited ``.gitignore`` in the
    install's folder; nothing called it, and it is gone."""
    assert not hasattr(SecurityHardeningRunner, "fix_issues")


# ── which files are read ────────────────────────────────────────────────


def test_the_scans_read_product_files_only(tmp_path: Path) -> None:
    """The live install held a package cache (``AppData/Local/uv/cache``,
    left by a command that ran with HOME at the install): the scans read it
    and reported its code. Environments, caches, data, task worktrees,
    tests and docs are not the install's product."""
    for rel in (
        "src/app.py", "config/kazma.yaml", "deploy.env.example",
        ".venv/lib/site.py", ".git/hooks/h.py", "kazma-data/workspace/w.py",
        ".claude/worktrees/task/other.py", "AppData/Local/uv/cache/pkg/mod.py",
        "tests/test_app.py", "pkg_tests/x.py", "docs/build/app.js", "src/test_thing.py",
        "src/conftest.py", ".env",
    ):
        _write(tmp_path, rel, "x = 1\n")

    found = sorted(SecurityHardeningRunner(tmp_path)._rel(p) for p in SecurityHardeningRunner(tmp_path)._product_files())
    assert found == ["config/kazma.yaml", "deploy.env.example", "src/app.py"]


def test_a_repository_s_ignored_folders_are_not_read(tmp_path: Path) -> None:
    import subprocess

    from kazma_core.security.child_env import tool_child_env

    _write(tmp_path, ".gitignore", "core/\n")
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, "core/cloned/leak.py", f'api_key = "{_key()}"\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=tool_child_env())

    check = _run(tmp_path, "check_no_hardcoded_secrets")
    assert check.passed is True, check.recommendation
    # Negative control: the same file outside the ignored folder is found.
    _write(tmp_path, "settings.py", f'api_key = "{_key()}"\n')
    assert _run(tmp_path, "check_no_hardcoded_secrets").passed is False


# ── secrets ─────────────────────────────────────────────────────────────


def test_a_credential_in_the_install_fails_the_check(tmp_path: Path) -> None:
    key = _key()
    _write(tmp_path, "pkg/settings.py", f'API_KEY = "{key}"\n')
    _write(tmp_path, "kazma.yaml", f"providers:\n  x:\n    api_key: {_key()}\n")
    check = _run(tmp_path, "check_no_hardcoded_secrets")
    assert check.passed is False and check.severity == "critical"
    assert "pkg/settings.py:1 (credential)" in check.recommendation
    assert "kazma.yaml:3 (credential)" in check.recommendation
    assert key not in check.recommendation + check.message


def test_stand_ins_and_the_env_file_are_not_credentials(tmp_path: Path) -> None:
    _write(tmp_path, "serve.py", '_KNOWN_BAD_SECRET = "kazma-local-dev-secret"\n')
    _write(tmp_path, "oauth.py", 'TOKEN_URL = "https://oauth2.googleapis.com/token"\ntoken_env = "KAZMA_X_TOKEN"\n')
    _write(tmp_path, "kazma.yaml", "api_key: ${DEEPSEEK_API_KEY}\n")
    _write(tmp_path, ".env", f"DEEPSEEK_API_KEY={_key()}\n")  # where a key belongs
    check = _run(tmp_path, "check_no_hardcoded_secrets")
    assert check.passed is True, check.recommendation
    assert "3 product files" in check.message


# ── shell and eval calls ────────────────────────────────────────────────


_RISKY = {
    "shell=True": "import subprocess\n\ndef f(cmd):\n    return subprocess.run(cmd, shell=True)\n",
    "an alias": "import subprocess as sp\n\ndef f(cmd):\n    sp.call(cmd, shell=True)\n",
    "an imported name": "from subprocess import getoutput\n\ndef f(cmd):\n    return getoutput(cmd)\n",
    "shell decided at run time": "import subprocess\n\ndef f(cmd, s):\n    subprocess.Popen(cmd, shell=s)\n",
    "os.system": "import os\nos.system('ls')\n",
    "eval": "def load(code):\n    return eval(code)\n",
    "asyncio shell": "import asyncio\n\nasync def f(c):\n    await asyncio.create_subprocess_shell(c)\n",
}

_SAFE = {
    "an argument list": "import subprocess\n\ndef f():\n    subprocess.run(['git', 'status'], check=False)\n",
    "shell=False": "import subprocess\n\ndef f(c):\n    subprocess.run(c, shell=False)\n",
    "a method named execute": "def f(cursor):\n    cursor.execute('SELECT 1')\n    cursor.exec('x')\n",
    "a method named eval": "def f(model):\n    model.eval()\n",
}


@pytest.mark.parametrize("name", sorted(_RISKY))
def test_a_shell_or_eval_call_fails_the_check(tmp_path: Path, name: str) -> None:
    _write(tmp_path, "pkg/mod.py", _RISKY[name])
    check = _run(tmp_path, "check_permission_escalation")
    assert check.passed is False and check.severity == "critical", name
    assert "pkg/mod.py:" in check.recommendation


@pytest.mark.parametrize("name", sorted(_SAFE))
def test_ordinary_calls_pass(tmp_path: Path, name: str) -> None:
    _write(tmp_path, "pkg/mod.py", _SAFE[name])
    assert _run(tmp_path, "check_permission_escalation").passed is True, name


def test_a_reviewed_site_passes_with_its_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "pkg/hooks.py", _RISKY["shell=True"])
    monkeypatch.setitem(
        hardening._REVIEWED_SITES, ("pkg/hooks.py", "f", "subprocess.run(shell=True)"), "the operator's own command",
    )
    check = _run(tmp_path, "check_permission_escalation")
    assert check.passed is True and "the operator's own command" in check.message
    # The review names the function: the same call anywhere else is unreviewed.
    _write(tmp_path, "pkg/other.py", _RISKY["shell=True"])
    assert _run(tmp_path, "check_permission_escalation").passed is False


def test_the_reviewed_list_matches_the_code() -> None:
    """Each reviewed site still exists, so a stale entry cannot hide a new one."""
    root = Path(__file__).resolve().parents[1]
    for (rel, function, label) in hardening._REVIEWED_SITES:
        calls = hardening._risky_calls((root / rel).read_text(encoding="utf-8"))
        assert any(f == function and lab == label for _line, f, lab in calls), (rel, function, label)


def test_tests_are_not_product_code(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_x.py", _RISKY["eval"])
    assert _run(tmp_path, "check_permission_escalation").passed is True


# ── who reaches the API ─────────────────────────────────────────────────


@pytest.mark.parametrize(("env", "passed", "severity"), [
    ({}, True, "high"),
    ({"KAZMA_HOST": "0.0.0.0", "KAZMA_SECRET": "x" * 32}, True, "high"),
    ({"KAZMA_HOST": "0.0.0.0", "KAZMA_AUTH_DISABLED": "1"}, False, "critical"),
    ({"KAZMA_TRUSTED_PROXIES": "127.0.0.1", "KAZMA_AUTH_DISABLED": "1"}, False, "critical"),
    ({"KAZMA_TRUSTED_PROXIES": "127.0.0.1", "KAZMA_DEV_WS_BYPASS": "1"}, False, "critical"),
    ({"KAZMA_HOST": "0.0.0.0", "KAZMA_DEMO_MODE": "1"}, False, "high"),
    ({"KAZMA_SECRET": "short-secret"}, False, "medium"),
    ({"KAZMA_AUTH_DISABLED": "1"}, True, "high"),
])
def test_access_is_judged_from_the_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], passed: bool, severity: str,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    check = _run(tmp_path, "check_rbac_enforcement")
    assert (check.passed, check.severity) == (passed, severity), check.message


def test_access_no_longer_passes_on_a_keyword(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The old check passed when any Python file said "permission"."""
    _write(tmp_path, "rbac.py", "# permission rbac authorization\n")
    monkeypatch.setenv("KAZMA_HOST", "0.0.0.0")
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert _run(tmp_path, "check_rbac_enforcement").passed is False


# ── TLS ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("env", "passed", "severity"), [
    ({}, True, "high"),
    ({"KAZMA_TRUSTED_PROXIES": "127.0.0.1", "KAZMA_PUBLIC_URL": "https://my.example.org"}, True, "high"),
    ({"KAZMA_TRUSTED_PROXIES": "127.0.0.1"}, False, "medium"),
    ({"KAZMA_HOST": "0.0.0.0"}, False, "high"),
    ({"KAZMA_HOST": "0.0.0.0", "KAZMA_PUBLIC_URL": "http://box:9090"}, False, "high"),
])
def test_tls_is_judged_from_exposure_and_the_public_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], passed: bool, severity: str,
) -> None:
    _write(tmp_path, "kazma.yaml", "public_url: https://example.org\ntls: true\n")  # the old keyword pass
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    check = _run(tmp_path, "check_encrypted_communications")
    assert (check.passed, check.severity) == (passed, severity), check.message


def test_packages_behind_this_build_fail_the_dependency_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy is a pull: the environment can lag the build's minimums even
    when OSV knows no advisory for what is installed."""
    from kazma_core.install_requirements import UnmetRequirement

    behind = [UnmetRequirement("pyjwt", ">=2.15.0", "2.13.0", ("base",))]
    monkeypatch.setattr("kazma_core.install_requirements.unmet_requirements", lambda root=None: behind)
    check = _run(tmp_path, "check_dependency_vulnerabilities")
    assert check.passed is False and check.severity == "high"
    assert "pyjwt >=2.15.0: 2.13.0 installed" in check.message
    assert "kazma update" in check.recommendation


# ── approvals are recorded ──────────────────────────────────────────────


def test_the_approval_registry_is_the_audit_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety import hitl_gates

    gate = hitl_gates.register_gate(hitl_gates.GateRow(
        gate_id="g1", thread_id="t1", tool="shell_exec", created_at=1.0,
    ))
    hitl_gates.claim_gate(gate.gate_id, "approved", "owner")
    check = _run(tmp_path, "check_audit_logging")
    assert check.passed is True and "(1 in hitl_gates.db)" in check.message

    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    check = _run(tmp_path, "check_audit_logging")
    assert check.passed is False and "not recorded" in check.message


# ── MCP ─────────────────────────────────────────────────────────────────


def test_mcp_secrets_typed_into_the_config_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core import mcp_servers_store

    monkeypatch.setattr(mcp_servers_store, "_cs_get", lambda: [])
    _write(tmp_path, "kazma.yaml", (
        "mcp:\n  servers:\n"
        "  - name: notes\n    command: [npx, notes-server]\n    env:\n      NOTES_API_KEY: vault://cfg:mcp.servers.notes.env.NOTES_API_KEY\n"
    ))
    assert _run(tmp_path, "check_mcp_sandboxing").passed is True

    _write(tmp_path, "kazma.yaml", (
        "mcp:\n  servers:\n"
        f"  - name: notes\n    command: [npx, notes-server]\n    env:\n      NOTES_API_KEY: {_key()}\n"
    ))
    check = _run(tmp_path, "check_mcp_sandboxing")
    assert check.passed is False and "notes" in check.message

    _write(tmp_path, "kazma.yaml", "mcp:\n  servers: []\n")
    monkeypatch.setenv("KAZMA_MCP_SCOPE_GUARD", "0")
    check = _run(tmp_path, "check_mcp_sandboxing")
    assert check.passed is False and "KAZMA_MCP_SCOPE_GUARD=0" in check.message


# ── skills ──────────────────────────────────────────────────────────────


def test_built_in_skills_are_checked_as_the_loader_loads_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old check looked for ``manifest.yaml`` under ``skills/``; the
    built-in skills name theirs ``skill_manifest.yaml``, so it never read
    one and failed with "No skill manifests found"."""
    check = _run(tmp_path, "check_skill_manifest_validity")
    assert check.passed is True, check.recommendation
    assert "built-in skills load" in check.message

    from kazma_skills.native_loader import NativeSkillLoader

    monkeypatch.setattr(NativeSkillLoader, "problems", lambda self: {"x_publisher": ["tool 'x_post' has no function"]})
    check = _run(tmp_path, "check_skill_manifest_validity")
    assert check.passed is False and "x_publisher" in check.recommendation


def test_an_agent_skill_activation_would_refuse_fails_the_check(tmp_path: Path) -> None:
    from kazma_core.agent_skills.integrity import write_install_meta

    skill = tmp_path / ".agents" / "skills" / "notes-helper"
    _write(skill, "SKILL.md", "---\nname: notes-helper\ndescription: Helps with notes.\n---\nDo things.\n")
    assert _run(tmp_path, "check_skill_manifest_validity").passed is True  # unsigned: loads with a warning

    write_install_meta(skill, {"checksum": "0" * 64})  # what was installed is not what is there
    check = _run(tmp_path, "check_skill_manifest_validity")
    assert check.passed is False and "notes-helper: refused at activation" in check.recommendation

    _write(tmp_path / ".agents" / "skills" / "broken", "SKILL.md", "no frontmatter\n")
    check = _run(tmp_path, "check_skill_manifest_validity")
    assert "not a usable SKILL.md" in check.recommendation
