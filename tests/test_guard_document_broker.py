"""The guard runs the document worker broker beside Kazma (2026-10-15).

Live 2026-10-15: the owner turned on the strict execution profile
(``security.execution.container_required``), documents ran in the local
``process`` profile, no broker ran -- the container profile needed a hand-run
process and three ``.env`` lines -- and every document was refused. The guard
now runs the broker when Kazma asks for it, hands Kazma its address, restarts
it when it dies, pages once when it cannot keep it running, and records it in
its state file.

The first half drives :class:`DocumentBroker` in-process with a fake clock
and fake processes, each test with a negative control. The second half runs
the real guard against the fake server (``fixtures/fake_kazma.py``) and a fake
broker (``fixtures/fake_document_broker.py``) that speaks the broker's status
operation. ``test_document_broker_guard_docker.py`` runs the real broker.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_guard_integration import GuardRun, _free_port

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
FAKE_BROKER = Path(__file__).resolve().parent / "fixtures" / "fake_document_broker.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_broker_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


# ── in-process: the supervisor's decisions ────────────────────────────────


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


class _Proc:
    """A broker process the test ends at will."""

    pids = iter(range(50_001, 59_999))

    def __init__(self, cmd, **kwargs) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = next(self.pids)
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _Harness:
    """A real Guard whose broker spawns fakes, answers by script, and pages
    into a list."""

    def __init__(self, monkeypatch, tmp_path: Path, *, mode: str = "on") -> None:
        self.clock = _Clock()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=self.clock.monotonic, time=self.clock.time, sleep=self.clock.sleep))
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", mode)
        monkeypatch.setenv("KAZMA_GUARD_BROKER_PORT", "28765")
        monkeypatch.setenv("KAZMA_GUARD_BROKER_IMAGE", "kazma-document-worker:test")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        self.procs: list[_Proc] = []

        def spawn(cmd, kwargs):
            proc = _Proc(cmd, **kwargs)
            self.procs.append(proc)
            return proc

        monkeypatch.setattr(guard, "_spawn_broker", spawn)
        monkeypatch.setattr(guard, "_restrict_to_owner", lambda path: True)
        monkeypatch.setattr(guard, "_process_started_at", lambda pid: 123.0)
        # A stop "asks" the fake and it ends at once.
        monkeypatch.setattr(guard, "_ask_broker_to_stop", self._ask)
        monkeypatch.setattr(guard, "_kill_broker_tree", self._ask)
        self.ready = True
        monkeypatch.setattr(guard, "broker_status",
                            lambda port, token, timeout=3.0: {"ready": True, "image_ref": "x"}
                            if self.ready else None)
        self.g = guard.Guard()
        self.pages: list[str] = []
        self.g.notify.send = self.pages.append  # type: ignore[method-assign]
        self.b = self.g.broker

    @staticmethod
    def _ask(proc):
        if proc.returncode is None:
            proc.returncode = 0

    def events(self) -> list[str]:
        return [json.loads(line)["event"] for line in
                Path(os.environ["KAZMA_GUARD_LOG"]).read_text(encoding="utf-8").splitlines()]

    def run_for(self, seconds: float, step: float = 1.0) -> None:
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.now += step
            self.b.tick()

    def exit_current(self, code: int) -> None:
        self.procs[-1].returncode = code


def test_a_broker_that_exits_is_restarted_with_a_backoff(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.run_for(3)
    assert h.b.state == "running" and len(h.procs) == 1
    h.exit_current(1)
    h.run_for(1)
    assert h.b.state == "backoff"
    h.run_for(4)
    assert len(h.procs) == 1, "restarted before its backoff"
    h.run_for(3)
    assert len(h.procs) == 2, "a broker that died was never started again"
    assert h.events().count("broker.exited") == 1
    state = json.loads(Path(os.environ["KAZMA_GUARD_STATE"]).read_text(encoding="utf-8"))
    assert state["document_broker"]["restarts"] == 1
    assert state["document_broker"]["pid"] == h.procs[-1].pid

    # Negative control: without the tick (the guard before 2026-10-15 had no
    # broker at all) the dead broker stays dead.
    h.exit_current(1)
    monkeypatch.setattr(guard.DocumentBroker, "_tick", lambda self: None)
    h.run_for(400)
    assert len(h.procs) == 2


def test_a_failing_broker_is_paged_once_and_its_recovery_once(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.ready = False
    h.b.before_spawn()
    for _ in range(6):
        h.exit_current(2)
        h.run_for(320)
    assert len(h.procs) >= 6
    failing = [p for p in h.pages if "keeps failing" in p]
    assert len(failing) == 1, h.pages
    assert h.events().count("broker.failed") == 1
    h.ready = True
    h.run_for(5)
    assert h.b.state == "running"
    assert len([p for p in h.pages if "running again" in p]) == 1
    # A later failure episode pages again (once).
    for _ in range(4):
        h.exit_current(2)
        h.ready = False
        h.run_for(320)
    assert len([p for p in h.pages if "keeps failing" in p]) == 2


def test_a_missing_image_is_retried_quietly_and_never_paged(monkeypatch, tmp_path):
    """The fix is the operator's (one build command) and Kazma raises it as an
    ops alert with the command: the guard says it once in its log, and checks
    again every 30 s without a line or a page."""
    h = _Harness(monkeypatch, tmp_path)
    h.ready = False  # a broker without an image never answers: it exits at start
    h.b.before_spawn()
    for _ in range(40):
        h.exit_current(guard.BROKER_EXIT_NO_IMAGE)
        h.run_for(guard.BROKER_RETRY_S + 1)
    assert len(h.procs) >= 35
    assert h.events().count("broker.image_missing") == 1
    assert h.events().count("broker.starting") == 1, "every retry logged a start"
    assert h.b.state in ("image_missing", "starting")
    assert not h.pages
    record = json.loads(Path(os.environ["KAZMA_GUARD_STATE"]).read_text(encoding="utf-8"))
    assert record["document_broker"]["last_exit"] == guard.BROKER_EXIT_NO_IMAGE


def test_docker_not_answering_is_paged_after_ten_minutes_once(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.ready = False
    h.b.before_spawn()
    for _ in range(50):
        h.exit_current(guard.BROKER_EXIT_NO_ENGINE)
        h.run_for(guard.BROKER_RETRY_S + 1)
    docker_pages = [p for p in h.pages if "Docker does not answer" in p]
    assert len(docker_pages) == 1
    assert h.events().count("broker.docker_unavailable") == 1


def test_a_newer_image_restarts_the_broker_at_once(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.run_for(3)
    h.exit_current(guard.BROKER_EXIT_NEWER_IMAGE)
    h.run_for(1)
    assert len(h.procs) == 2 and h.b.state == "starting"
    assert "broker.newer_image" in h.events() and "broker.restarting" not in h.events()


def test_a_broker_that_stops_answering_is_restarted(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.run_for(3)
    assert h.b.state == "running"
    h.ready = False
    h.run_for(guard.BROKER_PROBE_INTERVAL_S * (guard.BROKER_FAILURES_TO_KILL + 1))
    assert h.procs[0].returncode is not None, "a wedged broker was never stopped"
    assert "broker.stopped" in h.events()
    h.ready = True
    h.run_for(10)
    assert len(h.procs) == 2


def test_the_request_decides_in_auto_mode(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path, mode="auto")
    env = h.b.server_env()
    assert env["KAZMA_DOCUMENT_BROKER_URL"] == "tcp://127.0.0.1:28765"
    h.b.before_spawn()
    h.run_for(3)
    assert not h.procs, "no request, no broker"
    request = Path(env["KAZMA_GUARD_BROKER_REQUEST_FILE"])
    request.write_text(json.dumps({"want": True, "url": env["KAZMA_DOCUMENT_BROKER_URL"]}), "utf-8")
    h.run_for(1)
    assert len(h.procs) == 1, "asked for, but not started"
    h.run_for(3)
    request.write_text(json.dumps({"want": False, "url": env["KAZMA_DOCUMENT_BROKER_URL"]}), "utf-8")
    h.run_for(1)
    assert h.procs[0].returncode is not None and h.b.state == "idle"
    # Another broker named (.env names one): the guard runs none.
    request.write_text(json.dumps({"want": True, "url": "tcp://127.0.0.1:1"}), "utf-8")
    h.run_for(2)
    assert len(h.procs) == 1 and h.b.why == "external"


def test_a_standing_request_starts_the_broker_before_kazma(monkeypatch, tmp_path):
    """After a restart the last run's request is on disk: the broker starts in
    before_spawn, so it attests while Kazma boots."""
    h = _Harness(monkeypatch, tmp_path, mode="auto")
    env = h.b.server_env()
    Path(env["KAZMA_GUARD_BROKER_REQUEST_FILE"]).write_text(
        json.dumps({"want": True, "url": env["KAZMA_DOCUMENT_BROKER_URL"]}), "utf-8")
    fresh = guard.DocumentBroker(h.g)  # a new guard's supervisor, same files
    fresh.before_spawn()
    assert h.procs and h.procs[-1].returncode is None, "a standing request did not start the broker"
    assert fresh.why == "requested"


def test_nothing_starts_while_the_guard_holds_a_pause(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.run_for(3)
    h.b.on_pause()
    assert h.procs[0].returncode is not None
    h.run_for(600)
    assert len(h.procs) == 1, "the broker was started during maintenance"
    h.b.before_spawn()  # the resume's spawn
    assert len(h.procs) == 2


def test_changed_code_restarts_the_broker_at_the_next_spawn(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.run_for(3)
    h.b.before_spawn()
    assert len(h.procs) == 1, "an unchanged broker was restarted"
    monkeypatch.setenv("KAZMA_GUARD_BROKER_IMAGE", "kazma-document-worker:other")
    h.b.before_spawn()
    assert len(h.procs) == 2 and "--image" in h.procs[1].cmd
    assert h.procs[1].cmd[h.procs[1].cmd.index("--image") + 1] == "kazma-document-worker:other"
    assert "broker.code_changed" in h.events()


def test_a_broker_tick_that_fails_never_reaches_the_guard(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)

    def boom(self):
        raise RuntimeError("broken")

    monkeypatch.setattr(guard.DocumentBroker, "_tick", boom)
    h.b.tick()  # does not raise
    assert "broker.internal_error" in h.events()


def test_the_broker_gets_a_scrubbed_environment_and_the_install_s_image(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "123:secret-token")
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", "sk-secret")
    monkeypatch.delenv("KAZMA_GUARD_BROKER_IMAGE")
    h.b.before_spawn()
    proc = h.procs[0]
    env = proc.kwargs["env"]
    assert "KAZMA_GUARD_TELEGRAM_TOKEN" not in env and "SOME_PROVIDER_API_KEY" not in env
    assert {k.upper() for k in env} <= set(guard._BROKER_ENV_KEEP)
    image = proc.cmd[proc.cmd.index("--image") + 1]
    from kazma_core.documents.worker_image import expected_tag

    assert image == expected_tag(ROOT)
    assert "--fallback-to-newest-built" in proc.cmd


def test_the_token_file_is_made_once_and_kept(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    token = h.b.token_path.read_text(encoding="utf-8")
    assert len(token) >= 32
    h.exit_current(1)
    h.run_for(10)
    assert h.b.token_path.read_text(encoding="utf-8") == token
    assert token not in Path(os.environ["KAZMA_GUARD_LOG"]).read_text(encoding="utf-8")


def test_no_restriction_no_token_and_no_broker(monkeypatch, tmp_path):
    """A token file that cannot be restricted to the guard's account gets no
    secret written into it, and the broker does not start (Q04 defensive check
    C5, 2026-10-15: it logged a warning and served the token anyway)."""
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.setattr(guard, "_restrict_to_owner", lambda path: False)
    h.b.before_spawn()
    assert not h.b.token_path.exists()
    assert not list(h.b.token_path.parent.glob(f"{h.b.token_path.name}.*.tmp"))
    assert h.procs == []
    assert "broker.token_unavailable" in h.events()


def test_an_existing_token_is_restricted_again_at_every_start(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    assert len(h.procs) == 1
    restricted: list[Path] = []
    monkeypatch.setattr(guard, "_restrict_to_owner", lambda path: restricted.append(path) or True)
    h.exit_current(1)
    h.run_for(10)
    assert len(h.procs) == 2 and restricted == [h.b.token_path]

    # A token file that can no longer be restricted stops the next start.
    monkeypatch.setattr(guard, "_restrict_to_owner", lambda path: False)
    h.exit_current(1)
    h.run_for(30)
    assert len(h.procs) == 2
    assert "broker.token_unavailable" in h.events()


@pytest.mark.skipif(os.name != "nt", reason="the access list is a Windows ACL; POSIX gets mode 600")
def test_the_token_file_is_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "token"
    path.write_text("x", encoding="utf-8")
    assert guard._restrict_to_owner(path)
    acl = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=False).stdout
    for group in ("Everyone", "BUILTIN\\Users", "Authenticated Users", "BUILTIN\\Administrators"):
        assert group not in acl, acl
    assert acl.count(":(F)") == 1, acl
    # Negative control: a file left with what it inherits lists more.
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    inherited = subprocess.run(["icacls", str(plain)], capture_output=True, text=True, check=False).stdout
    assert inherited.count("(I)") >= 2


def test_status_names_the_broker_and_an_older_running_guard(monkeypatch, tmp_path, capsys):
    state = {"heartbeat": time.time(), "guard_pid": 1, "guard_features": ["pause_held"],
             "document_broker": {"state": "running", "why": "requested", "pid": 7,
                                 "url": "tcp://127.0.0.1:2", "image": "kazma-document-worker:src-1",
                                 "image_fallback": True, "log": "x.log"}}
    lines = guard._broker_status_lines(state, True)
    text = "\n".join(lines)
    assert "running (requested), pid 7" in text and "an older build" in text
    assert "older code than this file" in text
    state["guard_features"] = list(guard.GUARD_FEATURES)
    state["guard_code"] = guard._file_sha(Path(guard.__file__).resolve())
    assert "older code" not in "\n".join(guard._broker_status_lines(state, True))


# ── the real guard, a fake server, a fake broker ──────────────────────────


class _BrokerRun(GuardRun):
    """GuardRun with the fake broker in place of worker_broker.py."""

    def __init__(self, tmp: Path, port: int, *, mode: str = "on", args: list[str] | None = None,
                 broker: dict | None = None, **fake_env: str) -> None:
        self.broker_port = _free_port()
        super().__init__(tmp, port, **fake_env)
        self.args = args or []
        self.env.update({
            "KAZMA_USER_HOME": str(tmp),
            "KAZMA_GUARD_DOCUMENT_BROKER": mode,
            "KAZMA_GUARD_BROKER_SCRIPT": str(FAKE_BROKER),
            "KAZMA_GUARD_BROKER_PORT": str(self.broker_port),
            "KAZMA_GUARD_BROKER_IMAGE": "kazma-document-worker:fake",
            "FAKE_NOTE_ENV": "KAZMA_DOCUMENT_BROKER_URL,KAZMA_DOCUMENT_BROKER_TOKEN_FILE,"
                             "KAZMA_GUARD_BROKER_REQUEST_FILE",
        })
        self.write_broker_config(broker or {})

    def write_broker_config(self, config: dict) -> None:
        (self.tmp / "fake_broker.json").write_text(json.dumps(config), encoding="utf-8")

    def __enter__(self) -> _BrokerRun:
        self.proc = subprocess.Popen(
            [sys.executable, str(GUARD), *self.args], env=self.env, cwd=str(self.tmp),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return self

    def broker_marks(self) -> list[dict]:
        return self._json_lines(self.tmp / "fake_broker.marker.jsonl")

    def state(self) -> dict:
        try:
            return json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def wait_count(self, event: str, n: int, timeout: float = 90.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.count(event) >= n:
                return
            time.sleep(0.3)
        raise AssertionError(f"{event} x{n} never logged; saw {self.event_names()}")


def test_the_guard_starts_the_broker_before_kazma_and_hands_kazma_its_address(tmp_path):
    port = _free_port()
    with _BrokerRun(tmp_path, port) as g:
        g.wait_for("broker.ready", timeout=60)
        g.wait_for("child.ready", timeout=60)
        names = g.event_names()
        assert names.index("broker.starting") < names.index("child.spawned"), names
        spawned = next(x for x in g.generations() if x["event"] == "spawned")
        env = spawned["env"]
        assert env["KAZMA_DOCUMENT_BROKER_URL"] == f"tcp://127.0.0.1:{g.broker_port}"
        assert Path(env["KAZMA_DOCUMENT_BROKER_TOKEN_FILE"]) == tmp_path / "document_broker.token"
        assert Path(env["KAZMA_GUARD_BROKER_REQUEST_FILE"]) == tmp_path / "document_broker.request.json"
        token = (tmp_path / "document_broker.token").read_text(encoding="utf-8")
        assert len(token) >= 32 and token not in g.log.read_text(encoding="utf-8")
        record = g.state()["document_broker"]
        assert record["state"] == "running" and record["url"] == env["KAZMA_DOCUMENT_BROKER_URL"]
        assert "document_broker" in g.state()["guard_features"]
        started = next(x for x in g.broker_marks() if x["event"] == "spawned")
        assert not [k for k in started["env_keys"] if k.upper().startswith(("FAKE_", "KAZMA_GUARD"))]


def test_a_killed_broker_is_restarted_by_the_guard(tmp_path):
    port = _free_port()
    with _BrokerRun(tmp_path, port) as g:
        g.wait_for("broker.ready", timeout=60)
        first = g.state()["document_broker"]["pid"]
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(first), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(first, 9)
        g.wait_for("broker.exited", timeout=30)
        g.wait_count("broker.ready", 2, timeout=60)
        second = g.state()["document_broker"]["pid"]
        assert second and second != first
        assert g.count("child.spawned") == 1, "killing the broker must not restart Kazma"


def test_the_guard_runs_the_broker_only_when_kazma_asks(tmp_path):
    port = _free_port()
    with _BrokerRun(tmp_path, port, mode="auto", FAKE_BROKER_REQUEST="want") as g:
        g.wait_for("broker.ready", timeout=60)
        request = tmp_path / "document_broker.request.json"
        body = json.loads(request.read_text(encoding="utf-8"))
        body["want"] = False
        request.write_text(json.dumps(body), encoding="utf-8")
        g.wait_for("broker.stopped", timeout=30)
        assert any(x["event"] == "graceful_exit" for x in g.broker_marks()), \
            "the broker was killed, not asked to stop"


def test_no_request_no_broker(tmp_path):
    # Negative control for the test above: the same guard, nobody asks.
    port = _free_port()
    with _BrokerRun(tmp_path, port, mode="auto") as g:
        # The guard decides before it spawns Kazma: by the child's first
        # answer the decision is in the log.
        g.wait_for("child.ready", timeout=60)
        assert g.count("broker.starting") == 0 and g.count("broker.wanted") == 0
        assert not (tmp_path / "fake_broker.marker.jsonl").exists()


def test_a_broker_that_cannot_start_is_paged_once(tmp_path):
    port = _free_port()
    with _BrokerRun(tmp_path, port, broker={"exit_code_at_start": 2}) as g:
        g.wait_for("broker.failed", timeout=90)
        # The page goes out in the same step that logs broker.failed.
        g.wait_for("notify.skipped", timeout=30)
        pages = [e for e in g.events() if e.get("event") == "notify.skipped"
                 and "keeps failing" in str(e.get("text"))]
        assert len(pages) == 1, pages
        assert g.count("child.ready") == 1, "Kazma itself is unaffected"


def test_the_guard_takes_its_broker_with_it(tmp_path):
    """--once: the server exits, the guard ends, and asks the broker to stop."""
    port = _free_port()
    with _BrokerRun(tmp_path, port, args=["--once"], FAKE_EXIT_AFTER_S="4") as g:
        g.wait_for("broker.ready", timeout=60)
        assert g.proc is not None
        g.proc.wait(timeout=90)
        assert any(x["event"] == "graceful_exit" for x in g.broker_marks())
        assert g.state()["document_broker"]["pid"] is None


def test_a_broker_left_by_a_killed_guard_is_reaped(tmp_path):
    port = _free_port()
    with _BrokerRun(tmp_path, port) as g:
        g.wait_for("broker.ready", timeout=60)
        orphan = g.state()["document_broker"]["pid"]
        # The guard alone, hard (Stop-ScheduledTask): its children are left.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(g.proc.pid), "/F"], capture_output=True, check=False)
        else:
            g.proc.kill()
        g.proc.wait(timeout=30)
    assert guard._pid_alive(orphan)
    with _BrokerRun(tmp_path, port) as g2:
        g2.wait_for("broker.orphan_reaped", timeout=60)
        g2.wait_count("broker.ready", 2, timeout=60)
    deadline = time.time() + 20
    while time.time() < deadline and guard._pid_alive(orphan):
        time.sleep(0.5)
    assert not guard._pid_alive(orphan)


# ── a port the broker cannot listen on (live 2026-10-31) ──────────────────


def _reserve(monkeypatch, ports: set[int]) -> None:
    """Windows' reserved block: these ports cannot be bound."""
    monkeypatch.setattr(guard, "_port_is_free", lambda port: port not in ports)


