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

    def events(self) -> list[dict]:
        try:
            return [json.loads(x) for x in
                    self.log.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            return []

    def event_names(self) -> list[str]:
        return [e.get("event", "") for e in self.events()]

    def generations(self) -> list[dict]:
        try:
            return [json.loads(x) for x in
                    self.marker.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            return []

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


def test_a_not_ready_child_is_restarted(tmp_path):
    """503 / not_ready means a critical dependency is gone: restart it."""
    port = _free_port()
    with GuardRun(tmp_path, port, FAKE_NOT_READY_AFTER_S="4", FAILURES="2") as g:
        g.wait_for("child.ready", timeout=60)
        g.wait_for("health.failed", timeout=60)
        g.wait_for("guard.restarting", timeout=90)


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
