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
    python scripts/service/kazma_guard.py --reload        # pick up code changes
    python scripts/service/kazma_guard.py --status
    python scripts/service/kazma_guard.py --pause --stop --reason "…"
    python scripts/service/kazma_guard.py --resume

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

# Same-detail restart pages collapse inside this window. Every attempt
# still lands in guard.log. Crash-loop is a different condition and
# always pages. Override: KAZMA_GUARD_PAGE_COOLDOWN_S.
PAGE_COOLDOWN_S = float(os.environ.get("KAZMA_GUARD_PAGE_COOLDOWN_S", "900"))

TERMINATE_GRACE_S = 20.0

# A server whose build.started_at predates our spawn by more than this is
# NOT the child we launched -- it is an orphan or another install that won
# the port. Small negative slack absorbs clock jitter only.
FOREIGN_SERVER_SLACK_S = 5.0


def _default_log_path() -> Path:
    env = os.environ.get("KAZMA_GUARD_LOG")
    if env:
        return Path(env)
    return _guard_file("guard.log")


def _kazma_home() -> Path:
    """Kazma's own home: ``<install>/.kazma``, not ``~/.kazma``.

    The guard originally wrote here under the user's home, which broke a
    rule this project documents in paths.py: state is PROJECT-LOCAL so the
    whole install travels together. Worse, ``migrate_legacy_user_home``
    tells operators in a warning that ``~/.kazma`` is "safe to
    archive/delete" once the project home exists -- advice written before
    anything important lived there. The restic passphrase did, and it is
    gone (live, 2026-08-29).

    Resolved without importing kazma_core: the guard has to run before and
    independently of the app, including when the app cannot start at all.
    """
    env = (os.environ.get("KAZMA_USER_HOME") or "").strip()
    if env:
        return Path(env)
    # scripts/service/kazma_guard.py -> <install>
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file():
        return root / ".kazma"
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file():
        return cwd / ".kazma"
    # Never Path.home()/.kazma — operator rule: no files outside the install.
    d = cwd / ".kazma"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _legacy_home() -> Path:
    return Path.home() / ".kazma"


def _guard_file(name: str) -> Path:
    """Always ``<install>/.kazma/<name>``. Copy once from a legacy home.

    The old guard wrote under ``~/.kazma``. Preferring that path forever
    is why ``C:\\Users\\balfa\\.kazma`` kept growing after the product
    home moved next to the repo. Copy missing files in, then write only
    inside the install.
    """
    dest_dir = _kazma_home()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    legacy = _legacy_home() / name
    if not dest.exists() and legacy.exists() and legacy.is_file():
        try:
            dest.write_bytes(legacy.read_bytes())
        except OSError:
            pass
    return dest


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
    return _guard_file("guard.state.json")


def _pause_path() -> Path:
    """Maintenance flag. Its PRESENCE is the switch.

    A file, not an env var or a stopped task, because it has to outlive
    everything: the guard restarting, the task retriggering at logon, and
    the machine rebooting. Stop-ScheduledTask does none of that -- Kazma
    would quietly come back mid-diagnosis at the next logon.
    """
    env = os.environ.get("KAZMA_GUARD_PAUSE_FILE")
    if env:
        return Path(env)
    return _guard_file("guard.paused")


def _reload_path() -> Path:
    """Operator --reload marker. Presence means 'respawn now, not a crash'.

    --reload kills the child so the long-lived guard will start a new one.
    Without this flag the guard treats that kill as ``process exited (code 1)``
    and climbs the crash backoff (5s → 300s). The fifth deploy of the day
    then sits on connection-refused for five minutes (live, 2026-08-31).
    """
    env = os.environ.get("KAZMA_GUARD_RELOAD_FILE")
    if env:
        return Path(env)
    return _guard_file("guard.reload")


def request_reload() -> None:
    path = _reload_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")


def reload_requested() -> bool:
    try:
        return _reload_path().is_file()
    except Exception:
        return False


def consume_reload_request() -> bool:
    path = _reload_path()
    try:
        if not path.is_file():
            return False
        path.unlink()
        return True
    except Exception:
        return False


# A forgotten pause is an outage nobody is looking for -- the exact failure
# this whole project exists to prevent. So a pause EXPIRES by default, and
# nags on Telegram every hour until it does.
DEFAULT_PAUSE_TTL_S = 2 * 3600
PAUSE_NAG_EVERY_S = 3600


def read_pause() -> dict | None:
    """Active pause record, or None. Expired pauses clear themselves."""
    path = _pause_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        # An unreadable pause file still means "paused" -- fail safe toward
        # the operator's intent rather than restarting under them.
        return {"reason": "unreadable pause file", "until": 0.0, "since": 0.0}
    until = float(data.get("until") or 0.0)
    if until and time.time() >= until:
        try:
            path.unlink()
        except Exception:
            pass
        return None
    return data


