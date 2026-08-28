#!/usr/bin/env python3
"""Health-gated supervisor for the Kazma server.

Why this exists
---------------
Every OS supervisor (systemd, launchd, Windows Scheduled Task, Docker)
restarts a process when it *exits*. None of them restart a process that is
still running but wedged -- a hung event loop, a deadlocked worker, a server
that accepts connections and answers nothing. Those are the failures that
actually keep an agent "up" while doing nothing, and they are invisible to
a plain restart policy.

This guard closes that gap in one place, identically on every platform:

    OS supervisor  ->  keeps THIS guard alive
    this guard     ->  keeps the SERVER healthy

It launches the server, waits for it to become ready, then polls
``/health/ready``. Repeated failures are treated as death: the child is
terminated and restarted with exponential backoff. A crash loop escalates
to a long cooldown and an alert rather than hammering the LLM provider.

Design notes
------------
* **Standard library only.** The guard must keep working when the venv is
  half-broken -- that is precisely when it is needed.
* **It logs to its own file.** Writing to ``kazma.log`` would mean the
  supervisor goes silent in the same failure that silences the server.
* **The notifier does not route through Kazma.** Alerting through the
  agent's own message bus cannot tell you the agent is dead. The guard
  talks to Telegram directly, or logs and moves on.

Usage
-----
    python scripts/service/kazma_guard.py                 # supervise
    python scripts/service/kazma_guard.py --once          # no restarts (debug)
    python scripts/service/kazma_guard.py --dry-run       # print config, exit

Configuration (all optional, env vars):
    KAZMA_GUARD_CMD             command to run       (default: serve.py)
    KAZMA_GUARD_CWD             working directory    (default: repo root)
    KAZMA_GUARD_HEALTH_URL      health endpoint      (default: :9090/health/ready)
    KAZMA_GUARD_START_TIMEOUT   seconds to first ready       (default: 900)
    KAZMA_GUARD_INTERVAL        seconds between probes       (default: 30)
    KAZMA_GUARD_FAILURES        consecutive fails = dead     (default: 3)
    KAZMA_GUARD_LOG             guard log path
    KAZMA_GUARD_STATE           child-PID state file (orphan reaping)
    KAZMA_GUARD_TELEGRAM_TOKEN  bot token (falls back to SWARM_BOT_TOKEN)
    KAZMA_GUARD_TELEGRAM_CHAT   chat id   (falls back to SWARM_CHAT_ID)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

# -- configuration ----------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_HEALTH_URL = "http://127.0.0.1:9090/health/ready"

# Kazma's cold start is SLOW: it loads a local embedding model (bge-m3),
# connects MCP servers, hydrates the config store and warms the graph. The
# first version of this guard budgeted 180s and killed a perfectly healthy
# boot three minutes in, every time -- turning a working agent into a
# restart loop (live, 2026-08-28 12:22). Measured cold start on the
# reference host is ~3-5 minutes, so the budget is 15 with room to spare.
#
# Being generous here is safe because a child that has genuinely DIED is
# detected immediately by poll() rather than by this timeout; the budget
# only bounds the "started but never answered" case.
START_TIMEOUT_S = float(os.environ.get("KAZMA_GUARD_START_TIMEOUT", "900"))
PROBE_INTERVAL_S = float(os.environ.get("KAZMA_GUARD_INTERVAL", "30"))
PROBE_TIMEOUT_S = float(os.environ.get("KAZMA_GUARD_PROBE_TIMEOUT", "10"))
FAILURES_TO_KILL = int(os.environ.get("KAZMA_GUARD_FAILURES", "3"))

# Backoff between restarts: index by consecutive-restart count, capped.
BACKOFF_LADDER_S = (5, 15, 30, 60, 120, 300)

# Crash-loop guard: this many restarts inside this window triggers a long
# cooldown. Without it, a bad config becomes a restart storm that bills the
# LLM provider for nothing.
CRASH_LOOP_COUNT = 5
CRASH_LOOP_WINDOW_S = 600
CRASH_LOOP_COOLDOWN_S = 1800

TERMINATE_GRACE_S = 20.0


def _default_log_path() -> Path:
    env = os.environ.get("KAZMA_GUARD_LOG")
    if env:
        return Path(env)
    return Path.home() / ".kazma" / "guard.log"


def _state_path() -> Path:
    """Where the guard records the PID of the child it launched.

    Windows' Stop-ScheduledTask terminates the guard without giving it a
    chance to clean up, which ORPHANS the server it launched. The next
    guard then starts a second one, and two agents race for the port while
    sharing one Postgres and one workspace (live, 2026-08-28 12:26).

    Recording the child PID on disk lets the next guard find and reap that
    orphan before spawning, which no in-memory state could survive to do.
    """
    env = os.environ.get("KAZMA_GUARD_STATE")
    if env:
        return Path(env)
    return Path.home() / ".kazma" / "guard.state.json"


# -- logging (deliberately not the app's logger) ----------------------


class GuardLog:
    """Tiny append-only JSONL logger with stderr mirroring.

    Uses no third-party logging config so it cannot be silenced by the
    application's own logging setup.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def __call__(self, level: str, event: str, **fields: object) -> None:
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
            **fields,
        }
        line = json.dumps(rec, default=str)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        print(f"[guard] {level:<5} {event} {fields or ''}", file=sys.stderr, flush=True)


