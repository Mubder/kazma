"""Tenant daily reservations and process concurrency for X model calls."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from kazma_core.x_api.ownership import x_config_key

CALL_SLOTS = threading.BoundedSemaphore(6)
DEFAULT_LIMITS = {"max_calls_per_decision": 16, "max_output_tokens_per_decision": 32000,
                  "max_input_bytes_per_decision": 196608, "max_input_bytes_per_call": 65536,
                  "max_daily_calls": 500, "max_daily_output_tokens": 1000000, "max_daily_reads": 500,
                  "deadline_seconds": 120, "chat_timeout_seconds": 45}


class XBudgetUnavailable(ValueError):
    """Hold work without weakening checks when budget is exhausted or unreadable."""


def _current_usage(current: Any, today: str) -> dict[str, Any]:
    if current is None:
        return {"day": today, "calls": 0, "reserved_output_tokens": 0}
    if (not isinstance(current, dict) or not isinstance(current.get("day"), str)
            or any(type(current.get(key)) is not int or current[key] < 0 for key in ("calls", "reserved_output_tokens"))):
        raise XBudgetUnavailable("X usage could not be verified; work is held.")
    try:
        date = datetime.strptime(current["day"], "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise XBudgetUnavailable("X usage date is invalid; work is held.") from exc
    if date > today:
        raise XBudgetUnavailable("X usage is dated in the future; work is held.")
    return dict(current) if date == today else {"day": today, "calls": 0, "reserved_output_tokens": 0}


def read_limits() -> dict[str, int]:
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get(x_config_key("connectors.x.ai_limits"))
    if raw is None:
        return dict(DEFAULT_LIMITS)
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_LIMITS):
        raise XBudgetUnavailable("X AI limits are invalid; review Settings before drafting.")
    result = {**DEFAULT_LIMITS, **raw}
    if any(type(value) is not int or value < 1 or value > DEFAULT_LIMITS[key] * 10 for key, value in result.items()):
        raise XBudgetUnavailable("X AI limits must be positive bounded integers.")
    return result


def reserve_daily(max_tokens: int, limits: dict[str, int], *, read: bool = False) -> dict[str, Any]:
    """Atomic multi-caller reservation; uncertain calls never refund themselves."""
    from kazma_core.config_store import get_config_store

    today = datetime.now(UTC).date().isoformat()
    def reserve(current: Any) -> dict[str, Any]:
        current = _current_usage(current, today)
        if (current["calls"] >= limits["max_daily_reads" if read else "max_daily_calls"]
                or current["reserved_output_tokens"] + max_tokens > limits["max_daily_output_tokens"]):
            raise XBudgetUnavailable("X read daily budget reached." if read else "X AI daily budget reached. Work is held; required checks remain enabled.")
        return {"day": today, "calls": current["calls"] + 1,
                "reserved_output_tokens": current["reserved_output_tokens"] + max_tokens}
    return get_config_store().atomic_update(x_config_key("connectors.x.read_usage" if read else "connectors.x.ai_usage"), reserve, category="x_ai_usage")


def reserve_read() -> dict[str, Any]:
    return reserve_daily(0, read_limits(), read=True)


def budget_status() -> dict[str, Any]:
    """Read-only diagnostics; reserved ceilings are not a claim about billed usage."""
    from kazma_core.config_store import get_config_store

    today = datetime.now(UTC).date().isoformat()
    return {"limits": read_limits(), "usage": _current_usage(get_config_store().get(x_config_key("connectors.x.ai_usage")), today),
            "read_usage": _current_usage(get_config_store().get(x_config_key("connectors.x.read_usage")), today),
            "accounting": "Requested output ceilings reserved at call start; failed or cancelled calls retain reservations."}