def write_pause(reason: str, ttl_s: float) -> dict:
    now = time.time()
    rec = {
        "since": now,
        "until": (now + ttl_s) if ttl_s > 0 else 0.0,
        "reason": reason or "manual maintenance",
        "by": os.environ.get("USERNAME") or os.environ.get("USER") or "?",
    }
    path = _pause_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def clear_pause() -> bool:
    path = _pause_path()
    try:
        if path.exists():
            path.unlink()
            return True
    except Exception:
        pass
    return False


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
        # ENV ONLY here. Resolving from the vault means importing
        # kazma_core, which is a heavy application import -- and this
        # constructor runs before supervision begins. When that import was
        # slow, the guard started, blocked, and logged NOTHING, so it looked
        # dead while the server stayed down (live, 2026-08-28 12:37).
        # Credential lookup must never sit on the critical path of starting
        # supervision, so the vault is consulted lazily on first use.
        self.token = (
            os.environ.get("KAZMA_GUARD_TELEGRAM_TOKEN")
            or os.environ.get("SWARM_BOT_TOKEN")
            or ""
        ).strip()
        self.chat = (
            os.environ.get("KAZMA_GUARD_TELEGRAM_CHAT")
            or os.environ.get("SWARM_CHAT_ID")
            or ""
        ).strip()
        self._source = "env" if (self.token and self.chat) else "unresolved"
        self._resolved = bool(self.token and self.chat)

    def _resolve(self) -> None:
        """Fill in missing credentials from the vault. Once, lazily."""
        if self._resolved:
            return
        self._resolved = True
        v_token, v_chat = self._from_config_store()
        self.token = self.token or v_token
        self.chat = self.chat or v_chat
        if self.token and self.chat:
            self._source = "vault"

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
        self._resolve()
        return bool(self.token and self.chat)

    def describe(self) -> str:
        """Safe-to-log description. Never includes the token."""
        self._resolve()
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


def _live_url(ready_url: str) -> str:
    """Derive /health/live from the configured readiness URL."""
    if ready_url.endswith("/health/ready"):
        return ready_url[: -len("/ready")] + "/live"
    return ready_url


def server_started_at(ready_url: str, timeout: float) -> float | None:
    """Unix time the CURRENTLY SERVING process booted, or None.

    ``/health/live`` reports ``build.started_at``. Comparing it against the
    moment we spawned our child answers the question no port check can:
    *is the thing answering actually the thing I launched?*
    """
    try:
        req = urllib.request.Request(_live_url(ready_url), method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read(4096).decode("utf-8", "replace"))
        val = (data.get("build") or {}).get("started_at")
        return float(val) if val is not None else None
    except Exception:
        return None


def format_operator_card(
    source: str,
    severity: str,
    title: str,
    detail: str = "",
) -> str:
    """Operator-visible card. Layout matches kazma_core.observability.alert_card.

    Stdlib-only copy: the guard must not import kazma_core.
    """
    sources = {"guard": "Guard", "ops": "Ops", "system": "System"}
    severities = {
        "info": "Info",
        "success": "Success",
        "warn": "Warn",
        "error": "Error",
        "critical": "Critical",
    }
    icons = {
        "info": "\U0001f535",
        "success": "\U0001f7e2",
        "warn": "\U0001f7e1",
        "error": "\U0001f534",
        "critical": "\U0001f6a8",
    }
    src_key = str(source or "").strip().lower().strip("[]")
    src = sources.get(src_key, str(source or "Guard").strip() or "Guard")
    if src.lower() == "gaurd":
        src = "Guard"
    sev_key = str(severity or "warn").strip().lower()
    sev = severities.get(sev_key, str(severity or "Warn").strip().title() or "Warn")
    icon = icons.get(sev_key, icons["warn"])
    lines = [f"{icon} Kazma — {sev}", str(title or "").strip() or sev]
    body = str(detail or "").strip()
    if body:
        lines.append(body)
    lines.append(src)
    return "\n".join(lines)


def _failing_checks(data: dict) -> list[str]:
    """Named failed ready-checks, with error text when the body has it."""
    parts: list[str] = []
    for key, val in (data.get("checks") or {}).items():
        if not isinstance(val, dict):
            continue
        if str(val.get("status") or "").lower() != "failed":
            continue
        err = str(val.get("error") or val.get("detail") or "").strip()
        parts.append(f"{key}: {err}" if err else str(key))
    return parts


def _health_failure_detail(raw: str, *, http_status: int = 0) -> str:
    """One formatter for HTTP 200 not_ready and HTTP 503 JSON bodies."""
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if isinstance(data, dict):
        failing = _failing_checks(data)
        if failing:
            return "; ".join(failing[:6])
        status = str(data.get("status") or "").lower()
        if status == "not_ready":
            return "not_ready"
    if http_status:
        return f"HTTP {http_status}"
    return "unparsed body"


