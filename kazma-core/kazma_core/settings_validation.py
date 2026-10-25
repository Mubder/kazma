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


def _turn_timeout(value: Any) -> Any:
    # The limit every surface applies to a running turn (agent/turn_budget.py);
    # a value it cannot read would be ignored there with only a warning.
    from kazma_core.agent.turn_budget import MAX_TURN_TIMEOUT_SECONDS, parse_turn_timeout

    seconds = parse_turn_timeout(value)
    if seconds is None:
        raise SettingRejected(
            "The turn time limit is a whole number of seconds "
            f"from 0 (no limit) to {MAX_TURN_TIMEOUT_SECONDS}."
        )
    return seconds


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


def _cron_bound(key: str) -> Callable[[Any], Any]:
    """A whole number in the scheduler setting's range (kazma_core.cron.settings)."""
    def normalize(value: Any) -> Any:
        from kazma_core.cron.settings import SETTING_BOUNDS

        _default, low, high = SETTING_BOUNDS[key]
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = None
        if number is None or not low <= number <= high:
            raise SettingRejected(f"{key} is a whole number from {low} to {high}.")
        return number

    return normalize


def _strict_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no", ""):
        return False
    raise SettingRejected("This setting is on or off (true or false).")


def _tenant_mode(value: Any) -> Any:
    # Under enforcement memory belongs to each principal's tenant whatever the
    # mode; a per-platform or per-user mode there split one user's memory
    # between the page and the turns (Memory audit M07). Refused, until one
    # resolver serves both for those modes.
    from kazma_core.memory.config import memory_tenant_enforced

    mode = str(value or "").strip().lower()
    if mode not in ("shared", "per_platform", "per_user"):
        raise SettingRejected("Memory isolation is one of: shared, per_platform, per_user.")
    if mode != "shared" and memory_tenant_enforced():
        raise SettingRejected(
            "With tenants enforced (production or several users), memory is already kept per "
            "tenant: only 'shared' is available here.")
    return mode


def _ocr_engine(value: Any) -> Any:
    # An engine the document policy cannot read would make every document
    # job refuse to run (an invalid stored policy fails closed).
    from kazma_core.documents.config import OCR_ENGINES, ocr_engine_choice

    try:
        return ocr_engine_choice(value)
    except ValueError:
        raise SettingRejected(f"The OCR engine is one of: {', '.join(OCR_ENGINES)}.") from None


@functools.cache
def _rules() -> dict[str, tuple[Callable[[Any], Any], str | None]]:
    """key -> (normalize, category the key is stored under or None).

    Built on first use: the key names are the owning modules' own constants,
    and importing those modules here at import time would load the swarm
    package for every settings read."""
    from kazma_core.agent.turn_budget import TURN_TIMEOUT_KEY
    from kazma_core.checkpoint_retention import RETENTION_KEY
    from kazma_core.cron.settings import FIRE_WITHOUT_HITL_KEY, SETTING_BOUNDS
    from kazma_core.documents.config import OCR_ENGINE_KEY
    from kazma_core.lifecycle_notifier import EVENTS_KEY
    from kazma_core.memory.config import TENANT_MODE_KEY
    from kazma_core.runs.runbooks import RUNBOOKS_KEY, validate_runbooks
    from kazma_core.swarm.task_store import TASK_RETENTION_KEY

    return {
        OCR_ENGINE_KEY: (_ocr_engine, "documents"),
        "cron.timezone": (_timezone, None),
        # The scheduler's bounds (Scheduled audit SCH-E06, E14, S05).
        **{key: (_cron_bound(key), None) for key in SETTING_BOUNDS},
        FIRE_WITHOUT_HITL_KEY: (_strict_bool, None),
        TENANT_MODE_KEY: (_tenant_mode, "memory"),
        TASK_RETENTION_KEY: (_task_retention, "swarm"),
        RETENTION_KEY: (_checkpoint_retention, "system"),
        TURN_TIMEOUT_KEY: (_turn_timeout, "agent"),
        EVENTS_KEY: (_lifecycle_events, "notifications"),
        RUNBOOKS_KEY: (validate_runbooks, None),
    }


def validate_setting(key: str, value: Any) -> tuple[Any, str | None]:
    """``(value to store, category to store it under or None)``.

    Raises :class:`SettingRejected` for a value the setting cannot hold. A
    key with no rule passes unchanged."""
    from kazma_core.safety.deployment_policy import (
        CONTAINER_REQUIRED_KEY,
        DOCUMENT_WORKER_ISOLATION_KEY,
        WORKSPACE_ROOTS_KEY,
        normalize_container_required,
        normalize_document_worker_isolation,
        normalize_workspace_roots,
    )

    name = base_key(key)
    try:
        if name == WORKSPACE_ROOTS_KEY:
            return normalize_workspace_roots(value), "security"
        if name == CONTAINER_REQUIRED_KEY:
            return normalize_container_required(value), "security"
        if name == DOCUMENT_WORKER_ISOLATION_KEY:
            return normalize_document_worker_isolation(value), "security"
        # A parent mapping save or backup restore must enforce the same rules.
        if name in ("security", "security.execution") and isinstance(value, dict):
            return {child: validate_setting(name + "." + child, item)[0]
                    for child, item in value.items()}, "security"
        if name == "memory" and isinstance(value, dict) and "tenant_mode" in value:
            return {**value, "tenant_mode": _tenant_mode(value["tenant_mode"])}, "memory"
        if name == "documents.ocr" and isinstance(value, dict) and "engine" in value:
            return {**value, "engine": _ocr_engine(value["engine"])}, "documents"
        if (name == "documents" and isinstance(value, dict) and isinstance(value.get("ocr"), dict)
                and "engine" in value["ocr"]):
            ocr = {**value["ocr"], "engine": _ocr_engine(value["ocr"]["engine"])}
            return {**value, "ocr": ocr}, "documents"
    except ValueError as exc:
        raise SettingRejected(str(exc)) from None
    rule = _rules().get(name)
    if rule is None:
        return value, None
    normalize, category = rule
    return normalize(value), category
