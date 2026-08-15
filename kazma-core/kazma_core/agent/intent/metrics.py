"""Intent engine in-process metrics — route/act decision counters."""
from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

__all__ = ["record_decision", "get_intent_counters"]

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: Counter[tuple[str, str]] = Counter()


def record_decision(route: str, act: str) -> None:
    """Increment the decision counter for (route, act)."""
    with _lock:
        _counters[(route, act)] += 1


def get_intent_counters() -> dict[tuple[str, str], int]:
    """Snapshot of counters (for the metrics endpoint)."""
    with _lock:
        return dict(_counters)
