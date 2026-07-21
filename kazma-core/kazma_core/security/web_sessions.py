"""Opaque web session tokens (audit H1 / Phase 4).

Browser cookies hold a random session id — never the shared KAZMA_SECRET.
Server stores SHA-256(session_id) → expiry in ConfigStore.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from typing import Any

__all__ = [
    "SESSION_COOKIE",
    "create_session",
    "revoke_session",
    "validate_session",
    "use_opaque_sessions",
]

logger = logging.getLogger(__name__)

SESSION_COOKIE = "kazma-session"
_DEFAULT_TTL = 14 * 24 * 3600  # 14 days


def use_opaque_sessions() -> bool:
    """Opaque sessions are default-on; set KAZMA_OPAQUE_SESSIONS=0 to disable."""
    raw = (os.environ.get("KAZMA_OPAQUE_SESSIONS") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _ttl() -> int:
    raw = (os.environ.get("KAZMA_SESSION_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(300, int(raw))
    return _DEFAULT_TTL


def create_session(
    *,
    actor: str = "web",
    username: str | None = None,
    role: str | None = None,
    user_id: str | None = None,
) -> str:
    """Mint a new opaque session id and persist its hash. Returns raw id for cookie."""
    from kazma_core.config_store import get_config_store

    sid = secrets.token_urlsafe(32)
    now = time.time()
    ttl = _ttl()
    payload: dict[str, Any] = {
        "created_at": now,
        "expires_at": now + ttl,
        "actor": actor,
        "username": username,
        "role": role or "admin",  # legacy single-operator = full admin
        "user_id": user_id,
    }
    get_config_store().set(
        f"web_session.{_hash(sid)}",
        payload,
        category="auth",
    )
    logger.info(
        "[web_sessions] created session actor=%s user=%s role=%s ttl=%ss",
        actor,
        username or "-",
        payload["role"],
        ttl,
    )
    return sid


def get_session_payload(session_id: str | None) -> dict[str, Any] | None:
    """Return session payload dict if live; else None."""
    if not session_id or not str(session_id).strip():
        return None
    from kazma_core.config_store import get_config_store

    key = f"web_session.{_hash(str(session_id).strip())}"
    raw = get_config_store().get(key)
    if not isinstance(raw, dict):
        return None
    exp = raw.get("expires_at")
    try:
        if exp is not None and time.time() > float(exp):
            get_config_store().delete(key)
            return None
    except (TypeError, ValueError):
        return None
    return raw


def validate_session(session_id: str | None) -> bool:
    """Return True if *session_id* is a live opaque session."""
    return get_session_payload(session_id) is not None


def revoke_session(session_id: str | None) -> None:
    """Invalidate an opaque session."""
    if not session_id:
        return
    from kazma_core.config_store import get_config_store

    get_config_store().delete(f"web_session.{_hash(str(session_id).strip())}")
    logger.info("[web_sessions] revoked session")
