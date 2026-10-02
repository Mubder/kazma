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

The fix that day made the update refuse and print the three guard commands
to type around it; the owner ran the update and got the refusal. Since the
same day the update does it itself (``kazma_cli.update._ServerHold``): the
guard stops Kazma once no chat turn runs, the packages are replaced, Kazma
starts again. A failed install keeps Kazma stopped, and the repair is the
same command. The real guard against a stand-in server is in
``tests/test_guard_integration.py``.
"""

from __future__ import annotations

import ast
import importlib.util
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UPDATE_PY = REPO / "kazma-cli" / "kazma_cli" / "update.py"


# ── a stand-in for the guard's maintenance API ───────────────────────────


class _FakeGuard:
    """The guard's API as the updater calls it, against a pretend server."""

    def __init__(self, events: list, server: dict, *, plan: str = "guard",
                 stop: str = "held", resume: str = "up", pause: dict | None = None) -> None:
        self.events = events
        self.server = server
        self.plan = plan
        self.stop = stop
        self.resume = resume
        self.pause = pause

    def maintenance_preview(self, reason: str) -> str:
        self.events.append("preview")
        return self.plan

    def stop_for_maintenance(self, reason: str, *, idle_timeout_s: float, **_kw) -> str:
        self.events.append("stop")
        if self.stop == "held":
            self.server["running"] = False
            now = time.time()
            self.pause = {"reason": reason, "since": now, "until": now + 7200}
        return self.stop

    def resume_after_maintenance(self, reason: str, *, wait: bool = True) -> str:
        self.events.append("resume")
        if self.pause is None:
            return "not_paused"
        if self.pause.get("reason") != reason:
            return "not_ours"
        self.pause = None
        if self.resume == "up":
            self.server["running"] = True
        return self.resume

    def read_pause(self) -> dict | None:
        return self.pause


@pytest.fixture
def cli(monkeypatch):
    """``kazma update`` with a pretend server, guard and installer."""
    from kazma_cli import update

    events: list = []
    server = {"running": True}
    box: dict = {"guard": _FakeGuard(events, server), "install_ok": True}

    def install(cwd):
        events.append(("install", server["running"]))
        return box["install_ok"]

    monkeypatch.setattr(update, "detect_install_type", lambda: "git")  # no `pip show`
    monkeypatch.setattr(update, "get_current_version", lambda: "0.0.0")
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: server["running"])
    monkeypatch.setattr(update, "_launchers_in_use", lambda *a, **k: [])
    monkeypatch.setattr(update, "_load_guard", lambda root: box["guard"])
    monkeypatch.setattr(update, "_reinstall_local", install)
    monkeypatch.setattr(update, "persist_extras", lambda extras: None)
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: ["rag"])
    return update, events, server, box


# ── one command: stop, install, start ────────────────────────────────────


def test_one_command_stops_kazma_installs_and_starts_it_again(cli, capsys) -> None:
    update, events, server, _box = cli
    update.run(["--reinstall", "-y"])
    assert [e for e in events if e != "preview"] == ["stop", ("install", False), "resume"]
    assert server["running"] is True
    out = capsys.readouterr().out
    assert "Reinstall complete" in out
    assert "--pause" not in out  # nothing left for the operator to type


def test_the_instrument_sees_an_install_under_a_running_server(cli, monkeypatch) -> None:
    """Negative control: a flow that skips the hold installs under the server,
    and the recorded install says so -- the assertion above would fail."""
    update, events, _server, _box = cli
    monkeypatch.setattr(update, "_install_held", lambda command, install: "done" if install() else "failed")
    update.run(["--reinstall", "-y"])
    assert ("install", True) in events


def test_a_failed_install_keeps_kazma_stopped_and_names_the_repair(cli, capsys) -> None:
    update, events, server, box = cli
    box["install_ok"] = False
    with pytest.raises(SystemExit) as exited:
        update.run(["--reinstall", "-y"])
    assert exited.value.code == 1
    assert "resume" not in events, "a half-replaced install must not be started"
    assert server["running"] is False
    out = capsys.readouterr().out
    assert "stays stopped" in out
    assert "-m kazma_cli update --reinstall -y" in out and "--resume" in out


