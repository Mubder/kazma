"""The guard starts Docker Desktop and the containers Kazma uses (2026-11-04).

Live 2026-11-03: topgrade updated Docker Desktop and left it stopped. Kazma's
database (the ``kazma-db-win`` container) was gone, and the guard restarted
Kazma 34 times over eleven hours ("never became healthy") because nothing on
the machine started Docker again. Now the guard's ``Dependencies``:

* starts Docker Desktop when its engine has not answered for two minutes,
  through an on-demand task in the signed-in user's session (Windows) or
  ``docker desktop start`` (elsewhere), never while Docker's installer runs,
  never a second copy of a Docker Desktop that is up and not ready;
* starts only the containers Kazma's record names, and only those an outage
  stopped -- a container stopped while the engine stayed up was stopped by
  someone, and is left alone;
* pages once per outage and once on the recovery, ends the restart backoff
  when Docker is back, and takes the Docker page from the document broker.

The server side (``kazma_core.guard_dependencies``) writes the record: which
containers Kazma's configured addresses point to, never another project's.

Everything runs in process against a fake clock and a fake ``docker``; each
rule has a negative control. Nothing here starts Docker or a container.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_deps_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.wall0 = time.time()

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall0 + (self.now - 1000.0)

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class _OsAs:
    """The guard's ``os`` with another ``name``; everything else is real."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr):
        return getattr(os, attr)


class _Docker:
    """A fake ``docker`` (and ``whoami`` / ``schtasks``) for ``_run_command``."""

    def __init__(self) -> None:
        self.engine = True
        self.states: dict[str, str] = {}
        self.start_rc = 0
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[0] == "whoami":
            return 0, "PC\\balfa\n", ""
        args = cmd[1:]
        if args[:1] == ["version"]:
            return (0, "29.0.1\n", "") if self.engine else (1, "", "Cannot connect to the Docker daemon")
        if not self.engine:
            return 1, "", "Cannot connect to the Docker daemon"
        if args[:1] == ["inspect"]:
            names = args[3:]
            lines = [f"/{n}|{self.states[n]}" for n in names if n in self.states]
            return (0 if len(lines) == len(names) else 1), "\n".join(lines), ""
        if args[:1] == ["start"]:
            if self.start_rc == 0:
                self.states[args[1]] = "running"
                return 0, args[1] + "\n", ""
            return self.start_rc, "", "Error: port is already allocated"
        if args[:2] == ["desktop", "start"]:
            return 0, "", ""
        return 1, "", "unknown command"

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[1:1 + len(prefix)] == list(prefix)]


class _Harness:
    def __init__(self, monkeypatch, *, containers=("kazma-db-win",), mode="auto",
                 os_name="nt") -> None:
        self.clock = _Clock()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=self.clock.monotonic, time=self.clock.time, sleep=self.clock.sleep))
        monkeypatch.setenv("KAZMA_GUARD_START_DEPENDENCIES", mode)
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        self.docker = _Docker()
        monkeypatch.setattr(guard, "_run_command", self.docker)
        monkeypatch.setattr(guard, "_docker_cli", lambda: "docker-fake")
        self.images = {"explorer.exe", "svchost.exe"}
        monkeypatch.setattr(guard, "_running_images", lambda: set(self.images))
        self.launches: list[str] = []
        self.launch_result = (True, "started Docker Desktop.exe through the KazmaDockerDesktop task")
        monkeypatch.setattr(guard, "_launch_docker_desktop", self._launch)
        self.sid = "S-1-5-21-1000"
        monkeypatch.setattr(guard, "_current_user_sid", lambda: self.sid)
        self.g = guard.Guard()
        # Patched after the guard is built, so nothing at start-up takes the
        # Windows path on another OS.
        monkeypatch.setattr(guard, "os", _OsAs(os_name))
        self.g.broker.wanted = False
        self.pages: list[str] = []
        self.g.notify.send = self.pages.append
        if containers is not None:
            self.write_record(containers)

    def _launch(self, cli):
        self.launches.append(cli)
        return self.launch_result

    def write_record(self, names) -> None:
        path = guard._deps_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "containers": [
            {"name": n, "ports": [], "for": ["database"]} for n in names],
            "updated": time.time()}), encoding="utf-8")

    def run(self, seconds: float, step: float = 1.0) -> None:
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.g.deps.tick()
            self.clock.now += step

    @property
    def deps(self):
        return self.g.deps

    def events(self) -> list[str]:
        log = Path(os.environ["KAZMA_GUARD_LOG"])
        if not log.exists():
            return []
        out = []
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line).get("event", ""))
            except ValueError:
                continue
        return out

    def pages_titled(self, text: str) -> list[str]:
        return [p for p in self.pages if text in p]


