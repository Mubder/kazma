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
    python scripts/service/kazma_guard.py --reload --when-idle   # ...once no turn runs
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
    KAZMA_GUARD_GRACEFUL_STOP_S seconds a deliberate stop waits (default: 60)
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

# A deliberate stop -- operator reload, maintenance pause, the guard itself
# shutting down -- asks the server to shut down first and waits this long
# before the kill. uvicorn drains connections for 15s (serve.py
# timeout_graceful_shutdown); the rest is the app's own shutdown hooks,
# which announce the stop and flush what they hold. Every reload was a
# hard kill until 2026-09-26, so none of that ever ran on a deploy.
GRACEFUL_STOP_S = float(os.environ.get("KAZMA_GUARD_GRACEFUL_STOP_S", "60"))
# A server replaced for failing its health checks may be wedged: a short
# chance, then the kill.
UNHEALTHY_STOP_S = min(15.0, GRACEFUL_STOP_S)
# A child that never became ready has nothing to save.
NEVER_READY_STOP_S = min(5.0, GRACEFUL_STOP_S)

# The guard writes a heartbeat into its state file at least this often, in
# every state it waits in. It is how --reload, --status and the server tell
# a live guard from a dead one: the processes run elevated in the task's own
# logon session, so an operator shell cannot open them to ask (2026-09-26).
HEARTBEAT_EVERY_S = 10.0
# No heartbeat for this long = no guard. Longer than the longest stretch the
# guard spends without beating (a stop: graceful wait + kill wait).
GUARD_STALE_S = max(120.0, GRACEFUL_STOP_S + TERMINATE_GRACE_S + 3 * HEARTBEAT_EVERY_S)
# --reload hands the stop to the running guard; this is how long it waits
# for the guard to take the request before doing the stop itself.
GUARD_ACK_S = 20.0
# What the supervisor loop returns when it stopped the child for --reload.
RELOAD_REASON = "operator reload"

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
    """Operator --reload request: "boot the code on disk now, not a crash".

    The RUNNING GUARD acts on it: it stops its child (it owns the child and
    has the child's rights) and spawns a new one at once, without the crash
    backoff. The request carries the time it was made; a server spawned
    after that time already runs the new code, so an older request is
    satisfied, never acted on twice.

    History: --reload used to kill the child from the operator's shell and
    leave this file as a "that was not a crash" note. When the kill failed --
    the guard runs elevated, an operator shell does not -- the note stayed,
    and the guard woke on it every sleep: 53 health probes a second for 47
    hours (2026-09-20..22), again on 2026-09-26.
    """
    env = os.environ.get("KAZMA_GUARD_RELOAD_FILE")
    if env:
        return Path(env)
    return _guard_file("guard.reload")


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write *data* so a reader sees the old file or the new one, never half.

    On Windows the replace fails while another process has the file open
    (a --status or --reload reading it that instant), so it is retried
    briefly before giving up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05)


def request_reload() -> float:
    """Ask the running guard to boot new code. Returns the request's time."""
    ts = time.time()
    _write_json_atomic(_reload_path(), {"ts": ts, "pid": os.getpid()})
    return ts


def reload_requested() -> bool:
    try:
        return _reload_path().is_file()
    except Exception:
        return False


def read_reload_request() -> dict | None:
    """The pending reload request, or None. Never raises.

    A request that cannot be read is still a request, dated 0: older than
    any server, so it is cleared as satisfied rather than acted on.
    """
    path = _reload_path()
    try:
        if not path.is_file():
            return None
    except Exception:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        data["ts"] = float(data.get("ts") or 0.0)
    except (TypeError, ValueError):
        data["ts"] = 0.0
    return data


def _reload_signature() -> tuple[int, int] | None:
    """Identity of the request on disk (mtime, size), or None if there is none.

    The guard remembers the last one it handled, so a request file it could
    not delete is handled once -- never a wake-up on every sleep.
    """
    try:
        st = _reload_path().stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


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
        # A logger must never raise: under the scheduled task stderr is a
        # console nobody reads, and a failed write to it would otherwise end
        # the guard from inside the line meant to explain why.
        try:
            print(f"[guard] {level:<5} {event} {fields or ''}", file=sys.stderr, flush=True)
        except Exception:
            pass


# -- notifier (must not depend on Kazma being alive) ------------------


#: Run by :meth:`Notifier._from_config_store` in a child process, in the
#: install folder, with the interpreter that runs the server.
_NOTIFY_LOOKUP = r"""
import json
from kazma_core.env_files import load_env_files
load_env_files()
from kazma_core.config_store import get_config_store
from kazma_core.diagnostic_scope import read_only_diagnostic
with read_only_diagnostic("guard.notifier"):
    cs = get_config_store()
    token = str(cs.get("connectors.telegram.token", "") or "").strip()
    chat = (str(cs.get("guard.telegram.chat_id", "") or "")
            or str(cs.get("swarm.group_chat_id", "") or "")).strip()
print("KAZMA_GUARD_NOTIFY " + json.dumps({"token": token, "chat": chat}))
"""
_NOTIFY_LOOKUP_MARK = "KAZMA_GUARD_NOTIFY "
_NOTIFY_LOOKUP_TIMEOUT_S = 90.0
#: A lookup that found nothing is tried again when a page is due, at most
#: this often.
_NOTIFY_RELOOKUP_S = 600.0


