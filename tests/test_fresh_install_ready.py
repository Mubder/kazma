"""CI's fresh-install gate reads readiness's real answer.

The step grepped ``"needs_setup": true``; readiness lists the checks
(``"needs_setup":["llm_provider"]``), so every fresh install failed the gate
from the day it was added. The check is ``scripts/fresh_install_ready.py`` now,
fed here the body the real readiness builds.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from kazma_ui import health
from tests.test_readiness_needs_setup import _readiness, ready_except_the_provider  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "fresh_install_ready.py"


def _module():
    spec = importlib.util.spec_from_file_location("fresh_install_ready", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(status: int, body: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    path = tmp_path / "ready.json"
    path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(status), str(path)],
                          capture_output=True, text=True, timeout=60)


def test_a_fresh_install_waiting_for_its_model_passes(ready_except_the_provider, tmp_path):  # noqa: F811
    ready_except_the_provider(api_key="")
    code, body = _readiness()
    assert code == 503
    done = _run(code, body, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "llm_provider" in done.stdout


def test_control_the_old_grep_refused_that_same_answer(ready_except_the_provider):  # noqa: F811
    ready_except_the_provider(api_key="")
    _code, body = _readiness()
    as_served = json.dumps(body, separators=(",", ":"))
    assert not re.search(r'"needs_setup": *true', as_served)


def test_a_real_failure_fails_the_gate(ready_except_the_provider, monkeypatch, tmp_path):  # noqa: F811
    ready_except_the_provider(api_key="")
    monkeypatch.setattr(health, "check_database",
                        lambda: {"status": "failed", "component": "database", "error": "timed out"})
    code, body = _readiness()
    done = _run(code, body, tmp_path)
    assert done.returncode == 1 and "not for setup" in done.stdout


def test_setup_beside_another_failure_fails():
    verdict = _module().verdict
    assert verdict(200, {}) is None
    assert verdict(503, {"needs_setup": ["llm_provider"],
                         "unavailable_capabilities": ["llm_provider", "database"]})


def test_the_ci_step_runs_the_script():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  fresh-install:"):ci.index("\n  upgrade-drill:")]
    assert "scripts/fresh_install_ready.py" in job
    assert '"needs_setup": *true' not in job
