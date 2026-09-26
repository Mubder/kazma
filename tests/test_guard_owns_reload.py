"""The guard carries out reloads itself, never dies silently, never spins.

Live 2026-09-26: ``kazma_guard.py --reload`` from an operator shell could not
stop the server -- the KazmaAgent task runs the guard elevated, in its own
logon session -- so the old build kept serving ("Access is denied"). The
request it had left behind was the same one that, on 2026-09-20, made the
guard wake on every sleep and probe the server 53 times a second for 47
hours. And the guard itself had died unnoticed twenty minutes earlier: exit
code 1, nothing in guard.log, the server running with nobody watching it.

Each test names the behaviour and carries a negative control that shows the
assertion can fail.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVICE = Path(__file__).resolve().parents[1] / "scripts" / "service"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_owns_reload_{name}", _SERVICE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("kazma_guard")
installer = _load("install_service")


class _Clock:
    """A fake clock whose sleeps return a hair early, like Windows timers can."""

    def __init__(self, early: float = 0.001) -> None:
        self.now = 1000.0
        self.early = early
        self.wall0 = time.time()

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall0 + (self.now - 1000.0)

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        self.now += seconds * (1.0 - self.early)


def _use_clock(monkeypatch, clock: _Clock) -> None:
    monkeypatch.setattr(
        guard, "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=clock.time),
    )


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, level: str, event: str, **fields: object) -> None:
        self.events.append((level, event, fields))

    def names(self) -> list[str]:
        return [e for _, e, _ in self.events]

    def first(self, event: str) -> dict:
        return next(f for _, e, f in self.events if e == event)


class _Child:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


def _bare_guard() -> guard.Guard:
    g = guard.Guard.__new__(guard.Guard)
    g.log = _Log()
    g._stop = False
    g.proc = None
    g.health_url = "http://127.0.0.1:9/health/ready"
    return g


# ── the sleep loop ────────────────────────────────────────────────────────


def _old_sleep(g, seconds: float) -> None:
    """Guard._sleep before 2026-09-26, verbatim but for the names."""
    t = guard.time
    end = t.monotonic() + seconds
    while t.monotonic() < end and not g._stop:
        if g._reload_pending():
            return
        t.sleep(min(1.0, end - t.monotonic()))


def test_the_sleep_loop_never_asks_for_a_negative_sleep(monkeypatch):
    """time.sleep() raises on a negative length. The old loop read the clock,
    ran its checks, then read it again for the length -- an exception there
    ended the guard. The clock is now read once per pass."""
    clock = _Clock()
    _use_clock(monkeypatch, clock)

    def slow_check(self):  # the checks between two clock reads take time
        clock.now += 0.3
        return False

    monkeypatch.setattr(guard.Guard, "_reload_pending", slow_check)
    g = _bare_guard()
    for seconds in (0.5, 1.0, 2.2, 7.9, 30.0):
        assert g._sleep(seconds, wake_on_reload=True) is False

    # Negative control: the old loop, same clock, same checks, raises.
    with pytest.raises(ValueError, match="non-negative"):
        _old_sleep(g, 2.2)


# ── a reload request the guard has already handled ────────────────────────


def _stale_request_that_cannot_be_deleted(monkeypatch) -> Path:
    path = Path(os.environ["KAZMA_GUARD_RELOAD_FILE"])
    path.write_text(json.dumps({"ts": 1.0}), encoding="utf-8")  # older than any server
    monkeypatch.setattr(guard, "consume_reload_request", lambda: False)
    return path


def test_an_undeletable_leftover_request_neither_spins_nor_speeds_up_probes(monkeypatch):
    """The 2026-09-20 storm: a request the guard could not act on woke every
    sleep, and every wake-up was a probe. Now a handled request is remembered
    and probes keep their interval whatever wakes the loop."""
    clock = _Clock(early=0.0)
    _use_clock(monkeypatch, clock)
    _stale_request_that_cannot_be_deleted(monkeypatch)
    g = _bare_guard()
    g.proc = _Child()
    g.spawned_at = clock.time()
    probes: list[float] = []

    def probe(url, timeout):
        probes.append(clock.now)
        if clock.now - 1000.0 >= 300:
            g._stop = True
        return True, "ready"

    handled = []
    real_take = guard.Guard._take_reload_request

    def counting_take(self):
        handled.append(clock.now)
        return real_take(self)

    monkeypatch.setattr(guard, "probe", probe)
    monkeypatch.setattr(guard, "read_pause", lambda: None)
    monkeypatch.setattr(guard.Guard, "_take_reload_request", counting_take)

    assert g._supervise() == "guard shutting down"
    assert len(handled) == 1, "a handled request must not wake the guard again"
    assert 9 <= len(probes) <= 11, probes  # 300 s at a 30 s interval
    gaps = [b - a for a, b in zip(probes, probes[1:])]
    assert min(gaps) >= guard.PROBE_INTERVAL_S - 0.01, gaps
    assert "reload.already_satisfied" in g.log.names()


def test_negative_control_a_guard_that_forgets_handled_requests_spins(monkeypatch):
    """Without the memory the loop wakes forever in zero time: the storm."""
    clock = _Clock(early=0.0)
    _use_clock(monkeypatch, clock)
    _stale_request_that_cannot_be_deleted(monkeypatch)
    g = _bare_guard()
    g.proc = _Child()
    g.spawned_at = clock.time()
    handled = []

    def take(self):
        handled.append(clock.now)
        if len(handled) >= 1000:
            self._stop = True
        return False

    monkeypatch.setattr(guard.Guard, "_reload_pending", lambda self: True)
    monkeypatch.setattr(guard.Guard, "_take_reload_request", take)
    monkeypatch.setattr(guard, "read_pause", lambda: None)
    monkeypatch.setattr(guard, "probe", lambda *a: (True, "ready"))
    g._supervise()
    assert len(handled) == 1000 and clock.now == 1000.0, "1000 wake-ups in no time"


def test_a_request_older_than_the_server_is_satisfied_not_acted_on(tmp_path):
    g = _bare_guard()
    g.proc = _Child()
    guard.request_reload()
    g.spawned_at = time.time() + 5  # spawned after the request
    assert g._take_reload_request() is False
    assert not guard.reload_requested()
    state = guard._read_state()
    assert state["reload_action"] == "already_satisfied"
    assert "guard.reload_requested" not in g.log.names()


# ── a new request: the guard stops its own child ──────────────────────────


def test_a_new_request_is_carried_out_by_the_guard_itself(monkeypatch):
    g = _bare_guard()
    g.proc = _Child()
    g.spawned_at = time.time() - 60
    requested_at = guard.request_reload()
    stops = []

    def stop_child(proc, log, **kw):
        stops.append((proc.pid, kw.get("grace_s")))
        return True

    monkeypatch.setattr(guard, "stop_child", stop_child)
    monkeypatch.setattr(guard, "read_pause", lambda: None)
    monkeypatch.setattr(guard, "probe", lambda *a: pytest.fail("the request comes first"))

    assert g._supervise() == guard.RELOAD_REASON
    assert stops == [(4242, guard.GRACEFUL_STOP_S)]
    state = guard._read_state()
    assert state["reload_ack"] == requested_at and state["reload_action"] == "restarting"
    assert not guard.reload_requested()
    assert g._stopped_for_reload and g._last_stop_graceful


def test_a_request_during_boot_restarts_the_boot(monkeypatch):
    """New code landing mid-boot: the booting child may hold part of the old."""
    g = _bare_guard()
    g.proc = _Child()
    g.spawned_at = time.time() - 5
    guard.request_reload()
    monkeypatch.setattr(guard, "stop_child", lambda proc, log, **kw: True)
    monkeypatch.setattr(guard, "probe", lambda *a: (False, "starting"))
    assert g._wait_ready(g.spawned_at) is False
    assert g._stopped_for_reload


# ── graceful stop ─────────────────────────────────────────────────────────


class _StoppableChild(_Child):
    def __init__(self, *, exits_on_request: bool) -> None:
        super().__init__()
        self.exits_on_request = exits_on_request
        self.killed = False
        self.waits: list[float] = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.killed or self.exits_on_request:
            self.returncode = 0
            return 0
        raise subprocess.TimeoutExpired("fake", timeout)

    def kill(self):
        self.killed = True


def _record_signals(monkeypatch, child: _StoppableChild) -> list:
    sent: list = []
    if os.name == "nt":
        monkeypatch.setattr(guard.os, "kill", lambda pid, sig: sent.append((pid, sig)))

        def run(cmd, **kw):
            sent.append(tuple(cmd))
            child.killed = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(guard.subprocess, "run", run)
    else:
        monkeypatch.setattr(guard.os, "getpgid", lambda pid: pid)

        def killpg(pgid, sig):
            sent.append((pgid, sig))
            if sig == signal.SIGKILL:
                child.killed = True

        monkeypatch.setattr(guard.os, "killpg", killpg)
    return sent


def test_a_deliberate_stop_asks_the_server_to_shut_down_first(monkeypatch):
    child = _StoppableChild(exits_on_request=True)
    sent = _record_signals(monkeypatch, child)
    log = _Log()
    assert guard.stop_child(child, log, grace_s=30.0) is True
    expected = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM
    assert sent == [(4242, expected)], "asked, never killed"
    assert "child.stopped_gracefully" in log.names()


def test_a_server_that_does_not_shut_down_is_killed_after_the_grace(monkeypatch):
    child = _StoppableChild(exits_on_request=False)
    sent = _record_signals(monkeypatch, child)
    log = _Log()
    assert guard.stop_child(child, log, grace_s=3.0) is False
    assert "child.graceful_timeout" in log.names()
    if os.name == "nt":
        assert sent[0] == (4242, signal.CTRL_BREAK_EVENT)
        assert sent[1][:3] == ("taskkill", "/PID", "4242")
    else:
        assert sent == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_the_stop_request_is_never_broadcast_to_the_whole_console(monkeypatch):
    """Process group 0 is every process on the console -- the guard too."""
    child = _StoppableChild(exits_on_request=True)
    child.pid = 0
    calls: list = []
    monkeypatch.setattr(guard.os, "kill", lambda *a: calls.append(a))
    if os.name != "nt":
        monkeypatch.setattr(guard.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(guard.os, "killpg", lambda *a: calls.append(a))
    else:
        monkeypatch.setattr(guard.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert guard._request_graceful_stop(child, _Log()) is False
    assert calls == []


# ── the guard outlives its own bugs ───────────────────────────────────────


def _loop_is_protected(func_source: str) -> bool:
    """Every statement of the first ``while`` loop sits inside a try whose
    ``except Exception`` handler calls ``self._internal_error``."""
    fn = ast.parse(func_source.strip() if func_source.startswith(" ") else func_source)
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.While))
    if len(loop.body) != 1 or not isinstance(loop.body[0], ast.Try):
        return False
    for handler in loop.body[0].handlers:
        caught = ast.unparse(handler.type) if handler.type else ""
        calls = {ast.unparse(c.func) for c in ast.walk(handler) if isinstance(c, ast.Call)}
        if caught == "Exception" and "self._internal_error" in calls:
            return True
    return False


def test_the_supervision_loop_cannot_be_ended_by_an_exception():
    import textwrap

    src = textwrap.dedent(inspect.getsource(guard.Guard.run))
    assert _loop_is_protected(src)
    # Negative control: the loop as it was before 2026-09-26.
    unprotected = textwrap.dedent("""
        def run(self):
            while not self._stop:
                reason = self._supervise()
                self.restarts += 1
    """)
    assert not _loop_is_protected(unprotected)


def test_an_error_in_the_guard_is_logged_and_the_same_child_supervised_again(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(tmp_path / "guard.log"))
    g = guard.Guard()
    spawned, pages = [], []
    monkeypatch.setattr(guard, "reap_orphan", lambda log: None)
    monkeypatch.setattr(guard, "clear_stale_port", lambda url, log: False)
    monkeypatch.setattr(guard, "_port_holder_pid", lambda port: 0)
    monkeypatch.setattr(guard, "stop_child", lambda proc, log, **kw: True)
    monkeypatch.setattr(guard, "spawn", lambda *a: spawned.append(1) or _Child())
    g._foreign_server_present = lambda: False
    g._wait_ready = lambda spawned_at: True
    g.notify = SimpleNamespace(describe=lambda: "test", send=lambda text: None)
    g._page = lambda severity, title, *a, **k: pages.append(title)
    g._sleep = lambda *a, **k: False
    calls = []

    def supervise():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom in the guard")
        g._stop = True
        return "guard shutting down"

    g._supervise = supervise
    assert g.run() == 0
    assert len(spawned) == 1, "a live child must be supervised again, not doubled"
    assert len(calls) == 2
    events = [json.loads(x) for x in (tmp_path / "guard.log").read_text(encoding="utf-8").splitlines()]
    err = next(e for e in events if e["event"] == "guard.internal_error")
    assert err["error"] == "RuntimeError: boom in the guard"
    assert "boom in the guard" in err["traceback"] and err["where"]
    assert "guard.supervision_resumed" in [e["event"] for e in events]
    assert pages == ["Kazma's guard hit an error in its own code"]


def test_a_crash_of_the_guard_is_written_down_before_it_exits(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(tmp_path / "guard.log"))
    monkeypatch.setattr(guard, "_enable_fault_log", lambda: None)
    g = guard.Guard()
    pages = []
    g._page = lambda severity, title, *a, **k: pages.append(title)

    def run():
        raise MemoryError("synthetic")

    g.run = run
    with pytest.raises(MemoryError):
        guard._run_supervisor(g)
    events = [json.loads(x) for x in (tmp_path / "guard.log").read_text(encoding="utf-8").splitlines()]
    crashed = next(e for e in events if e["event"] == "guard.crashed")
    assert crashed["error"] == "MemoryError: synthetic"
    assert "in run" in crashed["traceback"]
    assert pages == ["Kazma's guard crashed"]


# ── heartbeat: how everyone else tells a live guard from a dead one ───────


def test_a_fresh_heartbeat_means_a_guard_and_a_stale_one_does_not(monkeypatch):
    guard._update_state(guard_pid=999999, heartbeat=time.time())
    assert guard._guard_alive() is True
    guard._update_state(heartbeat=time.time() - guard.GUARD_STALE_S - 5)
    assert guard._guard_alive() is False


def test_a_state_file_from_before_the_heartbeat_falls_back_to_the_pid(monkeypatch):
    guard._write_json_atomic(guard._state_path(), {"guard_pid": 4242, "child_pid": 1})
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(guard, "_windows_image_name", lambda pid: "python.exe")
    assert guard._guard_alive() is True
    if os.name == "nt":
        # A reused pid that is not a python process is not a guard.
        monkeypatch.setattr(guard, "_windows_image_name", lambda pid: "chrome.exe")
        assert guard._guard_alive() is False
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: False)
    assert guard._guard_alive() is False


def test_status_says_when_no_guard_is_running(monkeypatch, capsys):
    guard._update_state(guard_pid=4242, child_pid=77, heartbeat=time.time() - 3600)
    monkeypatch.setattr(guard, "probe", lambda *a: (True, "ready"))
    monkeypatch.setattr(guard, "_port_holder_pid", lambda port: 77)
    guard._cmd_status()
    out = capsys.readouterr().out
    assert "guard       : NOT RUNNING" in out and "not supervised" in out
    guard._update_state(heartbeat=time.time())
    guard._cmd_status()
    assert "guard       : running" in capsys.readouterr().out


# ── reaping an orphan: never a reused pid ─────────────────────────────────


def _kill_recorder(monkeypatch) -> list:
    kills: list = []

    def run(cmd, **kw):
        if cmd and cmd[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="postgres\n", stderr="")
        kills.append(tuple(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(guard.subprocess, "run", run)
    monkeypatch.setattr(guard.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(guard.os, "killpg", lambda pgid, sig: kills.append(("killpg", pgid)),
                        raising=False)
    return kills


def test_a_recorded_pid_now_owned_by_another_process_is_not_reaped(monkeypatch):
    guard._write_json_atomic(guard._state_path(), {"child_pid": 4242, "child_created": 1000.0})
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: 5000.0)
    kills = _kill_recorder(monkeypatch)
    log = _Log()
    guard.reap_orphan(log)
    assert kills == []
    assert "orphan.pid_reused" in log.names()

    # Negative control: the same pid, created when it was recorded, is reaped.
    alive = {"v": True}
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: alive["v"])
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: 1000.4)

    def killed_run(cmd, **kw):
        kills.append(tuple(cmd))
        alive["v"] = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(guard.subprocess, "run", killed_run)
    monkeypatch.setattr(guard.os, "killpg", lambda pgid, sig: (kills.append(("killpg", pgid)),
                                                               alive.update(v=False)),
                        raising=False)
    guard.reap_orphan(log)
    assert kills and "orphan.reaped" in log.names()


def test_a_legacy_record_is_reaped_only_if_it_is_a_python_process(monkeypatch):
    guard._write_json_atomic(guard._state_path(), {"child_pid": 4242})
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: None)
    monkeypatch.setattr(guard, "_windows_image_name", lambda pid: "sqlservr.exe")
    kills = _kill_recorder(monkeypatch)
    log = _Log()
    guard.reap_orphan(log)
    assert kills == [] and "orphan.not_python" in log.names()


# ── the operator command ──────────────────────────────────────────────────


class _Cli:
    """--reload with every outside effect replaced by a recorder."""

    def __init__(self, monkeypatch, *, guard_alive: bool, ack: bool,
                 old_server_keeps_serving: bool = False) -> None:
        self.calls: list[str] = []
        self.ack_timeouts: list[float] = []
        self.requested_at = 0.0
        clock = _Clock(early=0.0)
        _use_clock(monkeypatch, clock)
        real_request = guard.request_reload

        def request_reload():
            self.requested_at = real_request()
            return self.requested_at

        def wait_ack(ts, timeout):
            self.ack_timeouts.append(timeout)
            return ack

        def started_at(url, timeout):
            if old_server_keeps_serving or not self.requested_at:
                return 100.0
            return self.requested_at + 5.0

        monkeypatch.setattr(guard, "request_reload", request_reload)
        monkeypatch.setattr(guard, "_guard_alive", lambda: guard_alive)
        monkeypatch.setattr(guard, "_wait_for_reload_ack", wait_ack)
        monkeypatch.setattr(guard, "server_started_at", started_at)
        monkeypatch.setattr(guard, "probe", lambda *a: (True, "ready"))
        monkeypatch.setattr(guard, "_live_commit", lambda url: "abc123")
        monkeypatch.setattr(guard, "_kick_os_supervisor",
                            lambda log: self.calls.append("kick") or True)
        monkeypatch.setattr(guard, "_stop_recorded_child",
                            lambda log, **kw: self.calls.append("stop_child") or 0)
        monkeypatch.setattr(guard, "reap_port_holder",
                            lambda url, log: self.calls.append("reap") or False)


def test_reload_leaves_the_stop_to_a_running_guard(monkeypatch):
    cli = _Cli(monkeypatch, guard_alive=True, ack=True)
    assert guard._cmd_reload() == 0
    assert cli.calls == [], "the shell stopped nothing and started nothing"
    assert cli.ack_timeouts == [guard.GUARD_ACK_S]


def test_reload_with_no_guard_starts_one_instead_of_killing(monkeypatch):
    cli = _Cli(monkeypatch, guard_alive=False, ack=True)
    assert guard._cmd_reload() == 0
    assert cli.calls == ["kick"]
    assert cli.ack_timeouts[0] >= 90.0, "a fresh guard reaps and spawns before it acks"


def test_a_reload_nobody_can_apply_withdraws_its_request(monkeypatch):
    """An old guard wakes on a request every second until it is gone."""
    cli = _Cli(monkeypatch, guard_alive=True, ack=False, old_server_keeps_serving=True)
    assert guard._cmd_reload() == 1
    assert cli.calls == ["stop_child", "reap"]
    assert not guard.reload_requested(), "the request must not be left behind"


def test_negative_control_an_applied_fallback_keeps_the_request(monkeypatch):
    cli = _Cli(monkeypatch, guard_alive=True, ack=False)
    # The old server went away after the fallback: the request stays for
    # the (old) guard to see when it respawns.
    assert guard._cmd_reload() == 0
    assert cli.calls == ["stop_child"]
    assert guard.reload_requested()


def test_the_fallback_never_stops_a_server_spawned_after_the_request(monkeypatch):
    requested_at = time.time()
    guard._update_state(child_pid=4242, child_spawned=requested_at + 3)
    ran: list = []
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: True)
    assert guard._stop_recorded_child(_Log(), spawned_before=requested_at) == 0
    assert ran == []


def test_a_failed_stop_is_reported_as_failed(monkeypatch):
    """taskkill's "Access is denied" was logged as reload.child_stopped."""
    _use_clock(monkeypatch, _Clock(early=0.0))
    guard._update_state(child_pid=4242)
    monkeypatch.setattr(guard, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Access is denied."),
    )
    monkeypatch.setattr(guard.os, "getpgid", lambda pid: pid, raising=False)

    def refuse(pgid, sig):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(guard.os, "killpg", refuse, raising=False)
    log = _Log()
    assert guard._stop_recorded_child(log) == 0
    assert "reload.child_stopped" not in log.names()
    assert "reload.child_stop_failed" in log.names()
    assert log.first("reload.child_stop_failed")["error"]


