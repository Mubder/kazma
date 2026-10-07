"""Run the workflow's actual Python payloads against synthetic negative inputs."""
from __future__ import annotations

import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/skill-review.yml"


def payload(name):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["security-scan"]["steps"] if s.get("name") == name)
    return step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


@pytest.mark.parametrize("unsafe", [False, True])
def test_secret_scan_uses_actual_native_tree_and_never_prints_values(tmp_path, monkeypatch, capsys, unsafe):
    root = tmp_path / "kazma-skills/kazma_skills/native/proof"
    root.mkdir(parents=True)
    credential = secrets.token_urlsafe(32)
    value = credential if unsafe else "vault://proof"
    (root / "proof.yaml").write_text(f"api_key: {value}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as result:
        exec(compile(payload("Verify No Hardcoded Secrets"), str(WORKFLOW), "exec"), {})
    assert bool(result.value.code) is unsafe
    assert credential not in capsys.readouterr().out


def test_missing_skill_trees_do_not_report_clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as result:
        exec(compile(payload("Verify No Hardcoded Secrets"), str(WORKFLOW), "exec"), {})
    assert result.value.code == 1


@pytest.mark.parametrize("open_advisories", [False, True])
def test_dependency_scan_fails_on_unreviewed_advisories(monkeypatch, open_advisories):
    from kazma_core.security import dependency_scanner

    async def audit():
        item = SimpleNamespace(package="proof", version="1", vuln_id="synthetic-advisory")
        return SimpleNamespace(total=1, open_vulnerabilities=[item] if open_advisories else [])

    monkeypatch.setattr(dependency_scanner, "audit_installed", audit)
    with pytest.raises(SystemExit) as result:
        exec(compile(payload("Check Dependency Vulnerabilities"), str(WORKFLOW), "exec"), {})
    assert bool(result.value.code) is open_advisories
