"""A turn's cost and wall clock, across the approval pauses that split it.

A turn that pauses for approval runs as several graph segments, and each
segment's terminal frame used to describe only itself. Live 2026-09-26 a
four-minute turn with three approvals ended "Completed 0:08" and
"0 tokens · $0.0000 · 4.3s": the last segment's clock, and the last LLM
call's usage -- which is 0 after a resume, because a resume runs without
stream events.

The per-call ledger stamps every LLM call with the turn id
(``kazma_core.observability.llm_ledger``); :func:`turn_totals` sums it.
:func:`turn_started_epoch` finds when the question that opened the turn was
asked, so the clock runs from there in every segment.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["turn_started_epoch", "turn_totals"]


def _epoch(ts: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return None


def turn_started_epoch(session_id: str, turn_id: str) -> float | None:
    """When the question that opened *turn_id* was asked (epoch), or None.

    That is the user row just above the turn's assistant row. Reads the
    stored session -- call it off the loop.
    """
    if not session_id or not turn_id:
        return None
    try:
        from kazma_ui.session_manager import get_session_manager

        sess = get_session_manager().get(session_id)
    except Exception:  # noqa: BLE001 -- a clock hint must never break the turn it times
        logger.debug("[turn-usage] session read failed", exc_info=True)
        return None
    rows = list(getattr(sess, "messages", None) or []) if sess is not None else []
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if not (isinstance(row, dict) and row.get("role") == "assistant"
                and str(row.get("turn_id") or "") == turn_id):
            continue
        for j in range(i - 1, -1, -1):
            prev = rows[j]
            if isinstance(prev, dict) and prev.get("role") == "user":
                return _epoch(prev.get("ts"))
        return _epoch(row.get("ts"))
    return None


def turn_totals(turn_id: str, *, since: str = "") -> tuple[int, float, int]:
    """``(tokens, cost_usd, calls)`` of the turn's LLM calls, or of the ones
    recorded after ``since`` (one segment). Reads SQLite -- off the loop."""
    from kazma_core.observability.llm_ledger import turn_usage

    usage = turn_usage(turn_id, since=since)
    return int(usage["tokens"]), float(usage["cost"]), int(usage["calls"])
