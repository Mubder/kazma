"""Small helpers every run adapter shares."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

__all__ = ["epoch", "origin_from", "short_title"]


def epoch(value: Any) -> float | None:
    """Epoch seconds from an ISO string, a datetime or a number; None if none."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def origin_from(target: Any, thread_id: Any = "") -> dict[str, str]:
    """``{"platform", "target", "thread_id"}`` for a well-formed delivery
    target (``telegram:123``), else ``{}``. The target never leaves the
    server (``Run.to_dict`` keeps the platform only)."""
    from kazma_core.tools.send_message import is_valid_delivery_target

    text = str(target or "").strip()
    if not is_valid_delivery_target(text):
        return {}
    return {"platform": text.split(":", 1)[0].lower(), "target": text,
            "thread_id": str(thread_id or "")}


def short_title(text: Any, limit: int = 90) -> str:
    """One line of *text*, cut at *limit* characters."""
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"
