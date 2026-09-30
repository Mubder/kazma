"""End-to-end supervision tests: the REAL guard against a fake server.

The unit tests in test_service_supervision.py check the guard's parts. These
run the actual ``kazma_guard.py`` process against a controllable stand-in
and assert on what it does to a live child -- the only kind of evidence
that would have caught the defects found on 2026-08-28, every one of which
survived unit tests and appeared the first time the thing was run:

  * a start budget that killed healthy boots
  * children orphaned by a hard stop, then a second server spawned
  * a guard reporting healthy while supervising a stranger
  * startup blocked before anything was logged

Each test below is one of those failures, reproduced in seconds instead of
against a production agent holding real credentials.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_kazma.py"

pytestmark = pytest.mark.timeout(180)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class GuardRun:
    """A real guard subprocess supervising a real fake-server subprocess."""

    def __init__(self, tmp: Path, port: int, **fake_env: str):
        self.tmp = tmp
        self.port = port
        self.marker = tmp / "marker.jsonl"
        self.log = tmp / "guard.log"
        self.proc: subprocess.Popen | None = None
        env = dict(os.environ)
        env.update({
            "KAZMA_GUARD_CMD": f'"{sys.executable}" "{FAKE}"',
            "KAZMA_GUARD_CWD": str(tmp),
            "KAZMA_GUARD_HEALTH_URL": f"http://127.0.0.1:{port}/health/ready",
            "KAZMA_GUARD_LOG": str(self.log),
            "KAZMA_GUARD_STATE": str(tmp / "state.json"),
            "KAZMA_GUARD_PAUSE_FILE": str(tmp / "paused"),
            "KAZMA_GUARD_RELOAD_FILE": str(tmp / "guard.reload"),
            "KAZMA_GUARD_START_TIMEOUT": fake_env.pop("START_TIMEOUT", "25"),
            "KAZMA_GUARD_INTERVAL": fake_env.pop("INTERVAL", "2"),
            "KAZMA_GUARD_PROBE_TIMEOUT": "3",
            "KAZMA_GUARD_FAILURES": fake_env.pop("FAILURES", "2"),
            "FAKE_PORT": str(port),
            "FAKE_MARKER": str(self.marker),
            # never let a test try to message a real chat
            "KAZMA_GUARD_TELEGRAM_TOKEN": "",
            "KAZMA_GUARD_TELEGRAM_CHAT": "",
            "SWARM_BOT_TOKEN": "",
            "SWARM_CHAT_ID": "",
        })
        env.update(fake_env)
        self.env = env

    def __enter__(self) -> GuardRun:
        self.proc = subprocess.Popen(
            [sys.executable, str(GUARD)], env=self.env, cwd=str(self.tmp),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()

    # -- observation helpers ------------------------------------------

    @staticmethod
    def _json_lines(path: Path) -> list[dict]:
        """Every complete JSON line in *path*. A line still being written --
        the guard appends while the test reads -- is skipped, not allowed to
        void the whole read: one partial line used to return [] and hid the
        event being waited for ("guard never logged ...; saw []", a full-suite
        run on 2026-09-26)."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def events(self) -> list[dict]:
        return self._json_lines(self.log)

    def event_names(self) -> list[str]:
        return [e.get("event", "") for e in self.events()]

    def generations(self) -> list[dict]:
        return self._json_lines(self.marker)

    def wait_for(self, event: str, timeout: float = 90.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            for e in self.events():
                if e.get("event") == event:
                    return e
            time.sleep(0.4)
        raise AssertionError(
            f"guard never logged {event!r}; saw {self.event_names()}"
        )

    def count(self, event: str) -> int:
        return sum(1 for e in self.events() if e.get("event") == event)


# ── the happy path ────────────────────────────────────────────────────


def test_guard_starts_and_reports_the_child_ready(tmp_path):
    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        assert g.event_names()[0] == "guard.start", (
            "guard.start must be the FIRST line: if the guard is alive, that "
            "line exists, so a hang is diagnosable rather than silent"
        )
        assert g.count("child.spawned") == 1


# ── the failures that actually happened ───────────────────────────────


def test_a_crashed_child_is_restarted(tmp_path):
    """The core promise: the server dies, it comes back, nobody types."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_EXIT_AFTER_S="3") as g:
        g.wait_for("child.ready", timeout=60)
        g.wait_for("guard.restarting", timeout=60)
        g.wait_for("child.spawned", timeout=60)
        # a second generation of the fake server actually ran
        end = time.time() + 60
        while time.time() < end and len(
            [x for x in g.generations() if x["event"] == "spawned"]
        ) < 2:
            time.sleep(0.5)
        spawned = [x for x in g.generations() if x["event"] == "spawned"]
        assert len(spawned) >= 2, "the child was never actually restarted"
        assert spawned[0]["pid"] != spawned[1]["pid"]


def test_a_crashed_childs_last_words_are_kept(tmp_path):
    """The server's stderr is kept and quoted when it dies (2026-09-30).

    On 2026-09-25 the guard logged "process exited (code 1)" and nothing
    anywhere said why: serve.py printed the error to stdout, and under the
    scheduled task the guard's own output goes nowhere.
    """
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_EXIT_AFTER_S="3") as g:
        g.wait_for("child.ready", timeout=60)
        ev = g.wait_for("guard.restarting", timeout=60)
        assert "process exited" in str(ev.get("reason"))
        assert "fake_kazma crashed on purpose" in str(ev.get("stderr_tail", "")), ev
        kept = (tmp_path / "server.stderr.log").read_text(encoding="utf-8", errors="replace")
        assert "RuntimeError: fake_kazma crashed on purpose" in kept


def test_a_wedged_child_is_killed_and_replaced(tmp_path):
    """Alive, port open, answers nothing -- invisible to every OS supervisor.

    This is the failure the whole guard exists for.
    """
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_HANG_AFTER_S="4", FAILURES="2") as g:
        g.wait_for("child.ready", timeout=60)
        g.wait_for("health.failed", timeout=60)
        ev = g.wait_for("guard.restarting", timeout=90)
        assert "unhealthy" in str(ev.get("reason", ""))


def test_a_not_ready_child_is_ridden_out_not_restarted(tmp_path):
    """503 / not_ready: Kazma answers, its database does not.

    A restart cannot bring a database back. On 2026-09-28 a Docker Desktop
    update took Postgres away for two minutes and the old rule ("a critical
    dependency is gone: restart it") restarted Kazma over it. The outage is
    now paged once and ridden out.
    """
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_NOT_READY_AFTER_S="4", FAILURES="2",
                  INTERVAL="2", KAZMA_GUARD_DEPENDENCY_OUTAGE_S="600") as g:
        g.wait_for("child.ready", timeout=60)
        ev = g.wait_for("health.dependency_down", timeout=60)
        assert "database" in str(ev.get("detail", ""))
        time.sleep(8)  # several more probe cycles against a not-ready server
        assert g.count("guard.restarting") == 0
        assert g.count("child.spawned") == 1


def test_a_not_ready_that_only_a_restart_clears_is_restarted(tmp_path):
    """The volatile settings store: a boot while the database was away keeps
    the in-memory fallback for the life of the process. The app says so
    (``restart_required``), and the guard does not ride it out."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_NOT_READY_AFTER_S="4",
                  FAKE_NOT_READY_RESTART_REQUIRED="1", FAILURES="2", INTERVAL="2",
                  KAZMA_GUARD_DEPENDENCY_OUTAGE_S="600") as g:
        g.wait_for("child.ready", timeout=60)
        ev = g.wait_for("guard.restarting", timeout=60)
        assert "unhealthy" in str(ev.get("reason", ""))
        assert "not ready for" not in str(ev.get("reason", ""))
        assert g.count("health.dependency_down") == 0


def test_a_long_dependency_outage_is_restarted_after_the_runway(tmp_path):
    """Past the runway the guard restarts anyway: the database may be back
    and Kazma's own connections what is stuck."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_NOT_READY_AFTER_S="4", FAILURES="2",
                  INTERVAL="2", KAZMA_GUARD_DEPENDENCY_OUTAGE_S="8") as g:
        g.wait_for("child.ready", timeout=60)
        ev = g.wait_for("guard.restarting", timeout=90)
        assert "not ready for" in str(ev.get("reason", ""))


def test_a_degraded_child_keeps_serving_and_is_not_restarted(tmp_path):
    """The dangerous false positive.

    /health/ready reports "degraded" with HTTP 200 when a NON-critical
    dependency fails -- one bad MCP server, say -- and explicitly means
    "still accepts traffic". A guard that restarts on any word other than
    "ready" would kill a healthy agent every 90 seconds forever.
    """
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_DEGRADED_AFTER_S="3",
                  FAILURES="2", INTERVAL="2") as g:
        g.wait_for("child.ready", timeout=60)
        time.sleep(12)  # several probe cycles against a degraded server
        assert g.count("guard.restarting") == 0, (
            "a degraded-but-serving app must never be restarted"
        )
        assert g.count("child.spawned") == 1


def test_a_child_that_never_binds_is_reaped_after_the_budget(tmp_path):
    """The 180s budget killed healthy boots; the budget must still exist."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_NEVER_READY="1",
                  START_TIMEOUT="8") as g:
        ev = g.wait_for("child.never_ready", timeout=60)
        assert float(ev.get("waited_s", 0)) >= 7
        g.wait_for("guard.restarting", timeout=30)


