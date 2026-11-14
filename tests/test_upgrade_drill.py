"""Every release is tested as an upgrade from the one before (2026-11-14,
REMAINING 2.6; ``scripts/upgrade_drill.py``).

The drill's own judgements are checked here, each with the answer it must
refuse; the release workflow must run it before it publishes, and CI on both
systems. The whole drill (the previous release from GitHub, two servers, an
upgrade) is opt-in: ``KAZMA_TEST_UPGRADE_DRILL=1`` and a wheel in
``KAZMA_TEST_UPGRADE_WHEEL``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "upgrade_drill.py"


def _drill():
    spec = importlib.util.spec_from_file_location("_upgrade_drill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ready_means_ready_or_waiting_only_for_a_model() -> None:
    d = _drill()
    assert d.ready_is_fine(200, {"status": "ready"})
    assert d.ready_is_fine(503, {"status": "not_ready", "needs_setup": True})
    # Negative controls: not ready for any other reason, or no answer.
    assert not d.ready_is_fine(503, {"status": "not_ready", "needs_setup": False})
    assert not d.ready_is_fine(503, {"status": "not_ready"})
    assert not d.ready_is_fine(503, None)
    assert not d.ready_is_fine(0, None)


def test_an_error_line_or_a_traceback_fails_the_upgrade() -> None:
    d = _drill()
    log = "\n".join([
        '{"level": "INFO", "message": "started"}',
        '{"level": "ERROR", "message": "boom"}',
        "2026-11-14 10:00:00 ERROR kazma_ui.app: failed",
        "Traceback (most recent call last):",
        "INFO all 0 errors cleared",
    ])
    assert len(d.error_lines(log)) == 3
    assert d.error_lines('{"level": "INFO", "message": "fine"}\nINFO: ready') == []


def test_the_drill_sees_nothing_of_an_install_on_this_machine(monkeypatch, tmp_path) -> None:
    d = _drill()
    for name, value in {"KAZMA_SECRET": "s", "KAZMA_DATA_DIR": "C:/live", "OPENAI_API_KEY": "k",
                        "DATABASE_URL": "postgres://x", "PGPASSWORD": "p", "TELEGRAM_BOT_TOKEN": "t",
                        "GITHUB_TOKEN": "g"}.items():
        monkeypatch.setenv(name, value)
    env = d.scrubbed_env(tmp_path / "home", tmp_path / "data", 9876)
    assert env["KAZMA_DATA_DIR"] == str(tmp_path / "data") and env["KAZMA_PORT"] == "9876"
    assert env["KAZMA_USER_HOME"] == str(tmp_path / "home") and env["KAZMA_DB_BACKEND"] == "sqlite"
    for name in ("KAZMA_SECRET", "OPENAI_API_KEY", "DATABASE_URL", "PGPASSWORD", "TELEGRAM_BOT_TOKEN",
                 "GITHUB_TOKEN"):
        assert name not in env, name
    assert "PATH" in env or "Path" in env


def test_a_release_is_never_upgraded_from_itself_unless_ci_asks(monkeypatch) -> None:
    from kazma_cli import update

    d = _drill()
    monkeypatch.setattr(update, "get_latest_release", lambda: update.ReleaseInfo(version="0.22.0"))
    with pytest.raises(SystemExit):
        d.previous_release(None, "0.22.0")
    assert d.previous_release(None, "0.22.0", any_version=True).version == "0.22.0"
    assert d.previous_release(None, "0.23.0").version == "0.22.0"


def test_the_release_runs_the_drill_before_it_publishes_and_ci_runs_it_on_both_systems() -> None:
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    drill = release.index("python scripts/upgrade_drill.py --wheel")
    assert drill < release.index("- name: Publish the GitHub Release")
    assert release.index("uv build --out-dir dist") < drill
    assert "--any-version" not in release[drill:drill + 200]
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  upgrade-drill:"):]
    job = job[:job.index("\n  windows-selector-loop:")]
    assert "os: [ubuntu-latest, windows-latest]" in job
    assert "-o kazma-cli/kazma_cli/locked_versions.txt" in job and "scripts/upgrade_drill.py" in job


@pytest.mark.skipif(os.environ.get("KAZMA_TEST_UPGRADE_DRILL") != "1", reason="opt-in: the whole drill")
def test_the_whole_drill() -> None:
    wheel = Path(os.environ["KAZMA_TEST_UPGRADE_WHEEL"])
    assert _drill().drill(wheel, os.environ.get("KAZMA_TEST_UPGRADE_FROM"), any_version=True) == []
