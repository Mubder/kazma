"""Task-scoped HITL grants — auto-approve ALL danger tools for one task.

Between "approve once" (annoying for multi-step tasks) and YOLO (disables
ALL safety with no auto-recovery), there's "approve for task": grant ALL
danger tools for this thread until the user moves on to a new message.

Auto-expiry is two-layered:
  1. **New user message** — ``clear_task_grant(thread_id)`` is called at
     the gateway message-entry point, so the moment the user sends a new
     message (not a tool-approval callback), the task is done and safety
     re-engages. This is the natural task boundary.
  2. **TTL safety net** — default 10 minutes (``KAZMA_TASK_GRANT_TTL_SECONDS``),
     so a grant can never persist indefinitely even if the user walks away.

Storage: ConfigStore key ``task_grant.{thread_id}``, category ``safety``.
Mirrors the ``hitl_grants.py`` pattern exactly.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["grant_task", "has_task_grant", "clear_task_grant", "task_grant_status"]

# Default TTL: 10 minutes. Enough for a multi-step task (fix a PDF, run a
# build, etc.) without leaving safety disabled indefinitely. Override with
# KAZMA_TASK_GRANT_TTL_SECONDS.
_DEFAULT_GRANT_TTL = 10 * 60


def _cs() -> Any:
    """Lazy ConfigStore accessor (avoids circular import at module load)."""
    from kazma_core.config_store import get_config_store

    return get_config_store()


def grant_task(
    thread_id: str,
    *,
    actor: str = "unknown",
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Grant ALL danger tools for this thread until the next user message or TTL.

    Args:
        thread_id: The conversation thread to grant on.
        actor: Who approved (sender ID, for audit).
        ttl_seconds: Override the default TTL (default 10 min).

    Returns:
        Status dict with ``active``, ``expires_at``, etc.
    """
    if not thread_id:
        return {"active": False, "error": "no thread_id"}

    ttl = ttl_seconds if ttl_seconds is not None else _DEFAULT_GRANT_TTL
    now = time.time()
    payload = {
        "enabled": True,
        "since": now,
        "actor": actor,
        "ttl_seconds": ttl,
        "expires_at": (now + ttl) if ttl else None,
    }
    key = f"task_grant.{thread_id}"
    _cs().set(key, payload, category="safety")
    logger.warning(
        "[SECURITY] TASK GRANT enabled for thread=%s actor=%s ttl=%ds",
        thread_id,
        actor,
        ttl,
    )
    return task_grant_status(thread_id)


def has_task_grant(thread_id: str | None) -> bool:
    """True if an active (non-expired) task grant exists for this thread.

    Auto-expires + cleans up on TTL. Returns False for empty thread_id.
    """
    if not thread_id:
        return False
    status = task_grant_status(thread_id)
    return bool(status.get("active"))


def clear_task_grant(thread_id: str) -> bool:
    """Remove a task grant (safety re-engages). Returns True if one was cleared."""
    if not thread_id:
        return False
    key = f"task_grant.{thread_id}"
    try:
        existing = _cs().get(key)
        if existing is None:
            return False
        _cs().delete(key)
        logger.info("[SECURITY] TASK GRANT cleared for thread=%s", thread_id)
        return True
    except Exception:
        return False


def task_grant_status(thread_id: str | None) -> dict[str, Any]:
    """Return the task-grant status for a thread (auto-expires on TTL)."""
    if not thread_id:
        return {"active": False}
    key = f"task_grant.{thread_id}"
    try:
        raw = _cs().get(key)
        if raw is None:
            return {"active": False}
        # Tolerate legacy bare-True payloads.
        if raw is True:
            raw = {"enabled": True, "since": 0, "ttl_seconds": _DEFAULT_GRANT_TTL}
        if not isinstance(raw, dict):
            return {"active": False}

        now = time.time()
        expires_at = raw.get("expires_at")
        ttl = raw.get("ttl_seconds", _DEFAULT_GRANT_TTL)

        # Check expiry.
        if expires_at is not None and now >= float(expires_at):
            try:
                _cs().delete(key)
            except Exception:
                # Safe to ignore (audit O3): the grant is already treated as
                # expired below regardless of whether the row was removed, so
                # a failed delete costs a stale row, never a live grant.
                logger.debug(
                    "[task_grants] could not delete expired grant %s", key,
                    exc_info=True,
                )
            logger.info(
                "[SECURITY] TASK GRANT expired (TTL %ds) for thread=%s",
                ttl,
                thread_id,
            )
            return {"active": False, "expired": True}

        remaining = (float(expires_at) - now) if expires_at else None
        return {
            "active": bool(raw.get("enabled")),
            "since": raw.get("since"),
            "actor": raw.get("actor"),
            "ttl_seconds": ttl,
            "remaining_seconds": round(remaining, 1) if remaining else None,
            "expires_at": expires_at,
        }
    except Exception as exc:
        logger.debug("[task_grants] status read failed for %s: %s", thread_id, exc)
        return {"active": False, "error": str(exc)}