def test_the_repair_run_takes_the_hold_over_and_starts_kazma(cli) -> None:
    """After a failed run: Kazma down, the update's pause in place. The same
    command takes the pause over and lifts it once its install succeeds."""
    update, events, server, box = cli
    from kazma_cli.update import _UPDATE_PAUSE_REASON

    server["running"] = False
    box["guard"].pause = {"reason": _UPDATE_PAUSE_REASON, "since": 1.0, "until": 0.0}
    update.run(["--reinstall", "-y"])
    assert [e for e in events if e != "preview"] == ["stop", ("install", False), "resume"]
    assert server["running"] is True


def test_a_busy_kazma_is_not_stopped_and_nothing_installs(cli, capsys) -> None:
    update, events, server, box = cli
    box["guard"].stop = "busy"
    with pytest.raises(SystemExit) as exited:
        update.run(["--reinstall", "-y"])
    assert exited.value.code == 1
    assert [e for e in events if e != "preview"] == ["stop"]
    assert server["running"] is True
    assert "still running" in capsys.readouterr().out


@pytest.mark.parametrize("plan", ["no_guard", "paused"])
def test_a_server_the_guard_cannot_stop_is_refused_before_anything(plan, cli, capsys) -> None:
    update, events, server, box = cli
    box["guard"].plan = plan
    box["guard"].pause = {"reason": "diagnosis", "since": 1.0, "until": 0.0} if plan == "paused" else None
    with pytest.raises(SystemExit) as exited:
        update.run(["--reinstall", "-y"])
    assert exited.value.code == 1
    assert set(events) == {"preview"}, events  # nothing stopped, nothing installed
    out = capsys.readouterr().out
    assert "-m kazma_cli update --reinstall -y" in out
    assert ("No guard is running" in out) if plan == "no_guard" else ("diagnosis" in out)
    # Each command on one line, as pasted: Rich wrapped them at 80 columns.
    assert any(line.strip().endswith("-m kazma_cli update --reinstall -y") for line in out.splitlines()), out


def test_another_pause_is_left_in_place(cli, capsys) -> None:
    """An operator paused Kazma for a diagnosis (server stopped): the update
    installs and leaves their pause alone."""
    update, events, server, box = cli
    server["running"] = False
    box["guard"].plan = "paused"
    box["guard"].pause = {"reason": "diagnosis", "since": 1.0, "until": 0.0}
    update.run(["--reinstall", "-y"])
    assert ("install", False) in events
    assert box["guard"].pause is not None
    assert "stays paused (diagnosis)" in capsys.readouterr().out


@pytest.mark.parametrize("running", [True, False])
def test_an_install_with_no_guard_refuses_only_a_running_server(running, cli, capsys) -> None:
    update, events, server, box = cli
    box["guard"] = None  # a wheel install has no guard script
    server["running"] = running
    if running:
        with pytest.raises(SystemExit):
            update.run(["--reinstall", "-y"])
        assert events == []
        assert "Stop the server, run this, then start it again" in capsys.readouterr().out
    else:
        update.run(["--reinstall", "-y"])  # negative control: a stopped server is reinstalled
        assert events == [("install", False)]


def test_the_question_says_kazma_will_be_stopped(cli, monkeypatch) -> None:
    update, _events, _server, _box = cli
    asked: list[str] = []
    monkeypatch.setattr(update, "_confirm", lambda prompt: asked.append(prompt) or False)
    update.run(["--reinstall"])
    assert asked and "stopped through its guard" in asked[0]


# ── the git update: the hold covers the reinstall alone ──────────────────