def test_a_port_windows_reserved_is_replaced_and_kazma_still_asks_for_it(monkeypatch, tmp_path):
    """Docker restarted, Windows reserved 20181-20780, and the broker could not
    listen on its recorded port 20527 for hours. It moves now, at once."""
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.delenv("KAZMA_GUARD_BROKER_PORT")
    h.b.port = None
    h.b.before_spawn()
    old_port, old_url = h.b.port, h.b.url
    _reserve(monkeypatch, set(range(old_port - 300, old_port + 300)))
    h.exit_current(guard.BROKER_EXIT_PORT_UNAVAILABLE)
    h.run_for(1)
    assert h.b.port not in range(old_port - 300, old_port + 300)
    assert len(h.procs) == 2 and f"tcp:127.0.0.1:{h.b.port}" in h.procs[-1].cmd
    assert "broker.port_moved" in h.events() and "broker.restarting" not in h.events()
    state = json.loads(Path(os.environ["KAZMA_GUARD_STATE"]).read_text(encoding="utf-8"))
    assert state["document_broker"]["url"] == h.b.url != old_url
    # Kazma's request still names the address it was handed: still ours.
    h.b.request_path.parent.mkdir(parents=True, exist_ok=True)
    h.b.request_path.write_text(json.dumps({"want": True, "url": old_url}), encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "auto")
    h.b.mode = "auto"
    assert h.b._decide() == (True, "requested")
    assert h.b.server_env()["KAZMA_DOCUMENT_BROKER_URL_SOURCE"] == "guard"


