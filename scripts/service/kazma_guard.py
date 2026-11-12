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
    KAZMA_GUARD_HEALTH_URL      health endpoint      (default: the port the server
                                binds, KAZMA_PORT from the environment or the
                                install's .env, else :9090/health/ready)
    KAZMA_GUARD_START_TIMEOUT   seconds to first ready       (default: 900)
    KAZMA_GUARD_INTERVAL        seconds between probes       (default: 30)
    KAZMA_GUARD_FAILURES        consecutive fails = dead     (default: 3)
    KAZMA_GUARD_DEPENDENCY_OUTAGE_S  seconds Kazma may answer "not ready"
                                (a dependency down) before a restart (default: 600)
    KAZMA_GUARD_GRACEFUL_STOP_S seconds a deliberate stop waits (default: 60)
    KAZMA_GUARD_LOG             guard log path
    KAZMA_GUARD_STATE           child-PID state file (orphan reaping)
    KAZMA_GUARD_TELEGRAM_TOKEN  bot token (falls back to SWARM_BOT_TOKEN)
    KAZMA_GUARD_TELEGRAM_CHAT   chat id   (falls back to SWARM_CHAT_ID)
    KAZMA_GUARD_DOCUMENT_BROKER auto (run the document broker when Kazma
                                asks) | on (always) | off (never) (default: auto)
    KAZMA_GUARD_BROKER_PORT     the broker's loopback port (default: chosen once)
    KAZMA_GUARD_BROKER_IMAGE    pin the worker image (default: the one built
                                for the code on disk)
    KAZMA_GUARD_BROKER_SCRIPT   the broker program (default: worker_broker.py)
    KAZMA_GUARD_START_DEPENDENCIES  auto (default: start Docker Desktop, and
                                Kazma's own stopped containers, when the
                                engine does not answer) | off
    KAZMA_GUARD_DISK_WATCH      auto (default: check the free space where
                                Kazma writes, once a minute) | off
    KAZMA_GUARD_OUTSIDE_WATCH   auto (default: send the outside observer a
                                heartbeat once the install is paired) | off
    KAZMA_GUARD_OUTSIDE_WATCH_URL  the observer's heartbeat address
                                (default: https://status.kazma.ai/v1/heartbeat)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# -- configuration ----------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_HEALTH_URL = "http://127.0.0.1:9090/health/ready"


def _env_files() -> list[Path]:
    """The ``.env`` files the server reads its port from, in its order:
    the install's, then ``KAZMA_ENV_FILE`` (the later wins)."""
    files = [REPO_ROOT / ".env"]
    named = (os.environ.get("KAZMA_ENV_FILE") or "").strip()
    if named:
        files.append(Path(named))
    return files


def install_port(env_files: list[Path] | None = None) -> int | None:
    """``KAZMA_PORT`` as ``serve.py`` reads it: the environment, else the
    install's ``.env``. One key is read from the file; the file is never
    loaded into the guard's environment (every server inherits it, §33)."""
    raw = (os.environ.get("KAZMA_PORT") or "").strip()
    if not raw:
        for path in env_files if env_files is not None else _env_files():
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if line.startswith("KAZMA_PORT="):
                    raw = line.split("=", 1)[1].split("#", 1)[0].strip().strip("'\"")
    try:
        port = int(raw)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def health_url(*, of_running_guard: bool = False, env_files: list[Path] | None = None) -> str:
    """The readiness URL, one answer for the guard and every command.

    ``KAZMA_GUARD_HEALTH_URL`` when set; for a command (``of_running_guard``)
    the URL the running guard recorded; else the port the server itself
    binds (``KAZMA_PORT``); else 9090. The drill VM (2026-11-07) set its
    port in .env and the guard's URL with ``setx``: ``--status`` probed the
    built-in 9090 whatever was set and called a healthy Kazma "not
    answering", and a shell without the variable did the same for every
    command."""
    explicit = (os.environ.get("KAZMA_GUARD_HEALTH_URL") or "").strip()
    if explicit:
        return explicit
    if of_running_guard:
        recorded = _read_state().get("health_url")
        if isinstance(recorded, str) and recorded.startswith("http") and _guard_alive():
            return recorded
    port = install_port(env_files)
    return f"http://127.0.0.1:{port}/health/ready" if port else DEFAULT_HEALTH_URL

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
# Kazma answered, but "not ready": a dependency it cannot serve without (the
# database) is gone. A restart cannot bring the database back -- on
# 2026-09-28 a Docker Desktop update took Postgres away for two minutes and
# the old rule restarted Kazma over it. It is ridden out this long, and
# paged; past it the guard restarts anyway, for the case where the database
# is back and Kazma's own connections are what is stuck.
DEPENDENCY_OUTAGE_S = float(os.environ.get("KAZMA_GUARD_DEPENDENCY_OUTAGE_S", "600"))
# Kazma up and waiting for a dependency outside it (readiness ``waits_for``:
# the Docker engine) is never restarted for it, and is paged this long into
# the wait -- not at once: Docker Desktop takes a minute or two after every
# sign-in, and a reboot is no incident.
WAIT_PAGE_S = 300.0
# The same restart reason pages once an hour while it repeats: on
# 2026-11-12 eighteen "never became healthy" pages came 15-20 min apart,
# each past the page cooldown. Every restart still lands in guard.log.
RESTART_REPAGE_S = 3600.0
# A booting server rewrites its boot-status file on every try while it waits
# for its database (a try is the pool's attempts, ~30 s, plus a 10 s pause).
# A record older than this says nothing about now.
BOOT_STATUS_FRESH_S = 150.0
# A boot waiting this long for its database is paged, once.
BOOT_WAIT_PAGE_S = 120.0
# The server pages its own database outage (ops alert database.away) this
# long after it began (db.availability.PAGE_AFTER_S); the guard pages instead
# when the server has not, this long past that.
APP_PAGE_GRACE_S = 90.0

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
#: What of the graceful stop the server's own stop budget leaves for the
#: process to exit (KAZMA_SHUTDOWN_BUDGET_S, FA-261).
SHUTDOWN_MARGIN_S = 10.0
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
# What this guard does that a program reading its state file relies on. A
# guard started before a feature, still running after the update that added
# it, lists none of it:
#   pause_held -- the gate before every spawn records the pause it holds
#                 (state ``pause_held`` = that pause's ``since``).
#   document_broker -- runs the document worker broker beside Kazma when
#                 Kazma asks (state ``document_broker``; see DocumentBroker).
#   disk_watch -- checks the free space where Kazma writes (state ``disk``).
#   database_service -- watches the database's OS service named in the setup
#                 record (state ``database_service``; DatabaseServiceWatch).
#   outside_watch -- the heartbeat to the outside observer (state
#                 ``outside_watch``; see OutsideWatch).
#   pager_test -- answers Settings -> Alerts' "Send a test page" (state
#                 ``pager_test``; see PagerTest) and records each page's outcome
#                 (state ``pager``).
GUARD_FEATURES = ("pause_held", "document_broker", "dependencies", "boot_wait",
                  "database_hold", "disk_watch", "outside_watch", "database_service", "power_watch",
                  "pager_test")
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


def _boot_status_path() -> Path:
    """What the booting server says it waits for (resilience plan A3).

    The server writes it on every try while it waits for its settings
    database (``kazma_core.config_availability.write_boot_status``) and
    clears it once past the wait. A boot that waits is not a hang: on
    2026-11-03/04 33 of 34 restarts were a boot that waited 780 s for a
    database Docker had taken away, exited, and was restarted after a 300 s
    backoff with a page each time.
    """
    env = os.environ.get("KAZMA_GUARD_BOOT_STATUS")
    if env:
        return Path(env)
    return _guard_file("boot_status.json")


def read_boot_status(spawned_at: float, *, now: float | None = None) -> dict | None:
    """The boot-status record of the server spawned at ``spawned_at``, while
    fresh; else None.

    Matched by time, not pid: on Windows the venv's python.exe is a launcher,
    and the process that writes is not the guard's child. A record written
    before this spawn is an earlier boot's; one older than
    BOOT_STATUS_FRESH_S says nothing about now (a boot that stops rewriting it
    is treated as any other boot: the start budget applies).
    """
    try:
        data = json.loads(_boot_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        at = float(data.get("at") or 0.0)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    if at < spawned_at - 1.0 or current - at > BOOT_STATUS_FRESH_S:
        return None
    return data


def _clear_boot_status() -> None:
    """Remove an earlier boot's record before a spawn."""
    try:
        _boot_status_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass  # read_boot_status ignores a record older than the spawn


def database_state(result: object) -> dict | None:
    """The server's own database state from a readiness answer, or None.

    ``kazma_core.db.availability.snapshot()`` in ``database_availability``.
    """
    db = getattr(result, "database", None)
    return db if isinstance(db, dict) else None


def app_watches_database(db: dict | None) -> bool:
    """The server has confirmed its database away and its own prober is
    still trying: the outage is the database's, and the server will notice
    the moment it is back. A restart cannot bring the database back."""
    if not db or db.get("state") != "away" or not db.get("confirmed"):
        return False
    try:
        age = float(db.get("probe_age_s") or 0.0)
        stale = float(db.get("probe_stale_after_s") or 0.0)
    except (TypeError, ValueError):
        return False
    return stale > 0 and age <= stale


def app_pages_database_outage(db: dict | None) -> bool:
    """The server has paged its database outage, or will shortly.

    Kazma pages ``database.away`` itself ``page_at_s`` after the database
    went away; the guard leaves the page to it until APP_PAGE_GRACE_S past
    that, so one outage is one card. A page that has not reached anyone by
    then (``paged`` is what was delivered) is the guard's to send.
    """
    if not db or db.get("state") != "away":
        return False
    if db.get("paged"):
        return True
    try:
        away = float(db.get("away_s") or 0.0)
        page_at = float(db.get("page_at_s") or 0.0)
    except (TypeError, ValueError):
        return False
    return away < page_at + APP_PAGE_GRACE_S


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


def _pause_signature(pause: dict | None) -> str:
    """Which pause this is: its ``since`` (every write has its own); "" for none."""
    if pause is None:
        return ""
    try:
        return repr(float(pause.get("since") or 0.0))
    except (TypeError, ValueError):
        return "?"


# -- logging (deliberately not the app's logger) ----------------------


#: The guard's own log is rotated past this size, to one ``.1`` sibling
#: (2026-11, FA-143): it grew forever (1.35 MB in two months on the live
#: install). The weekly report and the daily digest read the sibling too.
GUARD_LOG_MAX_BYTES = 2 * 1024 * 1024


def _rotate_if_large(path: Path, max_bytes: int) -> None:
    """Move *path* to ``<name>.1`` (replacing it) once it passes *max_bytes*.

    The one rotation of every file the guard writes. Never raises: a log
    that cannot rotate is still written.
    """
    try:
        if path.is_file() and path.stat().st_size > max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


class GuardLog:
    """Tiny append-only JSONL logger with stderr mirroring.

    Uses no third-party logging config so it cannot be silenced by the
    application's own logging setup. ``echo=False`` keeps the record and
    skips the mirror: another program (``kazma update``) prints its own
    account of what it asked the guard, and the raw events read as noise
    there.
    """

    def __init__(self, path: Path, *, echo: bool = True) -> None:
        self.path = path
        self.echo = echo
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
        _rotate_if_large(self.path, GUARD_LOG_MAX_BYTES)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        if not self.echo:
            return
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
    from kazma_core.observability.ops_alerts import pager_credentials
    token, chat = pager_credentials(get_config_store())
print("KAZMA_GUARD_NOTIFY " + json.dumps({"token": token, "chat": chat}))
"""
#: Credentials read from Kazma's settings are read again at a page this much
#: later: the owner may have chosen another bot or chat (Settings -> Alerts,
#: the alerts bot, 2026-11-11), and the guard kept the first ones for life.
_NOTIFY_REFRESH_S = 600.0
_NOTIFY_LOOKUP_MARK = "KAZMA_GUARD_NOTIFY "
_NOTIFY_LOOKUP_TIMEOUT_S = 90.0
#: A lookup that found nothing is tried again when a page is due, at most
#: this often.
_NOTIFY_RELOOKUP_S = 600.0
#: Pages kept while the guard cannot send them (oldest dropped past this).
_NOTIFY_HELD_MAX = 20


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
       encrypted vault: the alerts bot and its chat when Settings -> Alerts
       chose one, else Kazma's chat bot (``connectors.telegram.token``) to
       ``guard.telegram.chat_id`` (``ops_alerts.pager_credentials``). Read
       again at a page ``_NOTIFY_REFRESH_S`` later, so a change of bot or
       chat reaches the guard without a restart.

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
        self._held: list[tuple[float, str]] = []
        # Every page's outcome is heard in one place (``_heard``), and told to
        # two listeners: ``on_outcome`` gets the last page's outcome (the
        # guard keeps it in its state file, ``pager``, which Settings ->
        # Alerts reads; never the text), ``on_delivered`` the text of a held
        # page that went out after all (its page receipt now says delivered).
        self.last_outcome: dict | None = None
        self.on_outcome: Callable[[dict], None] | None = None
        self.on_delivered: Callable[[str], None] | None = None
        # Why the last post failed (``_post``), for its outcome.
        self._post_error = ""

    def _resolve(self) -> None:
        """Fill in missing credentials from the app's settings. Lazily, retried.

        Resolved once and never again used to mean one failed lookup -- the
        database still starting, say -- kept the guard silent until the guard
        itself restarted. A lookup that found nothing is tried again when a
        page is due, at most every ``_NOTIFY_RELOOKUP_S``.
        """
        now = time.monotonic()
        if self.token and self.chat:
            # Read from the settings: read again once they may have changed
            # (another bot or chat chosen). A lookup that finds nothing keeps
            # the ones that worked. Environment credentials are never re-read.
            if self._source != "vault" or (self._looked_up_at is not None
                                           and now - self._looked_up_at < _NOTIFY_REFRESH_S):
                return
            self._looked_up_at = now
            v_token, v_chat = self._from_config_store()
            if v_token and v_chat and (v_token, v_chat) != (self.token, self.chat):
                self.token, self.chat = v_token, v_chat
                self._log("info", "notify.credentials_changed", chat=v_chat)
            return
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

    def send(self, text: str) -> bool:
        """Page *text*. True when Telegram took it; False when it failed or
        is held until the credentials resolve. Either way its outcome is
        heard (``_heard``)."""
        if not self.configured:
            # Held, not dropped: the credentials live in Kazma's settings
            # database, so the guard cannot page while it is down -- the
            # moment a page matters most. After the 2026-10-17 Windows
            # reboot three "never became healthy" pages were dropped this
            # way while Postgres started (19 of 237 pages that week).
            if len(self._held) >= _NOTIFY_HELD_MAX:
                self._held.pop(0)
            self._held.append((time.time(), text))
            self._log("info", "notify.skipped", reason="not configured", text=text, held=True)
            self._heard(text, False, error="not configured", held=len(self._held))
            return False
        self._flush_held()
        return self._send_and_hear(text)

    def send_now(self, text: str) -> tuple[bool, str]:
        """Send *text* now, never held: ``(taken, why not)``. The test page
        (``PagerTest``) proves the path a page takes, so a guard that cannot
        page says so instead of keeping it for later."""
        if not self.configured:
            return False, self.describe()
        ok = self._send_and_hear(text)
        return ok, "" if ok else str((self.last_outcome or {}).get("error") or "not sent")

    def _send_and_hear(self, text: str, *, held_page: str = "") -> bool:
        """Post *text* and hear its outcome. *held_page*: the page a delayed
        post carries (``_flush_held``), whose outcome this is."""
        self._post_error = ""
        ok = self._post(text, delayed=bool(held_page)) is True
        self._heard(held_page or text, ok, error="" if ok else (self._post_error or "not sent"),
                    delayed=bool(held_page))
        return ok

    def _heard(self, text: str, ok: bool, *, error: str = "", delayed: bool = False,
               **extra: object) -> None:
        """The one place a page's outcome is recorded (``last_outcome``) and
        told: ``on_outcome`` always, ``on_delivered`` when a held page went
        out after all. Never raises: a record must never stop a page."""
        self.last_outcome = {
            "at": time.time(), "ok": bool(ok), "head": text.split("\n", 1)[0][:160],
            "source": self._source, "chat": self.chat, "delayed": bool(delayed), **extra,
        }
        if error:
            # The state file reaches a web page: never the bot's token, even
            # inside an error that quotes the request's address.
            self.last_outcome["error"] = (error.replace(self.token, "***") if self.token else error)[:200]
        if self.on_outcome is not None:
            try:
                self.on_outcome(dict(self.last_outcome))
            except (OSError, ValueError, TypeError):
                pass
        if ok and delayed and self.on_delivered is not None:
            try:
                self.on_delivered(text)
            except Exception as exc:  # a receipt must not stop the pager
                self._log("warn", "notify.receipt_failed", error=str(exc)[:200])

    def retry_lookup(self) -> None:
        """Let the next page look the credentials up again whatever the
        rate limit says: the guard calls it when Kazma becomes ready, which
        proves the settings database answers."""
        if not (self.token and self.chat):
            self._looked_up_at = None

    def flush_held(self) -> None:
        """Send held pages once the credentials resolve (rate-limited)."""
        if self._held and self.configured:
            self._flush_held()

    def _flush_held(self) -> None:
        held, self._held = self._held, []
        now = time.time()
        for at, text in held:
            minutes = max(1, round((now - at) / 60))
            self._send_and_hear(f"⏳ Delayed {minutes} min (the guard could not page then):\n{text}",
                                held_page=text)

    def _post(self, text: str, *, delayed: bool = False) -> bool:
        """Send *text*; True when Telegram took it (else ``_post_error`` says
        why). The log line names the card's first line (``head``): Settings ->
        Alerts lists the guard's pages from it."""
        lines = text.split("\n")
        head = (lines[1] if delayed and len(lines) > 1 else lines[0])[:160]
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            body = urllib.parse.urlencode(
                {"chat_id": self.chat, "text": text, "disable_web_page_preview": "true"}
            ).encode()
            req = _request(url, data=body, method="POST")
            with urllib.request.urlopen(req, timeout=10):
                pass
            if delayed:
                self._log("info", "notify.sent", delayed=True, head=head)
            else:
                self._log("info", "notify.sent", head=head)
            return True
        except Exception as exc:
            # An alert that fails must not take the supervisor down with it.
            self._log("warn", "notify.failed", error=str(exc)[:200], head=head)
            self._post_error = f"{type(exc).__name__}: {exc}"
            return False


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
        req = _request(_live_url(ready_url))
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
    *,
    form: str = "page",
    since: str = "",
    duration: str = "",
    link: str = "",
) -> str:
    """Operator-visible card. Layout matches kazma_core.observability.alert_card.

    Stdlib-only copy: the guard must not import kazma_core. The three forms
    (page, ``reminder``, ``recovery``) and the "Since ... · Open: ..." line
    are held to ``tests/fixtures/alert_card/cards.json`` with the other two
    copies (tests/test_alert_catalog.py).
    """
    sources = {"guard": "Guard", "ops": "Ops", "system": "System",
               "watch": "Watch", "cloudflare": "Cloudflare"}
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
    form = str(form or "page").strip().lower()
    sev_key = "success" if form == "recovery" else str(severity or "warn").strip().lower()
    icon = icon_overrides.get(src_key, {}).get(sev_key) or icons.get(sev_key, icons["warn"])
    head = str(title or "").strip() or severities.get(sev_key, "Warn")
    lasted = str(duration or "").strip()
    if form == "reminder":
        first = f"{icon} ⏰ Still: [{src}] {head}"
    elif form == "recovery":
        first = f"{icon} [{src}] {head} is back" + (f": it lasted {lasted}." if lasted else ".")
    else:
        first = f"{icon} [{src}] {head}"
    lines = [first]
    body = str(detail or "").strip()
    if body:
        lines.append(body)
    footer = []
    when = str(since or "").strip()
    if when and form != "recovery":
        footer.append(f"Since {when}" + (f" ({lasted})" if lasted else ""))
    target = str(link or "").strip()
    if target:
        footer.append(f"Open: {target}")
    if footer:
        lines.append(" · ".join(footer))
    return "\n".join(lines)


#: The guard's copy of Kazma's alert catalog (kazma_core/observability/
#: alert_catalog.py, written by ``python -m kazma_core.observability.
#: alert_catalog --write``). Every page names its kind there; the kinds of
#: the ``_page`` calls below are held to it by tests/test_alert_catalog.py.
ALERT_CATALOG_PATH = Path(__file__).resolve().with_name("alert_catalog.json")
_alert_catalog: dict[str, dict] | None = None
_UNKNOWN_KINDS_SAID: set[str] = set()


def guard_alert_kinds() -> dict[str, dict]:
    """The guard's entries of the alert catalog, by kind. Read once; {} when
    the copy cannot be read (a page is sent either way)."""
    global _alert_catalog
    if _alert_catalog is None:
        try:
            data = json.loads(ALERT_CATALOG_PATH.read_text(encoding="utf-8"))
            _alert_catalog = {str(a["key"]): a for a in data["alerts"] if a.get("sender") == "guard"}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            _alert_catalog = {}
    return _alert_catalog


# -- one page per problem: holds and page receipts (unified alerts, Stage 2) --
#
# Kazma, this guard and Kazma Watch can each see some problems (a database
# outage, a restart, a full disk), and each paged on its own. Now a page
# whose catalog entry has a condition goes through _inside_hold first (a
# stdlib copy of alert_catalog.inside_hold, both held to
# tests/fixtures/alert_receipts/holds.json): the owner pages, a non-owner
# records it while an owner can speak, and of two inside owners the first to
# page it pages. A page that opens an incident is a receipt (its catalog key,
# when, whether a channel took it) until its recovery; Kazma writes its own
# to the file handed over in KAZMA_GUARD_PAGES_FILE, this guard keeps its
# own in its state file (``pages``), and the heartbeat carries both, so Kazma
# Watch stays quiet about what was already told.

#: Receipts the heartbeat carries at most.
PAGE_RECEIPTS_MAX = 16
#: An open receipt older than this is dropped: its recovery never came.
PAGE_RECEIPT_MAX_AGE_S = 24 * 3600.0
#: Kazma Watch watches when it answered a heartbeat this recently.
WATCH_ANSWERED_S = 180.0
_alert_table_cache: dict[str, dict] | None = None
_RECEIPT_KEY_RE = re.compile(r"^[a-z_]+(\.[a-z_:{}]+)+$")


def _pages_path() -> Path:
    """Where the server writes its page receipts (page_receipts.py): beside
    the state file, as the server finds it when only that is handed over."""
    env = os.environ.get("KAZMA_GUARD_PAGES_FILE")
    if env:
        return Path(env)
    return _state_path().with_name("kazma_pages.json")


def _alert_table() -> dict[str, dict]:
    """Every entry of the guard's copy of the catalog, by key ({} unread)."""
    global _alert_table_cache
    if _alert_table_cache is None:
        try:
            data = json.loads(ALERT_CATALOG_PATH.read_text(encoding="utf-8"))
            _alert_table_cache = {str(a["key"]): a for a in data["alerts"]}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            _alert_table_cache = {}
    return _alert_table_cache


def _inside_hold(key: str, receipts: list[dict], speaks: dict[str, bool]) -> str:
    """Who pages *key*'s condition instead of its sender; "" to page it.

    The stdlib copy of ``alert_catalog.inside_hold`` (the guard cannot import
    kazma_core): a non-owner records while an owning sender can speak; an
    owner records when another entry of its condition has an open page that
    reached someone.
    """
    table = _alert_table()
    entry = table.get(key)
    if not entry or not entry.get("condition"):
        return ""
    condition = entry["condition"]
    if not entry.get("owner", True):
        owners = sorted({str(e.get("sender")) for e in table.values()
                         if e.get("condition") == condition and e.get("owner", True)})
        return next((owner for owner in owners if speaks.get(owner)), "")
    for receipt in receipts or ():
        other = table.get(str(receipt.get("key") or ""))
        if (other and other.get("key") != key and other.get("condition") == condition
                and receipt.get("delivered") is True):
            return str(other.get("key"))
    return ""


def _opens_incident(entry: dict | None) -> bool:
    return bool(entry and entry.get("sender") in ("ops", "guard")
                and (entry.get("closed_by") or entry.get("condition")))


def receipts_allowlist(pages: object) -> list[dict]:
    """Page receipts cut to what may leave the machine: a catalog key of an
    inside sender, a time, whether it was delivered. Nothing when the copy of
    the catalog cannot be read (a key is checked against it, not trusted)."""
    table = _alert_table()
    if not table or not isinstance(pages, list):
        return []
    out: dict[str, dict] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        key, at = page.get("key"), page.get("paged_at")
        entry = table.get(key) if isinstance(key, str) and _RECEIPT_KEY_RE.match(key) else None
        if not _opens_incident(entry) or isinstance(at, bool) or not isinstance(at, (int, float)):
            continue
        if not 1_000_000_000 <= at < 10_000_000_000:
            continue
        out[key] = {"key": key, "paged_at": int(at), "delivered": page.get("delivered") is True}
    return sorted(out.values(), key=lambda r: r["key"])[:PAGE_RECEIPTS_MAX]


class PageReceipts:
    """The guard's open page receipts, and Kazma's (read from its file)."""

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}
        # Pages held by the notifier (no credentials yet): text -> receipt key.
        self._waiting: dict[str, tuple[str, int]] = {}

    def paged(self, key: str, delivered: bool, text: str, *, now: float | None = None) -> None:
        """This guard sent *key*: keep its receipt, or settle what it closes."""
        table = _alert_table()
        entry = table.get(key)
        closes = [k for k, e in table.items() if e.get("closed_by") and e.get("closed_by") == key]
        changed = bool([k for k in closes if self._open.pop(k, None) is not None])
        if _opens_incident(entry):
            at = int(time.time() if now is None else now)
            self._open[key] = {"key": key, "paged_at": at, "delivered": bool(delivered)}
            if not delivered:
                self._waiting[text] = (key, at)
                while len(self._waiting) > _NOTIFY_HELD_MAX:  # the notifier holds no more
                    self._waiting.pop(next(iter(self._waiting)))
            changed = True
        if changed:
            self._write()

    def delivered_later(self, text: str) -> None:
        """A held page went out (Notifier.on_delivered): its receipt says so."""
        key, at = self._waiting.pop(text, (None, 0))
        rec = self._open.get(key) if key else None
        if rec is not None and rec["paged_at"] == at:
            rec["delivered"] = True
            self._write()

    def own(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        for key in [k for k, r in self._open.items() if now - r["paged_at"] > PAGE_RECEIPT_MAX_AGE_S]:
            self._open.pop(key, None)
        return sorted((dict(r) for r in self._open.values()), key=lambda r: r["key"])

    @staticmethod
    def kazma(spawned_at: float, *, now: float | None = None) -> list[dict]:
        """The receipts of the server this guard runs now: a file an earlier
        server wrote says nothing about this one."""
        try:
            data = json.loads(_pages_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or not spawned_at:
            return []
        started = data.get("process_started")
        if not isinstance(started, (int, float)) or started < spawned_at - 1.0:
            return []
        now = time.time() if now is None else now
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        return [p for p in receipts_allowlist(pages) if now - p["paged_at"] <= PAGE_RECEIPT_MAX_AGE_S]

    def clear(self) -> None:
        self._open.clear()
        self._waiting.clear()
        self._write()

    def _write(self) -> None:
        _update_state(pages=self.own())


def _failing_checks(data: dict) -> list[str]:
    """Named failed ready-checks, with error text when the body has it: every
    check that failed, and every REQUIRED one (``unavailable_capabilities``)
    that is not ok -- a required check that is only "degraded" (no provider
    key) holds readiness too, and the log said a bare "not_ready" for it."""
    required = data.get("unavailable_capabilities")
    required = {str(n) for n in required} if isinstance(required, list) else set()
    parts: list[str] = []
    for key, val in (data.get("checks") or {}).items():
        if not isinstance(val, dict):
            continue
        status = str(val.get("status") or "").lower()
        if status != "failed" and not (key in required and status not in ("ok", "healthy")):
            continue
        err = str(val.get("error") or val.get("detail") or "").strip()
        parts.append(f"{key}: {err}" if err else str(key))
    return parts


def _needs_setup(data: object) -> tuple[str, ...]:
    """The required checks that wait for the owner (``needs_setup`` in a
    readiness body: a provider key). Names only, as ``_check_names``."""
    names = data.get("needs_setup") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return ()
    return tuple(sorted({n for n in names if isinstance(n, str) and 0 < len(n) <= 40
                         and set(n) <= _CHECK_NAME_CHARS}))[:8]


def _waits_for(data: object) -> tuple[str, ...]:
    """What Kazma waits for that no restart brings back (``waits_for`` in a
    readiness body: ``{check: dependency}``, said only when every failing
    required check waits for the owner or for such a dependency). The
    dependencies' names only, as ``_check_names``: ``("docker",)``."""
    waits = data.get("waits_for") if isinstance(data, dict) else None
    if not isinstance(waits, dict):
        return ()
    return tuple(sorted({d for d in waits.values() if isinstance(d, str) and 0 < len(d) <= 40
                         and set(d) <= _CHECK_NAME_CHARS}))[:8]


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


class ProbeResult(tuple):
    """``(healthy, detail)`` -- it unpacks as before -- plus whether Kazma
    answered at all, and whether it says only a restart clears its failure.

    A 503 naming a failing check is an answer: the server is alive and says
    its database is not. A timeout or a refused connection is not. An answer
    carrying ``restart_required`` (the volatile settings store: a boot while
    the database was away stays in memory for the life of the process) is
    not an outage to ride out.
    """

    answered: bool
    restart_required: bool
    database: dict | None
    # The NAMES of the checks the answer says failed (or are degraded):
    # what the outside watch may carry, never the detail's error text.
    checks: tuple[str, ...]
    # Not ready only because the owner has something to set up (a provider
    # key): Kazma is up, and no restart can fix it.
    needs_setup: tuple[str, ...]
    # Not ready only because of what the owner sets up or a dependency
    # outside Kazma (the Docker engine): Kazma is up and serves what does not
    # need it, and no restart brings the dependency back.
    waits_for: tuple[str, ...]

    def __new__(cls, healthy: bool, detail: str, answered: bool = True,
                restart_required: bool = False,
                database: dict | None = None,
                checks: tuple[str, ...] = (),
                needs_setup: tuple[str, ...] = (),
                waits_for: tuple[str, ...] = ()) -> ProbeResult:
        obj = super().__new__(cls, (healthy, detail))
        obj.answered = answered
        obj.restart_required = restart_required
        obj.database = database
        obj.checks = tuple(checks)
        obj.needs_setup = tuple(needs_setup)
        obj.waits_for = tuple(waits_for)
        return obj


_CHECK_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")


def _check_names(raw_or_data: object, *, failed_only: bool = True) -> tuple[str, ...]:
    """The names of the readiness checks that did not pass, from a body
    (text or parsed): failed ones, or (``failed_only=False``) every one not
    ``ok``. Names only, lowercase words, at most eight."""
    data = raw_or_data
    if isinstance(raw_or_data, str):
        try:
            data = json.loads(raw_or_data)
        except ValueError:
            return ()
    if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
        return ()
    required = data.get("unavailable_capabilities")
    required = {str(n) for n in required} if isinstance(required, list) else set()
    names = []
    for key, val in data["checks"].items():
        if not isinstance(val, dict) or not isinstance(key, str):
            continue
        status = str(val.get("status") or "").lower()
        bad = (status == "failed" or (key in required and status not in ("ok", "healthy"))
               if failed_only else status not in ("ok", "healthy", "not_initialized"))
        if bad and 0 < len(key) <= 40 and set(key) <= _CHECK_NAME_CHARS:
            names.append(key)
    return tuple(sorted(set(names))[:8])


# The readiness body names every check; with the database state it passes
# 4 KB, and a body cut short parses as nothing.
_PROBE_BODY_MAX = 32768


def _readiness_fields(raw: str) -> tuple[bool, dict | None]:
    """``restart_required`` and the server's database state from a readiness
    body (resilience plan A3: ``database_availability``)."""
    try:
        data = json.loads(raw)
    except Exception:
        return False, None
    if not isinstance(data, dict):
        return False, None
    db = data.get("database_availability")
    return data.get("restart_required") is True, db if isinstance(db, dict) else None


def _says_restart_required(raw: str) -> bool:
    """The readiness body names a failure only a restart clears."""
    return _readiness_fields(raw)[0]


def _setup_in(raw: str) -> tuple[str, ...]:
    """``_needs_setup`` of a readiness body given as text."""
    try:
        return _needs_setup(json.loads(raw))
    except ValueError:
        return ()


def _waits_in(raw: str) -> tuple[str, ...]:
    """``_waits_for`` of a readiness body given as text."""
    try:
        return _waits_for(json.loads(raw))
    except ValueError:
        return ()


def _answered_waits(result: object) -> tuple[str, ...]:
    """What a probe that Kazma answered says it waits for, outside Kazma; ()
    for no answer, or an answer that only a restart clears."""
    if not getattr(result, "answered", False) or getattr(result, "restart_required", False):
        return ()
    return tuple(getattr(result, "waits_for", ()) or ())


def probe(url: str, timeout: float) -> ProbeResult:
    """Return (healthy, detail). Any non-200 or exception is unhealthy."""
    try:
        req = _request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_PROBE_BODY_MAX).decode("utf-8", "replace")
            if resp.status != 200:
                restart, db = _readiness_fields(raw)
                return ProbeResult(False, _health_failure_detail(raw, http_status=resp.status),
                                   restart_required=restart, database=db,
                                   checks=_check_names(raw), needs_setup=_setup_in(raw),
                                   waits_for=_waits_in(raw))
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
            return ProbeResult(True, "200 (unparsed body)")
        if not isinstance(data, dict):
            return ProbeResult(True, "200 (unparsed body)")
        db = data.get("database_availability")
        db = db if isinstance(db, dict) else None
        status = str(data.get("status", "")).lower()
        if status == "not_ready":
            failing = _failing_checks(data)
            return ProbeResult(False, "; ".join(failing[:6]) if failing else "not_ready",
                               restart_required=data.get("restart_required") is True,
                               database=db, checks=_check_names(data),
                               needs_setup=_needs_setup(data), waits_for=_waits_for(data))
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
        return ProbeResult(True, detail, database=db,
                           checks=_check_names(data, failed_only=False))
    except urllib.error.HTTPError as exc:
        # urlopen raises on 503. The JSON body names the failing check;
        # do not format this as unreachable: Service Unavailable.
        raw = ""
        try:
            raw = exc.read(_PROBE_BODY_MAX).decode("utf-8", "replace")
        except Exception:
            raw = ""
        restart, db = _readiness_fields(raw)
        return ProbeResult(False, _health_failure_detail(raw, http_status=int(exc.code or 0)),
                           restart_required=restart, database=db,
                           checks=_check_names(raw), needs_setup=_setup_in(raw),
                           waits_for=_waits_in(raw))
    except urllib.error.URLError as exc:
        return ProbeResult(False, f"unreachable: {getattr(exc, 'reason', exc)}", answered=False)
    except Exception as exc:  # noqa: BLE001 -- a probe must never raise
        return ProbeResult(False, f"probe error: {exc}", answered=False)


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


#: The server's stderr, beside the guard's log. Its application log is a file
#: of its own; stderr is what is left when that cannot speak -- an uncaught
#: exception, a native crash (serve.py enables faulthandler), a refusal at
#: boot. Under the scheduled task the guard's own stderr goes nowhere, so a
#: server that exited with code 1 on 2026-09-25 left no trace at all.
SERVER_STDERR_MAX_BYTES = 2 * 1024 * 1024


def _server_stderr_path() -> Path:
    return _default_log_path().with_name("server.stderr.log")


def _open_server_stderr(log: GuardLog):
    """The file the next server's stderr goes to, and where its part starts."""
    path = _server_stderr_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_large(path, SERVER_STDERR_MAX_BYTES)
        fh = path.open("ab")
        return fh, fh.tell()
    except OSError as exc:
        log("warn", "child.stderr_unavailable", path=str(path), error=str(exc))
        return None, 0


def server_stderr_tail(proc: subprocess.Popen | None, max_lines: int = 20,
                       max_chars: int = 1500) -> str:
    """The last lines this server wrote to stderr, or ""."""
    path = getattr(proc, "kazma_stderr_path", None)
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(int(getattr(proc, "kazma_stderr_offset", 0)))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-max_lines:])[-max_chars:]


