"""Kazma's side of the document worker broker the guard runs.

On a native install the guard (``scripts/service/kazma_guard.py``) runs the
document worker broker beside Kazma and hands Kazma three paths: the broker's
loopback URL (``KAZMA_DOCUMENT_BROKER_URL``), its token file
(``KAZMA_DOCUMENT_BROKER_TOKEN_FILE``) and a request file
(``KAZMA_GUARD_BROKER_REQUEST_FILE``). Kazma decides whether it needs the
broker -- its document worker profile, from the environment or the admin's
setting -- and writes that decision to the request file
(:func:`_publish_broker_request`): at boot, when the setting changes and on the
maintenance cadence. The guard starts or stops the broker within a second and
records what it did in its state file (``KAZMA_GUARD_STATE_FILE``,
``document_broker``), which readiness reads here (:func:`supervisor_report`)
to say what runs and what to do when it does not (:func:`isolation_action`).

On 2026-10-15 the live install required containers and no broker ran: every
document was refused, and the only way out was a hand-run process and three
``.env`` lines. Nothing here starts a process; the guard does.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ALERTED_KEY",
    "REQUEST_FILE_ENV",
    "document_broker_sweep",
    "isolation_action",
    "local_build_fingerprint",
    "start_broker_requests",
    "supervisor_report",
]

#: Set by the guard for the server it spawns: where Kazma asks for the broker.
REQUEST_FILE_ENV = "KAZMA_GUARD_BROKER_REQUEST_FILE"
#: The guard's state file (``observability.supervisor_watch.STATE_ENV``).
_GUARD_STATE_ENV = "KAZMA_GUARD_STATE_FILE"
#: The build an outdated-image alert was raised for: once per build.
ALERTED_KEY = "system.documents.worker_image_alerted"
#: A guard heartbeat older than this is a guard that is gone
#: (``supervisor_watch.STALE_AFTER_S``).
_GUARD_STALE_S = 300.0
_INSTALL_ROOT = Path(__file__).resolve().parents[3]
_publish_lock = threading.Lock()
#: Publishes a settings change started, kept so they can be joined
#: (:func:`_drain_publishes`).
_publishers: set[threading.Thread] = set()
_publishers_lock = threading.Lock()


# ── what the guard says ─────────────────────────────────────────────────


def _guard_state() -> dict[str, Any] | None:
    path = (os.environ.get(_GUARD_STATE_ENV) or "").strip()
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def supervisor_report() -> dict[str, Any]:
    """Who runs the broker Kazma uses, and what the guard says about it.

    ``by``: ``guard`` (the guard that started Kazma runs it), ``old_guard``
    (Kazma runs under a guard from before it could), ``external`` (Kazma uses
    a broker the guard does not run: compose, or one ``.env`` names) or
    ``none`` (no guard started Kazma). Never raises; reads one small file.
    """

    url = (os.environ.get("KAZMA_DOCUMENT_BROKER_URL") or "").strip()
    state = _guard_state()
    if state is None:
        return {"by": "external" if url else "none", "url": url or None, "guard": None, "broker": None}
    beat = state.get("heartbeat")
    age = time.time() - float(beat) if isinstance(beat, (int, float)) else None
    guard = {
        "running": age is not None and age < _GUARD_STALE_S,
        "heartbeat_age_s": round(age, 1) if age is not None else None,
        "runs_broker": "document_broker" in (state.get("guard_features") or []),
    }
    broker = state.get("document_broker") if isinstance(state.get("document_broker"), dict) else None
    if not guard["runs_broker"]:
        by = "old_guard"
    elif broker is not None and url and broker.get("url") and broker.get("url") != url:
        by = "external"
    else:
        by = "guard"
    shown = None
    if broker is not None:
        shown = {key: broker.get(key) for key in (
            "mode", "state", "wanted", "why", "detail", "url", "pid", "image", "image_requested",
            "image_fallback", "restarts", "failures", "last_exit", "ready_at", "log",
        )}
    return {"by": by, "url": url or None, "guard": guard, "broker": shown}


# ── what Kazma asks for ─────────────────────────────────────────────────


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # A reader (the guard) has it open this instant on Windows.
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _publish_broker_request() -> bool:
    """Tell the guard whether Kazma needs the broker. True when written.

    Written only when the decision differs from what the file says, so the
    guard is woken only by a change. A profile that cannot be read leaves the
    last request standing: an outage must not stop a broker documents need.
    Blocking (a settings read, a file write): async callers use a thread.
    """

    path_text = (os.environ.get(REQUEST_FILE_ENV) or "").strip()
    if not path_text:
        return False
    from .worker_container import isolation_selection

    selection = isolation_selection()
    if selection["mode"] == "unknown":
        logger.info("[documents] the worker profile could not be read; the broker request stands")
        return False
    request = {
        "want": selection["mode"] == "container",
        "url": (os.environ.get("KAZMA_DOCUMENT_BROKER_URL") or "").strip(),
        "mode": selection["mode"],
        "source": selection["source"],
    }
    path = Path(path_text)
    with _publish_lock:
        try:
            current = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            current = {}
        if isinstance(current, dict) and all(current.get(k) == v for k, v in request.items()):
            return False
        try:
            _write_atomic(path, {**request, "ts": time.time(), "pid": os.getpid()})
        except OSError as exc:
            logger.warning("[documents] could not ask the guard about the document broker: %s", exc)
            return False
    logger.info(
        "[documents] asked the guard to %s the document worker broker (profile %s, from the %s)",
        "run" if request["want"] else "stop", request["mode"], request["source"],
    )
    return True


def _watch_profile_setting(store: Any) -> None:
    """Publish the request again whenever the profile setting changes.

    The store calls its listeners on the writer's thread once the write has
    committed; the publish (a settings read and a file write) runs on a short
    thread of its own, so the writer never waits for it.
    """

    if not (os.environ.get(REQUEST_FILE_ENV) or "").strip():
        return
    from kazma_core.safety.deployment_policy import DOCUMENT_WORKER_ISOLATION_KEY
    from kazma_core.settings_validation import base_key

    parents = {"security", "security.execution", DOCUMENT_WORKER_ISOLATION_KEY}

    def changed(keys: frozenset[str] | None) -> None:
        if keys is not None and not any(base_key(key) in parents for key in keys):
            return
        publisher = threading.Thread(target=_publish_quietly, name="document-broker-request", daemon=True)
        with _publishers_lock:
            _publishers.add(publisher)
        publisher.start()

    store.add_change_listener(changed)


def _drain_publishes(timeout: float = 5.0) -> None:
    """Wait for the publishes settings changes started (shutdown, tests)."""

    with _publishers_lock:
        pending = list(_publishers)
    for publisher in pending:
        publisher.join(timeout)


def start_broker_requests() -> None:
    """At boot (in a thread): ask the guard now, and on every profile change.

    Early in boot, so the guard's broker attests while the rest of Kazma
    starts. Nothing to do when no guard handed a request file."""

    if not (os.environ.get(REQUEST_FILE_ENV) or "").strip():
        return
    from kazma_core.config_store import get_config_store

    _watch_profile_setting(get_config_store())
    _publish_broker_request()


def _publish_quietly() -> None:
    from kazma_core.safety.deployment_policy import policy_read_failures

    try:
        _publish_broker_request()
    except (OSError, *policy_read_failures()):
        # A background notice: the maintenance pass publishes it again.
        logger.warning("[documents] the document broker request failed", exc_info=True)
    finally:
        with _publishers_lock:
            _publishers.discard(threading.current_thread())


# ── what to do ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def local_build_fingerprint() -> str | None:
    """The worker image build this checkout needs, or None (not a checkout)."""

    from .worker_image import build_fingerprint

    try:
        return build_fingerprint(_INSTALL_ROOT)
    except OSError:
        return None


def _install_python() -> str:
    """This install's interpreter as the operator types it in the install folder."""

    exe = sys.executable or "python"
    try:
        relative = os.path.relpath(exe, _INSTALL_ROOT)
    except ValueError:  # another drive
        relative = ""
    if relative and not relative.startswith(".."):
        exe = relative
    return f'"{exe}"' if " " in exe else exe


