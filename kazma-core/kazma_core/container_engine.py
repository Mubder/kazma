"""Which container engine Kazma uses, from its setting.

``containers.engine`` (2026-11-12, ``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md``):

* ``kazma`` -- Kazma's own container engine: Docker Engine in the
  ``kazma-engine`` WSL distro, started at boot by the ``KazmaEngine`` task,
  so containers come back after a restart with nobody signed in. Setup
  installs it when a container part is chosen and records it in the setup
  record (``container_engine``); that record makes it the choice while the
  setting is unset.
* ``desktop`` -- Docker Desktop, the owner's choice (Settings, or
  ``kazma setup install --docker desktop``).
* unset, and no engine in the setup record -- the docker CLI's current
  context, as before: an install is never switched to an engine it has not
  moved to (an empty engine would hide every container it runs).

The choice is pinned in ``DOCKER_CONTEXT`` for every docker call the process
starts (:func:`kazma_core.docker_cli.apply_engine_choice`): at the server's
boot before anything calls Docker, again whenever the setting changes, and
in any other Kazma process at its first docker lookup.

Blocking (a settings read, two file reads): call in a thread.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

from kazma_core.docker_cli import ENGINE_KEY, apply_engine_choice, normalize_engine

logger = logging.getLogger(__name__)

__all__ = [
    "RECORD_KEY",
    "apply_engine_setting",
    "engine_setting",
    "watch_engine_setting",
]

#: The setup record's entry for the engine setup installed.
RECORD_KEY = "container_engine"

_watching = False
_watch_lock = threading.Lock()


def _recorded_engine() -> str:
    """``kazma`` when setup installed Kazma's own engine, else ``""``."""
    from kazma_core.postgres_service import read_record

    entry = read_record().get(RECORD_KEY)
    if isinstance(entry, dict) and entry.get("kind") == "kazma":
        return "kazma"
    return ""


def engine_setting() -> str:
    """``kazma``, ``desktop`` or ``""`` (no choice: the CLI's current context)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.deployment_policy import policy_read_failures

    try:
        stored = normalize_engine(get_config_store().get(ENGINE_KEY))
    except policy_read_failures():
        logger.warning("[docker] the container engine setting could not be read; "
                       "the engine setup installed is used", exc_info=True)
        stored = ""
    return stored or _recorded_engine()


def apply_engine_setting() -> str | None:
    """Pin the chosen engine for this process. Returns the context in force."""
    return apply_engine_choice(engine_setting())


#: The last thread that applied a changed choice (kept, so it can be joined).
_apply_thread: threading.Thread | None = None


def _on_settings_written(changed: Iterable[str] | None) -> None:
    global _apply_thread
    if changed is not None and ENGINE_KEY not in changed:
        return
    # A write may land on the event loop: the settings read runs elsewhere.
    thread = threading.Thread(target=apply_engine_setting, name="container-engine-choice",
                              daemon=True)
    _apply_thread = thread
    thread.start()


def _drain_engine_choice(timeout: float = 5.0) -> None:
    """Wait for a changed choice being applied (tests, shutdown)."""
    thread = _apply_thread
    if thread is not None:
        thread.join(timeout)


def watch_engine_setting() -> None:
    """Apply now, then follow every change of the setting (once per process)."""
    global _watching
    from kazma_core.config_store import get_config_store

    apply_engine_setting()
    with _watch_lock:
        if _watching:
            return
        get_config_store().add_change_listener(_on_settings_written)
        _watching = True