def probe(url: str, timeout: float) -> tuple[bool, str]:
    """Return (healthy, detail). Any non-200 or exception is unhealthy."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
            if resp.status != 200:
                return False, _health_failure_detail(raw, http_status=resp.status)
        # HTTP 200 IS the contract. /health/ready returns 503 only when a
        # CRITICAL dependency (config store, database) is gone; a partial
        # failure is reported as "degraded" with 200 and the explicit
        # meaning "still accepts traffic".
        #
        # Restarting on any non-"ready" word would mean a single failing MCP
        # server -- which happens routinely -- kills a perfectly good agent
        # every 90 seconds forever. The guard restarts what the app says is
        # unable to serve, not what it says is imperfect.
        try:
            data = json.loads(raw)
        except Exception:
            return True, "200 (unparsed body)"
        status = str(data.get("status", "")).lower()
        if status == "not_ready":
            failing = _failing_checks(data)
            return False, "; ".join(failing[:6]) if failing else "not_ready"
        degraded = [
            k for k, v in (data.get("checks") or {}).items()
            if isinstance(v, dict)
            and v.get("status") not in ("ok", "healthy", "not_initialized")
        ]
        detail = status or "200"
        if degraded:
            # Serving, but say so -- this is how a partial outage becomes
            # visible in the guard log instead of passing silently.
            detail = f"{detail} (degraded: {','.join(sorted(degraded)[:4])})"
        return True, detail
    except urllib.error.HTTPError as exc:
        # urlopen raises on 503. The JSON body names the failing check;
        # do not format this as unreachable: Service Unavailable.
        raw = ""
        try:
            raw = exc.read(4096).decode("utf-8", "replace")
        except Exception:
            raw = ""
        return False, _health_failure_detail(raw, http_status=int(exc.code or 0))
    except urllib.error.URLError as exc:
        return False, f"unreachable: {getattr(exc, 'reason', exc)}"
    except Exception as exc:  # noqa: BLE001 -- a probe must never raise
        return False, f"probe error: {exc}"


# -- child process control --------------------------------------------


def build_command() -> list[str]:
    r"""Resolve the command to supervise.

    Windows quoting makes the obvious one-liner wrong in both directions:
    ``shlex.split(posix=True)`` treats backslashes as escapes and mangles
    ``C:\path\to\x`` into ``C:pathtox``, while ``posix=False`` keeps the
    surrounding quotes inside the token, so the executable name literally
    contains a quote character and the spawn fails. Any path with a space --
    the reason you would quote it at all -- hit one or the other.

    A JSON list is unambiguous everywhere and is preferred:

        KAZMA_GUARD_CMD='["C:\Program Files\py.exe", "serve.py"]'
    """
    raw = os.environ.get("KAZMA_GUARD_CMD", "").strip()
    if not raw:
        return [sys.executable, "serve.py"]

    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return [str(x) for x in parsed]
        except Exception:
            pass

    import shlex

    if os.name == "nt":
        parts = shlex.split(raw, posix=False)
        out = []
        for part in parts:
            if len(part) > 1 and part[0] == part[-1] and part[0] in "\"'":
                part = part[1:-1]
            out.append(part)
        return out
    return shlex.split(raw, posix=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False,
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


def _port_from_url(url: str) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
        return int(parsed.port or 0)
    except Exception:
        return 0


def _local_port_of(addr: str) -> str:
    """Return the port field of a netstat local-address token."""
    # 127.0.0.1:9090  |  [::]:9090  |  [::1]:9090
    if addr.startswith("["):
        return addr.rsplit("]", 1)[-1].lstrip(":")
    return addr.rsplit(":", 1)[-1]


def _port_holder_pid(port: int) -> int:
    """PID listening on *port*, or 0. Standard library / OS tools only."""
    if not port:
        return 0
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=20, check=False).stdout
            want = str(port)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    if _local_port_of(parts[1]) == want:
                        return int(parts[4])
            return 0
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, check=False).stdout.strip()
        return int(out.splitlines()[0]) if out else 0
    except Exception:
        return 0


def parse_tasklist_image(stdout: str) -> str:
    """Image name from ``tasklist /FO CSV /NH`` (or table) output.

    ``tasklist /NH`` on a miss prints ``INFO: No tasks are running…`` —
    taking ``split()[0]`` produced the name ``info:`` and --reload refused
    to kill the real uvicorn grandchild holding 9090 (live, 2026-08-31).
    """
    raw = (stdout or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    if not line or line.upper().startswith("INFO:") or line.upper().startswith("ERROR:"):
        return ""
    if line.startswith('"'):
        # CSV: "python.exe","95052","Console",...
        try:
            import csv
            from io import StringIO

            row = next(csv.reader(StringIO(line)))
            return (row[0] if row else "").strip()
        except Exception:
            return ""
    return line.split()[0]


def _is_reapable_image(name: str) -> bool:
    """True only for python/uvicorn holders — never sqlservr, nginx, …"""
    image = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if not image:
        return False
    return image.startswith("python") or image.startswith("uvicorn")


def _windows_image_name(pid: int) -> str:
    """Best-effort process image for *pid* on Windows. Empty if unknown."""
    if pid <= 0:
        return ""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False,
        ).stdout
        name = parse_tasklist_image(out)
        if name:
            return name
    except Exception:
        pass
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').Name",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=False,
        ).stdout.strip()
        if out and "error" not in out.lower():
            return out.splitlines()[0].strip()
    except Exception:
        pass
    return ""


def reap_port_holder(url: str, log: GuardLog) -> bool:
    """Kill whatever is squatting on the port this guard owns.

    Detecting a foreign server is not enough on its own: without this the
    guard refuses forever and escalates to a human, which is exactly what
    happened live (2026-08-28 12:54) when a manual kill removed serve.py
    but left its uvicorn grandchild holding port 9090 and serving.

    The guard is the designated owner of its configured port, so clearing a
    squatter is legitimate recovery -- but it is deliberately narrow: only
    a python process is ever killed, so a mistyped port cannot take out
    something unrelated.
    """
    port = _port_from_url(url)
    pid = _port_holder_pid(port)
    if not pid:
        return False
    name = ""
    try:
        if os.name == "nt":
            name = _windows_image_name(pid)
        else:
            name = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=15, check=False).stdout.strip()
    except Exception:
        name = ""
    name_l = name.lower()
    # Image allowlist is the ONLY reap credential. Health answering used to
    # make `ours=True` for sqlservr.exe (and any unknown image) whenever
    # THIS url still served — a mistyped port plus a live probe then killed
    # a foreign holder (audit T-3). Unknown name + health_ok → log and refuse.
    if not _is_reapable_image(name_l):
        health_ok, _ = probe(url, 5.0)
        log("error", "port.holder_not_ours", pid=pid, port=port,
            name=name or "unknown", health_ok=health_ok,
            note="refusing to kill a non-python process")
        return False
    log("warn", "port.reaping_holder", pid=pid, port=port, name=name)
    kill_note = ""
    try:
        if os.name == "nt":
            # /T takes the whole tree; capture the output so an Access
            # Denied on an elevated holder is SEEN, not swallowed.
            kill = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, check=False,
            )
            if kill.returncode != 0:
                lines = (kill.stderr or kill.stdout or "").strip().splitlines()
                kill_note = lines[-1][:160] if lines else f"exit {kill.returncode}"
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        log("error", "port.reap_failed", pid=pid, error=str(exc)[:200])
        return False
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.5)
    if _pid_alive(pid):
        # Honesty (2026-09-03): this used to log holder_reaped and return
        # True unconditionally. Live that night: an elevated zombie python
        # survived the guard's non-admin taskkill through THREE "successful"
        # reaps — every --reload silently discarded the new child while the
        # zombie kept serving the old build, so the operator's restart never
        # took effect and nothing said so. Success is measured, not assumed.
        log("error", "port.holder_reap_ineffective", pid=pid, port=port,
            name=name,
            kill_result=kill_note or "process still alive after taskkill",
            note=(f"restart will NOT take effect until this pid is killed "
                  f"from an ELEVATED shell: taskkill /PID {pid} /F"))
        return False
    log("info", "port.holder_reaped", pid=pid, port=port)
    return True


def clear_stale_port(url: str, log: GuardLog) -> bool:
    """Free the guard's port before spawning, if anything still holds it.

    ``reap_orphan`` only knows the PID it wrote down. On 2026-08-28 a
    deliberate restart killed the recorded child (76408) and left its
    uvicorn grandchild (4160) holding 9090 and still serving the OLD build.
    The recorded PID was dead, so ``reap_orphan`` returned immediately and
    did nothing -- the one case it exists for, missed on a technicality.

    The guard then spawned, discovered 36ms later that a foreign server
    owned the port, threw away its own perfectly good child and backed off
    30 seconds. It recovered, which is the point of a supervisor, but it
    recovered the expensive way: ~45s of extra downtime and two alarming
    messages ("never became healthy") for what was a clean deploy.

    Anything listening on our port BEFORE we spawn cannot be ours -- we
    have not started yet. That makes clearing it here unambiguous, and
    demotes the foreign-server branch in ``_wait_ready`` from the ordinary
    path to the backstop it was meant to be (it still catches a server that
    binds the port during our boot, which this check cannot see).
    """
    port = _port_from_url(url)
    if not port or not _port_holder_pid(port):
        return False
    log("warn", "port.stale_before_spawn", port=port,
        note="port still held and we have not spawned yet; clearing first")
    return reap_port_holder(url, log)


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
        # Last port-holder pid we paged about (mute-theorem dedupe): a
        # squatter the guard cannot kill repeats every spawn cycle, and
        # paging on each one trains the operator to ignore the channel.
        self._last_stale_holder_notified: int | None = None
        self._last_page_fp = ""
        self._last_page_at = 0.0
        self._awaiting_recovery = ""
        self.page_cooldown_s = PAGE_COOLDOWN_S

    # -- lifecycle ----------------------------------------------------

    def _should_page(self, fingerprint: str) -> bool:
        """True if this fingerprint is new or the cooldown has elapsed."""
        now = time.monotonic()
        if (
            fingerprint
            and fingerprint == self._last_page_fp
            and (now - self._last_page_at) < self.page_cooldown_s
        ):
            return False
        self._last_page_fp = fingerprint
        self._last_page_at = now
        return True

    def _page(
        self,
        severity: str,
        title: str,
        detail: str = "",
        *,
        fingerprint: str = "",
        force: bool = False,
    ) -> bool:
        """Send a Guard operator card. Same fingerprint inside the cooldown
        is logged, not sent. Crash-loop callers pass force=True."""
        fp = fingerprint or f"{title}\n{detail}"
        if not force and not self._should_page(fp):
            self.log("info", "guard.page_suppressed", title=title[:120])
            return False
        if force:
            self._last_page_fp = fp
            self._last_page_at = time.monotonic()
        self.notify.send(format_operator_card("Guard", severity, title, detail))
        return True

    def notify_restart(self, reason: str, delay_s: float) -> bool:
        """Page a restart. Collapses identical ``reason`` inside the cooldown."""
        title = f"Kazma stopped: {reason}"
        detail = f"Restarting in {int(delay_s)}s (attempt {self.restarts})."
        sent = self._page("warn", title, detail, fingerprint=reason)
        self._awaiting_recovery = reason
        return sent

    def notify_recovered(self) -> bool:
        """One recovery card after a kill/unhealthy restart, if we were waiting."""
        reason = self._awaiting_recovery
        if not reason:
            return False
        self._awaiting_recovery = ""
        title = "Kazma is healthy again"
        detail = f"{reason} recovered."
        return self._page("success", title, detail, fingerprint=f"recovered:{reason}")

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            self.log("info", "guard.signal", signal=int(signum))
            self._stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except Exception:
                pass

    def _wait_ready(self, spawned_at: float) -> bool:
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
                # Is the thing answering actually the thing we launched?
                # After a restart, an orphan holding the port answers this
                # probe perfectly, and the guard would supervise a stranger
                # while reporting healthy (live, 2026-08-28 12:33).
                boot = server_started_at(self.health_url, PROBE_TIMEOUT_S)
                if boot is not None and boot < spawned_at - FOREIGN_SERVER_SLACK_S:
                    self.log("error", "child.foreign_server_holds_port",
                             server_started_at=boot, our_child_spawned_at=spawned_at,
                             note="another process owns the port; not supervising it")
                    # Clear the squatter so the NEXT attempt can bind.
                    # Detection alone left the guard refusing forever.
                    reap_port_holder(self.health_url, self.log)
                    return False
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

    def _sleep(self, seconds: float, *, wake_on_child_exit: bool = False) -> None:
        """Interruptible sleep so shutdown, --reload, and a dead child stay responsive."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._stop:
            if reload_requested():
                return
            if (
                wake_on_child_exit
                and self.proc is not None
                and self.proc.poll() is not None
            ):
                return
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
        # Logged FIRST, and with nothing that can block: if the guard is
        # alive, this line exists. Anything slower (credential resolution)
        # comes after, so a hang is diagnosable instead of silent.
        self.log(
            "info", "guard.start",
            cmd=" ".join(self.cmd), cwd=str(self.cwd), health=self.health_url,
        )
        # NOT resolved here. describe() reaches into the vault, which means
        # importing kazma_core -- measured at tens of seconds on a cold
        # cache. Doing it before spawning delays the server by exactly that
        # long for a log line. It is emitted after the child is up instead.

        # Order matters: reap a known orphan from a previous guard FIRST,
        # then check whether anything else is serving. Reversed, a booting
        # orphan is invisible to the probe (nothing bound yet) and both
        # instances race for the port.
        reap_orphan(self.log)

        if self._foreign_server_present():
            title = "Kazma guard did not start"
            detail = (
                f"Something is already serving {self.health_url}. "
                "Stop the existing instance first, then start the guard — "
                "otherwise it would supervise a process it did not launch."
            )
            self.log("error", "guard.refused_to_start")
            self._page("error", title, detail, force=True)
            print(format_operator_card("Guard", "error", title, detail), file=sys.stderr)
            return 2

        first = True
        while not self._stop:
            # Maintenance gate. Checked before every spawn so a pause taken
            # while the guard is mid-backoff is still honoured.
            if self._await_resume():
                continue
            if self._stop:
                break
            # A previous instance (or its orphaned grandchild) may still own
            # the port. Clearing it here costs one netstat; discovering it
            # after the spawn costs a discarded child and a backoff.
            clear_stale_port(self.health_url, self.log)
            # Honesty (2026-09-03): when the clear FAILED the port is still
            # held, the spawn below cannot bind, and the server answering
            # requests is the OLD build — the operator's restart silently
            # did not take effect (live: elevated zombie python survived
            # three reaps while the guard logged holder_reaped). Page once
            # per holder pid; the kill needs an elevated shell only the
            # operator can open.
            _stale_pid = _port_holder_pid(_port_from_url(self.health_url))
            if _stale_pid:
                if _stale_pid != self._last_stale_holder_notified:
                    self._last_stale_holder_notified = _stale_pid
                    self.log("error", "guard.port_still_held", pid=_stale_pid,
                             port=_port_from_url(self.health_url),
                             note="spawn below cannot bind; old build still serving")
                    try:
                        port = _port_from_url(self.health_url)
                        self._page(
                            "error",
                            "Restart did not take effect",
                            f"pid {_stale_pid} still owns port {port} and is "
                            "serving the old build — the guard cannot kill it "
                            "(likely elevated). From an admin terminal: "
                            f"taskkill /PID {_stale_pid} /F, then restart the guard.",
                            fingerprint=f"stale:{_stale_pid}",
                            force=True,
                        )
                    except Exception:
                        self.log("debug", "guard.port_still_held.notify_failed")
            else:
                self._last_stale_holder_notified = None
            spawned_at = time.time()
            self.proc = spawn(self.cmd, self.cwd, self.log)

            if self._wait_ready(spawned_at):
                if first:
                    # Safe to resolve now: the server is already running, so
                    # a slow vault import costs nothing but a delayed alert.
                    self.log("info", "guard.notifier",
                             target=self.notify.describe())
                # NO notification on a healthy start. Kazma's own
                # lifecycle_notifier already sends "server starting up",
                # "server started" and "server restarted (was down ~Ns)"
                # from inside the app. The guard exists to say the things
                # the app CANNOT say -- because when they are true, the app
                # is dead. Announcing a successful start here just doubles
                # every message in the operator's Telegram.
                self.log("info", "guard.supervising",
                         restarts=self.restarts, note="app announces its own start")
                self.notify_recovered()
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

            if reason == "maintenance":
                # Not a failure: no restart count, no backoff, no crash-loop
                # accounting. Treating a deliberate pause as a crash would
                # push the guard into a 30-minute cooldown the moment the
                # operator resumed.
                self.log("info", "guard.paused_by_operator")
                continue

            if consume_reload_request():
                # Operator --reload killed this child on purpose. Spawn the
                # new process immediately; do not climb the crash ladder and
                # do not page Telegram as if Kazma died.
                self.log("info", "guard.operator_reload", reason=reason)
                # Informational notice (2026-09-03): operator reloads had
                # become the ONLY silent restart path — every restart since
                # Sep 2 was a --reload, so the operator's usual
                # "stopped/restarting" Telegram alerts vanished entirely.
                # Quiet tone: deliberate maintenance, not a crash.
                try:
                    self._page(
                        "info",
                        "Kazma restarting (operator reload)",
                        f"{reason}. Back in a moment — no action needed.",
                        fingerprint=f"reload:{reason}",
                    )
                except Exception:
                    self.log("debug", "guard.operator_reload.notify_failed")
                continue

            self.restarts += 1
            if self._crash_looping():
                self.log("error", "guard.crash_loop", restarts=self.restarts,
                         window_s=CRASH_LOOP_WINDOW_S)
                self._page(
                    "critical",
                    f"Kazma is crash-looping ({CRASH_LOOP_COUNT} restarts in "
                    f"{CRASH_LOOP_WINDOW_S // 60} min)",
                    f"Last reason: {reason}. Pausing "
                    f"{CRASH_LOOP_COOLDOWN_S // 60} min — this needs a human.",
                    force=True,
                )
                self.recent.clear()
                self._sleep(CRASH_LOOP_COOLDOWN_S)
                continue

            delay = self._backoff()
            self.log("warn", "guard.restarting", reason=reason, in_s=delay,
                     restarts=self.restarts)
            self.notify_restart(reason, delay)
            self._sleep(delay)

        if self.proc:
            stop_child(self.proc, self.log)
        self.log("info", "guard.stopped")
        return 0

    def _await_resume(self) -> bool:
        """Block while a maintenance pause is active. True if we waited.

        Nags on Telegram every hour so a forgotten pause cannot become a
        silent outage, and announces the resume so the operator knows
        supervision is live again.
        """
        pause = read_pause()
        if pause is None:
            return False

        until = float(pause.get("until") or 0.0)
        human_until = (
            datetime.fromtimestamp(until, UTC).strftime("%H:%M UTC")
            if until else "no expiry"
        )
        self.log("warn", "maintenance.active",
                 reason=pause.get("reason"), by=pause.get("by"),
                 expires=human_until, file=str(_pause_path()))
        self._page(
            "warn",
            "Kazma supervision paused",
            f"{pause.get('reason')}. It will not be restarted until resumed. "
            f"Expires: {human_until}.",
            fingerprint="pause",
            force=True,
        )

        next_nag = time.monotonic() + PAUSE_NAG_EVERY_S
        while not self._stop:
            self._sleep(10.0)
            if self._stop:
                return True
            if read_pause() is None:
                self.log("info", "maintenance.resumed")
                self._page(
                    "info",
                    "Kazma supervision resumed",
                    "Restarting the server.",
                    fingerprint="resume",
                    force=True,
                )
                return True
            if time.monotonic() >= next_nag:
                next_nag += PAUSE_NAG_EVERY_S
                mins = int((time.time() - float(pause.get("since") or 0)) / 60)
                self.log("warn", "maintenance.still_paused", minutes=mins)
                self._page(
                    "warn",
                    "Kazma is still paused",
                    f"Paused for {mins} min and is not being supervised.",
                    fingerprint=f"pause-nag:{mins // 60}",
                )
        return True

    def _supervise(self) -> str:
        """Watch a healthy child. Returns the reason it needs restarting."""
        consecutive = 0
        while not self._stop:
            self._sleep(PROBE_INTERVAL_S, wake_on_child_exit=True)
            if self._stop:
                return "guard shutting down"

            if read_pause() is not None:
                # Operator asked for quiet. Stop the child so diagnosis
                # happens against a stopped Kazma, not a moving target.
                self.log("info", "maintenance.requested_while_running")
                if self.proc:
                    stop_child(self.proc, self.log)
                return "maintenance"

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