def test_a_slow_boot_is_waited_out_not_killed(tmp_path):
    """The defect that cost the most downtime: a healthy boot slower than
    the budget was killed on every attempt, forever."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_BOOT_DELAY_S="6",
                  START_TIMEOUT="40") as g:
        g.wait_for("child.ready", timeout=90)
        assert g.count("child.never_ready") == 0
        assert g.count("guard.restarting") == 0, "a slow boot is not a failure"


# ── maintenance switch ────────────────────────────────────────────────


def test_pause_stops_restarts_and_resume_brings_it_back(tmp_path):
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_EXIT_AFTER_S="300") as g:
        g.wait_for("child.ready", timeout=60)

        env = dict(g.env)
        subprocess.run([sys.executable, str(GUARD), "--pause", "--stop",
                        "--reason", "integration test", "--ttl", "600"],
                       env=env, capture_output=True, timeout=60, check=False)

        g.wait_for("maintenance.active", timeout=60)
        before = g.count("child.spawned")
        time.sleep(6)
        assert g.count("child.spawned") == before, (
            "a paused guard must not restart the server"
        )

        subprocess.run([sys.executable, str(GUARD), "--resume"],
                       env=env, capture_output=True, timeout=60, check=False)
        g.wait_for("maintenance.resumed", timeout=60)
        end = time.time() + 60
        while time.time() < end and g.count("child.spawned") <= before:
            time.sleep(0.5)
        assert g.count("child.spawned") > before, "resume must restart the server"


def test_pause_is_not_counted_as_a_crash(tmp_path):
    """Treating a deliberate pause as a crash would push the operator into a
    30-minute cooldown for diagnosing carefully."""
    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        subprocess.run([sys.executable, str(GUARD), "--pause", "--stop",
                        "--reason", "t", "--ttl", "600"],
                       env=dict(g.env), capture_output=True, timeout=60, check=False)
        g.wait_for("guard.paused_by_operator", timeout=60)
        restarting = [e for e in g.events() if e.get("event") == "guard.restarting"]
        assert not restarting, "maintenance must not enter the restart/backoff path"


# ── operator reload: the guard does the stop ──────────────────────────


def _reload_cli(g: GuardRun, timeout: float = 150.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD), "--reload"],
        env=dict(g.env), cwd=str(g.tmp), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _probe_count(g: GuardRun) -> int:
    return sum(1 for x in g.generations() if x["event"] == "probe")


def test_the_servers_own_restart_request_is_carried_out_by_the_guard(tmp_path, monkeypatch):
    """Settings' "Restart server" asks the guard (2026-09-30).

    It used to start a detached copy of the server and hard-exit: the guard
    restarted its own child and killed the copy as a foreign server on its
    port, and no shutdown hook ran. The server now writes the guard's reload
    request -- this is that call, from the server's side, against a real
    guard -- and the guard does a graceful reload.
    """
    from kazma_core.observability import supervisor_watch

    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        # What the guard hands the server it starts.
        monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("KAZMA_GUARD_RELOAD_FILE", g.env["KAZMA_GUARD_RELOAD_FILE"])
        assert supervisor_watch.request_guard_reload("test") is True
        ev = g.wait_for("guard.operator_reload", timeout=60)
        assert ev.get("graceful") is True, g.event_names()
        gens = [x["event"] for x in g.generations()]
        assert gens.count("spawned") == 2, gens
        assert "port.reaping_holder" not in g.event_names()


def test_a_reload_is_carried_out_by_the_guard_and_is_graceful(tmp_path):
    """The guard stops its own child -- an operator shell may lack the rights
    (the KazmaAgent task runs elevated; live 2026-09-26 "Access is denied"
    and the old build kept serving) -- and asks it to shut down instead of
    killing it, so the app's shutdown hooks run."""
    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        done = _reload_cli(g)
        assert done.returncode == 0, done.stdout + done.stderr
        ev = g.wait_for("guard.operator_reload", timeout=30)
        assert ev.get("graceful") is True, g.event_names()
        names = g.event_names()
        assert "guard.reload_requested" in names
        assert "child.stopped_gracefully" in names
        # The shell never had to stop anything itself.
        assert "reload.child_stopped" not in names
        assert "port.reaping_holder" not in names
        gens = [x["event"] for x in g.generations()]
        assert gens.count("graceful_exit") == 1, gens
        assert gens.count("spawned") == 2, gens
        assert not (tmp_path / "guard.reload").exists()


