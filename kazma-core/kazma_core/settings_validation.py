"""What a setting may hold: one check for every way a value is written.

``PUT /api/settings/single``, ``PUT /api/settings`` and a settings restore
(:mod:`kazma_core.settings_restore`) all ask :func:`validate_setting`. The
checks lived in the single-setting route only, so the Settings page's batch
save and a restored backup could write what that route refuses: an unknown
server-status event switches that message off at the next boot without a
word, and a retention the sweep cannot read falls back to its default
(2026-10-01).

A rule normalizes the value or raises :class:`SettingRejected` with what the
setting may hold; the message is the caller's answer (a 400, or a line in the
restore preview).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

__all__ = ["SettingRejected", "base_key", "validate_setting"]


class SettingRejected(ValueError):
    """A value the setting cannot hold; the message says what it may hold."""


def base_key(key: str) -> str:
    """``tenant.<id>.<key>`` is ``<key>`` for its tenant
    (:func:`kazma_core.tenant_isolation.tenant_key` writes that shape)."""
    parts = key.split(".")
    if len(parts) > 2 and parts[0] == "tenant":
        return ".".join(parts[2:])
    return key


def _timezone(value: Any) -> Any:
    # A cron timezone the scheduler cannot resolve falls back to UTC with only
    # a warn-once, so a typo would silently become "always UTC".
    from zoneinfo import ZoneInfo

    name = str(value or "").strip()
    if not name:
        return ""  # an explicit clear: the UTC default
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
        raise SettingRejected(
            f"Invalid timezone {name!r} — use an IANA name "
            "(e.g. 'Asia/Kuwait', 'Europe/London', 'UTC')."
        ) from None
    return name


def _task_retention(value: Any) -> Any:
    # Decides what the 15-minute sweep deletes: a value it cannot read is
    # refused here rather than quietly replaced by the default there.
    from kazma_core.swarm.task_store import MAX_TASK_RETENTION_DAYS, parse_task_retention_days

    days = parse_task_retention_days(value)
    if days is None:
        raise SettingRejected(
            "Swarm task retention must be a whole number of days "
            f"from 0 (keep every task) to {MAX_TASK_RETENTION_DAYS}."
        )
    return days


def _checkpoint_retention(value: Any) -> Any:
    from kazma_core.checkpoint_retention import MAX_RETENTION_DAYS, parse_retention_days

    days = parse_retention_days(value)
    if days is None:
        raise SettingRejected(
            "Step history retention must be a whole number of days "
            f"from 0 (keep every step) to {MAX_RETENTION_DAYS}."
        )
    return days


def _lifecycle_events(value: Any) -> Any:
    # Only events Kazma sends: a name it does not know would switch that
    # message off at the next boot without a word.
    from kazma_core.lifecycle_notifier import EVENT_NAMES, parse_lifecycle_events

    known, unknown = parse_lifecycle_events(value)
    if unknown or not isinstance(value, (list, str)):
        raise SettingRejected(
            "Server status messages are a list of: "
            f"{', '.join(EVENT_NAMES)}"
            + (f" (not {', '.join(unknown)})." if unknown else ".")
        )
    return known


@functools.cache
def _rules() -> dict[str, tuple[Callable[[Any], Any], str | None]]:
    """key -> (normalize, category the key is stored under or None).

    Built on first use: the key names are the owning modules' own constants,
    and importing those modules here at import time would load the swarm
    package for every settings read."""
    from kazma_core.checkpoint_retention import RETENTION_KEY
    from kazma_core.lifecycle_notifier import EVENTS_KEY
    from kazma_core.swarm.task_store import TASK_RETENTION_KEY

    return {
        "cron.timezone": (_timezone, None),
        TASK_RETENTION_KEY: (_task_retention, "swarm"),
        RETENTION_KEY: (_checkpoint_retention, "system"),
        EVENTS_KEY: (_lifecycle_events, "notifications"),
    }


def validate_setting(key: str, value: Any) -> tuple[Any, str | None]:
    """``(value to store, category to store it under or None)``.

    Raises :class:`SettingRejected` for a value the setting cannot hold. A
    key with no rule passes unchanged."""
    rule = _rules().get(base_key(key))
    if rule is None:
        return value, None
    normalize, category = rule
    return normalize(value), category