def _broker_command() -> str:
    """The one command that builds the worker image for this code."""

    from .worker_image import build_command

    return build_command(_install_python())


_MESSAGES = {
    "use_container": "This install requires container isolation for documents: choose the container "
                     "profile in Settings -> Documents.",
    "remove_environment_override": "KAZMA_DOCUMENT_WORKER_ISOLATION in the environment overrides the "
                                   "setting: remove it from .env, then restart Kazma.",
    "fix_environment": "KAZMA_DOCUMENT_WORKER_ISOLATION must be 'process' or 'container'.",
    "settings_unavailable": "The document worker profile could not be read; documents are refused "
                            "until the settings store answers.",
    "build_image": "Build the document worker image for this code (one command, below); the broker "
                   "switches to it by itself.",
    "restart_guard": "The guard running Kazma started before it could run the document broker: "
                     "restart the guard once (Document processing operations, 'Restart the guard').",
    "start_guard": "The guard that started Kazma is not running, so nothing runs the document broker: "
                   "start it (schtasks /Run /TN KazmaAgent).",
    "wait": "The guard is starting the document broker; it attests its containers first.",
    "start_docker": "Docker does not answer: start Docker. The guard keeps trying.",
    "check_broker_log": "The document broker keeps failing: its log is below.",
    "enable_guard_broker": "The guard does not run a document broker here (KAZMA_GUARD_DOCUMENT_BROKER=off).",
    "run_broker": "Nothing runs a document broker: run Kazma under the guard (it runs one), or run a "
                  "broker and set KAZMA_DOCUMENT_BROKER_URL and KAZMA_DOCUMENT_BROKER_TOKEN_FILE.",
    "check_external_broker": "The document broker Kazma is set to use does not answer or is not "
                             "attested: check it and its log.",
}