def test_a_server_that_ignores_the_stop_request_is_killed_after_the_grace(tmp_path):
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_IGNORE_STOP="1",
                  KAZMA_GUARD_GRACEFUL_STOP_S="3") as g:
        g.wait_for("child.ready", timeout=60)
        done = _reload_cli(g)
        assert done.returncode == 0, done.stdout + done.stderr
        ev = g.wait_for("guard.operator_reload", timeout=30)
        assert ev.get("graceful") is False
        assert "child.graceful_timeout" in g.event_names()
        # Instrument check: the request did reach the server.
        assert "stop_ignored" in [x["event"] for x in g.generations()]


def test_a_leftover_reload_request_does_not_make_the_guard_probe_nonstop(tmp_path):
    """A request older than the running server is satisfied, not a reason to
    wake. A leftover one made the guard probe 53 times a second for 47 hours
    (2026-09-20..22)."""
    port = _free_port()
    leftover = tmp_path / "guard.reload"
    leftover.write_text(json.dumps({"ts": time.time() - 3600}), encoding="utf-8")
    with GuardRun(tmp_path, port, FAKE_COUNT_PROBES="1", INTERVAL="2") as g:
        g.wait_for("child.ready", timeout=60)
        g.wait_for("reload.already_satisfied", timeout=30)
        before = _probe_count(g)
        time.sleep(8)
        during = _probe_count(g) - before
        # Eight seconds at a two-second interval is about four probes.
        assert 2 <= during <= 6, during
        assert g.count("child.spawned") == 1, "a satisfied request must not restart"
        assert not leftover.exists()


def test_the_guard_keeps_a_heartbeat_that_status_reads(tmp_path):
    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert time.time() - float(state["heartbeat"]) < 30
        out = subprocess.run(
            [sys.executable, str(GUARD), "--status"], env=dict(g.env),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        ).stdout
        assert "guard       : running" in out, out
