"""Notice when the server is left running with no guard watching it.

The guard (``scripts/service/kazma_guard.py``) restarts a server that crashed
or stopped answering. On 2026-09-26 the guard died itself -- exit code 1,
nothing in its log -- and Kazma ran unsupervised until someone happened to
look: a crash in that window would have been an outage nobody was told
about. The guard now writes a heartbeat into its state file every 10 seconds
and hands the server that file's path in ``KAZMA_GUARD_STATE_FILE``; this
module reads it on the 15-minute maintenance cadence
(``worker_bootstrap._MAINTENANCE_SWEEPS``) and pages when it has gone stale.

A server started without the guard (a developer's uvicorn, a container with
its own restart policy) has no such variable and is not watched.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["STALE_AFTER_S", "STATE_ENV", "check_supervisor", "supervisor_status"]

#: Set by the guard for the server it spawns.
STATE_ENV = "KAZMA_GUARD_STATE_FILE"
#: The guard beats at least every 10 s; a stop can hold it for ~90 s.
STALE_AFTER_S = 300.0

_lock = threading.Lock()
_state = {"confirmed": False, "gone": False}


def supervisor_status(now: float | None = None) -> dict[str, Any] | None:
    """What the guard's state file says, or None when no guard started us."""
    path = (os.environ.get(STATE_ENV) or "").strip()
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    beat = data.get("heartbeat")
    now = time.time() if now is None else now
    age = (now - float(beat)) if isinstance(beat, (int, float)) else None
    return {
        "state_file": path,
        "guard_pid": data.get("guard_pid"),
        "heartbeat_age_s": age,
        "supervised": age is not None and age < STALE_AFTER_S,
    }


def check_supervisor() -> None:
    """One check. Pages when the guard that started this server is gone."""
    status = supervisor_status()
    if status is None:
        return
    age = status["heartbeat_age_s"]
    with _lock:
        if status["supervised"]:
            if _state["gone"]:
                logger.warning(
                    "[supervisor] the guard is back (pid %s): restarts are covered again",
                    status["guard_pid"],
                )
            elif not _state["confirmed"]:
                # A mechanism that speaks only when it breaks cannot be told
                # from one that never runs: say once that it looked.
                logger.info(
                    "[supervisor] supervised by guard pid %s (heartbeat %.0fs ago)",
                    status["guard_pid"], age,
                )
            _state["confirmed"] = True
            _state["gone"] = False
            return
        _state["gone"] = True
    since = "no heartbeat on record" if age is None else f"no heartbeat for {int(age // 60)} min"
    logger.error("[supervisor] Kazma is running without its guard (%s, state %s)",
                 since, status["state_file"])
    from kazma_core.observability import ops_alerts

    ops_alerts.alert(
        "guard.gone",
        "Kazma is running without its guard",
        f"The guard that started this server has stopped ({since}). A crash or a "
        "hang will not be restarted until it is back. Start it: "
        "schtasks /Run /TN KazmaAgent, or run kazma_guard.py --reload.",
        severity="critical",
        cooldown_s=6 * 3600,
    )


def _reset_for_tests() -> None:
    with _lock:
        _state.update(confirmed=False, gone=False)