def test_the_search_crosses_a_reserved_block(monkeypatch):
    """The 64-port search found nothing in a block of hundreds and fell back
    to the reserved port itself; the search crosses the whole range now."""
    monkeypatch.delenv("KAZMA_GUARD_BROKER_PORT", raising=False)
    base = guard._choose_broker_port(None)
    _reserve(monkeypatch, set(range(base, base + 600)))
    assert guard._choose_broker_port(base) not in range(base, base + 600)

    def old_search():  # negative control: the search before 2026-10-31
        for step in range(64):
            port = 20000 + (base - 20000 + step) % 10000
            if guard._port_is_free(port):
                return port
        return base

    assert old_search() == base


def test_a_pinned_port_is_not_moved(monkeypatch, tmp_path):
    """KAZMA_GUARD_BROKER_PORT is the operator's: it backs off and says so."""
    h = _Harness(monkeypatch, tmp_path)
    h.b.before_spawn()
    h.exit_current(guard.BROKER_EXIT_PORT_UNAVAILABLE)
    h.run_for(1)
    assert h.b.port == 28765 and h.b.state == "backoff"
    assert "broker.port_unavailable" in h.events() and "broker.port_moved" not in h.events()


def test_moving_stops_after_a_few_ports_in_a_row(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.delenv("KAZMA_GUARD_BROKER_PORT")
    h.b.port = None
    h.ready = False
    h.b.before_spawn()
    for _ in range(guard.BROKER_PORT_MOVES + 1):
        h.exit_current(guard.BROKER_EXIT_PORT_UNAVAILABLE)
        h.run_for(1)
    assert h.events().count("broker.port_moved") == guard.BROKER_PORT_MOVES
    assert h.b.state == "backoff" and "broker.port_unavailable" in h.events()


def test_the_real_guard_moves_a_broker_whose_port_is_refused(tmp_path):
    """The guard process itself, a fake broker refusing its first port: the
    broker is answering on another port with no backoff, and the state file
    (which Kazma reads, documents/broker_supervision.broker_url) names it."""
    port = _free_port()
    run = _BrokerRun(tmp_path, port, broker={"refuse_first_port": True})
    run.env.pop("KAZMA_GUARD_BROKER_PORT")  # not pinned: the guard chooses
    with run as g:
        g.wait_for("broker.ready", timeout=90)
        refused = (tmp_path / "fake_broker.refused_port").read_text(encoding="utf-8")
        record = g.state()["document_broker"]
        assert g.count("broker.port_moved") == 1 and g.count("broker.restarting") == 0
        assert record["url"] != "tcp://" + refused[len("tcp:"):] and record["state"] == "running"
