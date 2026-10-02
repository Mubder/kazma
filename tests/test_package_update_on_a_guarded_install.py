"""A package update on a guarded install stops the server through the guard.

Live 2026-10-02 the boot check said the install's packages were behind and
told the owner to "Run `kazma update` on the server". But:

- ``kazma update --reinstall`` (the fix) replaced packages under the running
  server: on Windows the server holds their compiled files open, and the
  reinstall fails half way, leaving some packages new and some old;
- the git update refused, and told the operator to kill the server with
  Stop-Process, which the guard undoes within seconds (and which nobody
  should do by hand);
- ``kazma_guard.py --pause --stop`` could not wait for a running chat turn,
  as ``--reload --when-idle`` does.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


# ── kazma update refuses under a running server ──────────────────────────


@pytest.mark.parametrize("running", [True, False])
def test_reinstall_refuses_while_the_server_runs(running: bool, monkeypatch, capsys) -> None:
    from kazma_cli import update

    installs: list[str] = []
    monkeypatch.setattr(update, "detect_install_type", lambda: "git")  # no `pip show`
    monkeypatch.setattr(update, "get_current_version", lambda: "0.0.0")
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: running)
    monkeypatch.setattr(update, "_reinstall_local", lambda cwd: installs.append(cwd) or True)
    monkeypatch.setattr(update, "persist_extras", lambda extras: None)
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: ["rag"])
    if running:
        with pytest.raises(SystemExit) as exited:
            update.run(["--reinstall", "-y"])
        assert exited.value.code == 1
        out = capsys.readouterr().out
        assert installs == []
        assert "--pause --stop --when-idle" in out and "--resume" in out
        assert "kazma update --reinstall -y" in out
        # Each command on one line, as pasted: Rich wrapped them at 80 columns.
        assert any(line.strip().endswith('--when-idle --reason "package update"')
                   and "kazma_guard.py --pause" in line for line in out.splitlines()), out
    else:
        update.run(["--reinstall", "-y"])
        assert len(installs) == 1  # negative control: a stopped server is reinstalled


def test_the_git_update_names_the_guard_not_a_kill(monkeypatch, capsys) -> None:
    from kazma_cli import update

    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: True)
    assert update.do_git_update() is False
    out = capsys.readouterr().out
    assert "Stop-Process" not in out
    assert "--pause --stop --when-idle" in out and "kazma update -y" in out


# ── the boot check names the same procedure ──────────────────────────────


def test_the_alert_says_how_to_update_a_guarded_install(monkeypatch) -> None:
    from kazma_core import install_requirements as ir

    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", "C:/kazma/.kazma/guard_state.json")
    guarded = ir._update_instructions()
    monkeypatch.delenv("KAZMA_GUARD_STATE_FILE")
    plain = ir._update_instructions()
    assert "--pause --stop --when-idle" in guarded and "--resume" in guarded
    assert "kazma update --reinstall -y" in guarded and "kazma update --reinstall -y" in plain
    assert "kazma_guard" not in plain


def test_the_boot_warning_and_alert_carry_it(monkeypatch) -> None:
    from kazma_core import install_requirements as ir
    from kazma_core.observability import ops_alerts

    sent: list[str] = []
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", "x")
    monkeypatch.setattr(ir, "unmet_requirements", lambda project_root=None: [
        ir.UnmetRequirement("pyjwt", ">=2.15.0", "2.13.0", ("base",)),
    ])
    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail, **kw: sent.append(detail))
    ir.report_unmet_requirements()
    assert sent and "--pause --stop --when-idle" in sent[0] and "Run `kazma update` on the server" not in sent[0]


# ── the guard waits for idle before a stop ───────────────────────────────


def _guard():
    spec = importlib.util.spec_from_file_location(
        "kazma_guard_pause_test", REPO / "scripts" / "service" / "kazma_guard.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, level, event, **fields):
        self.events.append(event)


def test_a_busy_stop_writes_no_pause(monkeypatch) -> None:
    guard = _guard()
    written: list[str] = []
    log = _Log()
    monkeypatch.setattr(guard, "GuardLog", lambda path: log)
    monkeypatch.setattr(guard, "_activity", lambda url: {"active_turns": 1})
    monkeypatch.setattr(guard, "write_pause", lambda reason, ttl: written.append(reason) or {})
    assert guard._cmd_pause("package update", 60, stop_now=True, when_idle=True, idle_timeout_s=0) == 3
    assert written == []
    assert "maintenance.busy_gave_up" in log.events


def test_an_idle_stop_pauses(monkeypatch) -> None:
    guard = _guard()
    written: list[str] = []
    log = _Log()
    monkeypatch.setattr(guard, "GuardLog", lambda path: log)
    monkeypatch.setattr(guard, "_activity", lambda url: {"active_turns": 0})
    monkeypatch.setattr(guard, "time", type("T", (), {"monotonic": staticmethod(lambda: 0.0), "sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(guard, "write_pause", lambda reason, ttl: written.append(reason) or {"reason": reason, "until": 0})
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    monkeypatch.setattr(guard, "_wait_until_down", lambda url, timeout: True)
    assert guard._cmd_pause("package update", 60, stop_now=True, when_idle=True, idle_timeout_s=60) == 0
    assert written == ["package update"]


def test_without_when_idle_the_stop_does_not_wait(monkeypatch) -> None:
    """Negative control: the wait happens only when asked for."""
    guard = _guard()
    asked: list[str] = []
    monkeypatch.setattr(guard, "GuardLog", lambda path: _Log())
    monkeypatch.setattr(guard, "_activity", lambda url: asked.append(url) or {"active_turns": 3})
    monkeypatch.setattr(guard, "write_pause", lambda reason, ttl: {"reason": reason, "until": 0})
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    monkeypatch.setattr(guard, "_wait_until_down", lambda url, timeout: True)
    assert guard._cmd_pause("diagnosis", 60, stop_now=True) == 0
    assert asked == []


# ── the commands name the install's own Python ───────────────────────────


@pytest.mark.parametrize("module", ["kazma_cli.update", "kazma_core.install_requirements"])
def test_the_guard_command_runs_the_installs_own_python(module, tmp_path, monkeypatch) -> None:
    """A bare ``python`` may be another interpreter, or none at all on Windows
    (the live refusal said ``python scripts/...``). The command names the
    interpreter running Kazma: relative inside the install folder, which cmd,
    PowerShell and a POSIX shell all run as typed."""
    import importlib
    import os
    import sys

    mod = importlib.import_module(module)
    root = tmp_path / "kazma"
    exe = root / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    assert mod._install_python(root) == os.path.join(".venv", "Scripts", "python.exe")
    # Outside the install folder: in full, quoted when it holds a space.
    elsewhere = tmp_path / "other dir" / "python.exe"
    monkeypatch.setattr(sys, "executable", str(elsewhere))
    assert mod._install_python(root) == f'"{elsewhere}"'


def test_the_alert_and_the_refusal_carry_that_python(tmp_path, monkeypatch, capsys) -> None:
    import os
    import sys

    from kazma_cli import update
    from kazma_core import install_requirements as ir

    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "Scripts" / "python.exe"))
    python = os.path.join(".venv", "Scripts", "python.exe")
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", "x")
    text = ir._update_instructions(tmp_path)
    assert f"{python} {os.path.join('scripts', 'service', 'kazma_guard.py')} --pause" in text
    assert "python scripts" not in text  # negative: the bare name is gone

    monkeypatch.setattr(update, "_find_git_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: True)
    refusal = update._server_running_refusal("kazma update --reinstall -y")
    assert f"{python} {os.path.join('scripts', 'service', 'kazma_guard.py')} --resume" in refusal