def _settings_lookup_allowed() -> bool:
    """False under pytest: the lookup child reads a real ``.env`` and vault.

    A test that let it run could message the operator's real chat -- the
    integration tests blank the Telegram variables for exactly that, and the
    child would find the token anyway -- and would break "no test reads a
    real .env" (AGENTS §38). A guard started by a test inherits the marker.
    Tests of the lookup patch this and ``subprocess.run``.
    """
    return not os.environ.get("PYTEST_CURRENT_TEST")


class Notifier:
    """Best-effort out-of-band alerting. Never raises, never blocks long.

    Credentials are resolved in this order, first hit wins:

    1. ``KAZMA_GUARD_TELEGRAM_TOKEN`` / ``KAZMA_GUARD_TELEGRAM_CHAT``
    2. ``SWARM_BOT_TOKEN`` / ``SWARM_CHAT_ID``
    3. Kazma's own config store, which resolves the token out of the
       encrypted vault (``connectors.telegram.token``)

    Step 3 is the only place the guard reaches into the application, and it
    does so in a CHILD process, so the guard itself never imports the app.
    The supervision loop stays standard-library-only: if the venv is too
    broken to import kazma_core, the guard loses ALERTING but never loses
    SUPERVISION. That is the right way round -- a supervisor that dies
    because its notifier could not load is worse than one that restarts
    silently.
    """

    def __init__(
        self, log: GuardLog, *, cwd: Path | None = None, python: str | None = None
    ) -> None:
        self._log = log
        self._cwd = Path(cwd) if cwd is not None else REPO_ROOT
        self._python = python or sys.executable
        self._looked_up_at: float | None = None
        self._lookup_error = ""
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

    def _resolve(self) -> None:
        """Fill in missing credentials from the app's settings. Lazily, retried.

        Resolved once and never again used to mean one failed lookup -- the
        database still starting, say -- kept the guard silent until the guard
        itself restarted. A lookup that found nothing is tried again when a
        page is due, at most every ``_NOTIFY_RELOOKUP_S``.
        """
        if self.token and self.chat:
            return
        now = time.monotonic()
        if self._looked_up_at is not None and now - self._looked_up_at < _NOTIFY_RELOOKUP_S:
            return
        self._looked_up_at = now
        v_token, v_chat = self._from_config_store()
        self.token = self.token or v_token
        self.chat = self.chat or v_chat
        if self.token and self.chat:
            self._source = "vault"

    def _from_config_store(self) -> tuple[str, str]:
        """Resolve token + chat from the app's settings, in a child. Never raises.

        In a child process that loads the install's ``.env`` the way the
        server does. Since 2026-09-22 importing kazma_core no longer loads a
        ``.env`` (entry points do), and this lookup used to import it into
        the guard: it ran with no vault key and no database URL, and from the
        guard restart of 2026-09-24 22:30 every page -- a restart among them
        -- was "not configured" for a day. Loading ``.env`` into the guard
        instead would leak Kazma's variables into the environment every
        server inherits from it, so a line removed from ``.env`` would
        outlive the edit until the guard restarts.
        """
        if not _settings_lookup_allowed():
            self._lookup_error = "skipped under pytest"
            return "", ""
        extra: dict = {}
        if os.name == "nt":
            # A background helper: never a console window on the desktop.
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self._python, "-c", _NOTIFY_LOOKUP],
                cwd=str(self._cwd), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=_NOTIFY_LOOKUP_TIMEOUT_S, check=False, **extra,
            )
            for line in reversed((proc.stdout or "").splitlines()):
                if line.startswith(_NOTIFY_LOOKUP_MARK):
                    data = json.loads(line[len(_NOTIFY_LOOKUP_MARK):])
                    self._lookup_error = ""
                    return (str(data.get("token") or "").strip(),
                            str(data.get("chat") or "").strip())
            tail = (proc.stderr or "").strip().splitlines()
            self._lookup_error = (
                f"exit {proc.returncode}: {tail[-1][:160] if tail else 'no output'}"
            )
        except Exception as exc:
            self._lookup_error = f"{type(exc).__name__}: {str(exc)[:120]}"
        self._log("info", "notify.config_store_unavailable", error=self._lookup_error)
        return "", ""

    @property
    def configured(self) -> bool:
        self._resolve()
        return bool(self.token and self.chat)

    def describe(self) -> str:
        """Safe-to-log description. Never includes the token."""
        self._resolve()
        if not self.configured:
            if self._lookup_error:
                return f"not configured (settings lookup failed: {self._lookup_error})"
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
    # Source-scoped override, identical to alert_card._SOURCE_ICON_OVERRIDES:
    # Guard info cards use the ORANGE circle (operator request 2026-09-05).
    icon_overrides = {"guard": {"info": "\U0001f7e0"}}
    src_key = str(source or "").strip().lower().strip("[]")
    src = sources.get(src_key, str(source or "Guard").strip() or "Guard")
    if src.lower() == "gaurd":
        src = "Guard"
    sev_key = str(severity or "warn").strip().lower()
    icon = icon_overrides.get(src_key, {}).get(sev_key) or icons.get(sev_key, icons["warn"])
    head = str(title or "").strip() or severities.get(sev_key, "Warn")
    lines = [f"{icon} [{src}] {head}"]
    body = str(detail or "").strip()
    if body:
        lines.append(body)
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


def _process_started_at(pid: int) -> float | None:
    """Unix time *pid* was created, or None when it cannot be read.

    Standard library only. On Windows this needs the right to query the
    process: the guard has it for the processes it (or an earlier guard of
    the same task) started; an operator shell usually does not.
    """
    if pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.GetProcessTimes.restype = wintypes.BOOL
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                    return None
            finally:
                k32.CloseHandle(handle)
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return ticks / 1e7 - 11644473600.0
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
        btime = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def _read_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _update_state(**fields: object) -> None:
    """Merge *fields* into the state file. Never raises."""
    try:
        state = _read_state()
        state.update(fields)
        _write_json_atomic(_state_path(), state)
    except Exception:
        pass