def test_the_request_file_is_never_read_half_written(tmp_path):
    guard.request_reload()
    leftovers = [p.name for p in Path(os.environ["KAZMA_GUARD_RELOAD_FILE"]).parent.iterdir()
                 if p.name.endswith(".tmp")]
    assert leftovers == []
    assert guard.read_reload_request()["ts"] > 0


# ── the OS layer brings a dead guard back ─────────────────────────────────


@pytest.mark.parametrize("elevated", [True, False])
def test_the_task_restarts_a_guard_that_exited(elevated):
    """Task Scheduler's restart-on-failure does not fire for a process that
    ran and exited, whatever its code: the task sat "Ready" with result 1
    while Kazma ran unsupervised (2026-09-26). A repeating trigger with
    IgnoreNew starts a dead guard and leaves a live one alone."""
    ps1 = installer.windows_task_ps1(elevated=elevated)
    assert "-RepetitionInterval" in ps1
    assert "-MultipleInstances IgnoreNew" in ps1


# ── --pause --stop: the same rights problem, the same answer ──────────────


def test_pause_stop_leaves_the_stop_to_a_running_guard(monkeypatch, capsys):
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    monkeypatch.setattr(guard, "_wait_until_down", lambda url, timeout: True)
    monkeypatch.setattr(guard, "_stop_recorded_child",
                        lambda log, **kw: pytest.fail("the shell must not stop it"))
    assert guard._cmd_pause("maintenance", 600, stop_now=True) == 0
    assert "server stopped." in capsys.readouterr().out