def spawn(cmd: list[str], cwd: Path, log: GuardLog,
          extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    # The server reads the guard's heartbeat from here to notice when it is
    # left running with no guard (kazma_core.observability.supervisor_watch).
    env = dict(os.environ)
    env["KAZMA_GUARD_STATE_FILE"] = str(_state_path())
    # ...and where to ask for a reload (Settings' "Restart server" goes
    # through the guard: supervisor_watch.request_guard_reload).
    env["KAZMA_GUARD_RELOAD_FILE"] = str(_reload_path())
    # ...and the stop's budget: its whole graceful stop, less a margin for
    # the process to exit (FA-261: each step had its own bound, and their sum
    # did not fit the 60 s grace).
    env["KAZMA_SHUTDOWN_BUDGET_S"] = str(max(5.0, GRACEFUL_STOP_S - SHUTDOWN_MARGIN_S))
    # ...and where to say what its boot waits for (_boot_status_path): with
    # it, the boot waits for its database as long as the guard lets it.
    _clear_boot_status()
    env["KAZMA_GUARD_BOOT_FILE"] = str(_boot_status_path())
    # ...and where to write what it paged (its page receipts: one page per
    # problem, PageReceipts).
    env["KAZMA_GUARD_PAGES_FILE"] = str(_pages_path())
    # ...and the document worker broker's address, token file and request
    # file (DocumentBroker.server_env), so .env needs nothing for it.
    env.update(extra_env or {})
    kwargs: dict = {"cwd": str(cwd), "env": env}
    if os.name == "nt":
        # Own process group so the child and ITS children (serve.py spawns
        # uvicorn) can be signalled and killed as a unit.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    stderr_fh, offset = _open_server_stderr(log)
    if stderr_fh is not None:
        kwargs["stderr"] = stderr_fh
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        if stderr_fh is not None:
            stderr_fh.close()  # the child holds its own handle
    if stderr_fh is not None:
        proc.kazma_stderr_path = str(_server_stderr_path())  # type: ignore[attr-defined]
        proc.kazma_stderr_offset = offset  # type: ignore[attr-defined]
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


# -- document worker broker -------------------------------------------
#
# Untrusted documents can run in a fresh, attested container per job (the
# "container" profile, kazma_core/documents/worker_container.py). Docker is
# reached by one narrow process, the broker
# (kazma_core/documents/worker_broker.py); Kazma talks to it over loopback
# with a token. Under docker compose the broker is its own service. On a
# native install nothing ran it: on 2026-10-15 the live install required
# containers (security.execution.container_required), no broker ran, and
# every document was refused. The guard runs it now, beside Kazma:
#
#   * Kazma decides whether it needs one (its profile setting, or
#     KAZMA_DOCUMENT_WORKER_ISOLATION) and says so in a request file the guard
#     hands it (KAZMA_GUARD_BROKER_REQUEST_FILE); the guard starts or stops
#     the broker within a second. The request outlives restarts, so after one
#     the broker starts before Kazma does.
#   * The guard hands Kazma the broker's loopback URL and token file
#     (KAZMA_DOCUMENT_BROKER_URL / _TOKEN_FILE), as it hands the state file:
#     .env needs nothing. A .env that names another broker wins; the guard then
#     runs none (Kazma's request names that other URL).
#   * The broker asks for the worker image built for the code on disk
#     (kazma_core/documents/worker_image.py, built by
#     scripts/document_worker_image.py), and serves the newest one built when
#     that is missing, until it appears.
#   * It is restarted when it exits or stops answering, with a backoff; the
#     guard pages once when it cannot keep it running, and records it all in
#     its state file (``document_broker``) for Kazma's readiness.

BROKER_MODE_ENV = "KAZMA_GUARD_DOCUMENT_BROKER"
BROKER_MODES = ("auto", "on", "off")
#: Between restarts of a broker that crashed, by consecutive failure.
BROKER_BACKOFF_S = (5, 15, 30, 60, 120, 300)
#: A missing image or a Docker engine that does not answer is checked again
#: this often: the checks are cheap, and the fix is the operator's.
BROKER_RETRY_S = 30.0
#: Spawned to answering ready: the broker attests its policy and asks the
#: image for its engines, a container run each.
BROKER_START_BUDGET_S = 600.0
BROKER_PROBE_INTERVAL_S = 30.0
#: Probes a ready broker may miss in a row before it is restarted.
BROKER_FAILURES_TO_KILL = 3
#: Failed starts in a row before the one page that says the guard cannot keep
#: the broker running.
BROKER_PAGE_AFTER = 3
#: Docker not answering this long is paged once.
BROKER_ENGINE_PAGE_AFTER_S = 600.0
#: A stop asks the broker to end its jobs and remove their containers first.
BROKER_STOP_GRACE_S = 45.0
#: worker_broker.py's exit codes the guard tells apart.
BROKER_EXIT_NO_IMAGE, BROKER_EXIT_NO_ENGINE, BROKER_EXIT_NEWER_IMAGE = 4, 5, 6
#: The address it was given cannot be listened on: the guard moves it.
BROKER_EXIT_PORT_UNAVAILABLE = 7
#: Ports in a row that could not be listened on before the guard stops moving
#: and backs off (something refuses every port, not just one).
BROKER_PORT_MOVES = 3
BROKER_LOG_MAX_BYTES = 2 * 1024 * 1024
#: Everything else in the guard's environment stays out of the broker, the
#: one process with authority over Docker: the guard's own secrets
#: (KAZMA_GUARD_TELEGRAM_TOKEN) included. What Python, Windows and the docker
#: CLI need (its config lives under the user profile).
_BROKER_ENV_KEEP = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMW6432", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY", "KAZMA_DOCKER_BIN", "LANG", "LC_ALL", "TZ",
)
_BROKER_REQUEST_MAGIC = b"KZDB-REQ/1\n"


def _broker_mode() -> str:
    raw = (os.environ.get(BROKER_MODE_ENV) or "auto").strip().lower()
    return raw if raw in BROKER_MODES else "auto"


def _broker_file(name: str) -> Path:
    """Beside the guard's state file (``<install>/.kazma``)."""
    return _state_path().with_name(name)


def _broker_log_path() -> Path:
    return _default_log_path().with_name("document_broker.log")


def _broker_script() -> Path:
    named = (os.environ.get("KAZMA_GUARD_BROKER_SCRIPT") or "").strip()
    if named:
        return Path(named)
    return REPO_ROOT / "kazma-core" / "kazma_core" / "documents" / "worker_broker.py"


def _broker_name() -> str:
    """Names this install's job containers (the broker's sweep removes only
    its own name's): two installs on one machine never remove each other's."""
    digest = hashlib.sha256(str(REPO_ROOT).lower().encode("utf-8")).hexdigest()
    return f"guard-{digest[:8]}"