def _recorded_child_is_ours(pid: int, data: dict, log: GuardLog) -> bool:
    """Is the live *pid* still the child a guard recorded, not a reused PID?

    A PID recorded by a guard that died hours ago may belong to anything
    now, and reaping it is ``taskkill /T /F`` of a whole tree. The recorded
    creation time settles it when both sides are known; otherwise only a
    python image is ever reaped -- the rule reap_port_holder already follows.
    """
    recorded = data.get("child_created")
    actual = _process_started_at(pid)
    if isinstance(recorded, (int, float)) and actual is not None:
        if abs(actual - float(recorded)) > 2.0:
            log("warn", "orphan.pid_reused", pid=pid,
                recorded_created=recorded, actual_created=round(actual, 3),
                note="the recorded child is gone; this pid is another process")
            return False
        return True
    name = ""
    try:
        if os.name == "nt":
            name = _windows_image_name(pid)
        else:
            name = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=15, check=False).stdout.strip()
    except Exception:
        name = ""
    if not _is_reapable_image(name.lower()):
        log("warn", "orphan.not_python", pid=pid, name=name or "unknown",
            note="refusing to kill: the recorded pid is not a python process now")
        return False
    return True


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
    if not _recorded_child_is_ours(pid, data, log):
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
    """Persist (or clear) the child PID, and when it was created. Never raises."""
    _update_state(
        child_pid=pid or 0,
        child_created=_process_started_at(pid) if pid else None,
        guard_pid=os.getpid(),
    )


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


#: Probe errors that mean "this machine has no free local port", not "Kazma is
#: down": WSAEADDRINUSE and WSAENOBUFS on connect. 169 of 257 failed probes in
#: the week to 2026-09-23 were 10048 -- and nothing recorded who held the ports.
_PORT_EXHAUSTION_MARKERS = ("10048", "10055")
TCP_SNAPSHOT_EVERY_S = 600.0
_last_tcp_snapshot = 0.0
#: Consecutive unrunnable probes (at PROBE_INTERVAL_S) before one page.
UNRUNNABLE_PAGE_AFTER = 10


def probe_could_not_run(detail: str) -> bool:
    """True when the probe failed for lack of a local port, not a bad answer.

    Windows' own event log confirms these are machine-wide (Tcpip 4231 /
    4227: 11 + 17 in the fortnight to 2026-09-23), at times Kazma was idle.
    """
    return any(m in (detail or "") for m in _PORT_EXHAUSTION_MARKERS)


def tcp_snapshot_from_netstat(stdout: str, names: dict[str, str] | None = None,
                              top: int = 6) -> dict[str, object]:
    """TCP sockets by state, and the processes holding the most.

    Parsed from ``netstat -a -n -o -q -p TCP`` (``-q`` adds BOUND sockets,
    which hold a port without a connection -- 338 of them on the live box).
    """
    states: dict[str, int] = {}
    owners: dict[str, int] = {}
    for line in (stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        state, pid = parts[3].upper(), parts[4]
        states[state] = states.get(state, 0) + 1
        # Listeners do not consume ephemeral ports; TIME_WAIT is reported as
        # pid 0 ("System Idle Process") and is already counted by state.
        if state != "LISTENING" and pid != "0":
            owners[pid] = owners.get(pid, 0) + 1
    ranked = sorted(owners.items(), key=lambda kv: -kv[1])[:top]
    return {
        "total": sum(states.values()),
        "states": dict(sorted(states.items(), key=lambda kv: -kv[1])),
        "top_owners": [
            f"{(names or {}).get(pid, '?')} (pid {pid}): {n}" for pid, n in ranked
        ],
    }


def _process_names() -> dict[str, str]:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20, check=False).stdout
    except Exception:
        return {}
    names: dict[str, str] = {}
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].strip('"').isdigit():
            names[cols[1].strip('"')] = cols[0].strip('"')
    return names


