"""Session YOLO mode — temporary HITL bypass with audit trail + TTL.

YOLO remains available (``/yolo`` / ``/yolo off``) but is no longer a silent
permanent flag: every enable is logged, optional expiry applies, and status
is inspectable.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

__all__ = [
    "disable_yolo",
    "enable_yolo",
    "is_yolo_active",
    "yolo_status",
]

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 4 * 3600  # 4 hours


def _ttl_seconds() -> int:
    raw = (os.environ.get("KAZMA_YOLO_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(60, int(raw))  # minimum 1 minute
    # 0 or "off" = no expiry
    if raw in ("0", "off", "none", "infinite"):
        return 0
    return _DEFAULT_TTL_SECONDS


def enable_yolo(thread_id: str, *, actor: str = "unknown") -> dict[str, Any]:
    """Enable YOLO for *thread_id*. Returns status dict for the user message."""
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    now = time.time()
    ttl = _ttl_seconds()
    payload = {
        "enabled": True,
        "since": now,
        "actor": actor,
        "ttl_seconds": ttl,
        "expires_at": (now + ttl) if ttl > 0 else None,
    }
    cs.set(f"yolo.{thread_id}", payload, category="safety")
    logger.warning(
        "[SECURITY] YOLO ENABLED thread=%s actor=%s ttl=%ss",
        thread_id,
        actor,
        ttl or "none",
    )
    return yolo_status(thread_id)


def disable_yolo(thread_id: str, *, actor: str = "unknown") -> None:
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    cs.delete(f"yolo.{thread_id}")
    logger.warning(
        "[SECURITY] YOLO DISABLED thread=%s actor=%s",
        thread_id,
        actor,
    )


def is_yolo_active(thread_id: str | None) -> bool:
    """True if YOLO is on and not expired for this thread."""
    if not thread_id:
        return False
    st = yolo_status(thread_id)
    return bool(st.get("active"))


def yolo_status(thread_id: str) -> dict[str, Any]:
    """Return structured YOLO status; auto-disables on expiry."""
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    raw = cs.get(f"yolo.{thread_id}")
    if not raw:
        return {"active": False, "thread_id": thread_id}

    # Legacy: bare True / "true" / 1
    if raw is True or raw in (1, "1", "true", "True", "yes"):
        return {
            "active": True,
            "thread_id": thread_id,
            "legacy": True,
            "ttl_seconds": _ttl_seconds(),
            "actor": "unknown",
        }

    if not isinstance(raw, dict) or not raw.get("enabled"):
        return {"active": False, "thread_id": thread_id}

    expires = raw.get("expires_at")
    if expires is not None:
        try:
            if time.time() > float(expires):
                cs.delete(f"yolo.{thread_id}")
                logger.warning(
                    "[SECURITY] YOLO EXPIRED thread=%s (auto-disabled)",
                    thread_id,
                )
                return {"active": False, "thread_id": thread_id, "expired": True}
        except (TypeError, ValueError):
            pass

    remaining = None
    if expires is not None:
        try:
            remaining = max(0, int(float(expires) - time.time()))
        except (TypeError, ValueError):
            remaining = None

    return {
        "active": True,
        "thread_id": thread_id,
        "actor": raw.get("actor", "unknown"),
        "since": raw.get("since"),
        "ttl_seconds": raw.get("ttl_seconds"),
        "expires_at": expires,
        "remaining_seconds": remaining,
        "legacy": False,
    }
