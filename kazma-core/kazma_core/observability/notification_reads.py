"""Which notifications in the bell the owner has read (2026-11-08).

The owner: a notification opened its page, "but the notification still there
with a bell badge showing". The badge counted every alert the server listed,
and nothing recorded that one had been seen: a research run's "Research
completed" stays in the bell's history (web_acquire.monitor keeps the latest
fifty), so the badge never went down.

Opening a notification (or "Mark all as read") records its id here, in the
settings store under :data:`READ_KEY` (Kazma's own state, ``system.``: never
carried by a settings backup), so a notification read on the phone is read
on the desktop too. ``/api/alerts/recent`` says ``read`` per item; the bell
counts the unread ones, drops read news (a success or information: it has
been seen) and keeps a read problem, dimmed, until it is fixed -- an open
problem is never hidden by having been looked at.

At most :data:`KEEP` ids, the oldest read dropped first. An id is the
alert's own (``AlertPayload.id``, a run incident's, a research or crawl
event's), stable while its condition lasts; an alert raised again later has
another id and is unread again.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["KEEP", "MAX_IDS", "READ_KEY", "clean_ids", "mark_read", "read_ids"]

READ_KEY = "system.notifications.read"
KEEP = 500
#: Ids one request may mark (the bell lists at most a few dozen).
MAX_IDS = 200
_MAX_ID_LEN = 200


def _store(store: Any = None) -> Any:
    if store is not None:
        return store
    from kazma_core.config_store import get_config_store

    return get_config_store()


def read_ids(store: Any = None) -> set[str]:
    """The ids of the notifications read. Blocking (the settings store)."""
    value = _store(store).get(READ_KEY) or {}
    return {str(k) for k in value} if isinstance(value, dict) else set()


def clean_ids(ids: Any) -> list[str]:
    """*ids* as notification ids: strings of at most 200 characters, each
    once, at most :data:`MAX_IDS`. Raises ValueError for anything else."""
    if not isinstance(ids, list) or len(ids) > MAX_IDS:
        raise ValueError(f"ids must be a list of at most {MAX_IDS} notification ids")
    out: list[str] = []
    for item in ids:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_ID_LEN:
            raise ValueError("each id must be a notification id")
        if item not in out:
            out.append(item)
    return out


def mark_read(ids: list[str], *, store: Any = None, now: float | None = None) -> int:
    """Record *ids* (cleaned by :func:`clean_ids`) as read; returns how many.
    Blocking (the settings store)."""
    if not ids:
        return 0
    at = time.time() if now is None else now

    def change(current: Any) -> dict[str, float]:
        seen = {str(k): float(v) for k, v in current.items() if isinstance(v, (int, float))} \
            if isinstance(current, dict) else {}
        for notification in ids:
            seen[notification] = at
        if len(seen) > KEEP:
            for old in sorted(seen, key=seen.__getitem__)[: len(seen) - KEEP]:
                seen.pop(old, None)
        return seen

    _store(store).atomic_update(READ_KEY, change, category="system")
    return len(ids)
