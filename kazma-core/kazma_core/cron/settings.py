"""The scheduler's settings, read in one place (Scheduled audit SCH-E06, E14, S05).

Every value is a Settings key with a bound, so a typo cannot disable the
scheduler; :func:`kazma_core.settings_validation.validate_setting` refuses a
value out of range before it is stored. Reading them is a settings read
(a database round trip on Postgres), so async code calls :func:`cron_settings`
through ``asyncio.to_thread`` (SCH-E10).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CATCHUP_KEY",
    "CronSettings",
    "FIRE_WITHOUT_HITL_KEY",
    "MAX_JOBS_PER_TENANT_KEY",
    "MAX_JOBS_PER_THREAD_KEY",
    "PROMPT_MAX_CHARS",
    "SETTING_BOUNDS",
    "TASK_BUDGET_KEY",
    "cron_settings",
    "hitl_enabled",
]

#: How late a due job may still run; later is "missed" (with a notice).
CATCHUP_KEY = "cron.catchup_minutes"
#: How long a fired agent task may run.
TASK_BUDGET_KEY = "cron.task_budget_seconds"
#: Jobs still holding work, per tenant and per conversation.
MAX_JOBS_PER_TENANT_KEY = "cron.max_jobs_per_tenant"
MAX_JOBS_PER_THREAD_KEY = "cron.max_jobs_per_thread"
#: Fire agent tasks while approvals are switched off (off: they do not fire).
#: Under ``safety.`` so the agent's config tools may never set it.
FIRE_WITHOUT_HITL_KEY = "safety.cron_fire_without_hitl"

#: The longest stored prompt (a reminder's message or a task's instruction).
PROMPT_MAX_CHARS = 8000

#: key -> (default, lowest, highest)
SETTING_BOUNDS: dict[str, tuple[int, int, int]] = {
    CATCHUP_KEY: (60, 1, 7 * 24 * 60),
    TASK_BUDGET_KEY: (300, 30, 3600),
    MAX_JOBS_PER_TENANT_KEY: (500, 1, 10000),
    MAX_JOBS_PER_THREAD_KEY: (50, 1, 1000),
}


@dataclass(frozen=True)
class CronSettings:
    catchup_seconds: int
    task_budget_seconds: float
    max_jobs_per_tenant: int
    max_jobs_per_thread: int
    fire_without_hitl: bool


def _int_setting(store: Any, key: str) -> int:
    default, low, high = SETTING_BOUNDS[key]
    raw = None
    if store is not None:
        try:
            raw = store.get(key)
        except Exception:  # noqa: BLE001 — a settings outage keeps the defaults
            logger.warning("[cron] setting %s unreadable; using %s", key, default, exc_info=True)
    if raw is None or raw == "":
        if key == CATCHUP_KEY:
            hours = os.environ.get("KAZMA_CRON_STALE_HOURS", "").strip()
            if hours:
                try:
                    return max(low, min(high, int(float(hours) * 60)))
                except ValueError:
                    pass
        return default
    try:
        return max(low, min(high, int(raw)))
    except (TypeError, ValueError):
        return default


def cron_settings() -> CronSettings:
    """The current settings. Blocking: async callers use ``asyncio.to_thread``."""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
    except Exception:  # noqa: BLE001
        store = None
    fire_without = False
    if store is not None:
        try:
            fire_without = store.get(FIRE_WITHOUT_HITL_KEY) is True
        except Exception:  # noqa: BLE001 — unreadable: the safe answer is "no"
            fire_without = False
    return CronSettings(
        catchup_seconds=_int_setting(store, CATCHUP_KEY) * 60,
        task_budget_seconds=float(_int_setting(store, TASK_BUDGET_KEY)),
        max_jobs_per_tenant=_int_setting(store, MAX_JOBS_PER_TENANT_KEY),
        max_jobs_per_thread=_int_setting(store, MAX_JOBS_PER_THREAD_KEY),
        fire_without_hitl=fire_without,
    )


def hitl_enabled() -> bool:
    """Whether approvals are on. Blocking. An unreadable config counts as on
    (a fire is then refused nothing it would not be refused anyway)."""
    try:
        from kazma_core.safety.hitl import get_hitl_config

        return bool(get_hitl_config().get("enabled", True))
    except Exception:  # noqa: BLE001
        return True