# ── Docker Desktop ────────────────────────────────────────────────────


def test_docker_desktop_is_started_after_the_grace_and_the_recovery_is_said(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.engine = False
    h.run(100)
    assert h.launches == []
    assert h.deps.state == "docker_down"
    assert "deps.engine_down" in h.events()
    h.run(40)
    assert len(h.launches) == 1
    assert h.deps.state == "docker_starting"
    assert "deps.docker_launched" in h.events()
    assert "the guard started Docker Desktop" in h.deps.restart_note()

    h.docker.engine = True
    h.run(20)
    assert h.deps.state == "ok"
    assert h.deps.engine_up is True
    assert "deps.engine_up" in h.events()
    assert len(h.pages_titled("Docker is back")) == 1
    assert h.deps.restart_note() == ""
    assert h.deps.take_recovered() is True
    assert h.deps.take_recovered() is False
    h.run(300)
    assert len(h.launches) == 1
    assert len(h.pages_titled("Docker is back")) == 1


def test_without_the_dependency_check_docker_is_never_started(monkeypatch):
    """Negative control: the guard as it was (no check) waits for ever."""
    h = _Harness(monkeypatch)
    monkeypatch.setattr(guard.Dependencies, "_tick", lambda self: None)
    h.docker.engine = False
    h.run(900)
    assert h.launches == []
    assert h.docker.calls == []


def test_a_short_blip_starts_nothing_and_pages_nothing(monkeypatch):
    h = _Harness(monkeypatch)
    h.run(5)
    h.docker.engine = False
    h.run(60)
    h.docker.engine = True
    h.run(120)
    assert h.launches == []
    assert h.pages == []
    assert h.deps.state == "ok"


def test_nothing_is_started_while_docker_is_being_updated(monkeypatch):
    h = _Harness(monkeypatch)
    h.images |= {"docker desktop installer.exe"}
    h.docker.engine = False
    h.run(400)
    assert h.launches == []
    assert h.deps.state == "docker_updating"
    assert h.events().count("deps.docker_updating") == 1
    assert "being updated" in h.deps.restart_note()
    # The installer finished and left Docker stopped: now it is started.
    h.images.discard("docker desktop installer.exe")
    h.run(20)
    assert len(h.launches) == 1


def test_a_running_docker_desktop_is_nudged_once_never_started_again(monkeypatch):
    h = _Harness(monkeypatch)
    h.images |= {"docker desktop.exe", "com.docker.backend.exe"}
    h.docker.engine = False
    h.run(600)
    assert h.launches == []
    assert len(h.docker.ran("desktop", "start")) == 1
    assert h.deps.state == "docker_not_ready"
    assert h.events().count("deps.desktop_nudged") == 1
    assert h.events().count("deps.docker_not_ready") == 1


def test_no_launch_without_a_signed_in_user(monkeypatch):
    h = _Harness(monkeypatch)
    h.images = {"svchost.exe", "wininit.exe"}
    h.docker.engine = False
    h.run(300)
    assert h.launches == []
    assert h.deps.state == "no_session"
    assert "nobody is signed in" in h.deps.restart_note()


def test_a_guard_running_as_system_does_not_try(monkeypatch):
    h = _Harness(monkeypatch)
    h.sid = "S-1-5-18"
    h.docker.engine = False
    h.run(300)
    assert h.launches == []
    assert h.deps.state == "cannot_launch"
    assert "SYSTEM" in h.deps.restart_note()


def test_a_failed_launch_backs_off(monkeypatch):
    h = _Harness(monkeypatch)
    h.launch_result = (False, "Docker Desktop.exe was not found")
    h.docker.engine = False
    h.run(130)
    assert len(h.launches) == 1
    assert h.deps.state == "launch_failed"
    assert "deps.launch_failed" in h.events()
    assert "could not start" in h.deps.restart_note()
    h.run(250)   # inside the first 300 s backoff
    assert len(h.launches) == 1
    h.run(60)
    assert len(h.launches) == 2


def test_a_docker_that_does_not_come_up_is_started_again_later_and_paged_once(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.engine = False
    h.run(130)
    assert len(h.launches) == 1
    h.run(160)  # 290 s down: Docker Desktop may still be starting
    assert h.pages_titled("Docker does not answer") == []
    h.run(80)
    assert h.deps.state == "not_coming_up"
    assert "deps.not_coming_up" in h.events()
    assert len(h.launches) == 1
    assert len(h.pages_titled("Docker does not answer")) == 1
    h.run(300)
    assert len(h.launches) == 2
    h.run(1200)
    assert len(h.pages_titled("Docker does not answer")) == 1


def test_nobody_signed_in_is_paged_sooner_with_the_way_out(monkeypatch):
    """2026-11-12: a Windows update restarted the PC and nobody signed in.
    The guard paged at ten minutes, two after Kazma Watch had paged the same
    problem. Nothing brings Docker back until someone signs in, so it pages
    at three minutes, and says what brings it back: a sign-in."""
    h = _Harness(monkeypatch)
    h.images = {"svchost.exe", "wininit.exe"}
    h.docker.engine = False
    h.run(175)
    assert h.deps.state == "no_session"
    assert h.pages_titled("Docker does not answer") == []
    h.run(10)
    pages = h.pages_titled("Docker does not answer")
    assert len(pages) == 1, h.pages
    assert "nobody is signed in" in pages[0]
    assert "Sign in to this PC to start it." in pages[0]
    assert "Kazma cannot start" not in pages[0], "Kazma keeps running; it waits for Docker"
    h.run(1200)
    assert len(h.pages_titled("Docker does not answer")) == 1
    # Control: a Docker Desktop being started is given the longer wait.
    h2 = _Harness(monkeypatch)
    h2.docker.engine = False
    h2.run(185)
    assert h2.deps.state == "docker_starting" and h2.pages_titled("Docker does not answer") == []


# ── Kazma up and waiting for Docker (2026-11-12) ──────────────────────


def test_a_wait_for_docker_the_docker_watch_sees_is_its_page_alone(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.engine = False
    detail = "code_execution: No available isolated execution backend"
    for _ in range(int(guard.WAIT_PAGE_S // 30) + 4):
        h.run(30)
        h.g._note_waiting(("docker",), detail)
    assert h.pages_titled("Kazma is up, but not ready") == [], "one page per problem"
    assert len(h.pages_titled("Docker does not answer")) == 1
    assert h.events().count("health.waiting_for") == 1
    assert "health.waiting_paged_by" in h.events()
    h.docker.engine = True
    h.run(20)
    h.g._end_waiting()
    assert h.pages_titled("Kazma is ready again") == [], "the Docker page's own recovery says it"
    assert len(h.pages_titled("Docker is back")) == 1


def test_a_wait_nothing_else_pages_is_paged_once_and_its_end_once(monkeypatch):
    h = _Harness(monkeypatch, mode="off")
    detail = "code_execution: No available isolated execution backend"
    h.g._note_waiting(("docker",), detail)
    h.clock.now += guard.WAIT_PAGE_S - 5
    h.g._note_waiting(("docker",), detail)
    assert h.pages_titled("Kazma is up, but not ready") == [], "a reboot's minute of waiting is no incident"
    h.clock.now += 10
    for _ in range(5):
        h.g._note_waiting(("docker",), detail)
        h.clock.now += 60
    pages = h.pages_titled("Kazma is up, but not ready")
    assert len(pages) == 1 and "a restart cannot bring docker back" in pages[0], pages
    h.g._end_waiting()
    assert len(h.pages_titled("Kazma is ready again")) == 1
    assert "health.wait_over" in h.events()
    # A wait that ended before its page ends quietly.
    h.g._note_waiting(("docker",), detail)
    h.clock.now += 60
    h.g._end_waiting()
    assert len(h.pages_titled("Kazma is ready again")) == 1


# ── restart pages ─────────────────────────────────────────────────────


def test_one_restart_page_an_hour_per_reason(monkeypatch):
    """2026-11-12: eighteen "never became healthy" pages, 15-20 min apart,
    each past the 15-minute page cooldown."""
    h = _Harness(monkeypatch)
    for _ in range(3):  # 0, 18 and 36 min
        h.g.restarts += 1
        h.g.notify_restart("never became healthy", 300)
        h.clock.now += 1080
    assert len(h.pages_titled("Kazma stopped: never became healthy")) == 1
    assert h.events().count("notify.restart_collapsed") == 2
    h.clock.now += 1600
    h.g.restarts += 1
    h.g.notify_restart("never became healthy", 300)  # 81 min after the first
    pages = h.pages_titled("Kazma stopped: never became healthy")
    assert len(pages) == 2 and "It restarted 2 more times for this since the last page." in pages[1]
    # Another reason pages; a healthy start ends the series.
    h.g.notify_restart("process exited (code 1)", 15)
    assert len(h.pages_titled("Kazma stopped: process exited")) == 1
    h.g.notify_recovered()
    h.clock.now += 60
    h.g.notify_restart("never became healthy", 15)
    assert len(h.pages_titled("Kazma stopped: never became healthy")) == 3
    # Control: the cooldown alone (900 s) let a restart every 20 min page.
    assert guard.PAGE_COOLDOWN_S < 1200 < guard.RESTART_REPAGE_S


def test_last_words_only_from_a_server_that_ended_by_itself(monkeypatch):
    h = _Harness(monkeypatch)
    assert h.g._ended_by_itself("process exited (code 3221225477)")
    assert not h.g._ended_by_itself("unhealthy (unreachable: timed out)")
    h.g._exited_while_starting = False
    assert not h.g._ended_by_itself("never became healthy"), "the guard stopped it: boot lines only"
    h.g._exited_while_starting = True
    assert h.g._ended_by_itself("never became healthy")


def test_off_and_held_do_nothing(monkeypatch):
    h = _Harness(monkeypatch, mode="off")
    assert h.deps.server_env() == {}
    h.docker.engine = False
    h.run(600)
    assert h.docker.calls == []
    assert h.deps.owns_engine_alerts() is False

    h = _Harness(monkeypatch)
    assert h.deps.server_env() == {
        "KAZMA_GUARD_DEPENDENCIES_FILE": str(guard._deps_record_path())}
    assert guard._deps_record_path().parent == guard._state_path().parent
    h.deps.on_pause()
    h.docker.engine = False
    h.run(600)
    assert h.docker.calls == []
    h.deps.before_spawn()
    h.run(20)
    assert h.docker.calls


def test_an_unknown_mode_means_auto(monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_START_DEPENDENCIES", "sometimes")
    assert guard._deps_mode() == "auto"


def test_nothing_wanted_asks_docker_nothing(monkeypatch):
    h = _Harness(monkeypatch, containers=())
    h.docker.engine = False
    h.run(600)
    assert h.docker.calls == []
    assert h.deps.state == "idle"
    assert h.deps.owns_engine_alerts() is False
    # The document broker wanting Docker is enough.
    h.g.broker.wanted = True
    h.run(200)
    assert h.docker.ran("version")
    assert h.deps.owns_engine_alerts() is True


def test_without_a_docker_cli_it_says_so_once(monkeypatch):
    h = _Harness(monkeypatch)
    monkeypatch.setattr(guard, "_docker_cli", lambda: None)
    h.run(600)
    assert h.deps.state == "no_docker_cli"
    assert h.events().count("deps.no_docker_cli") == 1
    assert h.launches == []
    assert h.deps.owns_engine_alerts() is False


# ── the containers ────────────────────────────────────────────────────


def _outage_then_up(h: _Harness) -> None:
    h.docker.engine = False
    h.run(130)
    h.docker.engine = True


def test_a_container_the_outage_stopped_is_started(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.states = {"kazma-db-win": "exited", "kazma-test-pg": "exited",
                       "shipx-db": "exited"}
    _outage_then_up(h)
    h.run(30)
    assert h.docker.ran("start") == []   # not before the engine settles
    h.run(60)
    assert h.docker.ran("start") == [["docker-fake", "start", "kazma-db-win"]]
    assert "deps.container_started" in h.events()
    h.run(600)
    assert len(h.docker.ran("start")) == 1
    assert not any("kazma-test-pg" in c or "shipx-db" in c for c in h.docker.calls)


def test_a_container_stopped_while_docker_stayed_up_is_left_alone(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.states = {"kazma-db-win": "exited"}
    h.run(900)
    assert h.docker.ran("start") == []
    assert h.events().count("deps.container_left_stopped") == 1


def test_a_container_that_ran_after_the_outage_and_was_stopped_is_left_alone(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.states = {"kazma-db-win": "running"}
    _outage_then_up(h)
    h.run(70)
    h.docker.states["kazma-db-win"] = "exited"
    h.run(600)
    assert h.docker.ran("start") == []
    assert "deps.container_left_stopped" in h.events()


def test_an_invalid_name_never_reaches_a_command(monkeypatch):
    h = _Harness(monkeypatch, containers=(
        "-rm", "../x", "a;b", "_hidden", "x" * 200, "kazma-db-win", "kazma-db-win"))
    assert h.deps.recorded_containers() == ["kazma-db-win"]
    h.docker.states = {"kazma-db-win": "running"}
    h.run(5)
    inspect = h.docker.ran("inspect")
    assert inspect and inspect[0][4:] == ["kazma-db-win"]
    assert guard._valid_container_name("kazma_db.1-win")
    assert not guard._valid_container_name(None)
    assert not guard._valid_container_name("")


def test_a_container_that_will_not_start_is_paged_once_then_its_recovery(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.states = {"kazma-db-win": "exited"}
    h.docker.start_rc = 1
    _outage_then_up(h)
    h.run(500)
    assert len(h.docker.ran("start")) == 3
    assert h.events().count("deps.container_start_failed") == 3
    assert len(h.pages_titled("Container kazma-db-win will not start")) == 1
    h.run(600)   # the next try waits 900 s
    assert len(h.docker.ran("start")) == 3
    h.docker.start_rc = 0
    h.run(500)
    assert len(h.docker.ran("start")) == 4
    assert h.docker.states["kazma-db-win"] == "running"
    assert len(h.pages_titled("Container kazma-db-win runs again")) == 1
    assert len(h.pages_titled("Container kazma-db-win will not start")) == 1


# ── how Docker Desktop is launched ────────────────────────────────────


def _fake_install(tmp_path: Path) -> tuple[str, Path]:
    app = tmp_path / "Docker" / "Docker"
    (app / "resources" / "bin").mkdir(parents=True)
    exe = app / "Docker Desktop.exe"
    exe.write_bytes(b"MZ")
    cli = app / "resources" / "bin" / "docker.exe"
    cli.write_bytes(b"MZ")
    return str(cli), exe


def test_windows_launch_registers_an_on_demand_task_in_the_users_session(monkeypatch, tmp_path):
    cli, exe = _fake_install(tmp_path)
    monkeypatch.setattr(guard, "os", _OsAs("nt"))
    seen: dict = {}
    calls: list[list[str]] = []

    def run(cmd, timeout):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if cmd[0] == "whoami":
            return 0, "PC\\balfa & co\n", ""
        if cmd[:2] == ["schtasks", "/Create"]:
            path = Path(cmd[3])
            seen["path"] = path
            seen["xml"] = path.read_bytes()
            return 0, "SUCCESS", ""
        if cmd[:2] == ["schtasks", "/Run"]:
            return 0, "SUCCESS", ""
        return 1, "", "unexpected"

    monkeypatch.setattr(guard, "_run_command", run)
    started, what = guard._launch_docker_desktop(cli)
    assert started, what
    assert "KazmaDockerDesktop" in what
    order = [c[1] for c in calls if c[0] == "schtasks"]
    assert order == ["/Create", "/Run"]
    create = next(c for c in calls if c[:2] == ["schtasks", "/Create"])
    assert create[4:] == ["/TN", "KazmaDockerDesktop", "/F"]
    xml = seen["xml"]
    assert xml[:2] == b"\xff\xfe"
    text = xml.decode("utf-16")
    assert "<UserId>PC\\balfa &amp; co</UserId>" in text
    assert "<LogonType>InteractiveToken</LogonType>" in text
    assert "<RunLevel>LeastPrivilege</RunLevel>" in text
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in text
    assert f"<Command>{escape(str(exe))}</Command>" in text
    assert not seen["path"].exists()


def test_windows_launch_says_why_it_failed(monkeypatch, tmp_path):
    cli, _exe = _fake_install(tmp_path)
    monkeypatch.setattr(guard, "os", _OsAs("nt"))

    def refuse_create(cmd, timeout):
        if cmd[0] == "whoami":
            return 0, "PC\\balfa\n", ""
        if cmd[:2] == ["schtasks", "/Create"]:
            return 1, "", "ERROR: Access is denied."
        return 0, "", ""

    monkeypatch.setattr(guard, "_run_command", refuse_create)
    assert guard._launch_docker_desktop(cli) == (
        False, "the task could not be registered: ERROR: Access is denied.")
    assert not guard._broker_file("docker_desktop_task.xml").exists()

    def refuse_run(cmd, timeout):
        if cmd[0] == "whoami":
            return 0, "PC\\balfa\n", ""
        if cmd[:2] == ["schtasks", "/Run"]:
            return 1, "", "ERROR: The task is disabled."
        return 0, "SUCCESS", ""

    monkeypatch.setattr(guard, "_run_command", refuse_run)
    assert guard._launch_docker_desktop(cli) == (
        False, "the task could not be run: ERROR: The task is disabled.")

    monkeypatch.setattr(guard, "_run_command", lambda cmd, timeout: (1, "", "no"))
    assert guard._launch_docker_desktop(cli) == (False, "the guard's account could not be read")

    monkeypatch.setattr(guard, "_docker_desktop_exe", lambda cli: None)
    assert guard._launch_docker_desktop(cli) == (False, "Docker Desktop.exe was not found")


def test_elsewhere_docker_desktop_start_is_used(monkeypatch):
    monkeypatch.setattr(guard, "os", _OsAs("posix"))
    calls = []
    monkeypatch.setattr(guard, "_run_command",
                        lambda cmd, timeout: calls.append(cmd) or (0, "", ""))
    started, _what = guard._launch_docker_desktop("docker")
    assert started
    assert calls == [["docker", "desktop", "start", "-d"]]
    monkeypatch.setattr(guard, "_run_command", lambda cmd, timeout: None)
    assert guard._launch_docker_desktop("docker") == (False, "docker desktop start could not run")


# ── wiring into the guard ─────────────────────────────────────────────


def test_a_tick_never_raises(monkeypatch):
    h = _Harness(monkeypatch)

    def boom(self):
        raise RuntimeError("docker exploded")

    monkeypatch.setattr(guard.Dependencies, "_engine_answers", boom)
    h.run(5)
    assert "deps.internal_error" in h.events()


def test_docker_coming_back_ends_the_restart_backoff(monkeypatch):
    h = _Harness(monkeypatch)
    h.deps._recovered = True
    assert h.g._sleep(30) is False          # not asked to wake: the recovery waits
    assert h.g._sleep(30, wake_on_deps_recovered=True) is True
    assert "guard.backoff_ended" in h.events()
    assert h.deps.take_recovered() is False
    h.deps._recovered = True
    h.deps.before_spawn()                   # a spawn spends it
    assert h.deps.take_recovered() is False


def test_the_broker_leaves_the_docker_page_to_the_dependency_check(monkeypatch):
    h = _Harness(monkeypatch)
    h.deps.state = "docker_starting"
    broker = h.g.broker
    broker._quiet_retry("docker_unavailable", "Docker does not answer", 5, "")
    h.clock.now += 601
    broker._quiet_retry("docker_unavailable", "Docker does not answer", 5, "")
    assert h.pages_titled("documents are refused") == []

    # Control: nothing watches the engine, so the broker pages.
    h = _Harness(monkeypatch)
    h.deps.state = "idle"
    broker = h.g.broker
    broker._quiet_retry("docker_unavailable", "Docker does not answer", 5, "")
    h.clock.now += 601
    broker._quiet_retry("docker_unavailable", "Docker does not answer", 5, "")
    assert len(h.pages_titled("documents are refused")) == 1


def test_the_restart_page_says_what_docker_is_doing(monkeypatch):
    h = _Harness(monkeypatch)
    h.docker.engine = False
    h.run(130)
    h.g.notify_restart("never became healthy", 300)
    assert "the guard started Docker Desktop" in h.pages[-1]


def test_status_and_features(monkeypatch):
    lines = guard._dependency_status_lines({"dependencies": {
        "engine_up": False, "state": "docker_starting", "containers": ["kazma-db-win"],
        "detail": "the guard started Docker Desktop; waiting for its engine"}})
    assert lines == [
        "docker      : DOES NOT ANSWER (docker_starting)",
        "  kazma uses: kazma-db-win",
        "  detail    : the guard started Docker Desktop; waiting for its engine",
    ]
    assert guard._dependency_status_lines({}) == []
    assert "dependencies" in guard.GUARD_FEATURES
    h = _Harness(monkeypatch)
    h.docker.states = {"kazma-db-win": "running"}
    h.run(5)
    state = json.loads(guard._state_path().read_text(encoding="utf-8"))
    assert state["dependencies"]["state"] == "ok"
    assert state["dependencies"]["containers"] == ["kazma-db-win"]


# ── the server side: the record ───────────────────────────────────────


from kazma_core import guard_dependencies as gd  # noqa: E402


def _row(name, port, *, running=True, host_ip="127.0.0.1", container_port="5432/tcp"):
    return {"Name": f"/{name}", "State": {"Running": running},
            "HostConfig": {"PortBindings": {container_port: [
                {"HostIp": host_ip, "HostPort": str(port)}]}}}


def test_loopback_ports():
    assert gd._loopback_port("host=localhost port=5433 dbname=kazma") == 5433
    assert gd._loopback_port("host=127.0.0.1 dbname=kazma") == 5432
    assert gd._loopback_port("host=db.example.com port=5433") is None
    assert gd._loopback_port("postgresql+psycopg://u@127.0.0.1/db") == 5432
    assert gd._loopback_port("postgresql://u:p@localhost:5433/kazma") == 5433
    assert gd._loopback_port("bolt://localhost") == 7687
    assert gd._loopback_port("http://[::1]:8088") == 8088
    assert gd._loopback_port("postgresql://u@10.0.0.5:5432/x") is None
    assert gd._loopback_port("") is None


def test_a_configured_address_takes_its_container():
    out, undecided = gd._match_containers(
        [gd.Endpoint("database", 5433)],
        [_row("kazma-db-win", 5433), _row("shipx-db", 5434)], [])
    assert out == [{"name": "kazma-db-win", "ports": [5433], "for": ["database"]}]
    assert undecided == []


def test_a_guessed_port_takes_only_a_kazma_container():
    rows = [_row("shipx-search", 8088, container_port="8080/tcp")]
    out, _ = gd._match_containers([gd.Endpoint("search", 8088, configured=False)], rows, [])
    assert out == []
    rows.append(_row("kazma-searxng", 8088, running=False, container_port="8080/tcp"))
    out, _ = gd._match_containers([gd.Endpoint("search", 8088, configured=False)], rows, [])
    assert [c["name"] for c in out] == ["kazma-searxng"]


def test_a_port_published_elsewhere_does_not_count():
    out, _ = gd._match_containers(
        [gd.Endpoint("database", 5433)],
        [_row("kazma-db-win", 5433, host_ip="192.168.1.20")], [])
    assert out == []


def test_ties_the_running_one_then_the_last_record_then_undecided():
    endpoint = [gd.Endpoint("database", 5433)]
    both_stopped = [_row("kazma-db-win", 5433, running=False),
                    _row("kazma-test-pg", 5433, running=False)]
    out, _ = gd._match_containers(
        endpoint, [_row("kazma-db-win", 5433), _row("kazma-test-pg", 5433, running=False)], [])
    assert [c["name"] for c in out] == ["kazma-db-win"]
    out, _ = gd._match_containers(endpoint, both_stopped, [{"name": "kazma-db-win"}])
    assert [c["name"] for c in out] == ["kazma-db-win"]
    out, undecided = gd._match_containers(endpoint, both_stopped, [])
    assert out == []
    assert undecided and "kazma-db-win" in undecided[0] and "kazma-test-pg" in undecided[0]
    out, _ = gd._match_containers(endpoint, both_stopped, [], named_db="kazma-test-pg")
    assert [c["name"] for c in out] == ["kazma-test-pg"]


def test_after_the_move_to_the_postgres_service_no_stopped_container_is_named():
    """Live 2026-11-06, right after the move: the database and the memory
    mirror on the native service (5432); the old container stopped on 5433 and
    still named by KAZMA_DB_CONTAINER; another project's stopped container
    publishing 5432. The record named both containers."""
    endpoints = [gd.Endpoint("database", 5432), gd.Endpoint("memory mirror", 5432)]
    rows = [_row("kazma-db-win", 5433, running=False), _row("kca-postgres", 5432, running=False)]
    out, undecided = gd._match_containers(endpoints, rows, [], named_db="kazma-db-win",
                                          answering=lambda port: port == 5432)
    assert out == [] and undecided == []
    # Controls: with nothing answering on 5432 (the outage this list exists
    # for), the stopped container that publishes it is taken; a named
    # container that publishes the database's port still counts.
    down, _ = gd._match_containers(endpoints, rows, [], named_db="kazma-db-win", answering=lambda port: False)
    assert [c["name"] for c in down] == ["kca-postgres"]
    named, _ = gd._match_containers([gd.Endpoint("database", 5433)], rows, [], named_db="kazma-db-win",
                                    answering=lambda port: True)
    assert [c["name"] for c in named] == ["kazma-db-win"]


def test_an_unread_setting_keeps_its_container_while_it_exists():
    previous = [{"name": "kazma-neo4j-win", "ports": [7687], "for": ["graph"]}]
    out, _ = gd._match_containers([], [_row("kazma-neo4j-win", 7687)], previous,
                                 unread={"graph"})
    assert out == [{"name": "kazma-neo4j-win", "ports": [7687], "for": ["graph"]}]
    out, _ = gd._match_containers([], [], previous, unread={"graph"})
    assert out == []
    # Control: read, and no longer configured -> dropped.
    out, _ = gd._match_containers([], [_row("kazma-neo4j-win", 7687)], previous)
    assert out == []


def test_publish_writes_only_a_change_and_never_on_a_silent_docker(monkeypatch, tmp_path):
    path = tmp_path / "guard_dependencies.json"
    monkeypatch.delenv(gd.RECORD_FILE_ENV, raising=False)
    assert gd._publish_dependencies() is False
    monkeypatch.setenv(gd.RECORD_FILE_ENV, str(path))
    monkeypatch.delenv("KAZMA_DB_CONTAINER", raising=False)
    monkeypatch.setattr(gd, "_endpoints", lambda: ([gd.Endpoint("database", 5433)], set()))
    monkeypatch.setattr(gd, "_containers", lambda: None)
    assert gd._publish_dependencies() is False
    assert not path.exists()
    monkeypatch.setattr(gd, "_containers", lambda: [_row("kazma-db-win", 5433)])
    assert gd._publish_dependencies() is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["containers"] == [{"name": "kazma-db-win", "ports": [5433], "for": ["database"]}]
    assert gd._publish_dependencies() is False
    monkeypatch.setattr(gd, "_containers", lambda: None)
    assert gd._publish_dependencies() is False
    assert json.loads(path.read_text(encoding="utf-8"))["containers"] == data["containers"]


def test_the_guard_reads_what_the_server_writes(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, containers=None)
    monkeypatch.setenv(gd.RECORD_FILE_ENV, h.deps.server_env()["KAZMA_GUARD_DEPENDENCIES_FILE"])
    monkeypatch.delenv("KAZMA_DB_CONTAINER", raising=False)
    monkeypatch.setattr(gd, "_endpoints", lambda: ([gd.Endpoint("database", 5433)], set()))
    monkeypatch.setattr(gd, "_containers", lambda: [_row("kazma-db-win", 5433),
                                                    _row("shipx-db", 5434)])
    assert gd._publish_dependencies() is True
    assert h.deps.recorded_containers() == ["kazma-db-win"]


@pytest.mark.parametrize("value", ["auto", "off"])
def test_the_mode_names(value, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_START_DEPENDENCIES", value)
    assert guard._deps_mode() == value
