"""Session-scoped HITL tool grants — stop approval floods without full YOLO.

When the user approves a danger tool they can choose:

* ``once``  — this call only (default)
* ``tool``  — allow *this tool name* for the thread until TTL expires
* ``yolo``  — full session bypass (delegates to :mod:`kazma_core.safety.yolo`)

Grants are stored in ConfigStore under ``hitl_grant.{thread_id}.{tool}``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

__all__ = [
    "clear_grants",
    "grant_tool",
    "has_tool_grant",
    "list_grants",
]

logger = logging.getLogger(__name__)

_DEFAULT_GRANT_TTL = 30 * 60  # 30 minutes — shorter than YOLO default


def _ttl_seconds() -> int:
    raw = (os.environ.get("KAZMA_HITL_GRANT_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(60, int(raw))
    if raw in ("0", "off", "none", "infinite"):
        return 0
    return _DEFAULT_GRANT_TTL


def _key(thread_id: str, tool: str) -> str:
    return f"hitl_grant.{thread_id}.{tool}"


def grant_tool(
    thread_id: str,
    tool: str,
    *,
    actor: str = "unknown",
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Grant *tool* for *thread_id*. Returns status dict."""
    from kazma_core.config_store import get_config_store

    if not thread_id or not tool:
        return {"active": False, "error": "thread_id and tool required"}

    cs = get_config_store()
    now = time.time()
    ttl = _ttl_seconds() if ttl_seconds is None else max(0, int(ttl_seconds))
    payload = {
        "enabled": True,
        "tool": tool,
        "since": now,
        "actor": actor,
        "ttl_seconds": ttl,
        "expires_at": (now + ttl) if ttl > 0 else None,
    }
    cs.set(_key(thread_id, tool), payload, category="safety")
    logger.warning(
        "[SECURITY] HITL GRANT tool=%s thread=%s actor=%s ttl=%ss",
        tool,
        thread_id,
        actor,
        ttl or "none",
    )
    return {
        "active": True,
        "tool": tool,
        "thread_id": thread_id,
        "ttl_seconds": ttl,
        "expires_at": payload["expires_at"],
        "remaining_seconds": ttl if ttl > 0 else None,
    }


def has_tool_grant(thread_id: str | None, tool: str) -> bool:
    """True if *tool* is granted and not expired for this thread."""
    if not thread_id or not tool:
        return False
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    raw = cs.get(_key(thread_id, tool))
    if not raw:
        return False

    if raw is True or raw in (1, "1", "true", "True"):
        return True

    if not isinstance(raw, dict) or not raw.get("enabled"):
        return False

    expires = raw.get("expires_at")
    if expires is not None:
        try:
            if time.time() > float(expires):
                cs.delete(_key(thread_id, tool))
                logger.info(
                    "[SECURITY] HITL GRANT EXPIRED tool=%s thread=%s",
                    tool,
                    thread_id,
                )
                return False
        except (TypeError, ValueError):
            pass
    return True


def _grant_keys(cs: Any, thread_id: str) -> list[str]:
    """Return ConfigStore keys for grants on *thread_id*."""
    prefix = f"hitl_grant.{thread_id}."
    keys: list[str] = []
    try:
        cat = cs.get_category("safety") if hasattr(cs, "get_category") else None
        if isinstance(cat, dict):
            keys = [k for k in cat if str(k).startswith(prefix)]
        if not keys and hasattr(cs, "get_all"):
            all_items = cs.get_all() or {}
            # get_all → {category: {key: value}} or flat
            if isinstance(all_items, dict):
                safety = all_items.get("safety")
                if isinstance(safety, dict):
                    keys = [k for k in safety if str(k).startswith(prefix)]
                else:
                    keys = [k for k in all_items if str(k).startswith(prefix)]
    except Exception:
        keys = []
    return keys


def clear_grants(thread_id: str, *, actor: str = "unknown") -> int:
    """Remove all tool grants for *thread_id*. Returns count cleared."""
    if not thread_id:
        return 0
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    cleared = 0
    for key in _grant_keys(cs, thread_id):
        try:
            cs.delete(key)
            cleared += 1
        except Exception:
            pass

    if cleared:
        logger.warning(
            "[SECURITY] HITL GRANTS CLEARED thread=%s count=%d actor=%s",
            thread_id,
            cleared,
            actor,
        )
    return cleared


def list_grants(thread_id: str) -> list[dict[str, Any]]:
    """List active (non-expired) grants for a thread."""
    if not thread_id:
        return []
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    prefix = f"hitl_grant.{thread_id}."
    out: list[dict[str, Any]] = []
    for key in _grant_keys(cs, thread_id):
        tool = str(key)[len(prefix) :]
        if not has_tool_grant(thread_id, tool):
            continue
        raw = cs.get(key) or {}
        rem = None
        if isinstance(raw, dict) and raw.get("expires_at") is not None:
            try:
                rem = max(0, int(float(raw["expires_at"]) - time.time()))
            except (TypeError, ValueError):
                rem = None
        out.append(
            {
                "tool": tool,
                "remaining_seconds": rem,
                "actor": raw.get("actor") if isinstance(raw, dict) else None,
            }
        )
    return out