def _action(code: str | None, command: str | None = None) -> dict[str, Any] | None:
    if code is None:
        return None
    return {"code": code, "message": _MESSAGES[code], "command": command}


def isolation_action(report: dict[str, Any]) -> dict[str, Any] | None:
    """What the operator should do about document isolation, or None.

    *report* is ``worker_container.isolation_report()``. ``code`` names the
    step (the pages word it from their catalog), ``message`` says it in
    English, ``command`` is the one to run, if any.
    """

    selection = report.get("selection") or {}
    mode = report.get("mode")
    if mode == "invalid" or selection.get("mode") == "invalid":
        return _action("fix_environment")
    if selection.get("mode") == "unknown":
        return _action("settings_unavailable")
    if mode == "process":
        if report.get("ready"):
            return None
        if selection.get("source") == "environment":
            return _action("remove_environment_override")
        return _action("use_container")
    supervisor = report.get("supervisor") or {}
    broker = supervisor.get("broker") or {}
    outdated = (
        report.get("image_code_matches") is False
        or report.get("image_current") is False
        or bool(report.get("image_fallback"))
    )
    if report.get("ready"):
        return _action("build_image", _broker_command()) if outdated else None
    by = supervisor.get("by")
    if by == "none":
        return _action("run_broker")
    if by == "external":
        return _action("check_external_broker")
    if by == "old_guard":
        return _action("restart_guard")
    if not (supervisor.get("guard") or {}).get("running"):
        return _action("start_guard")
    state = broker.get("state")
    if state == "off":
        return _action("enable_guard_broker")
    if state == "image_missing":
        return _action("build_image", _broker_command())
    if state == "docker_unavailable":
        return _action("start_docker")
    if state in ("backoff",) or (broker.get("failures") or 0) > 0:
        return _action("check_broker_log", broker.get("log"))
    if state in ("starting", "idle"):
        # Idle: Kazma's request is on its way (it is written at boot, on a
        # change and every maintenance pass).
        return _action("wait")
    return _action("check_broker_log", broker.get("log"))


# ── the maintenance pass ────────────────────────────────────────────────


def _report_outdated_image() -> bool:
    """One ops alert per build when the container profile runs without the
    worker image built for this code, naming the command. True when raised.

    A broker on an older image still isolates documents, so this is a
    warning, not an outage; the broker switches by itself once the image is
    built. Blocking (settings, a socket): run off the event loop.
    """

    from .errors import DocumentSandboxError
    from .worker_container import CONTAINER, isolation_report, worker_isolation_mode

    try:
        if worker_isolation_mode() != CONTAINER:
            return False
    except DocumentSandboxError:
        return False
    report = isolation_report()
    action = report.get("action") or {}
    if action.get("code") != "build_image":
        return False
    build = local_build_fingerprint() or "unknown"
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    if store.get(ALERTED_KEY) == build:
        return False
    from kazma_core.observability import ops_alerts

    missing = (report.get("supervisor") or {}).get("broker", {}) or {}
    title = (
        "The document worker image is not built"
        if missing.get("state") == "image_missing"
        else "The document worker image is older than this build"
    )
    ops_alerts.alert(
        "documents.worker_image_outdated",
        title,
        "Documents run in containers, and the worker image for this code has not been built"
        + ("" if missing.get("state") == "image_missing" else " (an older one serves meanwhile)")
        + f". Run this in the install folder:\n{action.get('command')}\n"
        "The guard's broker switches to the new image by itself.",
        severity="warn",
        cooldown_s=3600.0,
    )
    store.set(ALERTED_KEY, build, category="system")
    logger.warning("[documents] %s: %s", title, action.get("command"))
    return True


def document_broker_sweep() -> None:
    """The maintenance cadence's document broker pass (15 min, and shortly
    after boot): the request again (a write that failed is retried), then the
    outdated-image alert."""

    _publish_broker_request()
    _report_outdated_image()