# -- operator commands ------------------------------------------------


def _cmd_status() -> int:
    pause = read_pause()
    holder = _port_holder_pid(_port_from_url(DEFAULT_HEALTH_URL))
    ok, detail = probe(DEFAULT_HEALTH_URL, 5.0)
    print(f"supervision : {'PAUSED' if pause else 'active'}")
    if pause:
        until = float(pause.get("until") or 0.0)
        print(f"  reason    : {pause.get('reason')}")
        print(f"  paused by : {pause.get('by')}")
        print("  expires   : " + (
            datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%d %H:%M UTC")
            if until else "never (will nag hourly)"))
        print(f"  flag file : {_pause_path()}")
    print(f"server      : {'healthy' if ok else 'not answering'} ({detail})")
    if holder:
        print(f"  port {_port_from_url(DEFAULT_HEALTH_URL)}  : held by pid {holder}")
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        print(f"guard child : pid {state.get('child_pid')}")
    except Exception:
        print("guard child : unknown")
    return 0


def _cmd_pause(reason: str, ttl: float, *, stop_now: bool) -> int:
    rec = write_pause(reason, ttl)
    log = GuardLog(_default_log_path())
    log("warn", "maintenance.pause_requested", reason=rec["reason"], ttl_s=ttl)
    until = float(rec.get("until") or 0.0)
    print("Supervision PAUSED. Kazma will not be auto-restarted.")
    print(f"  reason  : {rec['reason']}")
    print("  expires : " + (
        datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%d %H:%M UTC")
        if until else "never -- you will be reminded hourly"))
    print(f"  file    : {_pause_path()}")

    if stop_now:
        # Stop via the recorded child so the whole tree goes, including the
        # uvicorn grandchild that would otherwise keep holding the port.
        try:
            state = json.loads(_state_path().read_text(encoding="utf-8"))
            pid = int(state.get("child_pid") or 0)
        except Exception:
            pid = 0
        if pid and _pid_alive(pid):
            print(f"  stopping server (pid {pid})...")
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=30, check=False)
                else:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                log("info", "maintenance.server_stopped", pid=pid)
                print("  server stopped.")
            except Exception as exc:
                print(f"  could not stop pid {pid}: {exc}")
        else:
            print("  no running server recorded; nothing to stop.")
    print("")
    print("Resume with:  python scripts/service/kazma_guard.py --resume")
    return 0


