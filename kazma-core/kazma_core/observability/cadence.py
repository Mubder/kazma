"""When a periodic job is due: counted from its last run, never from boot.

A job that sleeps its whole interval from process start never runs on a
server restarted more often than that. The restore drill hit it first (34
scheduler starts and no drill in three days). On 2026-09-28 the daily digest
had not been sent once in a week -- the live install reloads several times a
day -- and the backup sweep, skipping a fresh backup at boot, waited six
hours from boot: nine hours passed without a backup.

The last run is a ConfigStore stamp, so it survives restarts. The wait is the
rest of the interval, at least a minute (a clock step must not spin a loop),
or ``settle_s`` when the job has never run or is already due: a short pause
so it does not compete with startup.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

__all__ = ["last_run", "seconds_until_due", "stamp_run"]

_MIN_WAIT_S = 60.0


def last_run(key: str) -> float | None:
    """Epoch seconds the job stamped under *key*, or None (never, or unreadable)."""
    from kazma_core.db.pg_helpers import store_errors

    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(key)
    except store_errors():  # an unreadable stamp means "run it"
        logger.debug("[cadence] could not read %s", key, exc_info=True)
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def stamp_run(key: str, when: float | None = None) -> None:
    """Record that the job under *key* completed (now, unless *when*)."""
    from kazma_core.db.pg_helpers import store_errors

    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(key, float(time.time() if when is None else when), category="observability")
    except store_errors():  # a lost stamp only means one early run
        logger.warning("[cadence] could not record %s", key, exc_info=True)


def seconds_until_due(key: str, interval_s: float, *, settle_s: float, now: float | None = None) -> float:
    """How long to wait before the job's next run (see the module docstring)."""
    last = last_run(key)
    if last is None:
        return settle_s
    remaining = interval_s - ((time.time() if now is None else now) - last)
    return settle_s if remaining <= 0 else max(_MIN_WAIT_S, remaining)