# -- notifier (must not depend on Kazma being alive) ------------------


class Notifier:
    """Best-effort out-of-band alerting. Never raises, never blocks long.

    Credentials are resolved in this order, first hit wins:

    1. ``KAZMA_GUARD_TELEGRAM_TOKEN`` / ``KAZMA_GUARD_TELEGRAM_CHAT``
    2. ``SWARM_BOT_TOKEN`` / ``SWARM_CHAT_ID``
    3. Kazma's own config store, which resolves the token out of the
       encrypted vault (``connectors.telegram.token``)

    Step 3 is the only place the guard reaches into the application, and it
    is deliberately last and fully guarded. The supervision loop stays
    standard-library-only: if the venv is too broken to import kazma_core,
    the guard loses ALERTING but never loses SUPERVISION. That is the right
    way round -- a supervisor that dies because its notifier could not load
    is worse than one that restarts silently.
    """

    def __init__(self, log: GuardLog) -> None:
        self._log = log
        token = (
            os.environ.get("KAZMA_GUARD_TELEGRAM_TOKEN")
            or os.environ.get("SWARM_BOT_TOKEN")
            or ""
        ).strip()
        chat = (
            os.environ.get("KAZMA_GUARD_TELEGRAM_CHAT")
            or os.environ.get("SWARM_CHAT_ID")
            or ""
        ).strip()
        if not (token and chat):
            v_token, v_chat = self._from_config_store()
            token = token or v_token
            chat = chat or v_chat
        self.token = token
        self.chat = chat
        self._source = "env" if os.environ.get(
            "KAZMA_GUARD_TELEGRAM_TOKEN"
        ) or os.environ.get("SWARM_BOT_TOKEN") else "vault"

    def _from_config_store(self) -> tuple[str, str]:
        """Resolve token + chat from the app's config store. Never raises."""
        try:
            from kazma_core.config_store import get_config_store

            cs = get_config_store()
            token = str(cs.get("connectors.telegram.token", "") or "")
            chat = (
                str(cs.get("guard.telegram.chat_id", "") or "")
                or str(cs.get("swarm.group_chat_id", "") or "")
            )
            return token.strip(), chat.strip()
        except Exception as exc:
            self._log("info", "notify.config_store_unavailable",
                      error=str(exc)[:120])
            return "", ""

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat)

    def describe(self) -> str:
        """Safe-to-log description. Never includes the token."""
        if not self.configured:
            return "not configured"
        return f"telegram via {self._source} -> chat {self.chat}"

    def send(self, text: str) -> None:
        if not self.configured:
            self._log("info", "notify.skipped", reason="not configured", text=text)
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            body = urllib.parse.urlencode(
                {"chat_id": self.chat, "text": text, "disable_web_page_preview": "true"}
            ).encode()
            req = urllib.request.Request(url, data=body, method="POST")
            with urllib.request.urlopen(req, timeout=10):
                pass
            self._log("info", "notify.sent")
        except Exception as exc:
            # An alert that fails must not take the supervisor down with it.
            self._log("warn", "notify.failed", error=str(exc)[:200])