def test_pause_stop_without_a_guard_says_when_it_could_not_stop(monkeypatch, capsys):
    """It printed "server stopped." whatever taskkill answered."""
    monkeypatch.setattr(guard, "_guard_alive", lambda: False)
    monkeypatch.setattr(guard, "_stop_recorded_child", lambda log, **kw: 0)
    monkeypatch.setattr(guard, "probe", lambda *a: (True, "ready"))
    assert guard._cmd_pause("maintenance", 600, stop_now=True) == 0
    out = capsys.readouterr().out
    assert "could not stop the server" in out and "server stopped" not in out


def test_negative_control_a_stop_that_worked_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(guard, "_guard_alive", lambda: False)
    monkeypatch.setattr(guard, "_stop_recorded_child", lambda log, **kw: 4242)
    assert guard._cmd_pause("maintenance", 600, stop_now=True) == 0
    assert "server stopped (pid 4242)." in capsys.readouterr().out


def test_a_pause_wakes_the_guard_at_once(monkeypatch):
    """The guard stops the child when it sees the pause -- within a second,
    not at the next 30-second probe."""
    clock = _Clock(early=0.0)
    _use_clock(monkeypatch, clock)
    g = _bare_guard()
    g.proc = _Child()
    Path(os.environ["KAZMA_GUARD_PAUSE_FILE"]).write_text(
        json.dumps({"reason": "t", "until": 0.0, "since": 0.0}), encoding="utf-8")
    assert g._sleep(30.0, wake_on_pause=True) is True
    assert clock.now - 1000.0 < 1.5
    # Negative control: without wake_on_pause the full interval passes.
    clock.now = 1000.0
    assert g._sleep(30.0) is False
    assert clock.now - 1000.0 >= 29.9