def _port_is_free(port: int) -> bool:
    """Could the broker listen on *port* now? Asked the way the broker binds
    (worker_broker._TcpServer): with SO_REUSEADDR on POSIX, where a port whose
    last connections are in TIME_WAIT is free to a restarted broker; never on
    Windows, where the option would let a bind take a port in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _named_broker_port() -> int | None:
    """The port the operator pinned (``KAZMA_GUARD_BROKER_PORT``), or None."""
    named = (os.environ.get("KAZMA_GUARD_BROKER_PORT") or "").strip()
    return int(named) if named else None


def _choose_broker_port(preferred: int | None, *, avoid: frozenset[int] = frozenset()) -> int:
    """The broker's loopback port: KAZMA_GUARD_BROKER_PORT, else the one used
    before, else one derived from the install folder, below the ephemeral
    ranges (an outgoing connection can take an ephemeral port while the
    broker restarts): the first free one from there, through all of 20000 to
    29999. *avoid* are ports the broker could not listen on.

    The search covered 64 ports. Windows reserves ports in blocks of a hundred
    (Hyper-V and WinNAT, when Docker Desktop starts): live 2026-10-31 it took
    20181-20780 after a Docker restart, the recorded port 20527 and the 64
    after it with it, and the broker could not start for hours."""
    named = _named_broker_port()
    if named is not None:
        return named
    if preferred and preferred not in avoid and _port_is_free(preferred):
        return preferred
    digest = hashlib.sha256(str(REPO_ROOT).lower().encode("utf-8")).hexdigest()
    base = 20000 + int(digest[:8], 16) % 10000
    for step in range(10000):
        port = 20000 + (base - 20000 + step) % 10000
        if port not in avoid and _port_is_free(port):
            return port
    return base


def _current_user_sid() -> str:
    """The SID of the account this process runs as (Windows), or ""."""
    try:
        out = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    sid = out.rsplit(",", 1)[-1].strip().strip('"')
    return sid if sid.startswith("S-") else ""


def _restrict_to_owner(path: Path) -> bool:
    """Only the account that runs the guard (and so Kazma and the broker) may
    read *path*. True when the restriction was applied."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False
    sid = _current_user_sid()
    if not sid:
        return False
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _file_sha(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


_WORKER_IMAGE_MODULE = None


def _worker_image():
    """kazma_core/documents/worker_image.py, loaded by path (standard library
    only, like process_priority.py: the guard never imports the app)."""
    global _WORKER_IMAGE_MODULE
    if _WORKER_IMAGE_MODULE is None:
        import importlib.util

        path = REPO_ROOT / "kazma-core" / "kazma_core" / "documents" / "worker_image.py"
        name = "kazma_guard_worker_image"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _WORKER_IMAGE_MODULE = module
    return _WORKER_IMAGE_MODULE


def broker_status(port: int, token: str, timeout: float = 3.0) -> dict | None:
    """The broker's own status report (its ``status`` operation), or None."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            header = json.dumps({"protocol": 1, "token": token, "op": "status"}).encode("utf-8")
            sock.sendall(_BROKER_REQUEST_MAGIC + struct.pack(">I", len(header)) + header)
            head = _recv_all(sock, 5)
            kind, size = head[:1], struct.unpack(">I", head[1:])[0]
            if kind != b"S" or size > 1024 * 1024:
                return None
            data = json.loads(_recv_all(sock, size).decode("utf-8"))
    except (OSError, ValueError, struct.error):
        return None
    return data if isinstance(data, dict) else None


def _spawn_broker(cmd: list[str], kwargs: dict) -> subprocess.Popen:
    return subprocess.Popen(cmd, **kwargs)


def _ask_broker_to_stop(proc: subprocess.Popen) -> None:
    """CTRL_BREAK to its process group (Windows; Python raises SIGBREAK,
    which the broker handles), SIGTERM to it elsewhere. Raises OSError."""
    if os.name == "nt":
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)


def _kill_broker_tree(proc: subprocess.Popen) -> None:
    """The broker and what it started (docker CLI processes). Raises OSError."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=30, check=False)
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _recv_all(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data.extend(chunk)
    return bytes(data)


def _tick_safely(tick: Callable[[], None], internal: Callable[[BaseException], None]) -> None:
    """One pass of a guard feature (the document broker, the dependencies,
    the database service, the disk, the outside watch). A feature never
    raises into the guard's loop: whatever escapes it is logged as that
    feature's internal error and the guard keeps supervising."""
    try:
        tick()
    except Exception as exc:  # noqa: BLE001 -- a feature's fault must not stop supervision
        internal(exc)


class DocumentBroker:
    """The document worker broker beside Kazma (see the section comment).

    Driven from the guard's own loop -- :meth:`tick` runs in every wait the
    guard makes, about once a second -- so the guard stays one thread. Never
    raises out of a tick: broker supervision must not take down the
    supervision of Kazma itself.
    """

    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self.log = guard.log
        self.mode = _broker_mode()
        self.proc: subprocess.Popen | None = None
        self.state = "off" if self.mode == "off" else "idle"
        self.why = "disabled" if self.mode == "off" else "no_request"
        self.wanted = False
        self.detail = ""
        self.port: int | None = None
        # Addresses this guard handed Kazma before moving the broker: a
        # request naming one is still Kazma asking for this broker.
        self._retired_urls: set[str] = set()
        self._bad_ports: set[int] = set()
        self._port_moves = 0
        self.token = ""
        self.image_requested = ""
        self.fallback = False
        self.script_sha = ""
        self.restarts = 0
        self.failures = 0
        self.last_exit: int | None = None
        self.started_at = 0.0
        self.ready_at = 0.0
        self._spawned = 0.0
        self._next_start: float | None = None
        self._next_probe = 0.0
        self._probe_failures = 0
        self._request_seen: tuple[int, int] | None = None
        self._episode = ""          # the failure condition being retried quietly
        self._episode_since = 0.0
        self._paged = ""            # the condition paged; cleared (and recovered) when ready
        self._status: dict = {}
        self._stopping = False
        self._created: float | None = None
        # Maintenance: wanted or not, nothing starts until the next spawn.
        self.held = False

    # -- what Kazma is handed -----------------------------------------

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}" if self.port else ""

    @property
    def token_path(self) -> Path:
        return _broker_file("document_broker.token")

    @property
    def request_path(self) -> Path:
        return _broker_file("document_broker.request.json")

    def _ensure_port(self) -> None:
        if self.port is None:
            recorded = (_read_state().get("document_broker") or {}).get("port")
            recorded = recorded if isinstance(recorded, int) else None
            self.port = _choose_broker_port(recorded)
            if recorded and recorded != self.port:
                # Kazma's last request may still name the old address.
                self._retired_urls.add(f"tcp://127.0.0.1:{recorded}")

    def server_env(self) -> dict[str, str]:
        """What the server needs to use and ask for the broker (empty when off)."""
        if self.mode == "off":
            return {}
        self._ensure_port()
        return {
            "KAZMA_DOCUMENT_BROKER_URL": self.url,
            # The address may move (a port Windows reserved): Kazma then reads
            # the current one from this guard's state file.
            "KAZMA_DOCUMENT_BROKER_URL_SOURCE": "guard",
            "KAZMA_DOCUMENT_BROKER_TOKEN_FILE": str(self.token_path),
            "KAZMA_GUARD_BROKER_REQUEST_FILE": str(self.request_path),
        }

    # -- the request ---------------------------------------------------

    def _request_signature(self) -> tuple[int, int] | None:
        try:
            st = self.request_path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _read_request(self) -> dict | None:
        try:
            data = json.loads(self.request_path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _decide(self) -> tuple[bool, str]:
        self._request_seen = self._request_signature()
        if self.mode == "off":
            return False, "disabled"
        if self.mode == "on":
            return True, "forced"
        req = self._read_request()
        if req is None:
            return False, "no_request"
        if req.get("want") is not True:
            return False, "not_requested"
        self._ensure_port()
        if str(req.get("url") or "") not in {self.url, *self._retired_urls}:
            # Kazma uses another broker (its .env names one): not this one.
            return False, "external"
        return True, "requested"

    # -- lifecycle -------------------------------------------------------

    def on_guard_start(self) -> None:
        """Reap a broker an earlier guard left, keep the port it used, record."""
        self.reap_orphan()
        if self.mode != "off":
            self._ensure_port()
        self._write()

    def reap_orphan(self) -> None:
        """A broker left by a guard that was killed without cleanup."""
        rec = _read_state().get("document_broker") or {}
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            return
        if not pid or pid == os.getpid() or not _pid_alive(pid):
            return
        if not _recorded_child_is_ours(pid, {"child_created": rec.get("created")}, self.log):
            return
        self.log("warn", "broker.orphan_reaping", pid=pid)
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=30, check=False)
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log("error", "broker.orphan_reap_failed", pid=pid, error=str(exc)[:200])
            return
        self.log("info", "broker.orphan_reaped", pid=pid)

    def before_spawn(self) -> None:
        """Before Kazma starts: run the broker if it is wanted (it then warms up
        while Kazma boots), and restart one whose code or image changed."""
        try:
            self.held = False
            self._refresh("spawn")
            if self.proc is not None and self.proc.poll() is None:
                image, fallback, sha = self._planned()
                if image != self.image_requested or sha != self.script_sha or not self._token_ok():
                    self.log("info", "broker.code_changed", image=image,
                             was=self.image_requested)
                    self.stop("code changed")
                    self._start()
        except Exception as exc:  # noqa: BLE001 -- see the class docstring
            self._internal(exc)

    def on_pause(self) -> None:
        """Maintenance: nothing of Kazma runs while the guard holds."""
        self.held = True
        self._next_start = None
        if self.proc is not None:
            self.stop("maintenance")

    def shutdown(self) -> None:
        if self.proc is not None:
            self.stop("guard shutting down")

    def tick(self) -> None:
        _tick_safely(self._tick, self._internal)

    def _internal(self, exc: BaseException) -> None:
        self.log("error", "broker.internal_error", error=f"{type(exc).__name__}: {exc}"[:300])

    def _refresh(self, reason: str) -> None:
        if self.mode != "off":
            self._ensure_port()
        wanted, why = self._decide()
        changed = wanted != self.wanted or why != self.why
        self.wanted, self.why = wanted, why
        if changed:
            self.log("info", "broker.wanted" if wanted else "broker.not_wanted",
                     why=why, reason=reason, url=self.url)
        if not wanted:
            if self.proc is not None:
                self.stop("not wanted")
            self.state = "off" if self.mode == "off" else "idle"
            self._next_start = None
            self._episode = ""
            self._write()
            return
        if self.proc is None and self._next_start is None and not self.held:
            self._start()
        elif changed:
            self._write()

    def _tick(self) -> None:
        if self.mode == "off":
            return
        if self._request_signature() != self._request_seen:
            self._refresh("request")
        now = time.monotonic()
        if self.proc is not None:
            code = self.proc.poll()
            if code is not None:
                self._exited(code)
                return
            if now < self._next_probe:
                return
            if self.state == "starting":
                self._next_probe = now + 2.0
                status = broker_status(self.port or 0, self.token)
                if status is not None and status.get("ready") is True:
                    self._became_ready(status)
                elif now - self._spawned >= BROKER_START_BUDGET_S:
                    self.log("error", "broker.never_ready", budget_s=BROKER_START_BUDGET_S,
                             tail=self._log_tail())
                    self.stop("never ready")
                    self._failed("never answered ready", None)
                return
            self._next_probe = now + BROKER_PROBE_INTERVAL_S
            status = broker_status(self.port or 0, self.token)
            if status is not None and status.get("ready") is True:
                self._probe_failures = 0
                self._status = status
                return
            self._probe_failures += 1
            self.log("warn", "broker.health_failed", consecutive=self._probe_failures,
                     answered=status is not None)
            if self._probe_failures >= BROKER_FAILURES_TO_KILL:
                self.stop("not answering")
                self._failed("stopped answering", None)
            return
        if self.wanted and not self.held and self._next_start is not None and now >= self._next_start:
            self._next_start = None
            self._start()

    def _planned(self) -> tuple[str, bool, str]:
        """(image to ask for, fall back to the newest built?, script digest)."""
        script = _broker_script()
        sha = _file_sha(script, script.parent.parent / "docker_cli.py")
        pinned = (os.environ.get("KAZMA_GUARD_BROKER_IMAGE") or "").strip()
        if pinned:
            return pinned, False, sha
        try:
            module = _worker_image()
            image = module.expected_tag(REPO_ROOT)
        except (OSError, ImportError, AttributeError) as exc:
            self.log("warn", "broker.image_unknown", error=str(exc)[:200])
            image = "kazma-document-worker:src-unknown"
        return image, True, sha

    def _token_ok(self) -> bool:
        try:
            return len(self.token_path.read_text(encoding="utf-8").strip()) >= 32
        except OSError:
            return False

    def _ensure_token(self) -> str:
        """The broker's token, readable by this account only -- or OSError,
        and the broker does not start (``_start`` counts it as a failed
        start). Until 2026-10-15 a restriction that failed only logged a
        warning and the secret was written anyway, and a token file already
        there was used as it was (Q04 defensive check C5)."""
        path = self.token_path
        if self._token_ok():
            # Restricted again at every start: a file an older build left, or
            # one whose access list changed since, must not be served from.
            if not _restrict_to_owner(path):
                raise OSError("the broker token file could not be restricted to this account")
            return path.read_text(encoding="utf-8").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(48)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        # The empty file is restricted before the secret is in it; the
        # rename keeps its access list. No restriction, no secret.
        if not _restrict_to_owner(tmp):
            try:
                tmp.unlink()
            except OSError:
                pass
            raise OSError("the broker token file could not be restricted to this account; "
                          "no token was written")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(token)
        os.replace(tmp, path)
        self.log("info", "broker.token_created", path=str(path), owner_only=True)
        return token

    def _start(self) -> None:
        if self._stopping:
            return
        self._ensure_port()
        try:
            self.token = self._ensure_token()
        except OSError as exc:
            self.log("error", "broker.token_unavailable", error=str(exc)[:200])
            self._failed("token file unavailable", None)
            return
        image, fallback, sha = self._planned()
        self.image_requested, self.fallback, self.script_sha = image, fallback, sha
        python = self.guard.server_python or sys.executable
        cmd = [python, "-I", str(_broker_script()), "--image", image,
               "--token-file", str(self.token_path), "--listen", f"tcp:127.0.0.1:{self.port}",
               "--name", _broker_name()]
        if fallback:
            cmd.append("--fallback-to-newest-built")
        env = {k: v for k, v in os.environ.items() if k.upper() in _BROKER_ENV_KEEP}
        kwargs: dict = {"cwd": str(self.guard.cwd), "env": env, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        log_path = _broker_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_large(log_path, BROKER_LOG_MAX_BYTES)
            fh = log_path.open("ab")
        except OSError:
            fh = None
        if fh is not None:
            kwargs["stdout"] = fh
            kwargs["stderr"] = subprocess.STDOUT
        try:
            self.proc = _spawn_broker(cmd, kwargs)
        except OSError as exc:
            self.log("error", "broker.spawn_failed", error=str(exc)[:200])
            self.proc = None
            self._failed(f"could not start: {exc}", None)
            return
        finally:
            if fh is not None:
                fh.close()
        self._spawned = time.monotonic()
        self._created = _process_started_at(self.proc.pid)
        self._next_probe = self._spawned + 2.0
        self._probe_failures = 0
        self.started_at = time.time()
        self.ready_at = 0.0
        self._status = {}
        self.state = "starting"
        self.detail = ""
        if self._episode not in ("image_missing", "docker_unavailable"):
            # A quiet retry (every 30 s until the operator acts) is not logged
            # again: its episode was, once.
            self.log("info", "broker.starting", pid=self.proc.pid, image=image,
                     fallback_allowed=fallback, url=self.url, log=str(log_path))
        self._write()

    def _became_ready(self, status: dict) -> None:
        self._status = status
        self.state = "running"
        self.ready_at = time.time()
        self.failures = 0
        self._next_probe = time.monotonic() + BROKER_PROBE_INTERVAL_S
        self._port_moves = 0
        self.log("info", "broker.ready", pid=self.proc.pid if self.proc else None,
                 image=status.get("image_ref"), image_id=status.get("image_id"),
                 fallback=bool(status.get("image_fallback")),
                 after_s=round(time.monotonic() - self._spawned, 1))
        if self._episode:
            self.log("info", "broker.recovered", after=self._episode)
        if self._paged:
            self.guard._page("success", "Kazma's document broker is running again",
                             f"{self._paged} -- recovered.", kind="broker.recovered",
                             fingerprint="broker:recovered", force=True)
        self._episode, self._paged = "", ""
        self._write()

    def _exited(self, code: int) -> None:
        tail = self._log_tail()
        self.proc = None
        self.last_exit = code
        self.restarts += 1
        if code == BROKER_EXIT_NEWER_IMAGE:
            # The image built for this code appeared: start again, now.
            self.log("info", "broker.newer_image", code=code)
            self._start()
            return
        if code == BROKER_EXIT_NO_IMAGE:
            self._quiet_retry("image_missing", "no worker image is built", code, tail)
            return
        if code == BROKER_EXIT_NO_ENGINE:
            self._quiet_retry("docker_unavailable", "Docker does not answer", code, tail)
            return
        if code == BROKER_EXIT_PORT_UNAVAILABLE and self._move_port(tail):
            return
        self.log("warn", "broker.exited", code=code, tail=tail)
        self._failed(f"exited with code {code}", tail)

    def _move_port(self, tail: str) -> bool:
        """The broker could not listen on its port (taken, or reserved by
        Windows): start it on another, at once. Kazma follows the address
        through the state file (``KAZMA_DOCUMENT_BROKER_URL_SOURCE``), so
        nothing restarts. False -- the caller backs off -- for a port the
        operator pinned, or after BROKER_PORT_MOVES moves in a row."""
        old = self.port
        if _named_broker_port() is not None or self._port_moves >= BROKER_PORT_MOVES:
            self.log("error", "broker.port_unavailable", port=old, moves=self._port_moves,
                     pinned=_named_broker_port() is not None, tail=tail)
            return False
        self._port_moves += 1
        if old:
            self._bad_ports.add(old)
            self._retired_urls.add(self.url)
        self.port = _choose_broker_port(None, avoid=frozenset(self._bad_ports))
        self.log("warn", "broker.port_moved", was=old, port=self.port, url=self.url,
                 moves=self._port_moves, tail=tail)
        self._write()
        self._start()
        return True

    def _quiet_retry(self, state: str, detail: str, code: int, tail: str) -> None:
        """A condition the operator fixes (an image to build, Docker to start):
        logged once, then checked again every BROKER_RETRY_S without a line."""
        now = time.monotonic()
        if self._episode != state:
            self._episode, self._episode_since = state, now
            self.log("warn", f"broker.{state}", code=code, tail=tail,
                     retry_every_s=BROKER_RETRY_S)
        self.state, self.detail = state, detail
        self._next_start = now + BROKER_RETRY_S
        deps = getattr(self.guard, "deps", None)
        if (state == "docker_unavailable" and now - self._episode_since >= BROKER_ENGINE_PAGE_AFTER_S
                and self._paged != state
                and not (deps is not None and deps.owns_engine_alerts())):
            self._paged = state
            self.guard._page(
                "warn", "Docker does not answer: documents are refused",
                f"Kazma's document broker needs the Docker engine and it has not answered "
                f"for {int(BROKER_ENGINE_PAGE_AFTER_S // 60)} min. Start Docker; the guard "
                "keeps trying.", kind="broker.docker_unavailable", fingerprint="broker:docker",
                force=True)
        self._write()

    def _failed(self, detail: str, tail: str | None) -> None:
        self.failures += 1
        self.state, self.detail = "backoff", detail
        delay = float(BROKER_BACKOFF_S[min(self.failures, len(BROKER_BACKOFF_S)) - 1])
        self._next_start = time.monotonic() + delay
        self._episode = "failing"
        self.log("warn", "broker.restarting", reason=detail, in_s=delay, failures=self.failures)
        if self.failures >= BROKER_PAGE_AFTER and self._paged != "failing":
            self._paged = "failing"
            last = "\n".join((tail or self._log_tail()).splitlines()[-8:])[-600:]
            self.log("error", "broker.failed", failures=self.failures, reason=detail)
            self.guard._page(
                "error", "Kazma's document broker keeps failing: documents are refused",
                f"It {detail} ({self.failures} times in a row). The guard keeps trying "
                f"every {int(BROKER_BACKOFF_S[-1] // 60)} min at most.\n"
                f"Its log ({_broker_log_path().name}):\n{last}",
                kind="broker.failed", fingerprint="broker:failing", force=True)
        self._write()

    def stop(self, reason: str) -> None:
        """Ask the broker to end its jobs and remove their containers, then
        kill whatever is left. Never raises."""
        proc = self.proc
        if proc is None:
            return
        self._stopping = True
        try:
            if proc.poll() is None:
                self.log("info", "broker.stopping", pid=proc.pid, reason=reason)
                asked = False
                try:
                    _ask_broker_to_stop(proc)
                    asked = True
                except OSError as exc:
                    self.log("warn", "broker.graceful_unavailable", error=str(exc)[:200])
                if asked:
                    try:
                        proc.wait(timeout=BROKER_STOP_GRACE_S)
                    except subprocess.TimeoutExpired:
                        self.log("warn", "broker.graceful_timeout", pid=proc.pid)
                if proc.poll() is None:
                    try:
                        _kill_broker_tree(proc)
                        proc.wait(timeout=TERMINATE_GRACE_S)
                    except (OSError, subprocess.SubprocessError) as exc:
                        self.log("warn", "broker.kill_failed", error=str(exc)[:200])
            self.log("info", "broker.stopped", pid=proc.pid, reason=reason, code=proc.poll())
        finally:
            self.proc = None
            self._stopping = False
            self.state = "idle" if self.mode != "off" else "off"
            self._write()

    # -- reporting -------------------------------------------------------

    def _log_tail(self, max_lines: int = 12) -> str:
        try:
            with _broker_log_path().open("rb") as fh:
                fh.seek(max(0, fh.seek(0, os.SEEK_END) - 8192))
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            return ""
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[-max_lines:])[-1500:]

    def record(self) -> dict:
        """What the state file says about the broker; Kazma's readiness reads it."""
        proc = self.proc
        status = self._status or {}
        return {
            "mode": self.mode,
            "state": self.state,
            "wanted": self.wanted,
            "why": self.why,
            "detail": self.detail,
            "url": self.url,
            "port": self.port,
            "token_file": str(self.token_path),
            "request_file": str(self.request_path),
            "log": str(_broker_log_path()),
            "pid": proc.pid if proc is not None else None,
            "created": self._created if proc is not None else None,
            "image_requested": self.image_requested,
            "image": status.get("image_ref"),
            "image_id": status.get("image_id"),
            "image_fallback": status.get("image_fallback"),
            "restarts": self.restarts,
            "failures": self.failures,
            "last_exit": self.last_exit,
            "started_at": self.started_at or None,
            "ready_at": self.ready_at or None,
            "updated": time.time(),
        }

    def _write(self) -> None:
        _update_state(document_broker=self.record())


# -- Kazma's own dependencies (Docker Desktop, its containers) ---------
#
# Live 2026-11-03: topgrade updated Docker Desktop, which then stayed stopped.
# Kazma's database is a container (kazma-db-win), so Kazma could not become
# healthy, and the guard restarted it about 34 times over eleven hours
# ("never became healthy ... attempt 34"). Nothing on the machine was going to
# start Docker again: Docker Desktop starts at sign-in, and the update had
# already happened after it. The guard starts it now:
#
#   * Docker is wanted when Kazma's record names a container
#     (kazma_core/guard_dependencies.py writes it, to the file the guard hands
#     over in KAZMA_GUARD_DEPENDENCIES_FILE) or the document broker is wanted.
#     Nothing else makes the guard touch Docker.
#   * The engine down past a grace period, and nothing already on the way
#     (Docker Desktop running and starting, its installer running, a launch of
#     ours within its wait), the guard launches Docker Desktop -- into the
#     signed-in user's session: the guard runs in its own logon session, where
#     a GUI program would never show and Docker Desktop does not start. A
#     scheduled task of the guard's own account (KazmaDockerDesktop, run only
#     on demand) does that. Nobody signed in, or the guard running as SYSTEM:
#     nothing to launch into, said once.
#   * Containers are `unless-stopped`, so Docker brings them back itself. Only
#     a container Kazma's record names, stopped after the engine came back
#     (and not seen running since), is started: never created, stopped or
#     removed, and never one Kazma does not use.
#   * One page per outage, once it has lasted DEPS_PAGE_AFTER_S (sooner when
#     nobody can bring it back: DEPS_STUCK_STATES), and one when Docker
#     answers again; the restart backoff then ends early. Since 2026-11-12
#     Kazma itself is never restarted for Docker: readiness says it waits for
#     it (``waits_for``), and it serves what does not need Docker meanwhile.

DEPS_MODE_ENV = "KAZMA_GUARD_START_DEPENDENCIES"
DEPS_MODES = ("auto", "off")
#: How often Docker is asked while it answers, and while it does not.
DEPS_CHECK_EVERY_S = 60.0
DEPS_CHECK_DOWN_S = 15.0
#: The engine down this long before the guard launches Docker Desktop: a
#: restart Docker makes itself (an update, a switch of engine) takes about a
#: minute, and Docker Desktop started at sign-in is not ready at once.
DEPS_ENGINE_GRACE_S = 120.0
#: A launch of ours is given this long to answer before it counts as failed.
DEPS_LAUNCH_WAIT_S = 240.0
#: Between launches that did not bring the engine up, by consecutive failure.
DEPS_LAUNCH_BACKOFF_S = (300.0, 900.0, 1800.0)
#: The outage paged once it has lasted this long. Kazma Watch pages a
#: dependency outage nobody inside paged about seven minutes after it saw it
#: (deploy/observer: three failed minutes, then five for an inside page):
#: at 600 s it paged first on 2026-11-12, and both paged one problem.
DEPS_PAGE_AFTER_S = 300.0
#: Sooner when nothing can bring the engine back until someone acts: nobody
#: is signed in to start Docker Desktop for (a Windows update restart), or
#: the guard runs as SYSTEM.
DEPS_STUCK_PAGE_AFTER_S = 180.0
DEPS_STUCK_STATES = frozenset({"no_session", "cannot_launch"})
#: The engine back this long before a stopped container is started: Docker
#: restarts `unless-stopped` containers itself, in the first seconds.
DEPS_CONTAINER_SETTLE_S = 60.0
#: Between starts of a container that would not start, by consecutive failure.
DEPS_CONTAINER_BACKOFF_S = (60.0, 300.0, 900.0)
#: Failed starts of one container in a row before the one page about it.
DEPS_CONTAINER_PAGE_AFTER = 3
DEPS_TASK_NAME = "KazmaDockerDesktop"
#: Docker Desktop's own processes (lowercase image names): one running means
#: Docker is already on the way, and the guard launches nothing.
_DESKTOP_IMAGES = frozenset({"docker desktop.exe", "com.docker.backend.exe"})
#: An update in progress: never launch Docker under its installer.
_INSTALLER_IMAGES = frozenset({"docker desktop installer.exe", "dockerdesktopinstaller.exe"})
_CONTAINER_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _deps_mode() -> str:
    raw = (os.environ.get(DEPS_MODE_ENV) or "auto").strip().lower()
    return raw if raw in DEPS_MODES else "auto"


def _deps_record_path() -> Path:
    return _broker_file("guard_dependencies.json")


def _valid_container_name(name: object) -> bool:
    """Docker's own rule for a name, checked here before a name read from a
    file reaches ``docker start``."""
    return (isinstance(name, str) and 0 < len(name) <= 128 and name[0] not in "_.-"
            and set(name) <= _CONTAINER_NAME_CHARS)


def _run_command(cmd: list[str], timeout: float) -> tuple[int, str, str] | None:
    """(exit code, stdout, stderr) of *cmd*, or None when it could not run."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return result.returncode, result.stdout or "", result.stderr or ""


_DOCKER_CLI_MODULE = None


def _docker_cli_module():
    """kazma_core/docker_cli.py, loaded by path (standard library only)."""
    global _DOCKER_CLI_MODULE
    if _DOCKER_CLI_MODULE is None:
        import importlib.util

        path = REPO_ROOT / "kazma-core" / "kazma_core" / "docker_cli.py"
        name = "kazma_guard_docker_cli"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _DOCKER_CLI_MODULE = module
    return _DOCKER_CLI_MODULE


def _docker_cli() -> str | None:
    """The docker CLI, found the way Kazma finds it (KAZMA_DOCKER_BIN, PATH,
    Docker's install folders), or None."""
    try:
        return _docker_cli_module().find_docker_cli()
    except (OSError, ImportError, SyntaxError, AttributeError):
        return None


def _docker_desktop_exe(cli: str) -> Path | None:
    """Docker Desktop's program (Windows): beside the CLI it ships
    (``...\\Docker\\resources\\bin\\docker.exe``), else its install folder."""
    candidates: list[Path] = []
    try:
        candidates.append(Path(cli).parents[2] / "Docker Desktop.exe")
    except IndexError:
        pass
    for var in ("ProgramFiles", "ProgramW6432"):
        root = os.environ.get(var)
        if root:
            candidates.append(Path(root) / "Docker" / "Docker" / "Docker Desktop.exe")
    candidates.append(Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _docker_desktop_task_xml(user: str, exe: str) -> str:
    """The on-demand task that starts Docker Desktop in *user*'s signed-in
    session (an interactive token: it runs only while they are signed in)."""
    from xml.sax.saxutils import escape

    return (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        "<RegistrationInfo><Description>Started by Kazma's guard to bring Docker Desktop "
        "back when its engine is down.</Description></RegistrationInfo>"
        "<Triggers />"
        f'<Principals><Principal id="Author"><UserId>{escape(user)}</UserId>'
        "<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>"
        "</Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<AllowHardTerminate>false</AllowHardTerminate>"
        "<StartWhenAvailable>false</StartWhenAvailable>"
        "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
        "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle>"
        "</IdleSettings><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled>"
        "<Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>"
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>4</Priority></Settings>"
        f'<Actions Context="Author"><Exec><Command>{escape(exe)}</Command></Exec></Actions>'
        "</Task>"
    )


def _guard_account() -> str:
    """``DOMAIN\\user`` the guard runs as (Windows), or ""."""
    got = _run_command(["whoami"], 20)
    return got[1].strip() if got and got[0] == 0 else ""


def _running_images() -> set[str]:
    """Lowercase image names of every running process (Windows), or empty."""
    return {name.lower() for name in _process_names().values()}


def _launch_docker_desktop(cli: str) -> tuple[bool, str]:
    """Start Docker Desktop. (started, what happened)."""
    if os.name != "nt":
        got = _run_command([cli, "desktop", "start", "-d"], 60)
        if got is None:
            return False, "docker desktop start could not run"
        return got[0] == 0, (got[2] or got[1]).strip()[:300] or f"exit {got[0]}"
    exe = _docker_desktop_exe(cli)
    if exe is None:
        return False, "Docker Desktop.exe was not found"
    user = _guard_account()
    if not user:
        return False, "the guard's account could not be read"
    xml_path = _broker_file("docker_desktop_task.xml")
    try:
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_bytes(_docker_desktop_task_xml(user, str(exe)).encode("utf-16"))
        made = _run_command(
            ["schtasks", "/Create", "/XML", str(xml_path), "/TN", DEPS_TASK_NAME, "/F"], 30)
    finally:
        try:
            xml_path.unlink()
        except OSError:
            pass
    if made is None or made[0] != 0:
        why = (made[2] or made[1]).strip()[:300] if made else "schtasks could not run"
        return False, f"the task could not be registered: {why}"
    ran = _run_command(["schtasks", "/Run", "/TN", DEPS_TASK_NAME], 30)
    if ran is None or ran[0] != 0:
        why = (ran[2] or ran[1]).strip()[:300] if ran else "schtasks could not run"
        return False, f"the task could not be run: {why}"
    return True, f"started {exe.name} through the {DEPS_TASK_NAME} task"


class Dependencies:
    """Docker Desktop and the containers Kazma uses (see the section comment).

    Ticked from the guard's own loop, like :class:`DocumentBroker`, so the
    guard stays one thread; a check runs every DEPS_CHECK_EVERY_S (every
    DEPS_CHECK_DOWN_S while Docker does not answer), each docker call bounded.
    Never raises out of a tick, and never stops, kills or removes anything.
    """

    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self.log = guard.log
        self.mode = _deps_mode()
        self.state = "off" if self.mode == "off" else "idle"
        self.detail = ""
        self.held = False
        self.cli: str | None = None
        self._cli_said = False
        self._next_check = 0.0
        self.engine_up: bool | None = None
        self._down_since: float | None = None
        self._up_since: float | None = None
        self._seen_down = False      # an outage since the guard started
        self._launched_at: float | None = None
        self._launch_failures = 0
        self._next_launch = 0.0
        self._launches = 0
        self._nudged = False
        self._paged = False
        self._said = ""              # the waiting state last logged
        self._recovered = False
        self._exited_seen: dict[str, int] = {}
        self._ran_since_up: set[str] = set()
        self._start_failures: dict[str, int] = {}
        self._next_start: dict[str, float] = {}
        self._container_paged: set[str] = set()
        self._left_alone: set[str] = set()
        self.containers: list[str] = []

    # -- what Kazma is handed ------------------------------------------

    def server_env(self) -> dict[str, str]:
        """Where the server records the containers it uses (empty when off)."""
        if self.mode == "off":
            return {}
        return {"KAZMA_GUARD_DEPENDENCIES_FILE": str(_deps_record_path())}

    def recorded_containers(self) -> list[str]:
        """The containers Kazma's record names, valid names only."""
        try:
            data = json.loads(_deps_record_path().read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return []
        rows = data.get("containers") if isinstance(data, dict) else None
        names: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            name = row.get("name") if isinstance(row, dict) else None
            if _valid_container_name(name) and name not in names:
                names.append(name)
        return names

    # -- lifecycle -------------------------------------------------------

    def on_guard_start(self) -> None:
        self._write()

    def before_spawn(self) -> None:
        try:
            self.held = False
            # A spawn is what a recovery wakes the backoff for: one noted
            # before it is spent, and must not end a later backoff early.
            self._recovered = False
        except Exception as exc:  # noqa: BLE001 -- see the class docstring
            self._internal(exc)

    def on_pause(self) -> None:
        """Maintenance: the guard starts nothing while it holds."""
        self.held = True

    def take_recovered(self) -> bool:
        """True once after Docker answered again following an outage (the
        restart backoff then ends early)."""
        recovered, self._recovered = self._recovered, False
        return recovered

    def owns_engine_alerts(self) -> bool:
        """Whether this class pages about Docker (the broker then does not):
        only while it watches the engine (not off, not idle -- nothing wanted,
        or not checked yet -- and not without a docker CLI)."""
        return self.mode != "off" and self.state not in {"off", "idle", "no_docker_cli"}

    def restart_note(self) -> str:
        """A line for the restart page while Docker is the problem."""
        if self.mode == "off" or self.engine_up is not False:
            return ""
        notes = {
            "docker_starting": "The Docker engine does not answer; the guard started Docker Desktop.",
            "not_coming_up": "The Docker engine does not answer, and the Docker Desktop the guard "
                             "started has not brought it up.",
            "docker_not_ready": "The Docker engine does not answer; Docker Desktop is running "
                                "and not ready yet.",
            "docker_updating": "The Docker engine does not answer; Docker Desktop is being updated.",
            "no_session": "The Docker engine does not answer, and nobody is signed in to start "
                          "Docker Desktop for.",
            "cannot_launch": "The Docker engine does not answer, and the guard cannot start "
                             "Docker Desktop (it runs as SYSTEM).",
            "launch_failed": "The Docker engine does not answer, and the guard could not start "
                             "Docker Desktop.",
        }
        return notes.get(self.state, "The Docker engine does not answer; the guard will start "
                                     "Docker Desktop if it stays down.")

    def tick(self) -> None:
        _tick_safely(self._tick, self._internal)

    def _internal(self, exc: BaseException) -> None:
        self.log("error", "deps.internal_error", error=f"{type(exc).__name__}: {exc}"[:300])

    # -- the check ---------------------------------------------------------

    def _tick(self) -> None:
        if self.mode == "off" or self.held:
            return
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + DEPS_CHECK_EVERY_S
        self.containers = self.recorded_containers()
        broker = getattr(self.guard, "broker", None)
        if not self.containers and not (broker is not None and broker.wanted):
            self._set("idle", "Kazma uses no container and no document broker")
            return
        if self.cli is None:
            self.cli = _docker_cli()
        if self.cli is None:
            if not self._cli_said:
                self._cli_said = True
                self.log("warn", "deps.no_docker_cli", wanted=self.containers)
            self._set("no_docker_cli", "the docker CLI was not found")
            return
        if self._engine_answers():
            self._engine_is_up(now)
            if self.containers:
                self._check_containers(now)
        else:
            self._next_check = now + DEPS_CHECK_DOWN_S
            self._engine_is_down(now)
        self._write()

    def _engine_answers(self) -> bool:
        got = _run_command([self.cli or "docker", "version", "--format", "{{.Server.Version}}"], 15)
        return bool(got and got[0] == 0 and got[1].strip())

    def _set(self, state: str, detail: str) -> None:
        changed = state != self.state
        self.state, self.detail = state, detail
        if changed:
            self._write()

    def _say(self, state: str, detail: str, **fields) -> None:
        """A waiting state, logged once while it lasts."""
        self.state, self.detail = state, detail
        if self._said != state:
            self._said = state
            self.log("warn", f"deps.{state}", detail=detail[:300], **fields)

    def _engine_is_up(self, now: float) -> None:
        if self.engine_up is not True:
            was_down = self.engine_up is False
            down_s = int(now - self._down_since) if self._down_since is not None else 0
            self.engine_up, self._up_since = True, now
            self._down_since = None
            self._ran_since_up = set()
            self._exited_seen = {}
            if was_down:
                self.log("info", "deps.engine_up", down_s=down_s, launched=self._launches)
                if self._paged or self._launches:
                    what = (f"The guard started Docker Desktop; the engine answers again after "
                            f"{down_s // 60} min. " if self._launches else
                            f"The engine answers again after {down_s // 60} min. ")
                    self.guard._page("success", "Docker is back", what + "Kazma follows.",
                                     kind="deps.engine_up", fingerprint="deps:recovered", force=True)
                self._recovered = True
            self._launched_at, self._launch_failures, self._next_launch = None, 0, 0.0
            self._launches, self._nudged, self._paged, self._said = 0, False, False, ""
        self.state, self.detail = "ok", "the Docker engine answers"

    def _engine_is_down(self, now: float) -> None:
        if self.engine_up is not False:
            self.engine_up, self._up_since = False, None
            self._down_since, self._seen_down = now, True
            self.log("warn", "deps.engine_down", wanted=self.containers,
                     grace_s=int(DEPS_ENGINE_GRACE_S))
        down_for = now - (self._down_since or now)
        self._maybe_launch(now, down_for)
        page_after = DEPS_STUCK_PAGE_AFTER_S if self.state in DEPS_STUCK_STATES else DEPS_PAGE_AFTER_S
        if down_for >= page_after and not self._paged:
            self._paged = True
            minutes = max(1, int(down_for // 60))
            # Windows' "Use my sign-in info to automatically finish setting up
            # after an update" was on during 2026-11-12's restart and signed
            # nobody in: a sign-in is the one way out the guard can name.
            fix = " Sign in to this PC to start it." if self.state == "no_session" else ""
            self.guard._page(
                "warn", "Docker does not answer",
                f"The Docker engine has not answered for {minutes} min; what needs it waits "
                f"({', '.join(self.containers) or 'the document broker'}). "
                f"{self.detail.rstrip('.')}.{fix} The guard keeps trying.",
                kind="deps.engine_down", fingerprint="deps:docker", force=True)

    def _maybe_launch(self, now: float, down_for: float) -> None:
        if down_for < DEPS_ENGINE_GRACE_S:
            self.state, self.detail = "docker_down", "the Docker engine does not answer (waiting)"
            return
        images = _running_images() if os.name == "nt" else set()
        if images & _INSTALLER_IMAGES:
            self._say("docker_updating", "Docker Desktop's installer is running")
            return
        if self._launched_at is not None:
            if now - self._launched_at < DEPS_LAUNCH_WAIT_S:
                self.state = "docker_starting"
                self.detail = "the guard started Docker Desktop; waiting for its engine"
                return
            self._launched_at = None
            self._launch_failures += 1
            wait = DEPS_LAUNCH_BACKOFF_S[min(self._launch_failures, len(DEPS_LAUNCH_BACKOFF_S)) - 1]
            self._next_launch = now + wait
            self.log("warn", "deps.not_coming_up", waited_s=int(DEPS_LAUNCH_WAIT_S),
                     next_try_s=int(wait))
            self.state = "not_coming_up"
            self.detail = "Docker Desktop was started and its engine did not answer"
            return
        if images & _DESKTOP_IMAGES:
            # Starting, or stuck: never killed. One nudge, then it is Docker's.
            if not self._nudged:
                self._nudged = True
                got = _run_command([self.cli or "docker", "desktop", "start", "-d"], 60)
                self.log("info", "deps.desktop_nudged",
                         exit=got[0] if got else None)
            self._say("docker_not_ready", "Docker Desktop is running and its engine does not answer")
            return
        if now < self._next_launch:
            return
        if os.name == "nt":
            if images and "explorer.exe" not in images:
                self._say("no_session", "nobody is signed in to start Docker Desktop for")
                return
            if _current_user_sid() == "S-1-5-18":
                self._say("cannot_launch", "the guard runs as SYSTEM and cannot start "
                                           "Docker Desktop for anyone")
                return
        started, what = _launch_docker_desktop(self.cli or "docker")
        self._launches += 1
        if started:
            self._launched_at = now
            self._said = ""
            self.log("warn", "deps.docker_launched", detail=what, attempt=self._launches)
            self.state = "docker_starting"
            self.detail = "the guard started Docker Desktop; waiting for its engine"
            return
        self._launch_failures += 1
        wait = DEPS_LAUNCH_BACKOFF_S[min(self._launch_failures, len(DEPS_LAUNCH_BACKOFF_S)) - 1]
        self._next_launch = now + wait
        self.log("error", "deps.launch_failed", detail=what, next_try_s=int(wait))
        self.state, self.detail = "launch_failed", f"Docker Desktop could not be started: {what}"

    # -- containers --------------------------------------------------------

    def _container_states(self) -> dict[str, str]:
        """{name: status} for the recorded containers Docker knows."""
        got = _run_command([self.cli or "docker", "inspect", "--format",
                            "{{.Name}}|{{.State.Status}}", *self.containers], 15)
        states: dict[str, str] = {}
        if got is None:
            return states
        for line in got[1].splitlines():
            name, _, status = line.strip().lstrip("/").partition("|")
            if name in self.containers and status:
                states[name] = status.strip().lower()
        return states

    def _check_containers(self, now: float) -> None:
        states = self._container_states()
        for name in self.containers:
            status = states.get(name)
            if status == "running":
                self._ran_since_up.add(name)
                self._exited_seen.pop(name, None)
                if self._start_failures.pop(name, 0) and name in self._container_paged:
                    self._container_paged.discard(name)
                    self.guard._page("success", f"Container {name} runs again", "",
                                     kind="deps.container_recovered",
                                     fingerprint=f"deps:container:{name}:recovered", force=True)
                self._left_alone.discard(name)
                continue
            if status not in {"exited", "created"}:
                self._exited_seen.pop(name, None)
                continue
            if not self._seen_down or name in self._ran_since_up:
                # Stopped while the engine stayed up: someone stopped it.
                if name not in self._left_alone:
                    self._left_alone.add(name)
                    self.log("info", "deps.container_left_stopped", container=name,
                             status=status)
                continue
            seen = self._exited_seen.get(name, 0) + 1
            self._exited_seen[name] = seen
            if (seen < 2 or self._up_since is None
                    or now - self._up_since < DEPS_CONTAINER_SETTLE_S
                    or now < self._next_start.get(name, 0.0)):
                continue
            self._start_container(name, now)

    def _start_container(self, name: str, now: float) -> None:
        got = _run_command([self.cli or "docker", "start", name], 60)
        if got is not None and got[0] == 0:
            self.log("warn", "deps.container_started", container=name)
            self._exited_seen.pop(name, None)
            return
        failures = self._start_failures.get(name, 0) + 1
        self._start_failures[name] = failures
        wait = DEPS_CONTAINER_BACKOFF_S[min(failures, len(DEPS_CONTAINER_BACKOFF_S)) - 1]
        self._next_start[name] = now + wait
        why = (got[2] or got[1]).strip()[:300] if got else "docker start could not run"
        self.log("error", "deps.container_start_failed", container=name, error=why,
                 next_try_s=int(wait))
        if failures >= DEPS_CONTAINER_PAGE_AFTER and name not in self._container_paged:
            self._container_paged.add(name)
            self.guard._page(
                "warn", f"Container {name} will not start",
                f"Kazma needs it, and `docker start {name}` failed {failures} times: {why}",
                kind="deps.container_start_failed", fingerprint=f"deps:container:{name}", force=True)

    # -- the state file ------------------------------------------------------

    def record(self) -> dict:
        return {
            "mode": self.mode,
            "state": self.state,
            "detail": self.detail,
            "engine_up": self.engine_up,
            "containers": list(self.containers),
            "record_file": str(_deps_record_path()),
            "launches": self._launches,
            "updated": time.time(),
        }

    def _write(self) -> None:
        _update_state(dependencies=self.record())


def _dependency_status_lines(state: dict) -> list[str]:
    """What --status says about Docker and Kazma's containers."""
    rec = state.get("dependencies")
    if not isinstance(rec, dict):
        return []
    engine = {True: "answers", False: "DOES NOT ANSWER", None: "not checked"}.get(
        rec.get("engine_up"), "not checked")
    lines = [f"docker      : {engine} ({rec.get('state')})"]
    if rec.get("containers"):
        lines.append(f"  kazma uses: {', '.join(str(c) for c in rec['containers'])}")
    if rec.get("detail"):
        lines.append(f"  detail    : {rec.get('detail')}")
    return lines


# -- the database as an OS service ------------------------------------------
#
# Resilience plan A1. ``kazma setup postgres`` installs Postgres 16 as an OS
# service that starts at boot and restarts on failure, and records it in
# ``<data dir>/setup_record.json``. The guard reads that record and watches
# the service the way it watches Docker (Dependencies): the service manager's
# own state (``sc query``, ``systemctl is-active``, ``launchctl print``) and
# a TCP connection to the port. A service down for a minute after the guard's
# first two minutes is started once per outage (Windows: ``sc start``, which
# the guard's elevated task may do; elsewhere the OS's own restart rule does
# it, and ``systemctl start`` is tried only as root); one page at five minutes
# down, one when it answers again after that page. Nothing happens on an
# install the record names no service for.

DBSVC_CHECK_EVERY_S = 30.0
DBSVC_START_AFTER_S = 60.0
DBSVC_PAGE_AFTER_S = 300.0
DBSVC_SETTLE_S = 120.0


def _setup_record() -> dict:
    try:
        data = json.loads((_data_dir_guess() / "setup_record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rec = data.get("postgres") if isinstance(data, dict) else None
    return rec if isinstance(rec, dict) else {}


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2.0):
            return True
    except (OSError, ValueError):
        return False


class DatabaseServiceWatch:
    """The database's OS service (see the section comment). Ticked from the
    guard's loop; never raises out of a tick."""

    def __init__(self, guard: Guard, *, record=None, run=None, answers=None) -> None:
        self.guard = guard
        self.log = guard.log
        self._record = record or _setup_record
        self._run = run or _run_command
        self._answers = answers or _port_answers
        self._next_check = 0.0
        self._down_since: float | None = None
        self._started_this_outage = False
        self._paged = False
        self._refusal_said = False
        self.start_refused = False
        self.state = "none"
        self.service = ""

    def on_guard_start(self) -> None:
        self._write()

    def tick(self) -> None:
        _tick_safely(self._tick, lambda exc: self.log(
            "error", "dbsvc.internal_error", error=f"{type(exc).__name__}: {exc}"[:300]))

    def _refused(self, got, what: str, service: str) -> bool:
        """Whether Windows refused the guard *what* on the service (``sc``'s
        "OpenService FAILED 5: Access is denied"). A guard without admin
        rights -- the user-level task ``install_service.py --install``
        registers from a shell without them -- may not start the Postgres
        service, and on some machines may not query it. Said once."""
        text = " ".join(str(part) for part in got[1:]) if got else ""
        refused = bool(got) and (got[0] == 5 or "FAILED 5" in text)
        if refused and not self._refusal_said:
            self._refusal_said = True
            self.log("error", "dbsvc.access_denied", service=service, action=what,
                     fix="run scripts/service/install_service.py --install from an "
                         "admin PowerShell: the task it registers there may start the service")
        return refused

    def _running(self, os_name: str, service: str) -> bool | None:
        if os_name == "windows":
            got = self._run(["sc", "query", service], 15)
            if got is None or self._refused(got, "query", service):
                return None                  # unknown: the port decides
            return got[0] == 0 and "RUNNING" in got[1]
        if os_name == "macos":
            got = self._run(["launchctl", "print", f"system/{service}"], 15)
            return None if got is None else (got[0] == 0 and "state = running" in got[1])
        got = self._run(["systemctl", "is-active", service], 15)
        return None if got is None else got[0] == 0

    def _start(self, os_name: str, service: str) -> bool:
        if os_name == "windows":
            argv = ["sc", "start", service]
        elif os_name != "macos" and hasattr(os, "geteuid") and os.geteuid() == 0:
            argv = ["systemctl", "start", service]
        else:
            return False                  # the OS's own restart rule does it
        got = self._run(argv, 30)
        if got is not None and os_name == "windows" and self._refused(got, "start", service):
            self.start_refused = True
            return False
        return got is not None and got[0] == 0

    def _tick(self) -> None:
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + DBSVC_CHECK_EVERY_S
        rec = self._record()
        service, os_name = str(rec.get("service") or ""), str(rec.get("os") or "")
        if not service:
            if self.state != "none":
                self.state = "none"
                self._write()
            return
        self.service = service
        running = self._running(os_name, service)
        answers = self._answers(int(rec.get("port") or 5432))
        # A state the OS would not give (None: sc refused or could not run)
        # is unknown, and the port decides: a refused query is not an outage.
        up = answers and running is not False
        if up:
            if self._down_since is not None:
                self.log("info", "dbsvc.back", service=service,
                         after_s=round(now - self._down_since))
                if self._paged:
                    self.guard._page("success", "Kazma's database is running again",
                                     f"The {service} service answers again.", kind="dbsvc.back",
                                     fingerprint="dbsvc:ok", force=True)
            self._down_since, self._started_this_outage, self._paged = None, False, False
            self._set("running")
            return
        if self._down_since is None:
            self._down_since = now
            self.log("warn", "dbsvc.down", service=service, running=running, answers=answers)
        down_for = now - self._down_since
        guard_up = now - getattr(self.guard, "_started_mono", now - DBSVC_SETTLE_S)
        if (not self._started_this_outage and down_for >= DBSVC_START_AFTER_S
                and guard_up >= DBSVC_SETTLE_S and not running):
            self._started_this_outage = True
            started = self._start(os_name, service)
            self.log("warn" if started else "error", "dbsvc.start_tried", service=service, accepted=started)
        if down_for >= DBSVC_PAGE_AFTER_S and not self._paged:
            what = ("the guard is not allowed to start it: start it yourself, and "
                    "run scripts/service/install_service.py --install from an admin "
                    "PowerShell once, so the guard may next time" if self.start_refused
                    else "the guard asked the OS to start it" if self._started_this_outage
                    else "the OS's own restart rule is the one that starts it")
            # False while Kazma's own page has this outage (one page per
            # problem): asked again next check, and no recovery for it.
            self._paged = self.guard._page(
                "critical", "Kazma's database service is down",
                f"The {service} service has not answered for {int(down_for // 60)} minutes; "
                f"{what}. Chats, settings and memory wait until it is back.",
                kind="dbsvc.down", fingerprint="dbsvc:down", force=True)
        self._set("down")

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._write()

    def record(self) -> dict:
        return {"state": self.state, "service": self.service, "updated": time.time()}

    def _write(self) -> None:
        _update_state(database_service=self.record())


def _dbsvc_status_lines(state: dict) -> list[str]:
    """What --status says about the database service."""
    rec = state.get("database_service")
    if not isinstance(rec, dict) or rec.get("state") in (None, "none"):
        return []
    return [f"database    : {rec.get('state')} ({rec.get('service')})"]


# -- free disk space ---------------------------------------------------
#
# Resilience plan B2a. A full drive stops Kazma in a set order: the
# document store refuses work below its floor first, then backups fail, then
# the database (and Docker's disk image, when Docker keeps it on another
# drive) stops writing. The guard checks the free space once a minute, warns
# once at the low level and pages once at the critical one, naming what
# stops next, and says once when there is room again. Either measure (the
# share or the bytes) crosses a level; leaving it needs both clear of a
# margin, so a drive hovering at a line is not a page a minute. The level
# also travels in the outside-watch heartbeat (OutsideWatch below).

DISK_CHECK_EVERY_S = 60.0
DISK_LOW_PCT = 15.0
DISK_LOW_BYTES = 20 * 1024 ** 3
DISK_CRITICAL_PCT = 5.0
DISK_CRITICAL_BYTES = 5 * 1024 ** 3
DISK_MARGIN_PCT = 1.0
DISK_MARGIN_BYTES = 1024 ** 3
# kazma_core.documents keeps this much free before it starts a stage.
DOCUMENT_FLOOR_BYTES = 512 * 1024 ** 2
_DISK_LEVELS = ("ok", "low", "critical")


def disk_level(free: int, total: int, current: str = "ok") -> str:
    """``critical``, ``low`` or ``ok`` for *free* bytes of *total*.

    Entering a level takes either measure below its line; leaving one
    takes both above it by the margin (DISK_MARGIN_PCT, DISK_MARGIN_BYTES).
    """
    if total <= 0:
        return current if current in _DISK_LEVELS else "ok"
    pct = 100.0 * free / total

    def below(pct_line: float, bytes_line: int, margin: bool) -> bool:
        if margin:
            return pct < pct_line + DISK_MARGIN_PCT or free < bytes_line + DISK_MARGIN_BYTES
        return pct < pct_line or free < bytes_line

    if below(DISK_CRITICAL_PCT, DISK_CRITICAL_BYTES, current == "critical"):
        return "critical"
    if below(DISK_LOW_PCT, DISK_LOW_BYTES, current in ("low", "critical")):
        return "low"
    return "ok"


def _data_dir_guess() -> Path:
    """Where Kazma keeps its data: ``KAZMA_DATA_DIR``, else the install's
    ``kazma-data`` (kazma_core.paths.data_dir, without importing it)."""
    env = (os.environ.get("KAZMA_DATA_DIR") or "").strip()
    return Path(env) if env else REPO_ROOT / "kazma-data"


def _existing(path: Path) -> Path | None:
    """*path*, or its nearest existing parent (a data dir not made yet)."""
    for candidate in (path, *path.parents):
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _docker_disk_dir() -> Path | None:
    """Where Docker Desktop keeps its disk image, when it is on this machine."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or ""
        candidate = Path(base) / "Docker" / "wsl" if base else None
    elif sys.platform == "darwin":
        candidate = Path.home() / "Library" / "Containers" / "com.docker.docker" / "Data"
    else:
        candidate = Path("/var/lib/docker")
    try:
        return candidate if candidate is not None and candidate.is_dir() else None
    except OSError:
        return None


def _drive_label(path: Path) -> str:
    """``C:`` on Windows, the path itself elsewhere."""
    drive = getattr(path, "drive", "") or ""
    return drive if drive else str(path)


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return str(a.drive).lower() == str(b.drive).lower() if os.name == "nt" else False


class DiskWatch:
    """Free space where Kazma writes (see the section comment).

    Ticked from the guard's own loop; one check every DISK_CHECK_EVERY_S,
    the reader injectable for tests. Never raises out of a tick.
    """

    def __init__(self, guard: Guard, *, reader=None, paths=None) -> None:
        self.guard = guard
        self.log = guard.log
        self.reader = reader or shutil.disk_usage
        self._paths = paths
        self._next_check = 0.0
        self.levels: dict[str, str] = {}
        self.readings: dict[str, dict] = {}
        self._paged: set[str] = set()

    def watched(self) -> list[tuple[str, str, Path]]:
        """(key, role, path) of each volume to watch: the data dir, and
        Docker's disk image when it is on another volume."""
        if self._paths is not None:
            return list(self._paths)
        found: list[tuple[str, str, Path]] = []
        data = _existing(_data_dir_guess())
        if data is not None:
            found.append((_drive_label(data), "data", data))
        docker = _docker_disk_dir()
        if docker is not None and (data is None or not _same_volume(docker, data)):
            found.append((_drive_label(docker), "docker", docker))
        return found

    def mode(self) -> str:
        return "off" if (os.environ.get("KAZMA_GUARD_DISK_WATCH") or "").strip().lower() in (
            "off", "0", "false", "no") else "auto"

    def on_guard_start(self) -> None:
        self._write()

    def tick(self) -> None:
        _tick_safely(self._tick, self._internal)

    def _internal(self, exc: BaseException) -> None:
        self.log("error", "disk.internal_error", error=f"{type(exc).__name__}: {exc}"[:300])

    def worst(self) -> dict | None:
        """The reading with the least room, for the heartbeat."""
        if not self.readings:
            return None
        key = min(self.readings, key=lambda k: self.readings[k]["free_pct"])
        return {"free_pct": self.readings[key]["free_pct"],
                "level": self.levels.get(key, "ok")}

    def _tick(self) -> None:
        if self.mode() == "off":
            return
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + DISK_CHECK_EVERY_S
        for key, role, path in self.watched():
            try:
                usage = self.reader(str(path))
            except OSError as exc:
                self.readings.pop(key, None)
                self.log("warn", "disk.unreadable", drive=key, error=str(exc)[:200])
                continue
            total = int(getattr(usage, "total", 0) or 0)
            free = int(getattr(usage, "free", 0) or 0)
            before = self.levels.get(key, "ok")
            level = disk_level(free, total, before)
            pct = round(100.0 * free / total, 1) if total else 0.0
            self.readings[key] = {"role": role, "free_pct": pct, "free_gb": round(free / 1024 ** 3, 1),
                                  "total_gb": round(total / 1024 ** 3, 1)}
            self.levels[key] = level
            if level != before:
                self._changed(key, role, before, level, pct, free)
        self._write()

    def _changed(self, key: str, role: str, before: str, level: str, pct: float, free: int) -> None:
        gb = round(free / 1024 ** 3, 1)
        self.log("warn" if level != "ok" else "info", "disk.level", drive=key, role=role,
                 state=level, before=before, free_pct=pct, free_gb=gb)
        where = "Docker's disk image" if role == "docker" else "Kazma's data"
        if level == "ok":
            if key in self._paged:
                self._paged.discard(key)
                self.guard._page("success", f"Disk space is back on {key}",
                                 f"{pct}% ({gb} GB) free where {where} is kept.",
                                 kind="disk.back", fingerprint=f"disk:{key}:ok", force=True)
            return
        self._paged.add(key)
        severity = "critical" if level == "critical" else "warn"
        title = (f"Disk almost full on {key}" if level == "critical"
                 else f"Disk space low on {key}")
        self.guard._page(severity, title,
                         f"{pct}% ({gb} GB) free where {where} is kept. {self._what_stops(role)}",
                         kind="disk.critical" if level == "critical" else "disk.low",
                         fingerprint=f"disk:{key}:{level}", force=True)

    @staticmethod
    def _what_stops(role: str) -> str:
        floor_mb = DOCUMENT_FLOOR_BYTES // 1024 ** 2
        if role == "docker":
            return ("When it fills, the database in Docker stops writing: chats, "
                    "settings and memory stop saving. Free space on this drive.")
        return (f"What stops first: documents refuse work below {floor_mb} MB free, "
                "then backups fail, then the database stops writing. Free space on "
                "this drive.")

    def record(self) -> dict:
        return {
            "volumes": {key: {**reading, "level": self.levels.get(key, "ok")}
                        for key, reading in self.readings.items()},
            "updated": time.time(),
        }

    def _write(self) -> None:
        _update_state(disk=self.record())


def _disk_status_lines(state: dict) -> list[str]:
    """What --status says about free disk space."""
    rec = state.get("disk")
    if not isinstance(rec, dict) or not isinstance(rec.get("volumes"), dict):
        return []
    lines = []
    for key, vol in rec["volumes"].items():
        if not isinstance(vol, dict):
            continue
        lines.append(f"disk        : {vol.get('level')} ({vol.get('free_pct')}% / "
                     f"{vol.get('free_gb')} GB free on {key}, {vol.get('role')})")
    return lines


# -- the outside watch -------------------------------------------------
#
# Resilience plan B1. Everything else in the guard runs on this machine, so
# none of it can say the machine is off, asleep or cut off. Once the owner
# has paired the install with an observer (``kazma observer pair`` writes
# <install>/.kazma/observer.key), the guard sends it a heartbeat every
# OUTSIDE_WATCH_EVERY_S: the observer (deploy/observer, a Cloudflare Worker
# on kazma.ai) pages when the heartbeats stop or say something is wrong.
#
# The heartbeat is an allowlist, built by heartbeat_body: states, counts and
# check NAMES -- no chat, no content, no path, no name, no error text. It is
# signed with HMAC-SHA256 under the pairing key (sign_request, "v2="), over
# "v2\n<timestamp>\n<METHOD>\n<path>\n<body>", so the observer can refuse a
# forged or replayed one, and one signed for another of its routes.
# The key must be readable by the guard's account alone (restricted like the
# broker token); a key file anyone else may read is never used. One send at
# a time, on a daemon thread, bounded by OUTSIDE_WATCH_TIMEOUT_S: an observer
# that hangs never holds up the guard.

OUTSIDE_WATCH_EVERY_S = 60.0
OUTSIDE_WATCH_TIMEOUT_S = 5.0

#: The guard's name on every request it makes (``_request``). Cloudflare's bot
#: check refuses urllib's own name ("Python-urllib/3.x", error 1010): live
#: 2026-11-06 every heartbeat to the outside observer was refused that way, a
#: 403 before it reached the Worker, while the observer itself worked.
GUARD_USER_AGENT = "KazmaGuard/1 (+https://github.com/Mubder/kazma)"


def _request(url: str, *, data: bytes | None = None, method: str = "GET",
             headers: dict[str, str] | None = None) -> urllib.request.Request:
    """A request carrying the guard's own name. Every request the guard makes
    is built here (``tests/test_observer_heartbeat.py`` holds it)."""
    return urllib.request.Request(url, data=data, method=method,
                                  headers={"User-Agent": GUARD_USER_AGENT, **(headers or {})})
DEFAULT_OUTSIDE_WATCH_URL = "https://status.kazma.ai/v1/heartbeat"
OBSERVER_KEY_MIN_CHARS = 32
_NAME_RE = re.compile(r"^[a-z_]{1,40}$")
_STATE_WORD_RE = re.compile(r"^[a-z_]{1,24}$")


def signed_message(ts: int, method: str, path: str, body: bytes) -> bytes:
    """What a ``v2`` signature covers: ``b"v2\\n<ts>\\n<METHOD>\\n<path>\\n" + body``.

    The route (method and path, as the request line names them) is in it, so a
    signature made for one of the observer's routes is refused on another (the
    v1 form, ``"<ts>." + body``, did not bind the route). A newline can be in
    neither a method nor a path, so no two requests share a message; the
    ``v2`` tag keeps a v2 message from ever being a v1 one. The observer's
    copy is ``deploy/observer/src/verify.js`` (``v2Message``), and Kazma's
    ``kazma_core.observability.outside_watch``; the shared vector,
    ``tests/fixtures/observer/heartbeat_vector.json``, holds all three to it.
    """
    head = f"v2\n{int(ts)}\n{method.upper()}\n{path}\n".encode("ascii")
    return head + body


def sign_request(key: bytes, ts: int, method: str, path: str, body: bytes) -> str:
    """``v2=<hex>``: HMAC-SHA256 under *key* of :func:`signed_message`."""
    mac = hmac.new(key, signed_message(ts, method, path, body), hashlib.sha256)
    return "v2=" + mac.hexdigest()


def _word(value: object) -> str | None:
    """*value* when it is a short lowercase state word, else None."""
    text = str(value) if isinstance(value, str) else ""
    return text if _STATE_WORD_RE.match(text) else None


def _install_hash() -> str:
    """A stable, anonymous name for this install: the hash of its install
    id (``<data dir>/install_id``), else of where it is installed."""
    try:
        raw = (_data_dir_guess() / "install_id").read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    seed = raw or str(REPO_ROOT)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _git_head(root: Path) -> str | None:
    """The commit the install has checked out, read from .git's files."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            ref_file = git / ref
            if ref_file.is_file():
                head = ref_file.read_text(encoding="utf-8").strip()
            else:
                head = ""
                packed = git / "packed-refs"
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        head = parts[0]
                        break
    except OSError:
        return None
    head = head.lower()
    if len(head) >= 12 and all(c in "0123456789abcdef" for c in head):
        return head[:12]
    return None


def _cloudflared_state() -> str:
    """``running``, ``not_running`` or ``unknown`` (no tool to ask)."""
    if os.name == "nt":
        got = _run_command(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe",
                            "/FO", "CSV", "/NH"], 10)
        if got is None or got[0] != 0:
            return "unknown"
        return "running" if "cloudflared.exe" in got[1].lower() else "not_running"
    got = _run_command(["pgrep", "-x", "cloudflared"], 10)
    if got is None or got[0] not in (0, 1):
        return "unknown"
    return "running" if got[0] == 0 else "not_running"


def _power_state() -> dict | None:
    """``{"on_battery", "percent"}`` where the machine has a battery, else None."""
    if os.name == "nt":
        import ctypes

        class _PowerStatus(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte),
                        ("percent", ctypes.c_ubyte), ("saver", ctypes.c_ubyte),
                        ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

        status = _PowerStatus()
        try:
            ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
        except (AttributeError, OSError):
            return None
        if not ok or status.flag in (128, 255):  # no battery / unknown
            return None
        return {"on_battery": status.ac == 0,
                "percent": None if status.percent == 255 else int(status.percent)}
    base = Path("/sys/class/power_supply")
    try:
        supplies = sorted(base.iterdir()) if base.is_dir() else []
    except OSError:
        return None
    for supply in supplies:
        try:
            if (supply / "type").read_text(encoding="utf-8").strip() != "Battery":
                continue
            status = (supply / "status").read_text(encoding="utf-8").strip()
            percent = int((supply / "capacity").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        return {"on_battery": status == "Discharging", "percent": percent}
    return None


# -- power (resilience plan B2c) ------------------------------------------------
#
# A USB UPS shows itself to the OS as a battery. On battery the guard pages
# once; at POWER_STOP_PERCENT it stops Kazma gracefully (a pause of its own:
# turns finish or stop cleanly, the database is left consistent) before the
# UPS runs dry; power back, it lifts its pause and Kazma starts as usual. A
# machine with no battery (no UPS) is "none" and nothing happens.

POWER_CHECK_EVERY_S = 30.0
POWER_STOP_PERCENT = 30
#: The pause reason the power watch writes; it lifts only its own.
POWER_PAUSE_PREFIX = "power:"


class PowerWatch:
    """The power source, ticked from the guard's loop (see the section comment).
    The reader is injectable for tests; never raises out of a tick."""

    def __init__(self, guard: Guard, *, reader=None) -> None:
        self.guard = guard
        self.log = guard.log
        self.reader = reader or _power_state
        self._next_check = 0.0
        self.state = "unknown"  # none | ac | battery | stopped | unknown
        self.percent: int | None = None

    def mode(self) -> str:
        return "off" if (os.environ.get("KAZMA_GUARD_POWER_WATCH") or "").strip().lower() in (
            "off", "0", "false", "no") else "auto"

    def on_guard_start(self) -> None:
        # Power came back while nothing ran (a reboot after the UPS ran dry):
        # the pause this watch left is lifted at the next tick on AC power.
        self._next_check = 0.0
        self._write()

    def tick(self) -> None:
        _tick_safely(self._tick, lambda exc: self.log(
            "error", "power.internal_error", error=f"{type(exc).__name__}: {exc}"[:300]))

    @staticmethod
    def _own_pause() -> bool:
        pause = read_pause()
        return bool(pause and str(pause.get("reason") or "").startswith(POWER_PAUSE_PREFIX))

    def _tick(self) -> None:
        if self.mode() == "off":
            return
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + POWER_CHECK_EVERY_S
        reading = self.reader()
        before = self.state
        if reading is None:
            self.state, self.percent = "none", None
            if self._own_pause():  # the battery is gone (unplugged UPS): nothing to wait for
                clear_pause()
            self._write()
            return
        self.percent = reading.get("percent")
        pct = "?" if self.percent is None else f"{self.percent}%"
        if not reading.get("on_battery"):
            self.state = "ac"
            if before in ("battery", "stopped") or self._own_pause():
                stopped = self._own_pause()
                if stopped:
                    clear_pause()
                self.log("info", "power.back", percent=self.percent, restarted=stopped)
                self.guard._page("success", "Power is back",
                                 f"The machine runs on mains power again ({pct} battery)."
                                 + (" Kazma starts again." if stopped else ""),
                                 kind="power.back", fingerprint="power:back", force=True)
            self._write()
            return
        low = self.percent is not None and self.percent <= POWER_STOP_PERCENT
        if low and before != "stopped" and read_pause() is None:  # never over another pause
            self.state = "stopped"
            write_pause(f"{POWER_PAUSE_PREFIX} on battery at {pct}", 0)
            self.log("warn", "power.stopping", percent=self.percent)
            self.guard._page("critical", f"Stopping Kazma: battery at {pct}",
                             "The machine has been on battery and the UPS is running low. "
                             "Kazma is stopped cleanly now; it starts again when power is back.",
                             kind="power.stopping", fingerprint="power:stopped", force=True)
        elif before not in ("battery", "stopped"):
            self.state = "battery"
            self.log("warn", "power.on_battery", percent=self.percent)
            self.guard._page("warn", f"On battery power ({pct})",
                             f"The machine lost mains power. Kazma keeps running; below "
                             f"{POWER_STOP_PERCENT}% it is stopped cleanly and started again when power "
                             "is back.", kind="power.on_battery", fingerprint="power:battery", force=True)
        self._write()

    def record(self) -> dict:
        return {"state": self.state, "percent": self.percent, "updated": time.time()}

    def _write(self) -> None:
        _update_state(power=self.record())


def heartbeat_body(snapshot: dict) -> bytes:
    """The heartbeat's JSON body, built from *snapshot* through an allowlist.

    Only these keys, each a state word, a number, a boolean or a list of
    check names, ever leave the machine; anything else in *snapshot* (a
    probe's detail, a path, an error) is dropped here, not by the caller.
    """
    guard = snapshot.get("guard") or {}
    kazma = snapshot.get("kazma") or {}
    disk = snapshot.get("disk") or {}
    power = snapshot.get("power")
    db = snapshot.get("database") or {}

    def number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return round(float(value), 1)

    why = [name for name in (kazma.get("why") or []) if isinstance(name, str) and _NAME_RE.match(name)]
    build = kazma.get("build")
    body = {
        "v": 1,
        "install": str(snapshot.get("install") or "")[:16],
        "sent_at": int(snapshot.get("sent_at") or 0),
        "seq": int(snapshot.get("seq") or 0),
        "guard": {
            "state": _word(guard.get("state")),
            "uptime_s": number(guard.get("uptime_s")),
            "code": str(guard.get("code") or "")[:12] or None,
        },
        "kazma": {
            "state": _word(kazma.get("state")),
            "ready": kazma.get("ready") is True,
            "why": sorted(set(why))[:8],
            "uptime_s": number(kazma.get("uptime_s")),
            "build": build if isinstance(build, str) and len(build) == 12
            and all(c in "0123456789abcdef" for c in build) else None,
        },
        "maintenance": snapshot.get("maintenance") is True,
        "database": {"state": _word(db.get("state")), "confirmed": db.get("confirmed") is True},
        "docker": _word(snapshot.get("docker")),
        "cloudflared": _word(snapshot.get("cloudflared")),
        "disk": {"free_pct": number(disk.get("free_pct")), "level": _word(disk.get("level"))},
        "power": None if not isinstance(power, dict) else {
            "on_battery": power.get("on_battery") is True,
            "percent": number(power.get("percent")),
        },
        # The open inside pages (unified alerts, Stage 2): catalog keys,
        # times, delivered -- receipts_allowlist, never a title or text.
        "pages": receipts_allowlist(snapshot.get("pages")),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _outside_watch_url(paired: str | None = None) -> str | None:
    """The observer's heartbeat URL: KAZMA_GUARD_OUTSIDE_WATCH_URL, else the
    observer the install was paired with (its address + /v1/heartbeat), else
    the default. https, or http to this machine (tests)."""
    raw = (os.environ.get("KAZMA_GUARD_OUTSIDE_WATCH_URL") or "").strip()
    if not raw and paired:
        raw = paired.rstrip("/") + "/v1/heartbeat"
    raw = raw or DEFAULT_OUTSIDE_WATCH_URL
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.hostname:
        return raw
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return raw
    return None


def _observer_key_path() -> Path:
    return _kazma_home() / "observer.key"


class OutsideWatch:
    """The heartbeat to the outside observer (see the section comment).

    Off until the owner pairs (no key file), or with
    KAZMA_GUARD_OUTSIDE_WATCH=off. Ticked from the guard's loop: the tick
    builds the snapshot and starts one send thread; the thread only sends
    and records the answer, which the next tick logs. Never raises out of a
    tick.
    """

    def __init__(self, guard: Guard, *, opener=None, key_path: Path | None = None,
                 restrict=None) -> None:
        self.guard = guard
        self.log = guard.log
        self.opener = opener or urllib.request.urlopen
        self.key_path = key_path or _observer_key_path()
        self.restrict = restrict or _restrict_to_owner
        self.state = "off"
        self._said = "off"
        self._key: bytes | None = None
        self._key_sig: tuple[int, int] | None = None
        self._key_state = "missing"
        # What the pairing file names (kazma observer pair writes JSON): the
        # install id the observer knows this install by, and its address.
        self._paired_install: str | None = None
        self._paired_url: str | None = None
        self._next_send = 0.0
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._result: tuple[str, int | None, float] | None = None
        self.last_ack: float | None = None
        self.skipped = 0

    def mode(self) -> str:
        return "off" if (os.environ.get("KAZMA_GUARD_OUTSIDE_WATCH") or "").strip().lower() in (
            "off", "0", "false", "no") else "auto"

    def on_guard_start(self) -> None:
        self._write()

    def tick(self) -> None:
        _tick_safely(self._tick, self._internal)

    def _internal(self, exc: BaseException) -> None:
        self.log("error", "outside_watch.internal_error",
                 error=f"{type(exc).__name__}: {exc}"[:300])

    def _load_key(self) -> bytes | None:
        """The pairing key, read once per version of the file, and only when
        the file is restricted to the guard's account."""
        try:
            st = self.key_path.stat()
        except OSError:
            self._key, self._key_sig, self._key_state = None, None, "missing"
            return None
        sig = (int(st.st_mtime_ns), int(st.st_size))
        if sig == self._key_sig:
            return self._key
        self._key_sig = sig
        self._key = None
        if not self.restrict(self.key_path):
            self._key_state = "key_unprotected"
            return None
        try:
            text = self.key_path.read_text(encoding="utf-8").strip()
        except OSError:
            self._key_state = "missing"
            return None
        install = url = None
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                self._key_state = "key_invalid"
                return None
            text = str(data.get("key") or "")
            raw_install = data.get("install")
            if isinstance(raw_install, str) and len(raw_install) == 16 and all(
                    c in "0123456789abcdef" for c in raw_install):
                install = raw_install
            raw_url = data.get("url")
            url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
        if len(text) < OBSERVER_KEY_MIN_CHARS:
            self._key_state = "key_invalid"
            return None
        self._paired_install, self._paired_url = install, url
        self._key = text.encode("utf-8")
        self._key_state = "ok"
        return self._key

    def _tick(self) -> None:
        self._take_result()
        if self.mode() == "off":
            self._set("off")
            return
        key = self._load_key()
        if key is None:
            self._set("off" if self._key_state == "missing" else self._key_state)
            return
        url = _outside_watch_url(self._paired_url)
        if url is None:
            self._set("bad_url")
            return
        now = time.monotonic()
        if now < self._next_send:
            return
        self._next_send = now + OUTSIDE_WATCH_EVERY_S
        if self._thread is not None and self._thread.is_alive():
            self.skipped += 1
            return
        if self.state == "off":
            self._set("reaching")
        self._seq += 1
        snapshot = self.guard._heartbeat_snapshot(self._seq)
        if self._paired_install:
            snapshot["install"] = self._paired_install
        thread = threading.Thread(target=self._send, args=(url, key, snapshot),
                                  name="kazma-guard-outside-watch", daemon=True)
        self._thread = thread
        thread.start()

    def _send(self, url: str, key: bytes, snapshot: dict) -> None:
        """On the send thread: fill what is slow to read, sign, post."""
        outcome, status = "unreachable", None
        try:
            snapshot["cloudflared"] = _cloudflared_state()
            snapshot["power"] = _power_state()
            body = heartbeat_body(snapshot)
            ts = int(time.time())
            path = urllib.parse.urlsplit(url).path or "/"
            req = _request(url, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "X-Kazma-Install": str(snapshot.get("install") or ""),
                "X-Kazma-Timestamp": str(ts),
                "X-Kazma-Signature": sign_request(key, ts, "POST", path, body),
            })
            with self.opener(req, timeout=OUTSIDE_WATCH_TIMEOUT_S) as resp:
                status = int(getattr(resp, "status", 0) or 0)
            outcome = "reached" if 200 <= status < 300 else "unreachable"
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            outcome = "rejected" if status in (401, 403) else "unreachable"
        except Exception:  # noqa: BLE001 -- a send on its own thread must never raise
            outcome = "unreachable"
        with self._lock:
            self._result = (outcome, status, time.time())

    def _take_result(self) -> None:
        with self._lock:
            result, self._result = self._result, None
        if result is None:
            return
        outcome, status, at = result
        if outcome == "reached":
            self.last_ack = at
        unchanged = outcome == self.state
        self._set(outcome, status=status)
        if outcome == "reached" and unchanged:
            # Each answered heartbeat's time reaches the state file: --status,
            # `kazma observer status` and Settings read its age from there.
            # Written only on a change of state, it froze at the first answer
            # ("last answered 403 s ago" over a minute's heartbeats, 2026-11-06).
            self._write()

    def _set(self, state: str, *, status: int | None = None) -> None:
        if state != self.state:
            self.state = state
            self._write()
        if state != self._said:
            self._said = state
            level = "info" if state in ("off", "reached", "reaching") else "warn"
            self.log(level, f"outside_watch.{state}", http_status=status)

    def record(self) -> dict:
        return {
            "state": self.state,
            "mode": self.mode(),
            "last_ack": self.last_ack,
            "skipped": self.skipped,
            "updated": time.time(),
        }

    def _write(self) -> None:
        _update_state(outside_watch=self.record())


def _outside_status_lines(state: dict) -> list[str]:
    """What --status says about the outside watch."""
    rec = state.get("outside_watch")
    if not isinstance(rec, dict):
        return []
    line = f"outside     : {rec.get('state')}"
    ack = rec.get("last_ack")
    if isinstance(ack, (int, float)) and ack > 0:
        line += f" (last answered {int(max(0, time.time() - ack))} s ago)"
    return [line]


# -- the pager's test page (unified alerts plan, Stage 3) --------------------
#
# Settings -> Alerts' "Send a test page" proves the path the guard pages by
# when Kazma is down. The server writes a request beside the reload request
# (``guard.test_page``: an id and the time it was asked); the running guard
# sends one card through a notifier of its own -- the credentials as a page
# would resolve them now, so a chat just changed in Settings is the one
# tested -- on a thread, and records the answer in its state file
# (``pager_test``), which the page reads. A request older than
# ``PAGER_TEST_MAX_AGE_S`` is answered "expired", never sent late.

PAGER_TEST_MAX_AGE_S = 300.0
_PAGER_TEST_ID = re.compile(r"^[0-9a-f]{8,32}$")


def _pager_test_path() -> Path:
    """``guard.test_page``, in the reload request's folder."""
    return _reload_path().with_name("guard.test_page")


def _test_page_card() -> str:
    """The test page (alert catalog ``guard.test_page``)."""
    return format_operator_card(
        "Guard", "info", "Test page",
        "Sent from Settings -> Alerts to check that the guard can reach you when Kazma cannot. "
        "Nothing is wrong.")


class PagerTest:
    """Answers the test page requests (see the section comment). Ticked from
    the guard's loop; the send runs on its own thread. Never raises out of a
    tick."""

    def __init__(self, guard: Guard, *, notifier_factory=None, path: Path | None = None) -> None:
        self.guard = guard
        self.log = guard.log
        self.path = path or _pager_test_path()
        self._factory = notifier_factory or (lambda: Notifier(
            guard.log, cwd=getattr(guard, "cwd", None), python=getattr(guard, "server_python", None)))
        self._seen: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._result: dict | None = None
        self._notifier: Notifier | None = None

    def tick(self) -> None:
        _tick_safely(self._tick, self._internal)

    def _internal(self, exc: BaseException) -> None:
        self.log("error", "pager_test.internal_error", error=f"{type(exc).__name__}: {exc}"[:300])

    def _signature(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (int(st.st_mtime_ns), int(st.st_size))

    def _tick(self) -> None:
        self._take_result()
        sig = self._signature()
        if sig is None or sig == self._seen:
            return
        if self._thread is not None and self._thread.is_alive():
            return  # one test at a time; the next tick takes this request
        self._seen = sig
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            data = {}
        try:
            self.path.unlink()
        except OSError:
            pass
        req_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        try:
            asked = float(data.get("ts") or 0.0) if isinstance(data, dict) else 0.0
        except (TypeError, ValueError):
            asked = 0.0
        if not _PAGER_TEST_ID.match(req_id):
            self.log("warn", "pager_test.bad_request")
            return
        if time.time() - asked > PAGER_TEST_MAX_AGE_S:
            self._write({"id": req_id, "state": "expired", "requested_at": asked, "at": time.time()})
            self.log("info", "pager_test.expired", id=req_id)
            return
        self._write({"id": req_id, "state": "sending", "requested_at": asked, "at": time.time()})
        self.log("info", "pager_test.requested", id=req_id)
        thread = threading.Thread(target=self._run, args=(req_id, asked),
                                  name="kazma-guard-pager-test", daemon=True)
        self._thread = thread
        thread.start()

    def _run(self, req_id: str, asked: float) -> None:
        """On the test's thread: a fresh notifier (a lookup of its own), one card."""
        result: dict = {"id": req_id, "requested_at": asked}
        try:
            notifier = self._factory()
            ok, why = notifier.send_now(_test_page_card())
            result.update(state="sent" if ok else "failed", source=notifier._source,
                          chat=notifier.chat)
            if not ok:
                result["error"] = (why.replace(notifier.token, "***") if notifier.token else why)[:200]
            # A test page is a page: the last one, as Settings shows it.
            result["outcome"] = notifier.last_outcome
            with self._lock:
                self._notifier = notifier if ok else None
        except Exception as exc:  # noqa: BLE001 -- a test on its own thread must never raise
            result.update(state="failed", error=f"{type(exc).__name__}"[:200])
        result["at"] = time.time()
        with self._lock:
            self._result = result

    def _take_result(self) -> None:
        with self._lock:
            result, self._result = self._result, None
            fresh, self._notifier = self._notifier, None
        if result is None:
            return
        outcome = result.pop("outcome", None)
        if isinstance(outcome, dict):
            _update_state(pager_test=result, pager=outcome)
        else:
            self._write(result)
        self.log("info" if result.get("state") == "sent" else "warn",
                 f"pager_test.{result.get('state')}", id=result.get("id"),
                 source=result.get("source"), error=result.get("error"))
        main = getattr(self.guard, "notify", None)
        if fresh is not None and main is not None and getattr(main, "_source", "") != "env" \
                and fresh._source == "vault":
            # The settings the test just proved are the pager's from now on
            # (a chat changed in Settings -> Alerts): the guard's own
            # notifier resolved them once, at its first page.
            main.token, main.chat, main._source = fresh.token, fresh.chat, "vault"

    def _write(self, record: dict) -> None:
        _update_state(pager_test=record)


# -- supervisor -------------------------------------------------------


class Guard:
    # Defaults for the per-child bookkeeping (set properly in __init__ and
    # run()); class-level so a partially built Guard still behaves.
    spawned_at = 0.0
    _reload_seen: tuple[int, int] | None = None
    _stopped_for_reload = False
    _stopped_for_maintenance = False
    _last_stop_graceful = False
    _last_beat = 0.0
    _internal_errors = 0
    deps = None
    disk = None
    outside = None
    dbsvc = None
    started_at = 0.0
    _started_mono = 0.0
    # The last readiness answer (state, check names, database), for the
    # outside watch: see _note_probe.
    _probe_note: dict | None = None

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
        self.server_python = server_python
        self.health_url = health_url()
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
        # A restart reason's last page and the restarts since (notify_restart:
        # one page an hour per reason while it repeats).
        self._restart_paged: dict[str, tuple[float, int]] = {}
        # The child exited by itself while it started (its stderr is then
        # its last words; a child the guard stopped has none).
        self._exited_while_starting = False
        # Kazma is up and waits for a dependency outside it (``waits_for``):
        # since when, for what, and who paged it (_note_waiting).
        self._waiting: dict | None = None
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
        # The document worker broker beside Kazma (DocumentBroker).
        self.broker = DocumentBroker(self)
        # Docker Desktop and the containers Kazma uses (Dependencies).
        self.deps = Dependencies(self)
        # Free disk space where Kazma writes (DiskWatch).
        self.disk = DiskWatch(self)
        # The heartbeat to the outside observer (OutsideWatch).
        self.outside = OutsideWatch(self)
        # The database's OS service (DatabaseServiceWatch).
        self.dbsvc = DatabaseServiceWatch(self)
        # The power source: a UPS on battery (PowerWatch, plan B2c).
        self.power = PowerWatch(self)
        # What this guard paged for an incident still open (PageReceipts,
        # Stage 2), and Settings -> Alerts' test page (PagerTest, Stage 3).
        # Every page's outcome is heard once (Notifier._heard) and reaches
        # both records: the last page's outcome in the state file (``pager``,
        # which Settings -> Alerts reads) and, for a held page that went out
        # after all, its page receipt (``pages``, which the heartbeat carries).
        self.receipts = PageReceipts()
        self._held_kinds: set[str] = set()
        self.notify.on_delivered = self.receipts.delivered_later
        self.pager_test = PagerTest(self)
        self.notify.on_outcome = self._note_page_outcome

    # -- lifecycle ----------------------------------------------------

    def _note_page_outcome(self, outcome: dict) -> None:
        """The last page's outcome, for Settings -> Alerts (state ``pager``)."""
        _update_state(pager=outcome)

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

    def _say_needs_setup(self, needs: tuple[str, ...], detail: str) -> None:
        """Kazma is up and waits for its owner (``needs_setup``: a provider
        key). Said once and paged once: never a restart, which cannot add a
        key (the drill VM, 2026-11-06: a fresh install with no key was
        restarted every 15 minutes while its owner tried to set it up)."""
        self._waiting_setup = True
        self.log("warn", "health.needs_setup", needs=",".join(needs), detail=detail)
        home = self.health_url.split("/health", 1)[0] + "/"
        self._page("warn", "Kazma is up and needs setup",
                   f"{detail}. Open {home} and go to Settings -> Providers. "
                   "Not restarting: a restart cannot add what is missing.",
                   kind="health.needs_setup", fingerprint="needs-setup")

    def _deps_speaks_for(self, waits: tuple[str, ...]) -> bool:
        """The guard's own Docker watch pages this wait (one page per
        problem): Kazma waits for Docker, and the watch sees the engine down."""
        deps = getattr(self, "deps", None)
        return bool("docker" in waits and deps is not None and deps.owns_engine_alerts()
                    and deps.engine_up is False)

    def _note_waiting(self, waits: tuple[str, ...], detail: str) -> None:
        """Kazma is up and waits for a dependency outside it (``waits_for``:
        the Docker engine). Never restarted for it: a restart cannot bring it
        back (2026-11-12: after a Windows update restart nobody was signed in
        to start Docker Desktop, and the guard restarted a Kazma that served
        chat 18 times in six hours, paging each time). Said once; paged once
        after ``WAIT_PAGE_S``, unless the guard's Docker watch pages it."""
        now = time.monotonic()
        w = getattr(self, "_waiting", None)
        if w is None or w["waits"] != waits:
            self.log("warn", "health.waiting_for", waits_for=",".join(waits), detail=detail,
                     restart="never: a restart cannot bring it back")
            w = {"waits": waits, "since": w["since"] if w else now,
                 "paged": w["paged"] if w else ""}
            self._waiting = w
        if w["paged"] or now - w["since"] < WAIT_PAGE_S:
            return
        if self._deps_speaks_for(waits):
            w["paged"] = "deps"
            self.log("info", "health.waiting_paged_by", by="deps.engine_down")
            return
        what = ", ".join(waits)
        sent = self._page(
            "warn", "Kazma is up, but not ready",
            f"{detail}. Kazma keeps serving what does not need {what}; what needs it waits. "
            f"Not restarting: a restart cannot bring {what} back.",
            kind="health.dependency_down", fingerprint="waiting-for")
        w["paged"] = "guard" if sent else "not_sent"

    def _end_waiting(self) -> None:
        """Kazma is ready: a wait it was in is over (one recovery card, when
        this guard paged the wait)."""
        w = getattr(self, "_waiting", None)
        if w is None:
            return
        self._waiting = None
        self.log("info", "health.wait_over", waits_for=",".join(w["waits"]),
                 waited_s=int(time.monotonic() - w["since"]))
        if w["paged"] == "guard":
            self._page("success", "Kazma is ready again",
                       f"{', '.join(w['waits'])} answers again; no restart was needed.",
                       kind="health.ready_again", fingerprint="waiting-over")

    def _page(
        self,
        severity: str,
        title: str,
        detail: str = "",
        *,
        kind: str,
        fingerprint: str = "",
        force: bool = False,
    ) -> bool:
        """Send a Guard operator card. Same fingerprint inside the cooldown
        is logged, not sent. Crash-loop callers pass force=True.

        *kind* is the page's entry in Kazma's alert catalog (the guard's
        copy, ``alert_catalog.json``); a kind the copy does not hold is said
        once in guard.log, and the page is sent either way.

        One page per problem: a kind whose condition another sender pages
        (``_page_held_by``) is recorded, not sent (``guard.page_held``, once
        a kind until it pages), and False is returned, so the caller sends
        no recovery for it. A page sent that opens an incident is a receipt
        until its recovery (``PageReceipts``)."""
        known = guard_alert_kinds()
        if known and kind not in known and kind not in _UNKNOWN_KINDS_SAID:
            _UNKNOWN_KINDS_SAID.add(kind)
            self.log("warn", "guard.page_kind_unknown", kind=kind, catalog=str(ALERT_CATALOG_PATH))
        held = self._page_held_by(kind)
        held_kinds = getattr(self, "_held_kinds", None)
        if held_kinds is None:  # a Guard built without __init__ (tests)
            held_kinds = self._held_kinds = set()
        if held:
            if kind not in held_kinds:
                held_kinds.add(kind)
                self.log("info", "guard.page_held", kind=kind, paged_by=held, title=title[:120])
            return False
        held_kinds.discard(kind)
        fp = fingerprint or f"{title}\n{detail}"
        if not force and not self._should_page(fp):
            self.log("info", "guard.page_suppressed", title=title[:120], kind=kind)
            return False
        if force:
            self._last_page_fp = fp
            self._last_page_at = time.monotonic()
        text = format_operator_card("Guard", severity, title, detail)
        delivered = self.notify.send(text) is True
        receipts = getattr(self, "receipts", None)
        if receipts is not None:
            receipts.paged(kind, delivered, text)
        return True

    def _page_held_by(self, kind: str) -> str:
        """Who pages *kind*'s condition instead of this guard ("" for the
        guard itself): ``_inside_hold`` over the guard's receipts and its
        current server's, with who can speak now. Never raises."""
        try:
            receipts = getattr(self, "receipts", None)
            if receipts is None or not (_alert_table().get(kind) or {}).get("condition"):
                return ""
            speaks = {"guard": True, "ops": self._server_speaks(), "watch": self._watch_answers()}
            pages = receipts.own() + PageReceipts.kazma(getattr(self, "spawned_at", 0.0))
            return _inside_hold(kind, pages, speaks)
        except Exception as exc:  # a page is never lost to this check
            self.log("warn", "guard.page_hold_failed", kind=kind, error=f"{type(exc).__name__}: {exc}"[:200])
            return ""

    def _server_speaks(self) -> bool:
        """The server runs and answers: it can page what it sees."""
        proc = getattr(self, "proc", None)
        note = getattr(self, "_probe_note", None) or {}
        return bool(proc is not None and proc.poll() is None and note.get("answered")
                    and float(note.get("at") or 0.0) >= getattr(self, "spawned_at", 0.0))

    def _watch_answers(self) -> bool:
        """Kazma Watch answered this guard's heartbeat recently."""
        outside = getattr(self, "outside", None)
        ack = getattr(outside, "last_ack", None)
        return bool(outside is not None and outside.state == "reached" and isinstance(ack, (int, float))
                    and time.time() - ack <= WATCH_ANSWERED_S)

    def _open_page_receipts(self) -> list[dict]:
        """For the heartbeat: this guard's open receipts and its server's."""
        receipts = getattr(self, "receipts", None)
        if receipts is None:
            return []
        return receipts.own() + PageReceipts.kazma(getattr(self, "spawned_at", 0.0))

    def notify_restart(self, reason: str, delay_s: float, *, tail: str = "") -> bool:
        """Page a restart: once an hour per ``reason`` while it repeats
        (``RESTART_REPAGE_S``; every restart is in guard.log), saying how
        many more there were since the last page. *tail* is the server's
        last words: pass it only when the server ended by itself."""
        now = time.monotonic()
        paged = getattr(self, "_restart_paged", None)
        if paged is None:
            paged = self._restart_paged = {}
        self._awaiting_recovery = reason
        last_page = paged.get(reason)
        if last_page is not None and now - last_page[0] < RESTART_REPAGE_S:
            paged[reason] = (last_page[0], last_page[1] + 1)
            self.log("info", "notify.restart_collapsed", reason=reason,
                     since_page_s=int(now - last_page[0]), restarts_since=last_page[1] + 1)
            return False
        title = f"Kazma stopped: {reason}"
        detail = f"Restarting in {int(delay_s)}s (attempt {self.restarts})."
        if last_page is not None and last_page[1]:
            detail += (f"\nIt restarted {last_page[1]} more time{'s' if last_page[1] != 1 else ''} "
                       "for this since the last page.")
        deps = getattr(self, "deps", None)
        note = deps.restart_note() if deps is not None else ""
        if note:
            detail += f"\n{note}"
        if tail:
            last = "\n".join(tail.splitlines()[-8:])[-600:]
            detail += f"\nIts last words (server.stderr.log):\n{last}"
        sent = self._page("warn", title, detail, kind="guard.restarting", fingerprint=reason)
        paged[reason] = (now, 0)
        return sent

    def _ended_by_itself(self, reason: str) -> bool:
        """The server ended on its own (a crash, an exit while starting):
        what it wrote on stderr is its last words. A server the guard
        stopped (never ready, not answering) wrote only its boot's lines --
        on 2026-11-12 every page quoted the same settings warning and a
        weights progress bar as the reason."""
        if reason.startswith("process exited"):
            return True
        return reason == "never became healthy" and bool(getattr(self, "_exited_while_starting", False))

    def notify_recovered(self) -> bool:
        """One recovery card after a kill/unhealthy restart, if we were waiting."""
        reason = self._awaiting_recovery
        # A healthy start ends every series of restarts: the next one pages.
        getattr(self, "_restart_paged", {}).clear()
        if not reason:
            return False
        self._awaiting_recovery = ""
        title = "Kazma is healthy again"
        detail = f"It answers again after: {reason}."
        return self._page("success", title, detail, kind="guard.recovered", fingerprint=f"recovered:{reason}")

    def _raise_own_priority(self) -> None:
        """Run at the priority of an interactive program (2026-09-30).

        The KazmaAgent task starts the guard at Task Scheduler's default
        priority, 7 (background tasks): BELOW_NORMAL, and a child inherits a
        below-normal class and its parent's memory and I/O priority. The
        server ran below every normal program on the machine and froze
        whenever something heavy ran beside it. The server raises itself at
        boot; the guard does the same, so what it spawns starts at normal
        priority -- with the same file, loaded by path, because the guard
        never imports the app (kazma_core/process_priority.py is standard
        library only; a test holds it to that).
        """
        import importlib.util

        path = REPO_ROOT / "kazma-core" / "kazma_core" / "process_priority.py"
        name = "kazma_guard_process_priority"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            # Registered before it runs (importlib's recipe for a source file):
            # @dataclass looks its module up in sys.modules.
            sys.modules[name] = module
            spec.loader.exec_module(module)
            report = module.ensure_interactive_priority()
        except (OSError, ImportError, SyntaxError, AttributeError) as exc:
            self.log("warn", "guard.priority_unavailable", error=f"{type(exc).__name__}: {exc}")
            return
        if report.raised or report.errors or report.kept:
            self.log("warn" if report.errors else "info", "guard.priority",
                     raised=report.raised, before=report.describe(report.before),
                     after=report.describe(report.after), kept=report.kept,
                     errors=report.errors)

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
        self._waiting_setup = False  # a new child: its own setup wait, if any
        # The boot says it waits for its database (read_boot_status): the
        # start budget then runs from the latest word, a page goes out once,
        # and a recovery card once it is ready (resilience plan A3).
        waiting_for: str | None = None
        wait_logged = False
        wait_paged = False
        self._exited_while_starting = False
        while time.monotonic() < deadline and not self._stop:
            self._beat()
            self._broker_tick()
            if self._reload_pending() and self._take_reload_request():
                # New code landed while this child was booting, and it may
                # already have imported part of the old. Start over rather
                # than finish a boot of mixed builds.
                return False
            if read_pause() is not None and self.proc is not None:
                # A pause taken while the child boots (kazma update is about
                # to replace the packages it is loading) stops it now. The
                # boot used to run to the end -- minutes -- and only then
                # meet the pause in _supervise.
                self.log("info", "maintenance.requested_while_starting", pid=self.proc.pid)
                self._stopped_for_maintenance = True
                self._last_stop_graceful = stop_child(self.proc, self.log, grace_s=GRACEFUL_STOP_S)
                return False
            if self.proc and self.proc.poll() is not None:
                self._exited_while_starting = True
                self.log("error", "child.exited_during_startup",
                         code=self.proc.returncode,
                         after_s=round(time.monotonic() - started, 1))
                return False
            boot_result = probe(self.health_url, PROBE_TIMEOUT_S)
            self._note_probe(boot_result)
            ok, detail = boot_result
            needs = (tuple(getattr(boot_result, "needs_setup", ()) or ())
                     if getattr(boot_result, "answered", False) else ())
            waits = _answered_waits(boot_result)
            if ok or needs or waits:
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
                if not ok and needs:
                    # Up, and waiting for its owner: started, not "never
                    # ready". _supervise holds it the same way.
                    self.log("info", "child.up_needs_setup", needs=",".join(needs),
                             detail=detail, after_s=round(time.monotonic() - started, 1))
                    self._say_needs_setup(needs, detail)
                    return True
                if not ok:
                    # Up, and waiting for a dependency outside it (the
                    # Docker engine): started, not "never ready". A restart
                    # cannot bring the dependency back; _supervise holds it.
                    self.log("info", "child.up_waiting_for", waits_for=",".join(waits),
                             detail=detail, after_s=round(time.monotonic() - started, 1))
                    self._note_waiting(waits, detail)
                    return True
                self.log("info", "child.ready",
                         detail=detail, after_s=round(time.monotonic() - started, 1))
                self._end_waiting()
                if wait_paged:
                    self._page("success", "Kazma is up: its database answers again",
                               f"The boot waited {round(time.monotonic() - started)} s "
                               "for its database and went on by itself.",
                               kind="child.database_answers", fingerprint="boot-db-wait-over")
                return True
            last = detail
            status = read_boot_status(spawned_at)
            waiting_for = str(status.get("waiting_for") or "") or None if status else None
            if waiting_for:
                # Waiting is not hanging: the boot rewrites the record on
                # every try, so the budget runs from the latest one.
                deadline = max(deadline, time.monotonic() + START_TIMEOUT_S)
                reason = str(status.get("reason") or "")[:300]
                if not wait_logged:
                    self.log("warn", "child.waiting_for_database",
                             reason=reason, since=status.get("since"))
                    wait_logged = True
                if not wait_paged and time.monotonic() - started >= BOOT_WAIT_PAGE_S:
                    self._page(
                        "warn", "Kazma is waiting for its database",
                        "The server started and is waiting for its settings "
                        "database; it goes on by itself the moment it answers. "
                        "Not restarting it."
                        + (f" The database said: {reason}" if reason else ""),
                        kind="child.waiting_for_database", fingerprint="boot-db-wait")
                    # Not while another page has this outage (one page per
                    # problem): asked again next try, and no recovery for it.
                    wait_paged = "child.waiting_for_database" not in self._held_kinds
            # Heartbeat while waiting, so a slow boot is visibly different
            # from a hang when someone reads this log at 3am.
            if time.monotonic() >= next_progress:
                self.log("info", "child.starting",
                         waited_s=round(time.monotonic() - started),
                         budget_s=START_TIMEOUT_S, last=last,
                         waiting_for=waiting_for)
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
        wake_on_pause: bool = False,
        wake_on_pause_change: str | None = None,
        wake_on_deps_recovered: bool = False,
    ) -> bool:
        """Interruptible sleep so shutdown, --reload, and a dead child stay responsive.

        Wakes early only for what the caller handles: a reload request the
        guard has not dealt with yet (``wake_on_reload``), a pause
        (``wake_on_pause``), the pause lifted or rewritten
        (``wake_on_pause_change``: the signature of the one the caller
        holds), a child that exited (``wake_on_child_exit``), Docker
        answering again after an outage (``wake_on_deps_recovered``: the
        restart backoff ends, Kazma's database is back), or shutdown.
        Beats the heartbeat.
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
            self._broker_tick()
            if wake_on_reload and self._reload_pending():
                return True
            if wake_on_pause and read_pause() is not None:
                return True
            if wake_on_pause_change is not None and _pause_signature(read_pause()) != wake_on_pause_change:
                return True
            if wake_on_deps_recovered:
                deps = getattr(self, "deps", None)
                if deps is not None and deps.take_recovered():
                    self.log("info", "guard.backoff_ended", why="docker answers again")
                    return True
            if (
                wake_on_child_exit
                and self.proc is not None
                and self.proc.poll() is not None
            ):
                return True
            time.sleep(min(1.0, remaining))
        return True

    def _broker_tick(self) -> None:
        # Every wait the guard makes supervises the broker too (about once a
        # second). A Guard built without __init__ (tests) has none.
        broker = getattr(self, "broker", None)
        if broker is not None:
            broker.tick()
        pager_test = getattr(self, "pager_test", None)
        if pager_test is not None:
            pager_test.tick()
        deps = getattr(self, "deps", None)
        if deps is not None:
            deps.tick()
        disk = getattr(self, "disk", None)
        if disk is not None:
            disk.tick()
        outside = getattr(self, "outside", None)
        if outside is not None:
            outside.tick()
        dbsvc = getattr(self, "dbsvc", None)
        if dbsvc is not None:
            dbsvc.tick()
        power = getattr(self, "power", None)
        if power is not None:
            power.tick()

    def _note_probe(self, result: object) -> None:
        """Remember the last readiness answer, for the outside watch."""
        try:
            ready = bool(result[0])  # type: ignore[index]
        except (TypeError, IndexError, KeyError):
            return
        self._probe_note = {
            "ready": ready,
            "answered": getattr(result, "answered", True) is not False,
            "checks": list(getattr(result, "checks", ()) or ()),
            "database": database_state(result),
            "at": time.time(),
        }

    def _heartbeat_snapshot(self, seq: int) -> dict:
        """What the outside watch may report. heartbeat_body keeps only its
        allowlist of this; the send thread adds cloudflared and power."""
        now = time.time()
        paused = read_pause() is not None
        proc = getattr(self, "proc", None)
        note = self._probe_note
        if note is not None and float(note.get("at") or 0.0) < self.spawned_at:
            note = None  # an answer from the child before this one
        if proc is None or proc.poll() is not None:
            kazma_state = "down"
        elif note is None:
            kazma_state = "starting"
        elif not note.get("answered"):
            kazma_state = "unreachable"
        else:
            kazma_state = "ready" if note.get("ready") else "not_ready"
        alive = kazma_state not in ("down",)
        db = (note or {}).get("database") or {}
        deps = getattr(self, "deps", None)
        engine = getattr(deps, "engine_up", None) if deps is not None else None
        disk = getattr(self, "disk", None)
        return {
            "install": _install_hash(),
            "sent_at": int(now),
            "seq": int(seq),
            "guard": {
                "state": "paused" if paused else "supervising",
                "uptime_s": now - self.started_at if self.started_at else None,
                "code": _file_sha(Path(__file__).resolve())[:12],
            },
            "kazma": {
                "state": kazma_state,
                "ready": kazma_state == "ready",
                "why": list((note or {}).get("checks") or []),
                "uptime_s": now - self.spawned_at if alive and self.spawned_at else None,
                "build": _git_head(self.cwd),
            },
            "maintenance": paused,
            "database": {"state": db.get("state"), "confirmed": db.get("confirmed") is True},
            "docker": {True: "up", False: "down"}.get(engine, "unknown"),
            "disk": disk.worst() if disk is not None else None,
            "pages": self._open_page_receipts(),
        }

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
        self._raise_own_priority()
        # NOT resolved here. describe() reaches into the vault, which means
        # importing kazma_core -- measured at tens of seconds on a cold
        # cache. Doing it before spawning delays the server by exactly that
        # long for a log line. It is emitted after the child is up instead.

        # Order matters: reap a known orphan from a previous guard FIRST,
        # then check whether anything else is serving. Reversed, a booting
        # orphan is invisible to the probe (nothing bound yet) and both
        # instances race for the port.
        reap_orphan(self.log)
        self.started_at = time.time()
        self._started_mono = time.monotonic()
        # From here on this is the guard --reload, --status and the server
        # see as alive (the heartbeat; see HEARTBEAT_EVERY_S).
        _update_state(guard_pid=os.getpid(), guard_started=time.time(),
                      guard_features=list(GUARD_FEATURES), pause_held=None,
                      health_url=self.health_url,
                      guard_code=_file_sha(Path(__file__).resolve()))
        self._beat(force=True)

        if self._foreign_server_present():
            title = "Kazma guard did not start"
            detail = (
                f"Something is already serving {self.health_url}. "
                "Stop the existing instance first, then start the guard — "
                "otherwise it would supervise a process it did not launch."
            )
            self.log("error", "guard.refused_to_start")
            self._page("error", title, detail, kind="guard.refused_to_start", force=True)
            print(format_operator_card("Guard", "error", title, detail), file=sys.stderr)
            return 2
        # Only a guard that will supervise touches the broker: a refused one
        # rewrote the broker's record (and could reap it) on its way out.
        self.broker.on_guard_start()
        self.deps.on_guard_start()
        self.disk.on_guard_start()
        self.power.on_guard_start()
        self.outside.on_guard_start()
        self.dbsvc.on_guard_start()
        # An earlier guard's receipts: its watches start over and page again.
        self.receipts.clear()

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
                                    kind="guard.port_still_held",
                                    fingerprint=f"stale:{_stale_pid}",
                                    force=True,
                                )
                            except Exception:
                                self.log("debug", "guard.port_still_held.notify_failed")
                    else:
                        self._last_stale_holder_notified = None
                    # The broker first, when Kazma asked for one: it attests
                    # while Kazma boots (minutes), so it is ready before it.
                    self.broker.before_spawn()
                    self.deps.before_spawn()
                    spawned_at = time.time()
                    self.spawned_at = spawned_at
                    self._stopped_for_reload = False
                    self._stopped_for_maintenance = False
                    self.proc = spawn(self.cmd, self.cwd, self.log,
                                      {**self.broker.server_env(), **self.deps.server_env()})
                    # --reload reads this: a child spawned after its request
                    # already runs the code it asked for.
                    _update_state(child_spawned=spawned_at)

                    if self._wait_ready(spawned_at):
                        # A ready server reached its settings database, so a
                        # lookup that failed while it started succeeds now:
                        # ask at once, not when the rate limit next allows
                        # (live 2026-10-29: Kazma ready at 11:19, the held
                        # pages sent at 11:27). Held pages go before the
                        # recovery card, in the order they happened.
                        self.notify.retry_lookup()
                        self.notify.flush_held()
                        if first:
                            # Safe to resolve now: the server is already
                            # running, so a slow vault import costs nothing
                            # but a delayed alert.
                            self.log("info", "guard.notifier",
                                     target=self.notify.describe())
                        # NO notification on a healthy start. Kazma's own
                        # lifecycle_notifier sends one start card from inside
                        # the app ("Kazma restarted", the downtime, each chat
                        # app's connection). The guard exists to
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
                    elif self._stopped_for_maintenance:
                        reason = "maintenance"
                    else:
                        reason = "never became healthy"
                        stop_child(self.proc, self.log, grace_s=NEVER_READY_STOP_S)

                if self._stop:
                    break
                if self.once:
                    self.log("info", "guard.once_exit", reason=reason)
                    self.broker.shutdown()
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
                                kind="guard.operator_reload",
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
                        kind="guard.crash_loop",
                        force=True,
                    )
                    self.recent.clear()
                    # An operator reload ends the cooldown: it is someone
                    # acting on exactly this page. So does a pause (kazma
                    # update repairing the packages that crash it): the
                    # guard holds at once, and the resume after the repair
                    # starts Kazma without waiting out the cooldown.
                    # Docker answering again ends it too: the database was
                    # what crashed it.
                    self._sleep(CRASH_LOOP_COOLDOWN_S, wake_on_reload=True, wake_on_pause=True,
                                wake_on_deps_recovered=True)
                    continue

                delay = self._backoff()
                # What the server said on stderr before it went: a traceback,
                # a native crash's stacks, a refusal at boot.
                tail = server_stderr_tail(self.proc)
                extra = {"stderr_tail": tail} if tail else {}
                self.log("warn", "guard.restarting", reason=reason, in_s=delay,
                         restarts=self.restarts, **extra)
                self.notify_restart(reason, delay, tail=tail if self._ended_by_itself(reason) else "")
                # A pause during the backoff holds at once (the gate before
                # the next spawn), not after up to five minutes; Docker
                # answering again starts Kazma at once.
                self._sleep(delay, wake_on_reload=True, wake_on_pause=True,
                            wake_on_deps_recovered=True)
            except Exception as exc:  # noqa: BLE001 -- see the comment above the try
                self._internal_error(exc)

        if self.proc:
            stop_child(self.proc, self.log, grace_s=GRACEFUL_STOP_S)
        # After Kazma, which may be in the middle of a job on it.
        self.broker.shutdown()
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
                kind="guard.internal_error",
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
        # The answer a program waiting on this pause reads (kazma update,
        # stop_for_maintenance): the guard holds -- no child runs, and none
        # starts until the pause is lifted. Only this gate writes it, and
        # it is reached only with no live child.
        held = _pause_signature(pause)
        _update_state(pause_held=pause.get("since"))
        # Nothing of Kazma runs while the guard holds: an update may be
        # replacing the broker's code too. The next spawn starts it again.
        broker = getattr(self, "broker", None)
        if broker is not None:
            broker.on_pause()
        # Nor does the guard start Docker or containers while held: the
        # pause may be for Docker itself (an update, a restart by hand).
        deps = getattr(self, "deps", None)
        if deps is not None:
            deps.on_pause()

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
            kind="maintenance.active",
            fingerprint="pause",
            force=True,
        )

        next_nag = time.monotonic() + PAUSE_NAG_EVERY_S
        while not self._stop:
            # Wakes within a second of a resume (it used to take up to ten).
            self._sleep(10.0, wake_on_pause_change=held)
            if self._stop:
                return True
            current = read_pause()
            if current is not None and _pause_signature(current) != held:
                # Rewritten (a new TTL, another program's pause): held
                # still, and the new one is acknowledged too.
                pause, held = current, _pause_signature(current)
                _update_state(pause_held=current.get("since"))
                continue
            if current is None:
                _update_state(pause_held=None)
                self.log("info", "maintenance.resumed")
                self._page(
                    "info",
                    "Kazma supervision resumed",
                    "Restarting the server.",
                    kind="maintenance.resumed",
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
                    kind="maintenance.still_paused",
                    fingerprint=f"pause-nag:{mins // 60}",
                )
        return True

    def _supervise(self) -> str:
        """Watch a healthy child. Returns the reason it needs restarting.

        Two kinds of failed probe. No answer (a timeout, a refused
        connection): the process is wedged or gone, and FAILURES_TO_KILL of
        them in a row restart it. An answer that says "not ready" (a 503
        naming the database): the process is alive and its dependency is
        not -- a restart cannot fix that, so it is ridden out for
        DEPENDENCY_OUTAGE_S and paged once, then restarted if it persists.

        Unless the answer says the database is away and Kazma's own check is
        still watching it (``app_watches_database``, resilience plan A3): a
        restart cannot bring the database back, and a restarted Kazma only
        waits for it again at boot, so the restart is held for as long as
        that holds. The page is then Kazma's own (``database.away``, and
        ``database.back`` when it returns); the guard pages only when Kazma's
        page has not reached anyone APP_PAGE_GRACE_S after it was due.
        """
        consecutive = 0
        unanswered = 0
        outage_since: float | None = None
        outage_paged = False
        app_paged = False
        held_logged_at: float | None = None
        unrunnable = 0
        next_probe = time.monotonic() + PROBE_INTERVAL_S
        while not self._stop:
            # Probes keep their cadence whatever wakes the sleep: a wake-up
            # handled below is not a probe tick. A reload flag that woke
            # every sleep made this a busy loop of 53 probes a second for
            # 47 hours (2026-09-20..22).
            woke_early = self._sleep(max(0.0, next_probe - time.monotonic()),
                                     wake_on_child_exit=True, wake_on_reload=True,
                                     wake_on_pause=True)
            if self._stop:
                return "guard shutting down"
            notify = getattr(self, "notify", None)
            if not woke_early and notify is not None:
                notify.flush_held()  # pages held while the guard could not send

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

            result = probe(self.health_url, PROBE_TIMEOUT_S)
            self._note_probe(result)
            ok, detail = result
            needs = (tuple(getattr(result, "needs_setup", ()) or ())
                     if getattr(result, "answered", False) else ())
            waits = _answered_waits(result)
            if not ok and (needs or waits):
                # Up, waiting for its owner (a provider key) or for a
                # dependency outside it (the Docker engine): no restart can
                # fix it, so none counts toward one. Said once.
                if needs and not getattr(self, "_waiting_setup", False):
                    self._say_needs_setup(needs, detail)
                if waits:
                    self._note_waiting(waits, detail)
                consecutive = 0
                unanswered = 0
                outage_since = None
                outage_paged = False
                held_logged_at = None
                unrunnable = 0
                continue
            if ok and getattr(self, "_waiting_setup", False):
                self._waiting_setup = False
                self.log("info", "health.setup_done")
            if ok:
                self._end_waiting()
                if consecutive:
                    self.log("info", "health.recovered", after_failures=consecutive)
                if outage_paged and not app_paged:
                    # When Kazma paged the outage it says it is over itself
                    # (database.back): a second card would say the same.
                    self._page(
                        "success",
                        "Kazma is ready again",
                        "The dependency it was waiting for is back; no restart was needed.",
                        kind="health.ready_again",
                        fingerprint="not-ready-recovered",
                    )
                if held_logged_at is not None and outage_since is not None:
                    self.log("info", "health.restart_hold_over",
                             not_ready_s=int(time.monotonic() - outage_since))
                consecutive = 0
                unanswered = 0
                outage_since = None
                outage_paged = False
                app_paged = False
                held_logged_at = None
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
                        kind="health.probe_unrunnable",
                        fingerprint="probe-unrunnable",
                    )
                continue
            unrunnable = 0

            consecutive += 1
            # A result without the flag gets the old rule (restart after
            # FAILURES_TO_KILL): only probe() says a failure was answered.
            # An answer that says only a restart clears it is no outage to
            # ride out either.
            answered = bool(getattr(result, "answered", False)) and not bool(
                getattr(result, "restart_required", False))
            now = time.monotonic()
            if answered:
                unanswered = 0
                if outage_since is None:
                    outage_since = now
            else:
                unanswered += 1
            db = database_state(result) if answered else None
            watching = app_watches_database(db)
            if db and db.get("paged"):
                app_paged = True
            self.log("warn", "health.failed", detail=detail, answered=answered,
                     consecutive=consecutive, threshold=FAILURES_TO_KILL,
                     database=(db or {}).get("state"))
            if unanswered >= FAILURES_TO_KILL:
                # Alive but not answering -- the case no OS supervisor catches.
                stop_child(self.proc, self.log, grace_s=UNHEALTHY_STOP_S)
                return f"unhealthy ({detail})"
            if outage_since is not None and now - outage_since >= DEPENDENCY_OUTAGE_S:
                if watching:
                    # The database is away and Kazma's own check is watching
                    # it: a restart would only have Kazma wait for it again,
                    # at boot. Held; said once, and again every
                    # DEPENDENCY_OUTAGE_S while it lasts.
                    if held_logged_at is None or now - held_logged_at >= DEPENDENCY_OUTAGE_S:
                        self.log("warn", "health.restart_held", detail=detail,
                                 not_ready_s=int(now - outage_since),
                                 database_away_s=(db or {}).get("away_s"))
                        held_logged_at = now
                else:
                    # Ridden out as long as it is worth: the dependency may be
                    # back and Kazma's own connections what is stuck.
                    stop_child(self.proc, self.log, grace_s=UNHEALTHY_STOP_S)
                    return f"unhealthy ({detail}; not ready for {int(now - outage_since)}s)"
            if answered and consecutive >= FAILURES_TO_KILL and not outage_paged:
                if app_paged:
                    # Kazma's own page (database.away) reached the owner.
                    outage_paged = True
                    self.log("warn", "health.dependency_down", detail=detail,
                             paged_by="kazma", database_held=watching)
                    continue
                if app_pages_database_outage(db):
                    continue  # Kazma pages it itself, shortly
                outage_paged = True
                self.log("warn", "health.dependency_down", detail=detail,
                         restart_after_s=int(DEPENDENCY_OUTAGE_S), database_held=watching)
                if watching:
                    tail = ("Kazma's own check is watching the database and Kazma "
                            "goes on the moment it answers; the guard does not restart it.")
                else:
                    tail = (f"If it lasts {int(DEPENDENCY_OUTAGE_S // 60)} min the guard "
                            "restarts Kazma anyway.")
                self._page(
                    "warn",
                    "Kazma is up, but not ready",
                    f"{detail}. Not restarting: Kazma answers, and a restart "
                    f"cannot bring back what it is waiting for. {tail}",
                    kind="health.dependency_down",
                    fingerprint="not-ready",
                )
        return "guard shutting down"


# -- operator commands ------------------------------------------------


def _cmd_page(title: str, detail: str) -> int:
    """Send one page through the guard's own notifier (its credentials, its
    Telegram route): ``scripts/deploy_live.py`` and ``kazma maintenance end``
    tell the owner what failed this way. 0 when Telegram took it."""
    notify = Guard(once=True).notify
    if not notify.configured:
        print("not sent: the guard has no Telegram credentials")
        return 1
    text = format_operator_card("Guard", "error", title, detail)
    try:
        url = f"https://api.telegram.org/bot{notify.token}/sendMessage"
        body = urllib.parse.urlencode(
            {"chat_id": notify.chat, "text": text, "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(_request(url, data=body, method="POST"), timeout=10):
            pass
    except (urllib.error.URLError, OSError) as exc:
        print(f"not sent: {type(exc).__name__}")
        return 1
    print("sent")
    return 0


def _cmd_status() -> int:
    pause = read_pause()
    url = health_url(of_running_guard=True)
    holder = _port_holder_pid(_port_from_url(url))
    result = probe(url, 5.0)
    ok, detail = result
    if ok:
        word = "healthy"
    elif result.answered and result.needs_setup:
        word = "up, needs setup in Settings -> Providers"
    elif result.answered and result.waits_for:
        word = f"up, waiting for {', '.join(result.waits_for)}"
    elif result.answered:
        word = "up, not ready"
    else:
        word = "not answering"
    print(f"supervision : {'PAUSED' if pause else 'active'}")
    if pause:
        until = float(pause.get("until") or 0.0)
        print(f"  reason    : {pause.get('reason')}")
        print(f"  paused by : {pause.get('by')}")
        print("  expires   : " + (
            datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%d %H:%M UTC")
            if until else "never (will nag hourly)"))
        print(f"  flag file : {_pause_path()}")
    print(f"server      : {word} ({detail})")
    if holder:
        print(f"  port {_port_from_url(url)}  : held by pid {holder}")
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
    for line in _dependency_status_lines(state):
        print(line)
    for line in _broker_status_lines(state, alive):
        print(line)
    for line in _disk_status_lines(state):
        print(line)
    for line in _dbsvc_status_lines(state):
        print(line)
    for line in _outside_status_lines(state):
        print(line)
    return 0


def _broker_status_lines(state: dict, alive: bool) -> list[str]:
    """What --status says about the document worker broker, and about a
    running guard older than this file (it may lack what the file does)."""
    lines: list[str] = []
    rec = state.get("document_broker")
    if isinstance(rec, dict):
        where = rec.get("url") or "no address yet"
        what = f"{rec.get('state')} ({rec.get('why')})"
        if rec.get("pid"):
            what += f", pid {rec.get('pid')}"
        lines.append(f"doc broker  : {what}, {where}")
        if rec.get("image") or rec.get("image_requested"):
            image = rec.get("image") or rec.get("image_requested")
            note = " (an older build; the one for this code is not built)" if rec.get("image_fallback") else ""
            lines.append(f"  image     : {image}{note}")
        if rec.get("detail"):
            lines.append(f"  detail    : {rec.get('detail')}")
        lines.append(f"  log       : {rec.get('log')}")
    if alive:
        here = _file_sha(Path(__file__).resolve())
        running = state.get("guard_code")
        features = state.get("guard_features") or []
        if (running and running != here) or any(f not in features for f in GUARD_FEATURES):
            lines.append("NOTE        : the running guard started from older code than this file.")
            lines.append("  A reload restarts Kazma, not the guard: restart the guard to run "
                         "this code (Document processing operations, 'Restart the guard').")
    return lines


def _cmd_pause(
    reason: str,
    ttl: float,
    *,
    stop_now: bool,
    when_idle: bool = False,
    idle_timeout_s: float = 900.0,
) -> int:
    log = GuardLog(_default_log_path())
    # --stop --when-idle: a package update or a diagnosis stops the server;
    # mid-turn that drops the reply in flight, as a reload would. The wait
    # comes BEFORE the pause is written: the guard stops its child the
    # moment it sees one.
    if stop_now and when_idle:
        health = health_url(of_running_guard=True)
        if not _wait_until_idle(health, idle_timeout_s, log, action="stopping", event="maintenance"):
            return 3
    rec = write_pause(reason, ttl)
    log("warn", "maintenance.pause_requested", reason=rec["reason"], ttl_s=ttl)
    until = float(rec.get("until") or 0.0)
    print("Supervision PAUSED. Kazma will not be auto-restarted.")
    print(f"  reason  : {rec['reason']}")
    print("  expires : " + (
        datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%d %H:%M UTC")
        if until else "never -- you will be reminded hourly"))
    print(f"  file    : {_pause_path()}")

    if stop_now:
        health = health_url(of_running_guard=True)
        if _guard_alive():
            # The guard stops its own child the moment it sees the pause, and
            # gracefully; this shell may not have the rights to (the same
            # "Access is denied" as --reload, 2026-09-26).
            print("  the guard is stopping the server…")
            if _wait_until_down(health, GRACEFUL_STOP_S + TERMINATE_GRACE_S + 15.0):
                log("info", "maintenance.server_stopped", by="guard")
                print("  server stopped.")
            else:
                log("error", "maintenance.server_still_up", by="guard")
                print("  the server is still answering; check --status.")
        else:
            # No guard to do it: stop the recorded child from here, the whole
            # tree, including the uvicorn grandchild that would otherwise keep
            # holding the port -- and say so only if it worked.
            stopped = _stop_recorded_child(log)
            if stopped:
                log("info", "maintenance.server_stopped", pid=stopped)
                print(f"  server stopped (pid {stopped}).")
            elif probe(health, 5.0)[0]:
                print("  could not stop the server: it runs with more rights than "
                      "this shell. Run this from an elevated terminal.")
            else:
                print("  no running server; nothing to stop.")
    print("")
    print(f"Resume with:  {_command_here()} --resume")
    return 0


def _command_here(script: str = "kazma_guard.py") -> str:
    """A guard command as typed in the install folder, with the Python running
    this one: a bare ``python`` may be another interpreter, or none at all on
    Windows. Relative inside the install, in full elsewhere."""
    exe = sys.executable or "python"
    try:
        rel = os.path.relpath(exe, REPO_ROOT)
    except ValueError:  # on another drive
        rel = ""
    if rel and not rel.startswith(".."):
        exe = rel
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} {os.path.join('scripts', 'service', script)}"


def _cmd_resume() -> int:
    log = GuardLog(_default_log_path())
    if clear_pause():
        log("info", "maintenance.resume_requested")
        print("Supervision RESUMED. The guard will restart Kazma within ~10s.")
    else:
        print("Not paused; nothing to resume.")
    return 0


# -- maintenance for another program ----------------------------------
#
# `kazma update` replaces Kazma's packages, which the running server holds
# open: on Windows a reinstall under it fails half way. It stops Kazma
# through these two, loading this file: a server stopped any other way is
# restarted by the guard within seconds, and the update's shell may not have
# the rights the server runs with (the KazmaAgent task runs elevated). The
# switch is the pause, as for --pause/--resume. These add what a program
# needs: the guard's own word that it holds, and a resume that lifts only
# the pause it was given.


def maintenance_preview(reason: str) -> str:
    """What :func:`stop_for_maintenance` would meet, changing nothing:
    ``"guard"`` (a live guard would stop and hold Kazma), ``"no_guard"``,
    or ``"paused"`` (paused for something other than *reason*)."""
    current = read_pause()
    if current is not None and current.get("reason") != reason:
        return "paused"
    return "guard" if _guard_alive() else "no_guard"


def stop_for_maintenance(reason: str, *, ttl_s: float = DEFAULT_PAUSE_TTL_S,
                         idle_timeout_s: float = 900.0,
                         hold_timeout_s: float | None = None) -> str:
    """Have the running guard stop Kazma and hold it stopped.

    Waits until no chat turn runs (a stop mid-turn drops the reply), pauses
    supervision under *reason*, then waits until the guard holds: nothing
    answers, and nothing starts until :func:`resume_after_maintenance`. A
    pause already taken under the same *reason* (an earlier run that failed
    half way) is taken over with a fresh expiry. Returns:

    ``"held"``      the guard holds; Kazma is stopped.
    ``"busy"``      a turn still ran at *idle_timeout_s*; nothing changed.
    ``"not_held"``  the guard did not hold in time. A pause this call wrote
                    is lifted again; one it took over stays.
    ``"no_guard"``  no live guard supervises this install; nothing changed.
    ``"paused"``    supervision is paused for something else; left alone.
    """
    current = read_pause()
    if current is not None and current.get("reason") != reason:
        return "paused"
    if not _guard_alive():
        return "no_guard"
    log = GuardLog(_default_log_path(), echo=False)  # the caller prints its own account
    health = health_url(of_running_guard=True)
    if current is None and probe(health, 5.0).answered and not _wait_until_idle(
        health, idle_timeout_s, log, action="stopping", event="maintenance",
    ):
        return "busy"
    rec = write_pause(reason, ttl_s)
    log("warn", "maintenance.pause_requested", reason=reason, ttl_s=ttl_s,
        took_over=current is not None)
    budget = (
        hold_timeout_s if hold_timeout_s is not None
        else GRACEFUL_STOP_S + TERMINATE_GRACE_S + 30.0
    )
    if _wait_until_held(rec, health, budget):
        log("info", "maintenance.held", reason=reason)
        return "held"
    if current is None:
        clear_pause()
    log("error", "maintenance.not_held", reason=reason, waited_s=budget)
    return "not_held"


def _wait_until_held(pause: dict, health_url: str, timeout_s: float) -> bool:
    """Has the guard taken *pause*: no server, and none about to start?

    A guard that records what it holds (``pause_held`` in its
    ``guard_features``) is asked. An older one -- still running after the
    update that taught it -- is judged by what it leaves: no recorded child
    and nothing answering, twice, a second apart, so a spawn the guard had
    decided on before the pause has time to show.
    """
    try:
        since = float(pause.get("since") or 0.0)
    except (TypeError, ValueError):
        since = 0.0
    deadline = time.monotonic() + timeout_s
    quiet = 0
    while True:
        state = _read_state()
        if "pause_held" in (state.get("guard_features") or ()):
            ack = state.get("pause_held")
            if (
                isinstance(ack, (int, float))
                and abs(float(ack) - since) < 1e-6
                and not probe(health_url, 3.0).answered
            ):
                return True
        else:
            try:
                child = int(state.get("child_pid") or 0)
            except (TypeError, ValueError):
                child = 0
            if not child and not probe(health_url, 3.0).answered:
                quiet += 1
                if quiet >= 2:
                    return True
            else:
                quiet = 0
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)


def resume_after_maintenance(reason: str, *, wait: bool = True) -> str:
    """Lift the pause *reason* names and, with a live guard, wait for Kazma.

    Never lifts another pause: an operator's diagnosis stays paused. Returns
    ``"up"`` (a server started after the resume answers ready), ``"slow"``
    (not within the start budget; the guard is still at it), ``"resumed"``
    (lifted, not waited for), ``"unsupervised"`` (lifted, but no guard runs
    to start Kazma), ``"not_ours"`` or ``"not_paused"`` (nothing lifted).
    """
    current = read_pause()
    if current is None:
        return "not_paused"
    if current.get("reason") != reason:
        return "not_ours"
    log = GuardLog(_default_log_path(), echo=False)
    health = health_url(of_running_guard=True)
    resumed_at = time.time()
    clear_pause()
    log("info", "maintenance.resume_requested", reason=reason)
    if not _guard_alive():
        return "unsupervised"
    if not wait:
        return "resumed"
    code = _wait_for_new_boot(health, resumed_at, None, log, event="maintenance")
    return "up" if code == 0 else "slow"


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
    # The state file outlives the guard (a reboot, a hard stop), so the PID
    # may belong to anything now; the other two reapers asked first, this
    # one force-killed the tree (audit 2026-10-26 AUD-013, §39).
    if not _recorded_child_is_ours(pid, state, log):
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
        with urllib.request.urlopen(_request(live), timeout=5) as resp:
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
        with urllib.request.urlopen(_request(url), timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _wait_until_idle(health_url: str, max_wait_s: float, log: GuardLog, *,
                     poll_s: float = 5.0, action: str = "reloading",
                     event: str = "reload") -> bool:
    """Wait until no turn is running (two quiet polls in a row).

    True when idle -- or when this build cannot say, which is reported and
    then treated as idle, since the operator asked for the reload (or the
    stop: *action* and *event* name which). False when still busy at the
    deadline: nothing happens.
    """
    deadline = time.monotonic() + max_wait_s
    quiet = 0
    told = False
    while True:
        act = _activity(health_url)
        if act is None:
            print("This build cannot report activity (no /health/activity); "
                  f"{action} without waiting.")
            log("warn", f"{event}.idle_unknown")
            return True
        # A scheduled reminder or task firing counts as a running turn: a
        # reload would interrupt it too (Scheduled audit SCH-E04). An older
        # server reports no active_fires (0).
        running = int(act.get("active_turns") or 0) + int(act.get("active_fires") or 0)
        if running == 0:
            quiet += 1
            if quiet >= 2:
                return True
        else:
            quiet = 0
            if not told:
                print(f"Waiting for {running} running turn(s) to finish before {action}…")
                log("info", f"{event}.waiting_for_idle", running=running)
                told = True
        if time.monotonic() >= deadline:
            print(f"Still busy after {max_wait_s:.0f}s ({running} turn(s) running); not {action}.")
            log("warn", f"{event}.busy_gave_up", running=running)
            return False
        time.sleep(poll_s)


def _wait_until_down(health_url: str, timeout_s: float) -> bool:
    """Wait until the server stops answering. True if it did in time."""
    deadline = time.monotonic() + timeout_s
    while True:
        if not probe(health_url, 3.0)[0]:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


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


def _wait_for_new_boot(health_url: str, requested_at: float, before: str | None,
                       log: GuardLog, *, event: str = "reload") -> int:
    """Wait until a server that booted AFTER the request answers ready.

    *before* is the build that served before a reload; None after a
    maintenance stop, which says nothing about the build.
    """
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
            result = probe(health_url, 5.0)
            ok, detail = result
            needs = (tuple(getattr(result, "needs_setup", ()) or ())
                     if getattr(result, "answered", False) else ())
            if not ok and needs:
                # The new build is up and waits for its owner: done, said so.
                # It waited out the whole start budget for a provider key
                # (the drill VM, 2026-11-06).
                after = _live_commit(health_url)
                print(f"Kazma is up on build {after or '?'}, but needs setup: {detail}")
                print(f"  Open {health_url.split('/health', 1)[0]}/ and go to Settings -> Providers.")
                log("warn", f"{event}.needs_setup", commit=after, needs=",".join(needs))
                return 0
            if ok:
                after = _live_commit(health_url)
                if before is None:
                    print(f"Kazma is up. build {after or '?'}")
                else:
                    print(f"Kazma is up. build {after or '?'} (was {before or '?'})")
                if before and after and before == after:
                    print(
                        "NOTE: commit hash unchanged — the process restarted but "
                        "git HEAD is the same. Code edits still need this reload."
                    )
                log("info", f"{event}.ready", commit=after, previous=before)
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
    print(f"  {_command_here()} --status")
    print(f"  {_command_here('install_service.py')} --status")
    print(f"  {_command_here()}          # start supervision in this terminal")
    log("error", f"{event}.not_ready")
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
    health = health_url(of_running_guard=True)
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
                    help="with --reload or --pause --stop: first wait until no "
                         "chat turn is running")
    ap.add_argument("--idle-timeout", type=float, default=900.0,
                    help="with --when-idle: seconds to wait before giving up (default 900)")
    ap.add_argument("--install", action="store_true",
                    help="install the OS supervisor (runs install_service.py --install)")
    ap.add_argument("--stop", action="store_true",
                    help="with --pause: also stop the running server now")
    ap.add_argument("--reason", default="", help="why (recorded and alerted)")
    ap.add_argument("--page", metavar="TITLE",
                    help="send the owner one page the way the guard does (deploy and maintenance checks)")
    ap.add_argument("--detail", default="", help="with --page: the page's detail")
    ap.add_argument("--ttl", type=float, default=DEFAULT_PAUSE_TTL_S,
                    help="seconds before the pause auto-expires; 0 = never "
                         f"(default {DEFAULT_PAUSE_TTL_S:.0f})")
    args = ap.parse_args()

    if args.page:
        return _cmd_page(args.page, args.detail)
    if args.status:
        return _cmd_status()
    if args.install:
        return _cmd_install()
    if args.reload:
        return _cmd_reload(when_idle=args.when_idle, idle_timeout_s=args.idle_timeout)
    if args.pause:
        return _cmd_pause(
            args.reason, args.ttl, stop_now=args.stop,
            when_idle=args.when_idle, idle_timeout_s=args.idle_timeout,
        )
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
            "document_broker": {
                "mode": guard.broker.mode,
                "script": str(_broker_script()),
                "state_dir": str(_broker_file("x").parent),
                "log": str(_broker_log_path()),
            },
            "dependencies": {
                "mode": guard.deps.mode,
                "record_file": str(_deps_record_path()),
                "desktop_task": DEPS_TASK_NAME,
            },
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

        # The broker is the guard's to stop: a dead guard leaves nothing to
        # stop it until the next guard reaps it.
        broker = getattr(guard, "broker", None)
        if broker is not None:
            try:
                broker.shutdown()
            except Exception as stop_exc:  # noqa: BLE001 -- the crash below is what matters
                guard.log("warn", "broker.shutdown_failed", error=str(stop_exc)[:200])
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
                kind="guard.crashed",
                force=True,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