def _cmd_resume() -> int:
    log = GuardLog(_default_log_path())
    if clear_pause():
        log("info", "maintenance.resume_requested")
        print("Supervision RESUMED. The guard will restart Kazma within ~10s.")
    else:
        print("Not paused; nothing to resume.")
    return 0


def _stop_recorded_child(log: GuardLog) -> int:
    """Kill the guard's recorded child tree. Returns the pid stopped, or 0."""
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        pid = int(state.get("child_pid") or 0)
    except Exception:
        pid = 0
    if pid and _pid_alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=30, check=False,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            log("info", "reload.child_stopped", pid=pid)
            return pid
        except Exception as exc:
            log("error", "reload.child_stop_failed", pid=pid, error=str(exc)[:200])
    return 0


def _live_commit(health_url: str) -> str:
    live = health_url.rstrip("/").rsplit("/", 1)[0] + "/live"
    if not live.endswith("/health/live"):
        live = "http://127.0.0.1:9090/health/live"
    try:
        with urllib.request.urlopen(live, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        build = data.get("build") if isinstance(data, dict) else None
        if isinstance(build, dict):
            return str(build.get("commit") or "")
        return str((data or {}).get("commit") or "")
    except Exception:
        return ""


def _cmd_reload() -> int:
    """Operator deploy: stop the running server so the supervisor boots new code.

    Killing python / uvicorn by hand fights the guard: it either respawns the
    OLD process that still holds the port, or refuses to start because a
    squatter is healthy. This command is the one path that (1) lifts a
    leftover pause, (2) kills the recorded child AND the port holder, and
    (3) waits until /health/live reports a new boot.
    """
    log = GuardLog(_default_log_path())
    health = os.environ.get("KAZMA_GUARD_HEALTH_URL", DEFAULT_HEALTH_URL)
    before = _live_commit(health)
    if clear_pause():
        print("Cleared leftover pause so the supervisor can respawn.")
        log("info", "reload.cleared_pause")
    # Must land BEFORE the kill: the running guard treats a dead child as a
    # crash unless this flag is sitting there when it notices.
    request_reload()
    log("info", "reload.requested")
    stopped = _stop_recorded_child(log)
    if stopped:
        print(f"Stopped recorded server pid {stopped}.")
    else:
        print("No recorded child; clearing whoever holds the port.")
    reap_port_holder(health, log)

    # Wait until the old process is actually gone (port free / health down).
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        ok, _ = probe(health, 3.0)
        if not ok:
            break
        time.sleep(0.5)
    else:
        print("WARNING: something is still answering /health/ready after kill.")
        print("  python scripts/service/kazma_guard.py --status")
        return 1

    print("Waiting for the running guard to respawn serve.py…")
    print(
        f"(Typical bind is under 2 minutes; budget {int(START_TIMEOUT_S)}s. "
        "Do not Ctrl+C unless you intend to abort.)"
    )
    kicked = False
    started_wait = time.monotonic()
    boot_deadline = started_wait + START_TIMEOUT_S
    next_progress = started_wait + 30.0
    while time.monotonic() < boot_deadline:
        ok, detail = probe(health, 5.0)
        if ok:
            after = _live_commit(health)
            print(f"Kazma is up. build {after or '?'} (was {before or '?'})")
            if before and after and before == after:
                print(
                    "NOTE: commit hash unchanged — the process restarted but "
                    "git HEAD is the same. Code edits still need this reload."
                )
            log("info", "reload.ready", commit=after, previous=before)
            return 0
        now = time.monotonic()
        # If the watcher PID is gone, taskkill /T took it with the child.
        # Kick KazmaAgent once. Do NOT kick while a live guard is still
        # booting — cold start is minutes, and a second guard fights for 9090.
        if not kicked and now - started_wait >= 45.0 and not _guard_pid_alive():
            kicked = True
            if _kick_os_supervisor(log):
                print("Watcher process was gone; kicked the KazmaAgent scheduled task.")
        if now >= next_progress:
            print(
                f"  still starting… {int(now - started_wait)}s "
                f"(last: {detail})"
            )
            next_progress += 30.0
        time.sleep(2.0)

    print("Server did not become ready within the start budget.")
    print("  python scripts/service/install_service.py --status")
    print("  python scripts/service/kazma_guard.py --status")
    print("  python scripts/service/kazma_guard.py --install")
    print("  python scripts/service/kazma_guard.py          # start supervision in this terminal")
    return 2


def _guard_pid_alive() -> bool:
    """True if the last recorded kazma_guard process is still running."""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8") or "{}")
        gpid = int(data.get("guard_pid") or 0)
    except Exception:
        return False
    return bool(gpid) and _pid_alive(gpid)


def _kick_os_supervisor(log: GuardLog) -> bool:
    """Ask the OS supervisor to run the guard now. No-op if none is installed."""
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["schtasks", "/Run", "/TN", "KazmaAgent"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, check=False,
            )
            ok = r.returncode == 0
            log("info" if ok else "warn", "reload.kick_task",
                ok=ok, code=r.returncode, err=(r.stderr or "")[:200])
            return ok
        # systemd user unit (install_service.py name)
        r = subprocess.run(
            ["systemctl", "--user", "restart", "kazma"],
            capture_output=True, timeout=30, check=False,
        )
        return r.returncode == 0
    except Exception as exc:
        log("debug", "reload.kick_failed", error=str(exc)[:200])
        return False


def _cmd_install() -> int:
    """Operator typed --install on the guard; the installer is a sibling script."""
    script = Path(__file__).resolve().parent / "install_service.py"
    cmd = [sys.executable, str(script), "--install"]
    print("The OS supervisor installer is install_service.py, not kazma_guard.")
    print("Running: " + " ".join(cmd))
    return int(subprocess.call(cmd))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Health-gated supervisor for Kazma.",
        epilog=(
            "Maintenance:  --pause to stop auto-restart while you diagnose, "
            "--resume when done. A pause survives guard restarts, task "
            "retriggers and reboots, and expires on its own so it cannot "
            "become a forgotten outage."
        ),
    )
    ap.add_argument("--once", action="store_true",
                    help="run the server once; do not restart it")
    ap.add_argument("--dry-run", action="store_true",
                    help="print resolved configuration and exit")
    ap.add_argument("--pause", action="store_true",
                    help="stop auto-restart (Kazma is left alone for diagnosis)")
    ap.add_argument("--resume", action="store_true",
                    help="lift a pause and let the guard restart Kazma")
    ap.add_argument("--status", action="store_true",
                    help="show whether supervision is active or paused")
    ap.add_argument("--reload", action="store_true",
                    help="stop the running server so the supervisor boots new code")
    ap.add_argument("--install", action="store_true",
                    help="install the OS supervisor (runs install_service.py --install)")
    ap.add_argument("--stop", action="store_true",
                    help="with --pause: also stop the running server now")
    ap.add_argument("--reason", default="", help="why (recorded and alerted)")
    ap.add_argument("--ttl", type=float, default=DEFAULT_PAUSE_TTL_S,
                    help="seconds before the pause auto-expires; 0 = never "
                         f"(default {DEFAULT_PAUSE_TTL_S:.0f})")
    args = ap.parse_args()

    if args.status:
        return _cmd_status()
    if args.install:
        return _cmd_install()
    if args.reload:
        return _cmd_reload()
    if args.pause:
        return _cmd_pause(args.reason, args.ttl, stop_now=args.stop)
    if args.resume:
        return _cmd_resume()

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