# -- health probe -----------------------------------------------------


def probe(url: str, timeout: float) -> tuple[bool, str]:
    """Return (healthy, detail). Any non-200 or exception is unhealthy."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            raw = resp.read(4096).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            return True, "200 (unparsed body)"
        status = str(data.get("status", "")).lower()
        if status in ("ready", "alive", "healthy", "ok"):
            return True, status
        failing = [
            k for k, v in (data.get("checks") or {}).items()
            if isinstance(v, dict) and v.get("status") not in ("ok", "healthy")
        ]
        return False, f"status={status or '?'} failing={failing or '?'}"
    except urllib.error.URLError as exc:
        return False, f"unreachable: {getattr(exc, 'reason', exc)}"
    except Exception as exc:  # noqa: BLE001 -- a probe must never raise
        return False, f"probe error: {exc}"


# -- child process control --------------------------------------------


def build_command() -> list[str]:
    raw = os.environ.get("KAZMA_GUARD_CMD", "").strip()
    if raw:
        import shlex

        return shlex.split(raw, posix=(os.name != "nt"))
    return [sys.executable, "serve.py"]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def reap_orphan(log: GuardLog) -> None:
    """Kill a server left behind by a previous guard, before spawning ours.

    Without this, every hard stop of the guard (Stop-ScheduledTask, a task
    restart, a machine going to sleep mid-run) leaves the server running
    and the next guard starts a SECOND one.
    """
    path = _state_path()
    try:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        pid = int(data.get("child_pid") or 0)
    except Exception:
        return
    if not pid or pid == os.getpid() or not _pid_alive(pid):
        return
    log("warn", "orphan.reaping", pid=pid,
        note="left by a previous guard that was killed without cleanup")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30, check=False)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception as exc:
        log("error", "orphan.reap_failed", pid=pid, error=str(exc)[:200])
        return
    # Give the OS a moment to release the listening socket.
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.5)
    log("info", "orphan.reaped", pid=pid)


def _record_child(pid: int | None) -> None:
    """Persist (or clear) the child PID. Never raises."""
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"child_pid": pid or 0, "guard_pid": os.getpid()}),
            encoding="utf-8",
        )
    except Exception:
        pass


def spawn(cmd: list[str], cwd: Path, log: GuardLog) -> subprocess.Popen:
    kwargs: dict = {"cwd": str(cwd)}
    if os.name == "nt":
        # Own process group so the child and ITS children (serve.py spawns
        # uvicorn) can be signalled and killed as a unit.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    _record_child(proc.pid)
    log("info", "child.spawned", pid=proc.pid, cmd=" ".join(cmd))
    return proc


def stop_child(proc: subprocess.Popen, log: GuardLog) -> None:
    """Terminate the child and everything it spawned. Never raises."""
    if proc.poll() is not None:
        return
    log("info", "child.terminating", pid=proc.pid)
    try:
        if os.name == "nt":
            # serve.py launches uvicorn as a grandchild; terminate() would
            # orphan it holding port 9090, and the restart would then fail
            # to bind. taskkill /T takes the whole tree.
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=20, check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception as exc:
        log("warn", "child.terminate_failed", error=str(exc)[:200])

    try:
        proc.wait(timeout=TERMINATE_GRACE_S)
    except Exception:
        log("warn", "child.kill_forced", pid=proc.pid)
        try:
            proc.kill()
        except Exception:
            pass
    _record_child(None)


# -- supervisor -------------------------------------------------------


class Guard:
    def __init__(self, *, once: bool = False) -> None:
        self.log = GuardLog(_default_log_path())
        self.notify = Notifier(self.log)
        self.cmd = build_command()
        self.cwd = Path(os.environ.get("KAZMA_GUARD_CWD") or REPO_ROOT)
        self.health_url = os.environ.get("KAZMA_GUARD_HEALTH_URL", DEFAULT_HEALTH_URL)
        self.once = once
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.recent = deque(maxlen=CRASH_LOOP_COUNT)
        self._stop = False

    # -- lifecycle ----------------------------------------------------

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            self.log("info", "guard.signal", signal=int(signum))
            self._stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except Exception:
                pass

    def _wait_ready(self) -> bool:
        """Poll until the server reports ready or the start budget expires.

        A child that EXITS is failure detected instantly. The budget only
        bounds the slower "running but never answered" case, so it can
        afford to be generous -- see START_TIMEOUT_S.
        """
        started = time.monotonic()
        deadline = started + START_TIMEOUT_S
        last = ""
        next_progress = started + 60.0
        while time.monotonic() < deadline and not self._stop:
            if self.proc and self.proc.poll() is not None:
                self.log("error", "child.exited_during_startup",
                         code=self.proc.returncode,
                         after_s=round(time.monotonic() - started, 1))
                return False
            ok, detail = probe(self.health_url, PROBE_TIMEOUT_S)
            if ok:
                self.log("info", "child.ready",
                         detail=detail, after_s=round(time.monotonic() - started, 1))
                return True
            last = detail
            # Heartbeat while waiting, so a slow boot is visibly different
            # from a hang when someone reads this log at 3am.
            if time.monotonic() >= next_progress:
                self.log("info", "child.starting",
                         waited_s=round(time.monotonic() - started),
                         budget_s=START_TIMEOUT_S, last=last)
                next_progress += 60.0
            time.sleep(3.0)
        self.log("error", "child.never_ready", last=last,
                 budget_s=START_TIMEOUT_S,
                 waited_s=round(time.monotonic() - started, 1))
        return False

    def _backoff(self) -> float:
        idx = min(self.restarts, len(BACKOFF_LADDER_S) - 1)
        return float(BACKOFF_LADDER_S[idx])

    def _crash_looping(self) -> bool:
        now = time.monotonic()
        self.recent.append(now)
        if len(self.recent) < CRASH_LOOP_COUNT:
            return False
        return (now - self.recent[0]) <= CRASH_LOOP_WINDOW_S

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep so shutdown stays responsive."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._stop:
            time.sleep(min(1.0, end - time.monotonic()))

    # -- main loop ----------------------------------------------------

    def _foreign_server_present(self) -> bool:
        """True if something is ALREADY serving our health URL.

        Without this check the guard can supervise nothing at all: it spawns
        a child, the child fails to bind because another Kazma still holds
        the port, and the probe is satisfied by that other process. The
        guard then reports healthy forever while the thing it launched is
        gone -- a supervisor fooled into watching a stranger.

        Refusing to start is the safe outcome. Two agents sharing one
        Postgres and one workspace is worse than a delayed handover.
        """
        ok, detail = probe(self.health_url, PROBE_TIMEOUT_S)
        if ok:
            self.log("error", "guard.port_already_served",
                     health=self.health_url, detail=detail)
        return ok

    def run(self) -> int:
        self._install_signals()
        self.log(
            "info", "guard.start",
            cmd=" ".join(self.cmd), cwd=str(self.cwd),
            health=self.health_url, notifier=self.notify.describe(),
        )

        # Order matters: reap a known orphan from a previous guard FIRST,
        # then check whether anything else is serving. Reversed, a booting
        # orphan is invisible to the probe (nothing bound yet) and both
        # instances race for the port.
        reap_orphan(self.log)

        if self._foreign_server_present():
            msg = (
                "Kazma guard did NOT start: something is already serving "
                f"{self.health_url}. Stop the existing instance first, then "
                "start the guard -- otherwise it would supervise a process "
                "it did not launch."
            )
            self.log("error", "guard.refused_to_start")
            self.notify.send(msg)
            print(msg, file=sys.stderr)
            return 2

        first = True
        while not self._stop:
            self.proc = spawn(self.cmd, self.cwd, self.log)

            if self._wait_ready():
                msg = (
                    "Kazma is up." if first
                    else f"Kazma restarted and is healthy (restart #{self.restarts})."
                )
                self.notify.send(msg)
                first = False
                reason = self._supervise()
            else:
                reason = "never became healthy"
                stop_child(self.proc, self.log)

            if self._stop:
                break
            if self.once:
                self.log("info", "guard.once_exit", reason=reason)
                return 1

            self.restarts += 1
            if self._crash_looping():
                self.log("error", "guard.crash_loop", restarts=self.restarts,
                         window_s=CRASH_LOOP_WINDOW_S)
                self.notify.send(
                    f"Kazma is crash-looping ({CRASH_LOOP_COUNT} restarts in "
                    f"{CRASH_LOOP_WINDOW_S // 60} min). Last reason: {reason}. "
                    f"Pausing {CRASH_LOOP_COOLDOWN_S // 60} min -- this needs a human."
                )
                self.recent.clear()
                self._sleep(CRASH_LOOP_COOLDOWN_S)
                continue

            delay = self._backoff()
            self.log("warn", "guard.restarting", reason=reason, in_s=delay,
                     restarts=self.restarts)
            self.notify.send(f"Kazma stopped ({reason}). Restarting in {int(delay)}s.")
            self._sleep(delay)

        if self.proc:
            stop_child(self.proc, self.log)
        self.log("info", "guard.stopped")
        return 0

    def _supervise(self) -> str:
        """Watch a healthy child. Returns the reason it needs restarting."""
        consecutive = 0
        while not self._stop:
            self._sleep(PROBE_INTERVAL_S)
            if self._stop:
                return "guard shutting down"

            assert self.proc is not None
            if self.proc.poll() is not None:
                return f"process exited (code {self.proc.returncode})"

            ok, detail = probe(self.health_url, PROBE_TIMEOUT_S)
            if ok:
                if consecutive:
                    self.log("info", "health.recovered", after_failures=consecutive)
                consecutive = 0
                continue

            consecutive += 1
            self.log("warn", "health.failed", detail=detail,
                     consecutive=consecutive, threshold=FAILURES_TO_KILL)
            if consecutive >= FAILURES_TO_KILL:
                # Alive but not healthy -- the case no OS supervisor catches.
                stop_child(self.proc, self.log)
                return f"unhealthy ({detail})"
        return "guard shutting down"


def main() -> int:
    ap = argparse.ArgumentParser(description="Health-gated supervisor for Kazma.")
    ap.add_argument("--once", action="store_true",
                    help="run the server once; do not restart it")
    ap.add_argument("--dry-run", action="store_true",
                    help="print resolved configuration and exit")
    args = ap.parse_args()

    guard = Guard(once=args.once)
    if args.dry_run:
        print(json.dumps({
            "command": guard.cmd,
            "cwd": str(guard.cwd),
            "health_url": guard.health_url,
            "guard_log": str(guard.log.path),
            "start_timeout_s": START_TIMEOUT_S,
            "probe_interval_s": PROBE_INTERVAL_S,
            "failures_to_kill": FAILURES_TO_KILL,
            "backoff_ladder_s": list(BACKOFF_LADDER_S),
            "crash_loop": {
                "count": CRASH_LOOP_COUNT,
                "window_s": CRASH_LOOP_WINDOW_S,
                "cooldown_s": CRASH_LOOP_COOLDOWN_S,
            },
            "notifier_configured": guard.notify.configured,
            "notifier": guard.notify.describe(),
        }, indent=2))
        return 0
    return guard.run()


if __name__ == "__main__":
    raise SystemExit(main())