@pytest.fixture
def git_update(cli, monkeypatch):
    update, events, server, box = cli

    def run_cmd(cmd, cwd=None, timeout=None):
        if cmd[:2] == ["git", "reset"]:
            events.append(("reset", cmd[-1], server["running"]))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(update, "_find_git_root", lambda: REPO)
    monkeypatch.setattr(update, "_origin_is_trusted", lambda cwd: (True, "github.com/mubder/kazma"))
    monkeypatch.setattr(update, "_preflight_git", lambda cwd, **kw: (True, "main"))
    monkeypatch.setattr(update, "_git_status_porcelain", lambda cwd: "")
    monkeypatch.setattr(update, "_commits_ahead_of_origin", lambda cwd: 0)
    monkeypatch.setattr(update, "_run_cmd", run_cmd)
    monkeypatch.setattr(update, "get_git_commit", lambda ref="HEAD": "abc1234")
    for name in ("_write_update_state", "_clear_update_state"):
        monkeypatch.setattr(update, name, lambda *a, **k: None)
    monkeypatch.setattr(update, "_read_update_state", lambda cwd: {})
    monkeypatch.setattr(update, "_postflight_ok", lambda cwd: True)
    monkeypatch.setattr(
        update, "_reinstall_via_subprocess",
        lambda cwd: events.append(("install", server["running"])) or box["install_ok"],
    )
    return update, events, server, box


def test_the_git_update_stops_kazma_for_the_reinstall_alone(git_update) -> None:
    update, events, server, _box = git_update
    assert update.do_git_update() is True
    steps = [e for e in events if e != "preview"]
    assert steps == [("reset", "origin/main", True), "stop", ("install", False), "resume"]
    assert server["running"] is True


def test_a_busy_kazma_puts_the_checkout_back(git_update, capsys) -> None:
    update, events, server, box = git_update
    box["guard"].stop = "busy"
    assert update.do_git_update() is False
    steps = [e for e in events if e != "preview"]
    assert steps == [("reset", "origin/main", True), "stop", ("reset", "abc1234", True)]
    assert server["running"] is True
    assert "back at abc1234" in capsys.readouterr().out


def test_a_failed_git_reinstall_keeps_kazma_stopped(git_update, capsys) -> None:
    update, events, server, box = git_update
    box["install_ok"] = False
    assert update.do_git_update() is False
    assert "resume" not in events
    assert server["running"] is False
    out = capsys.readouterr().out
    assert "stays stopped" in out and "-m kazma_cli update --reinstall -y" in out


def test_the_git_update_names_the_guard_not_a_kill(git_update, capsys) -> None:
    update, _events, _server, box = git_update
    box["guard"].plan = "no_guard"
    assert update.do_git_update() is False
    out = capsys.readouterr().out
    assert "Stop-Process" not in out
    assert "No guard is running" in out and "-m kazma_cli update -y" in out


# ── class gate: every installer checks the server first ──────────────────


def _installer_calls(tree: ast.AST) -> dict[str, list[int]]:
    """Every function that runs an installer -> where it builds one.

    It builds one when it holds an argv naming pip or uv together with
    ``install`` or ``sync`` (wherever it is run from: a variable, a list of
    attempts), or calls ``_run_pip`` with ``install``.
    """
    found: dict[str, list[int]] = {}

    def words(node: ast.AST) -> set[str]:
        return {
            n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.List):
                argv = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if {"pip", "uv"} & argv and {"install", "sync"} & argv:
                    found.setdefault(fn.name, []).append(node.lineno)
            elif (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "_run_pip"
                and "install" in set().union(*(words(a) for a in node.args))
            ):
                found.setdefault(fn.name, []).append(node.lineno)
    return found


def _checks_first(fn: ast.FunctionDef, first_install: int) -> bool:
    return any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_packages_in_use"
        and n.lineno < first_install
        for n in ast.walk(fn)
    )


def test_every_installer_checks_the_server_before_it_installs() -> None:
    tree = ast.parse(UPDATE_PY.read_text(encoding="utf-8"))
    installers = _installer_calls(tree)
    assert {"do_pip_update", "_reinstall_local"} <= set(installers), installers
    functions = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    unchecked = [
        name for name, lines in installers.items()
        if not _checks_first(functions[name], min(lines))
    ]
    assert not unchecked, (
        f"{unchecked} run an installer without asking _packages_in_use() first: "
        "under a running server the install fails half way on Windows"
    )