def maybe_log_tcp_snapshot(log, detail: str, *, now: float | None = None) -> bool:
    """On a port-exhaustion probe failure, record who holds the sockets.

    Rate-limited, Windows-only, and never raises: supervision must not depend
    on a diagnostic. Returns True when a snapshot was logged.
    """
    global _last_tcp_snapshot
    if os.name != "nt" or not probe_could_not_run(detail):
        return False
    stamp = time.time() if now is None else now
    if stamp - _last_tcp_snapshot < TCP_SNAPSHOT_EVERY_S:
        return False
    _last_tcp_snapshot = stamp
    try:
        out = subprocess.run(["netstat", "-a", "-n", "-o", "-q", "-p", "TCP"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, check=False).stdout
        log("warn", "health.port_exhaustion", **tcp_snapshot_from_netstat(out, _process_names()))
        return True
    except Exception as exc:  # noqa: BLE001 -- a diagnostic, never fatal
        log("warn", "health.port_exhaustion", error=str(exc)[:200])
        return True


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
    # The server reads the guard's heartbeat from here to notice when it is
    # left running with no guard (kazma_core.observability.supervisor_watch).
    env = dict(os.environ)
    env["KAZMA_GUARD_STATE_FILE"] = str(_state_path())
    kwargs: dict = {"cwd": str(cwd), "env": env}
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


def _request_graceful_stop(proc: subprocess.Popen, log: GuardLog) -> bool:
    """Ask the child to shut itself down. True if the request was delivered.

    Windows: CTRL_BREAK_EVENT to the child's process group (spawn() gives it
    its own). uvicorn handles it like Ctrl+C in a terminal: stop accepting,
    drain, run the app's shutdown hooks. The venv's python.exe launcher that
    fronts the real interpreter ignores console events and exits when its
    child does. POSIX: SIGTERM to the process group.
    """
    # Group 0 is "every process on this console" -- the guard included.
    if not isinstance(proc.pid, int) or proc.pid <= 0:
        return False
    try:
        if os.name == "nt":
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return True
    except Exception as exc:
        log("warn", "child.graceful_unavailable", pid=proc.pid, error=str(exc)[:200])
        return False


def stop_child(proc: subprocess.Popen, log: GuardLog, *, grace_s: float = 0.0) -> bool:
    """Stop the child and everything it spawned. Never raises.

    With ``grace_s`` the child is asked to shut down and given that long;
    whatever still runs afterwards is killed. Returns True when the child
    shut itself down (so the app announced its own stop), False when it had
    to be killed.
    """
    if proc.poll() is not None:
        _record_child(None)
        return True
    log("info", "child.terminating", pid=proc.pid, grace_s=grace_s)
    if grace_s > 0 and _request_graceful_stop(proc, log):
        started = time.monotonic()
        try:
            proc.wait(timeout=grace_s)
            log("info", "child.stopped_gracefully", pid=proc.pid,
                after_s=round(time.monotonic() - started, 1))
            _record_child(None)
            return True
        except subprocess.TimeoutExpired:
            log("warn", "child.graceful_timeout", pid=proc.pid, grace_s=grace_s,
                note="did not shut down in time; killing it")
        except Exception as exc:
            log("warn", "child.graceful_wait_failed", pid=proc.pid, error=str(exc)[:200])
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
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
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
    return False


# -- supervisor -------------------------------------------------------


class Guard:
    # Defaults for the per-child bookkeeping (set properly in __init__ and
    # run()); class-level so a partially built Guard still behaves.
    spawned_at = 0.0
    _reload_seen: tuple[int, int] | None = None
    _stopped_for_reload = False
    _last_stop_graceful = False
    _last_beat = 0.0
    _internal_errors = 0

    def __init__(self, *, once: bool = False) -> None:
        self.log = GuardLog(_default_log_path())
        self.cmd = build_command()
        self.cwd = Path(os.environ.get("KAZMA_GUARD_CWD") or REPO_ROOT)
        # The notifier's settings lookup runs where, and with what, the
        # server runs: its folder, and its interpreter when the command names
        # one (KAZMA_GUARD_CMD may point at a different Python than ours).
        server_python = (
            self.cmd[0] if self.cmd and "python" in Path(self.cmd[0]).name.lower() else None
        )
        self.notify = Notifier(self.log, cwd=self.cwd, python=server_python)
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
        # Wall-clock time the current child was spawned: a reload request
        # made before it is already satisfied by it.
        self.spawned_at = 0.0
        # The last reload request handled (its on-disk signature), so the
        # same request can never wake the guard twice.
        self._reload_seen: tuple[int, int] | None = None
        # Whether the last deliberate stop let the app shut itself down.
        self._last_stop_graceful = False
        # Set when a reload request stopped the child (also mid-boot).
        self._stopped_for_reload = False
        self._last_beat = 0.0
        # Consecutive errors in the guard's own code (backoff between them).
        self._internal_errors = 0

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
            self._beat()
            if self._reload_pending() and self._take_reload_request():
                # New code landed while this child was booting, and it may
                # already have imported part of the old. Start over rather
                # than finish a boot of mixed builds.
                return False
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

    def _sleep(
        self,
        seconds: float,
        *,
        wake_on_child_exit: bool = False,
        wake_on_reload: bool = False,
    ) -> bool:
        """Interruptible sleep so shutdown, --reload, and a dead child stay responsive.

        Wakes early only for what the caller handles: a reload request the
        guard has not dealt with yet (``wake_on_reload``), a child that
        exited (``wake_on_child_exit``), or shutdown. Beats the heartbeat.
        Returns True when it woke early, False when the time ran out.
        """
        end = time.monotonic() + seconds
        while not self._stop:
            # The clock is read ONCE per pass. The old loop tested it, ran
            # the checks below, then read it again for the sleep length --
            # which could come out negative, and time.sleep() raises on that.
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            self._beat()
            if wake_on_reload and self._reload_pending():
                return True
            if (
                wake_on_child_exit
                and self.proc is not None
                and self.proc.poll() is not None
            ):
                return True
            time.sleep(min(1.0, remaining))
        return True

    def _beat(self, *, force: bool = False) -> None:
        """Record that this guard is alive (see HEARTBEAT_EVERY_S)."""
        now = time.monotonic()
        if not force and now - self._last_beat < HEARTBEAT_EVERY_S:
            return
        self._last_beat = now
        _update_state(guard_pid=os.getpid(), heartbeat=time.time())

    def _reload_pending(self) -> bool:
        """A reload request is on disk that this guard has not handled yet."""
        sig = _reload_signature()
        return sig is not None and sig != self._reload_seen

    def _take_reload_request(self) -> bool:
        """Handle the pending reload request. True if the child was stopped for it.

        A request older than the running child is already satisfied -- the
        child was spawned after it, from the code it asked for -- so it is
        cleared, never acted on. A newer one stops the child here, in the
        guard: the process that owns it and has its rights.
        """
        self._reload_seen = _reload_signature()
        req = read_reload_request()
        if req is None:
            return False
        requested_at = float(req.get("ts") or 0.0)
        consume_reload_request()
        age_s = round(max(0.0, time.time() - requested_at), 1) if requested_at else None
        if requested_at <= self.spawned_at:
            _update_state(reload_ack=requested_at, reload_action="already_satisfied")
            self.log("info", "reload.already_satisfied", age_s=age_s,
                     note="requested before the running server was started")
            return False
        _update_state(reload_ack=requested_at, reload_action="restarting")
        assert self.proc is not None
        self.log("info", "guard.reload_requested", pid=self.proc.pid, age_s=age_s)
        self._stopped_for_reload = True
        self._last_stop_graceful = stop_child(self.proc, self.log, grace_s=GRACEFUL_STOP_S)
        return True

    def _fresh_reload_request(self) -> bool:
        """After a child exit: was it stopped for a reload by an older --reload?

        Before 2026-09-26 the CLI killed the child itself and left the
        request as a "that was not a crash" note. A guard that outlives a
        pull can still meet one; only a request newer than the child counts.
        """
        req = read_reload_request()
        if req is None:
            return False
        consume_reload_request()
        self._reload_seen = None
        return float(req.get("ts") or 0.0) > self.spawned_at

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
        # From here on this is the guard --reload, --status and the server
        # see as alive (the heartbeat; see HEARTBEAT_EVERY_S).
        _update_state(guard_pid=os.getpid(), guard_started=time.time())
        self._beat(force=True)

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
            # The supervisor must outlive its own bugs. Until 2026-09-26 any
            # exception in this loop ended the process: exit code 1, nothing
            # in guard.log, the server left running with nobody watching it
            # (the task sat "Ready" -- Task Scheduler does not restart a task
            # whose process ran and exited, whatever the code).
            try:
                if self.proc is not None and self.proc.poll() is None:
                    # An error interrupted supervision of a live child: keep
                    # supervising it. Spawning a second server next to it is
                    # the one outcome worse than the error.
                    self.log("warn", "guard.supervision_resumed", pid=self.proc.pid)
                    reason = self._supervise()
                else:
                    # Maintenance gate. Checked before every spawn so a pause
                    # taken while the guard is mid-backoff is still honoured.
                    if self._await_resume():
                        continue
                    if self._stop:
                        break
                    # A previous instance (or its orphaned grandchild) may
                    # still own the port. Clearing it here costs one netstat;
                    # discovering it after the spawn costs a discarded child
                    # and a backoff.
                    clear_stale_port(self.health_url, self.log)
                    # Honesty (2026-09-03): when the clear FAILED the port is
                    # still held, the spawn below cannot bind, and the server
                    # answering requests is the OLD build — the operator's
                    # restart silently did not take effect (live: elevated
                    # zombie python survived three reaps while the guard
                    # logged holder_reaped). Page once per holder pid; the
                    # kill needs an elevated shell only the operator can open.
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
                    self.spawned_at = spawned_at
                    self._stopped_for_reload = False
                    self.proc = spawn(self.cmd, self.cwd, self.log)
                    # --reload reads this: a child spawned after its request
                    # already runs the code it asked for.
                    _update_state(child_spawned=spawned_at)

                    if self._wait_ready(spawned_at):
                        if first:
                            # Safe to resolve now: the server is already
                            # running, so a slow vault import costs nothing
                            # but a delayed alert.
                            self.log("info", "guard.notifier",
                                     target=self.notify.describe())
                        # NO notification on a healthy start. Kazma's own
                        # lifecycle_notifier already sends "server starting
                        # up", "server started" and "server restarted (was
                        # down ~Ns)" from inside the app. The guard exists to
                        # say the things the app CANNOT say -- because when
                        # they are true, the app is dead. Announcing a
                        # successful start here just doubles every message in
                        # the operator's Telegram.
                        self.log("info", "guard.supervising",
                                 restarts=self.restarts, note="app announces its own start")
                        self.notify_recovered()
                        first = False
                        self._internal_errors = 0
                        reason = self._supervise()
                    elif self._stopped_for_reload:
                        reason = RELOAD_REASON
                    else:
                        reason = "never became healthy"
                        stop_child(self.proc, self.log, grace_s=NEVER_READY_STOP_S)

                if self._stop:
                    break
                if self.once:
                    self.log("info", "guard.once_exit", reason=reason)
                    return 1

                if reason == "maintenance":
                    # Not a failure: no restart count, no backoff, no
                    # crash-loop accounting. Treating a deliberate pause as a
                    # crash would push the guard into a 30-minute cooldown
                    # the moment the operator resumed.
                    self.log("info", "guard.paused_by_operator")
                    continue

                if reason == RELOAD_REASON or self._fresh_reload_request():
                    # An operator reload stopped this child on purpose. Spawn
                    # the new process immediately; do not climb the crash
                    # ladder and do not page Telegram as if Kazma died.
                    graceful = reason == RELOAD_REASON and self._last_stop_graceful
                    self.log("info", "guard.operator_reload", reason=reason,
                             graceful=graceful)
                    # Informational notice (2026-09-03): a reload used to be a
                    # hard kill, the ONLY silent restart path -- the app never
                    # got to say it was stopping. A graceful stop runs the
                    # app's own shutdown notice, so the guard speaks only
                    # when the app could not.
                    if not graceful:
                        try:
                            self._page(
                                "info",
                                "Kazma is restarting for an operator reload.",
                                "Back in a moment — no action needed.",
                                fingerprint="reload",
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
                    # An operator reload ends the cooldown: it is someone
                    # acting on exactly this page.
                    self._sleep(CRASH_LOOP_COOLDOWN_S, wake_on_reload=True)
                    continue

                delay = self._backoff()
                self.log("warn", "guard.restarting", reason=reason, in_s=delay,
                         restarts=self.restarts)
                self.notify_restart(reason, delay)
                self._sleep(delay, wake_on_reload=True)
            except Exception as exc:  # noqa: BLE001 -- see the comment above the try
                self._internal_error(exc)

        if self.proc:
            stop_child(self.proc, self.log, grace_s=GRACEFUL_STOP_S)
        self.log("info", "guard.stopped")
        return 0

    def _internal_error(self, exc: BaseException) -> None:
        """An error in the guard's own code: log it whole, page once, go on."""
        import traceback

        self._internal_errors += 1
        frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        where = frames[-1] if frames else None
        self.log(
            "error", "guard.internal_error",
            error=f"{type(exc).__name__}: {exc}"[:300],
            where=f"{Path(where.filename).name}:{where.lineno} in {where.name}" if where else "",
            streak=self._internal_errors,
            traceback="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-3000:],
        )
        try:
            self._page(
                "error",
                "Kazma's guard hit an error in its own code",
                f"{type(exc).__name__}: {exc}. It keeps supervising; "
                "guard.log has the traceback (guard.internal_error).",
                fingerprint=(
                    f"internal:{type(exc).__name__}:"
                    f"{where.name if where else '?'}:{where.lineno if where else 0}"
                ),
            )
        except Exception:
            pass
        delay = float(BACKOFF_LADDER_S[min(self._internal_errors, len(BACKOFF_LADDER_S)) - 1])
        try:
            self._sleep(delay, wake_on_reload=True)
        except Exception:
            time.sleep(delay)

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
        unrunnable = 0
        next_probe = time.monotonic() + PROBE_INTERVAL_S
        while not self._stop:
            # Probes keep their cadence whatever wakes the sleep: a wake-up
            # handled below is not a probe tick. A reload flag that woke
            # every sleep made this a busy loop of 53 probes a second for
            # 47 hours (2026-09-20..22).
            woke_early = self._sleep(max(0.0, next_probe - time.monotonic()),
                                     wake_on_child_exit=True, wake_on_reload=True)
            if self._stop:
                return "guard shutting down"

            if read_pause() is not None:
                # Operator asked for quiet. Stop the child so diagnosis
                # happens against a stopped Kazma, not a moving target.
                self.log("info", "maintenance.requested_while_running")
                if self.proc:
                    stop_child(self.proc, self.log, grace_s=GRACEFUL_STOP_S)
                return "maintenance"

            assert self.proc is not None
            if self.proc.poll() is not None:
                return f"process exited (code {self.proc.returncode})"

            if self._reload_pending():
                if self._take_reload_request():
                    return RELOAD_REASON
                continue

            if woke_early and time.monotonic() < next_probe:
                continue
            next_probe = time.monotonic() + PROBE_INTERVAL_S

            ok, detail = probe(self.health_url, PROBE_TIMEOUT_S)
            if ok:
                if consecutive:
                    self.log("info", "health.recovered", after_failures=consecutive)
                consecutive = 0
                unrunnable = 0
                continue

            if probe_could_not_run(detail):
                # The MACHINE had no free local port: Kazma was never asked.
                # Killing it cannot free a port and adds churn, so this does
                # not count toward the restart -- 169 of 257 "failed" probes
                # in one week were this. It is recorded, with who holds the
                # sockets, and a long run of it pages once.
                unrunnable += 1
                self.log("warn", "health.probe_unrunnable", detail=detail,
                         run=unrunnable)
                maybe_log_tcp_snapshot(self.log, detail)
                if unrunnable == UNRUNNABLE_PAGE_AFTER:
                    self._page(
                        "warn",
                        "This machine has run out of network ports",
                        "The guard cannot reach Kazma to check it -- not because "
                        "Kazma is down, but because Windows has no free local "
                        "port. Restarting Kazma would not help. guard.log "
                        "health.port_exhaustion names who holds the sockets.",
                        fingerprint="probe-unrunnable",
                    )
                continue
            unrunnable = 0

            consecutive += 1
            self.log("warn", "health.failed", detail=detail,
                     consecutive=consecutive, threshold=FAILURES_TO_KILL)
            if consecutive >= FAILURES_TO_KILL:
                # Alive but not healthy -- the case no OS supervisor catches.
                stop_child(self.proc, self.log, grace_s=UNHEALTHY_STOP_S)
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
    state = _read_state()
    alive = _guard_alive()
    beat = state.get("heartbeat")
    seen = (
        f", heartbeat {int(time.time() - float(beat))}s ago"
        if isinstance(beat, (int, float)) else ""
    )
    # "supervision: active" only ever meant "not paused". On 2026-09-26 it
    # said so for 20 minutes while no guard was running at all.
    print(f"guard       : {'running' if alive else 'NOT RUNNING'} "
          f"(pid {state.get('guard_pid') or '?'}{seen})")
    if not alive:
        print("  Kazma is not supervised: a crash will not be restarted.")
        print("  Start it:  schtasks /Run /TN KazmaAgent   (or run --reload)")
    print(f"guard child : pid {state.get('child_pid') if state else 'unknown'}")
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


def _stop_recorded_child(log: GuardLog, *, spawned_before: float | None = None) -> int:
    """Kill the guard's recorded child tree. Returns the pid stopped, or 0.

    The fallback for a guard too old to stop its own child (_cmd_reload).
    ``spawned_before`` leaves alone a child the guard spawned after that
    time: it already runs the code the reload asked for. Honest about
    failure -- taskkill's "Access is denied" used to be logged as
    reload.child_stopped (live, 2026-09-26).
    """
    state = _read_state()
    try:
        pid = int(state.get("child_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    spawned = state.get("child_spawned")
    if (
        spawned_before is not None
        and isinstance(spawned, (int, float))
        and float(spawned) > spawned_before
    ):
        return 0
    if not pid or not _pid_alive(pid):
        return 0
    note = ""
    try:
        if os.name == "nt":
            kill = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, check=False,
            )
            if kill.returncode != 0:
                lines = (kill.stderr or kill.stdout or "").strip().splitlines()
                note = lines[-1][:160] if lines else f"exit {kill.returncode}"
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception as exc:
        note = str(exc)[:200]
    for _ in range(20):
        if not _pid_alive(pid):
            log("info", "reload.child_stopped", pid=pid)
            return pid
        time.sleep(0.5)
    log("error", "reload.child_stop_failed", pid=pid,
        error=note or "still alive after the kill")
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


def _activity(health_url: str) -> dict | None:
    """The server's /health/activity answer, or None when it cannot give one.

    None covers a build from before the route existed (404) and a server
    that is down; the caller decides what not knowing means.
    """
    base = health_url.rstrip("/").rsplit("/", 1)[0]
    url = base + "/activity" if base.endswith("/health") else "http://127.0.0.1:9090/health/activity"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _wait_until_idle(health_url: str, max_wait_s: float, log: GuardLog, *,
                     poll_s: float = 5.0) -> bool:
    """Wait until no turn is running (two quiet polls in a row).

    True when idle -- or when this build cannot say, which is reported and
    then treated as idle, since the operator asked for the reload. False
    when still busy at the deadline: the reload does not happen.
    """
    deadline = time.monotonic() + max_wait_s
    quiet = 0
    told = False
    while True:
        act = _activity(health_url)
        if act is None:
            print("This build cannot report activity (no /health/activity); "
                  "reloading without waiting.")
            log("warn", "reload.idle_unknown")
            return True
        running = int(act.get("active_turns") or 0)
        if running == 0:
            quiet += 1
            if quiet >= 2:
                return True
        else:
            quiet = 0
            if not told:
                print(f"Waiting for {running} running turn(s) to finish before reloading…")
                log("info", "reload.waiting_for_idle", running=running)
                told = True
        if time.monotonic() >= deadline:
            print(f"Still busy after {max_wait_s:.0f}s ({running} turn(s) running); not reloading.")
            log("warn", "reload.busy_gave_up", running=running)
            return False
        time.sleep(poll_s)


def _wait_for_reload_ack(requested_at: float, timeout_s: float) -> bool:
    """Did a guard take the request made at *requested_at*?

    Taken = the guard recorded it (reload_ack), or spawned a child after it
    (a guard waking from a backoff or a pause, or a fresh guard started by
    the task, satisfies it with that spawn).
    """
    deadline = time.monotonic() + timeout_s
    while True:
        state = _read_state()
        ack = state.get("reload_ack")
        if isinstance(ack, (int, float)) and abs(float(ack) - requested_at) < 1e-6:
            return True
        spawned = state.get("child_spawned")
        if isinstance(spawned, (int, float)) and float(spawned) > requested_at:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _old_server_serving(health_url: str, requested_at: float) -> bool:
    """A server that booted before the request is still answering."""
    boot = server_started_at(health_url, 5.0)
    return boot is not None and boot <= requested_at


def _wait_for_new_boot(health_url: str, requested_at: float, before: str,
                       log: GuardLog) -> int:
    """Wait until a server that booted AFTER the request answers ready."""
    print("Waiting for Kazma to come back on the new code…")
    print(
        f"(Typical bind is under 2 minutes; budget {int(START_TIMEOUT_S)}s. "
        "Do not Ctrl+C unless you intend to abort.)"
    )
    started = time.monotonic()
    deadline = started + START_TIMEOUT_S + GRACEFUL_STOP_S
    next_progress = started + 30.0
    while time.monotonic() < deadline:
        boot = server_started_at(health_url, 5.0)
        if boot is not None and boot > requested_at:
            ok, _detail = probe(health_url, 5.0)
            if ok:
                after = _live_commit(health_url)
                print(f"Kazma is up. build {after or '?'} (was {before or '?'})")
                if before and after and before == after:
                    print(
                        "NOTE: commit hash unchanged — the process restarted but "
                        "git HEAD is the same. Code edits still need this reload."
                    )
                log("info", "reload.ready", commit=after, previous=before)
                return 0
        now = time.monotonic()
        if now >= next_progress:
            if boot is None:
                where = "not answering yet"
            elif boot <= requested_at:
                where = "old server still shutting down"
            else:
                where = "new server booting"
            print(f"  still restarting… {int(now - started)}s ({where})")
            next_progress += 30.0
        time.sleep(2.0)

    print("Server did not come back within the start budget.")
    print("  python scripts/service/kazma_guard.py --status")
    print("  python scripts/service/install_service.py --status")
    print("  python scripts/service/kazma_guard.py          # start supervision in this terminal")
    log("error", "reload.not_ready")
    return 2


def _cmd_reload(*, when_idle: bool = False, idle_timeout_s: float = 900.0) -> int:
    """Operator deploy: have the supervisor boot the code on disk.

    The RUNNING GUARD does the stop. It owns the child and runs with the
    child's rights; this shell may not have them. Killing the child from
    here failed with "Access is denied" whenever the guard ran elevated --
    the KazmaAgent task does -- and left the old build serving while the
    request this command had written sent the guard into a probe storm
    (2026-09-03, 2026-09-20..22, 2026-09-26). So this command:

      1. with --when-idle, waits until no chat turn is running;
      2. lifts a leftover pause and writes the reload request;
      3. starts the KazmaAgent task if no guard is alive (the new guard
         clears the old server with its own rights);
      4. waits for a guard to take the request -- and only if none does
         (a guard from before this change), stops the server from here;
      5. waits until a server that booted AFTER the request answers ready.

    A request no guard will act on is never left behind.
    """
    log = GuardLog(_default_log_path())
    health = os.environ.get("KAZMA_GUARD_HEALTH_URL", DEFAULT_HEALTH_URL)
    # --when-idle: a reload mid-turn drops the reply in flight (and on
    # 2026-09-24 a restart discarded a finished answer held only in memory).
    if when_idle and not _wait_until_idle(health, idle_timeout_s, log):
        return 3
    before = _live_commit(health)
    if clear_pause():
        print("Cleared leftover pause so the supervisor can respawn.")
        log("info", "reload.cleared_pause")
    requested_at = request_reload()
    log("info", "reload.requested")

    ack_wait = GUARD_ACK_S
    if _guard_alive():
        print("Asked the running guard to restart Kazma; it stops the server itself.")
    else:
        print("No guard is running for this install. Starting the KazmaAgent task: "
              "its guard clears the old server and starts the new code.")
        log("warn", "reload.no_guard")
        if _kick_os_supervisor(log):
            # A fresh guard reaps the old server and spawns before it acks.
            ack_wait = max(GUARD_ACK_S, 90.0)

    if not _wait_for_reload_ack(requested_at, ack_wait):
        # Nobody took the request: a guard from before guard-side reloads
        # (it only notices a child that died), or no guard at all. Stop the
        # OLD server from here, the way this command used to -- it works
        # when this shell has the rights the server runs with. A server
        # that booted after the request is never touched.
        print("No guard took the request; stopping the old server from here.")
        log("warn", "reload.no_ack", waited_s=ack_wait)
        stopped = _stop_recorded_child(log, spawned_before=requested_at)
        if stopped:
            print(f"Stopped recorded server pid {stopped}.")
        if _old_server_serving(health, requested_at):
            reap_port_holder(health, log)
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline and _old_server_serving(health, requested_at):
            time.sleep(0.5)
        if _old_server_serving(health, requested_at):
            # Never leave a request no guard will act on: a guard from
            # before this change wakes on it every second, forever.
            consume_reload_request()
            print("Could not stop the running server: it runs with more rights "
                  "than this shell (the KazmaAgent task runs elevated).")
            print("  Run this command from an elevated terminal, or restart the "
                  "KazmaAgent task once from one -- guards from now on restart "
                  "Kazma themselves.")
            log("error", "reload.not_applied",
                note="old server still serving; request withdrawn")
            return 1
    return _wait_for_new_boot(health, requested_at, before, log)


def _guard_alive() -> bool:
    """Is a guard running for this install?

    Read from the heartbeat the guard writes into its state file: the guard
    runs elevated in the task's own logon session, and an operator shell
    cannot open its process to ask. A state file written by a guard from
    before the heartbeat falls back to "is that pid a python process".
    """
    state = _read_state()
    beat = state.get("heartbeat")
    if isinstance(beat, (int, float)):
        return (time.time() - float(beat)) < GUARD_STALE_S
    try:
        gpid = int(state.get("guard_pid") or 0)
    except (TypeError, ValueError):
        return False
    if not gpid or gpid == os.getpid() or not _pid_alive(gpid):
        return False
    if os.name == "nt":
        return _is_reapable_image(_windows_image_name(gpid).lower())
    return True


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
                    help="have the supervisor boot new code (the guard stops the server)")
    ap.add_argument("--when-idle", action="store_true",
                    help="with --reload: first wait until no chat turn is running")
    ap.add_argument("--idle-timeout", type=float, default=900.0,
                    help="with --when-idle: seconds to wait before giving up (default 900)")
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
        return _cmd_reload(when_idle=args.when_idle, idle_timeout_s=args.idle_timeout)
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
    return _run_supervisor(guard)


#: Held open for the life of the process (faulthandler writes into it).
_FAULT_FILE = None


def _enable_fault_log() -> None:
    """Send a native crash's stack to guard.fault.log, next to guard.log."""
    global _FAULT_FILE
    try:
        import faulthandler

        _FAULT_FILE = _default_log_path().with_name("guard.fault.log").open(
            "a", encoding="utf-8"
        )
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
    except Exception:
        _FAULT_FILE = None


def _run_supervisor(guard: Guard) -> int:
    """Run the supervisor so that its own death is never silent.

    The KazmaAgent task gives the guard a console nobody reads: a Python
    exception or a native crash used to end it with no trace but a task
    result of 1 (live 2026-09-26, and at least twice before). Exceptions are
    logged whole into guard.log and paged; native crashes are written to
    guard.fault.log by faulthandler.
    """
    _enable_fault_log()
    try:
        return guard.run()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        import traceback

        guard.log(
            "critical", "guard.crashed",
            error=f"{type(exc).__name__}: {exc}"[:300],
            traceback="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-4000:],
        )
        try:
            guard._page(
                "critical",
                "Kazma's guard crashed",
                f"{type(exc).__name__}: {exc}. Kazma keeps running WITHOUT "
                "supervision until the guard is started again: "
                "schtasks /Run /TN KazmaAgent.",
                force=True,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
