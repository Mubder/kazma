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
    "try_enable_yolo",
    "yolo_allowed",
    "yolo_block_reason",
    "yolo_status",
    "YoloDisabledError",
]

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 3600  # 1 hour — was 4h; a stale YOLO window let a
# misread intent run an unprompted git commit (2026-08-27). Raise via
# KAZMA_YOLO_TTL_SECONDS (0/off = no expiry) if you truly need longer.


class YoloDisabledError(PermissionError):
    """Raised when YOLO is blocked by production policy."""


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "on", "yes")


def yolo_allowed() -> bool:
    """Return True when YOLO may be enabled.

    Explicit ``KAZMA_ALLOW_YOLO=0`` always wins (lab or prod).
    Production (``KAZMA_PRODUCTION=1``) disables YOLO unless the operator
    opts in with ``KAZMA_ALLOW_YOLO=1``. Unset + non-production stays allowed.
    """
    raw = os.environ.get("KAZMA_ALLOW_YOLO")
    if raw is not None and str(raw).strip() != "":
        return _truthy(str(raw))
    if _truthy(os.environ.get("KAZMA_PRODUCTION")):
        return False
    return True


def yolo_block_reason() -> str:
    """Operator-facing reason when ``yolo_allowed()`` is False."""
    raw = os.environ.get("KAZMA_ALLOW_YOLO")
    if raw is not None and str(raw).strip() != "" and not _truthy(str(raw)):
        return (
            "YOLO is turned off (KAZMA_ALLOW_YOLO=0). "
            "This tool can still be approved once."
        )
    return (
        "YOLO is disabled in production. "
        "Set KAZMA_ALLOW_YOLO=1 to opt in."
    )


def try_enable_yolo(thread_id: str, *, actor: str = "unknown") -> dict[str, Any]:
    """Enable session YOLO from a HITL card click.

    The operator is already on an approval card — that is consent for
    this thread. ``KAZMA_ALLOW_YOLO=0`` still blocks the ``/yolo`` slash
    command (``enable_yolo`` without ``force``).
    """
    st = enable_yolo(thread_id, actor=actor, force=True)
    st["downgraded"] = False
    return st


def _ttl_seconds() -> int:
    raw = (os.environ.get("KAZMA_YOLO_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(60, int(raw))  # minimum 1 minute
    # 0 or "off" = no expiry
    if raw in ("0", "off", "none", "infinite"):
        return 0
    return _DEFAULT_TTL_SECONDS


def enable_yolo(
    thread_id: str, *, actor: str = "unknown", force: bool = False,
) -> dict[str, Any]:
    """Enable YOLO for *thread_id*. Returns status dict for the user message.

    Raises:
        YoloDisabledError: When ``/yolo`` is blocked by policy, unless
            *force* (HITL card click).
    """
    if not force and not yolo_allowed():
        reason = yolo_block_reason()
        logger.warning(
            "[SECURITY] YOLO blocked thread=%s actor=%s reason=%s",
            thread_id,
            actor,
            reason,
        )
        raise YoloDisabledError(reason)

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
    # Also drop per-tool grants so /yolo off fully restores HITL.
    try:
        from kazma_core.safety.hitl_grants import clear_grants

        clear_grants(thread_id, actor=actor)
    except Exception:
        pass
    logger.warning(
        "[SECURITY] YOLO DISABLED thread=%s actor=%s",
        thread_id,
        actor,
    )


def is_yolo_active(thread_id: str | None) -> bool:
    """True if this thread has a live YOLO grant (any danger tool).

    Does not re-check ``yolo_allowed()``. A card-enabled session stays
    YOLO until ``/yolo off`` or TTL — otherwise MCP/native tools keep
    prompting after the first card.
    """
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