def test_the_gate_finds_an_installer_that_does_not_check() -> None:
    """Negative control: a new installer without the check is found."""
    tree = ast.parse(
        "def new_installer(cwd):\n"
        "    cmd = ['uv', 'pip', 'install', '-e', '.']\n"
        "    return _run_cmd(cmd, cwd=cwd)\n"
    )
    installers = _installer_calls(tree)
    assert set(installers) == {"new_installer"}
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef))
    assert not _checks_first(fn, min(installers["new_installer"]))


def test_the_installers_refuse_under_a_running_server(monkeypatch, tmp_path) -> None:
    from kazma_cli import update

    ran: list = []
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: True)
    monkeypatch.setattr(update, "_run_cmd", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(update, "_run_pip", lambda *a, **k: ran.append(a))
    assert update._reinstall_local(str(tmp_path)) is False
    release = update.ReleaseInfo(
        version="9.9.9", wheel_name="kazma-9.9.9-py3-none-any.whl",
        wheel_url=update._RELEASE_DOWNLOAD_PREFIX + "v9.9.9/kazma-9.9.9-py3-none-any.whl",
        wheel_sha256="0" * 64,
    )
    assert update.do_pip_update(release) is False
    assert ran == []


# ── the boot check names the one command ─────────────────────────────────


def test_the_alert_says_how_to_update_a_guarded_install(monkeypatch) -> None:
    from kazma_core import install_requirements as ir

    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", "C:/kazma/.kazma/guard_state.json")
    guarded = ir._update_instructions()
    monkeypatch.delenv("KAZMA_GUARD_STATE_FILE")
    plain = ir._update_instructions()
    assert "-m kazma_cli update --reinstall -y" in guarded
    assert "--pause" not in guarded and "stops Kazma through its guard" in guarded
    assert "-m kazma_cli update --reinstall -y" in plain
    assert "stop the server" in plain and "guard" not in plain


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
    assert sent and "-m kazma_cli update --reinstall -y" in sent[0]
    assert "Run `kazma update` on the server" not in sent[0]


# ── the guard's maintenance API ──────────────────────────────────────────


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


@pytest.fixture
def api(monkeypatch, tmp_path):
    """The real guard module, its files in *tmp_path*, the server pretended."""
    guard = _guard()
    monkeypatch.setenv("KAZMA_GUARD_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("KAZMA_GUARD_PAUSE_FILE", str(tmp_path / "paused"))
    monkeypatch.setattr(guard, "GuardLog", lambda path: _Log())
    return guard


def _answers(guard, monkeypatch, answering: bool) -> None:
    monkeypatch.setattr(guard, "probe", lambda url, timeout: guard.ProbeResult(
        answering, "x", answered=answering))


def test_stop_leaves_another_pause_alone(api, monkeypatch) -> None:
    api.write_pause("diagnosis", 600)
    assert api.stop_for_maintenance("update") == "paused"
    assert api.read_pause()["reason"] == "diagnosis"


def test_stop_without_a_live_guard_changes_nothing(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_guard_alive", lambda: False)
    assert api.stop_for_maintenance("update") == "no_guard"
    assert api.read_pause() is None


def test_a_busy_server_is_not_paused(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_guard_alive", lambda: True)
    _answers(api, monkeypatch, True)
    monkeypatch.setattr(api, "_activity", lambda url: {"active_turns": 1})
    assert api.stop_for_maintenance("update", idle_timeout_s=0) == "busy"
    assert api.read_pause() is None


def test_held_is_the_guards_own_word(api, monkeypatch) -> None:
    """A guard that records the pause it holds is believed only for THAT pause."""
    monkeypatch.setattr(api, "_guard_alive", lambda: True)
    _answers(api, monkeypatch, False)
    state = Path(api._state_path())
    state.write_text('{"guard_features": ["pause_held"], "pause_held": 1.0}', encoding="utf-8")
    # An acknowledgement of an older pause is not this one's.
    assert api.stop_for_maintenance("update", hold_timeout_s=0) == "not_held"
    assert api.read_pause() is None, "a pause this call wrote is lifted when the guard did not hold"

    real_write = api.write_pause

    def write_and_ack(reason, ttl):
        rec = real_write(reason, ttl)
        state.write_text(
            f'{{"guard_features": ["pause_held"], "pause_held": {rec["since"]!r}}}', encoding="utf-8")
        return rec

    monkeypatch.setattr(api, "write_pause", write_and_ack)
    assert api.stop_for_maintenance("update", hold_timeout_s=0) == "held"
    assert api.read_pause()["reason"] == "update"


def test_an_older_guard_is_judged_by_what_it_leaves(api, monkeypatch) -> None:
    """A guard from before the acknowledgement (still running after the
    update that taught it): no recorded child and nothing answering, twice."""
    monkeypatch.setattr(api, "_guard_alive", lambda: True)
    monkeypatch.setattr(api, "time", type("T", (), {
        "monotonic": staticmethod(lambda: 0.0), "sleep": staticmethod(lambda s: None),
        "time": staticmethod(time.time),
    }))
    state = Path(api._state_path())
    _answers(api, monkeypatch, False)
    state.write_text('{"child_pid": 4242}', encoding="utf-8")
    assert api._wait_until_held({"since": 1.0}, "http://x/health/ready", 0) is False
    state.write_text('{"child_pid": 0}', encoding="utf-8")
    assert api._wait_until_held({"since": 1.0}, "http://x/health/ready", 5) is True


def test_a_taken_over_pause_stays_when_the_guard_does_not_hold(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_guard_alive", lambda: True)
    _answers(api, monkeypatch, True)  # something answers: not held
    api.write_pause("update", 600)
    assert api.stop_for_maintenance("update", hold_timeout_s=0) == "not_held"
    assert api.read_pause()["reason"] == "update"


def test_resume_lifts_only_its_own_pause(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_guard_alive", lambda: True)
    assert api.resume_after_maintenance("update") == "not_paused"
    api.write_pause("diagnosis", 600)
    assert api.resume_after_maintenance("update") == "not_ours"
    assert api.read_pause()["reason"] == "diagnosis"
    api.clear_pause()
    api.write_pause("update", 600)
    assert api.resume_after_maintenance("update", wait=False) == "resumed"
    assert api.read_pause() is None
    api.write_pause("update", 600)
    monkeypatch.setattr(api, "_guard_alive", lambda: False)
    assert api.resume_after_maintenance("update") == "unsupervised"
    assert api.read_pause() is None


# ── the operator's --pause --stop waits for idle ─────────────────────────


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
    assert f"{python} -m kazma_cli update --reinstall -y" in text
    assert " python -m" not in text  # negative: the bare name is gone

    monkeypatch.setattr(update, "_find_git_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: True)
    monkeypatch.setattr(update, "_load_guard", lambda root: _FakeGuard([], {"running": True}, plan="no_guard"))
    go, refusal = update._ServerHold(update._update_command("--reinstall", "-y")).preview()
    assert go is False
    assert f"{python} {os.path.join('scripts', 'service', 'kazma_guard.py')} --status" in refusal
    assert f"{python} -m kazma_cli update --reinstall -y" in refusal


def test_the_guard_names_its_own_python(monkeypatch) -> None:
    """The guard's hints ("Resume with: ...") name the interpreter running it."""
    import os
    import sys

    guard = _guard()
    monkeypatch.setattr(sys, "executable", str(guard.REPO_ROOT / ".venv" / "Scripts" / "python.exe"))
    expected = os.path.join(".venv", "Scripts", "python.exe") + " " + os.path.join("scripts", "service", "kazma_guard.py")
    assert guard._command_here() == expected
    assert guard._command_here("install_service.py").endswith(os.path.join("scripts", "service", "install_service.py"))


def test_the_updater_loads_the_installs_guard_api() -> None:
    """The real script: everything the updater calls is there."""
    from kazma_cli import update

    guard = update._load_guard(REPO)
    assert guard is not None
    assert all(callable(getattr(guard, fn)) for fn in update._GUARD_API)
