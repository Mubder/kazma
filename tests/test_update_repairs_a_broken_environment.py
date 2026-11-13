"""A failed package install never leaves Kazma unable to start, unseen.

Live, 2026-11-13: ``kazma update --reinstall -y`` failed 26 s into installing
new floors. Its fallback, ``uv sync --inexact``, moved every package to
uv.lock's exact version -- hundreds on an install that runs newer versions
within the floors -- and a failure partway through removed about 200
packages, Kazma itself among them. The check that it worked looked the
modules up (``find_spec``) without importing them, and a package whose
record outlived its files (``dotenv``) passed it. The deploy script printed
the last 300 characters of the output (a settings notice), and its revert
only reloaded: the reload lifted the update's hold, and nothing put the
packages back.

Now the update tries the additive install, then rewrites every package
(``--reinstall``) at most twice, judging each by importing Kazma in a child;
every step's whole output goes to ``.kazma/update.log``; and the deploy's
revert of a ``pyproject.toml`` change puts the reverted code's packages back
(the update, else every package rewritten with uv while the guard holds
Kazma). Each with the old behaviour as its negative control.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# -- kazma update ----------------------------------------------------------------------


@pytest.fixture()
def update(monkeypatch, tmp_path):
    from kazma_cli import update as module

    monkeypatch.setattr(module, "_find_git_root", lambda: tmp_path)
    monkeypatch.setattr(module, "_packages_in_use", lambda: False)
    monkeypatch.setattr(module, "detect_active_extras", lambda cwd: ["rag", "web"])
    monkeypatch.setattr(module, "persist_extras", lambda extras: None)
    monkeypatch.setattr(module, "_cleanup_broken_distributions", lambda: None)
    monkeypatch.setattr(module, "_REPAIR_PAUSE_S", 0.0)
    return module


class _Steps:
    """``_run_cmd`` answering each install and import check in turn."""

    def __init__(self, installs: list[tuple[int, str]], imports: list[tuple[int, str]]) -> None:
        self.installs, self.imports = list(installs), list(imports)
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, timeout=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:1] == [sys.executable] and cmd[1] == "-c":
            code, out = self.imports.pop(0)
        else:
            code, out = self.installs.pop(0)
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr=out)


def _installs(steps: _Steps) -> list[list[str]]:
    return [c for c in steps.calls if c[:1] != [sys.executable]]


def test_a_failed_install_is_followed_by_every_package_rewritten(update, monkeypatch, tmp_path):
    steps = _Steps(installs=[(2, "Resolved 320 packages\nerror: failed to remove file pydantic_core.pyd: "
                                "Access is denied. (os error 5)"), (0, "")],
                   imports=[(0, "")])
    monkeypatch.setattr(update, "_run_cmd", steps)
    assert update._reinstall_local(str(tmp_path)) is True
    first, second = _installs(steps)
    assert "--reinstall" not in first and "--reinstall" in second
    assert not any(c[:2] == ["uv", "sync"] for c in steps.calls), "no uv sync: it moves every package to the lock"
    log = (tmp_path / ".kazma" / "update.log").read_text(encoding="utf-8")
    assert "Access is denied. (os error 5)" in log, "the whole output is kept"


def test_an_install_that_exits_0_but_leaves_kazma_unable_to_import_is_repaired(update, monkeypatch, tmp_path):
    steps = _Steps(installs=[(0, ""), (0, "")],
                   imports=[(1, "ModuleNotFoundError: No module named 'dotenv'"), (0, "")])
    monkeypatch.setattr(update, "_run_cmd", steps)
    assert update._reinstall_local(str(tmp_path)) is True
    assert "--reinstall" in _installs(steps)[1]


def test_when_nothing_works_the_update_fails_and_names_the_error(update, monkeypatch, tmp_path, capsys):
    steps = _Steps(installs=[(2, "error: Access is denied")] * 3, imports=[])
    monkeypatch.setattr(update, "_run_cmd", steps)
    assert update._reinstall_local(str(tmp_path)) is False
    assert len(_installs(steps)) == 3, "the install, then two rewrites"
    out = capsys.readouterr().out
    assert "error: Access is denied" in out and "update.log" in out


def test_without_uv_the_update_installs_with_pip(update, monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def run(cmd, cwd=None, timeout=None):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(update, "_run_cmd", run)
    assert update._reinstall_local(str(tmp_path)) is True
    assert any(c[1:4] == ["-m", "pip", "install"] for c in calls)


def _broken_dotenv(tmp_path: Path, monkeypatch) -> None:
    """A package whose record says installed and whose module cannot load,
    first on the child's path (what the failed install left of dotenv)."""
    site = tmp_path / "broken-site"
    (site / "dotenv").mkdir(parents=True)
    (site / "dotenv" / "__init__.py").write_text("raise ImportError('its files were removed')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(site))


def test_the_check_imports_kazma_and_catches_a_package_without_its_files(update, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_find_git_root", lambda: tmp_path)
    assert update._environment_problem() == "", "this environment runs Kazma"
    _broken_dotenv(tmp_path, monkeypatch)
    assert "its files were removed" in update._environment_problem()


def test_negative_control_the_old_check_only_found_the_modules(update, monkeypatch, tmp_path):
    _broken_dotenv(tmp_path, monkeypatch)
    old_probe = ("import importlib.util, sys\n"
                 "missing = [m for m in ('kazma_cli', 'kazma_core') if importlib.util.find_spec(m) is None]\n"
                 "sys.exit(1 if missing else 0)\n")
    done = subprocess.run([sys.executable, "-c", old_probe], capture_output=True, text=True, check=False)
    assert done.returncode == 0, "the old check passed the broken environment"


# -- the deploy script -----------------------------------------------------------------


@pytest.fixture()
def deploy_live():
    spec = importlib.util.spec_from_file_location("_deploy_live_repair", ROOT / "scripts" / "deploy_live.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_error_lines_are_shown_not_the_last_300_characters(deploy_live):
    notice = ("[ConfigStore] C:\\Users\\x\\kazma\\kazma-data\\settings.db: its `settings` table holds 91 row(s) "
              "that are NOT read -- Postgres is the live config backend. Do not debug configuration or "
              "credentials against this file; it can show providers as disabled with empty keys while the "
              "real store has them enabled.\n")
    out = ("Resolved 320 packages in 1.38s\n"
           "error: failed to remove file C:\\x\\pydantic_core.pyd: Access is denied. (os error 5)\n" + notice)
    assert "Access is denied" in deploy_live.essential(out)
    assert "Access is denied" not in out[-300:], "the old tail held only the notice"


def test_a_revert_of_new_packages_puts_the_old_ones_back(deploy_live, monkeypatch, tmp_path):
    live = []
    monkeypatch.setattr(deploy_live, "run_live", lambda live_dir, *args, timeout=1800: (live.append(args), (0, ""))[1])
    assert deploy_live.put_packages_back(tmp_path) == ""
    assert live[0] == ("-m", "kazma_cli", "update", "--reinstall", "-y")
    assert ("scripts/service/kazma_guard.py", "--reload", "--when-idle") not in live


def test_when_the_update_cannot_run_every_package_is_rewritten_under_the_guard(deploy_live, monkeypatch, tmp_path):
    (tmp_path / ".kazma").mkdir()
    (tmp_path / ".kazma" / "installed_extras.json").write_text('{"extras": ["rag", "web"]}', encoding="utf-8")
    live: list[tuple] = []
    answers = {("-m", "kazma_cli"): (1, "No module named kazma_cli")}

    def run_live(live_dir, *args, timeout=1800):
        live.append(args)
        return answers.get(args[:2], (0, ""))

    cmds: list[list[str]] = []
    monkeypatch.setattr(deploy_live, "run_live", run_live)
    monkeypatch.setattr(deploy_live, "live_python", lambda live_dir: "python")
    monkeypatch.setattr(deploy_live, "run_cmd", lambda argv, cwd, timeout=3600: (cmds.append(argv), (0, ""))[1])
    monkeypatch.setattr(deploy_live.shutil, "which", lambda name: "uv")
    assert deploy_live.put_packages_back(tmp_path) == ""
    assert cmds == [["uv", "pip", "install", "--reinstall", "--python", "python", "-e", ".[rag,web]"]]
    guard = [a for a in live if a[:1] == ("scripts/service/kazma_guard.py",)]
    assert guard[0][1:3] == ("--pause", "--stop") and guard[-1] == ("scripts/service/kazma_guard.py", "--resume")


def test_a_repair_that_fails_keeps_kazma_held(deploy_live, monkeypatch, tmp_path):
    live: list[tuple] = []
    monkeypatch.setattr(deploy_live, "run_live", lambda live_dir, *args, timeout=1800: (live.append(args), (0, ""))[1])
    monkeypatch.setattr(deploy_live, "live_python", lambda live_dir: "python")
    monkeypatch.setattr(deploy_live, "run_cmd", lambda argv, cwd, timeout=3600: (1, "error: Access is denied"))
    monkeypatch.setattr(deploy_live.shutil, "which", lambda name: "uv")
    problem = deploy_live.repair_packages(tmp_path, pause_s=0)
    assert "Kazma stays held" in problem and "Access is denied" in problem
    assert ("scripts/service/kazma_guard.py", "--resume") not in live, "resumed broken, it would crash-loop"
