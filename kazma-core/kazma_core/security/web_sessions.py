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
    "SESSION_KEY_PREFIX",
    "create_session",
    "revoke_session",
    "validate_session",
    "use_opaque_sessions",
    "purge_expired_sessions",
]

#: ConfigStore key prefix for session records.
SESSION_KEY_PREFIX = "web_session."

logger = logging.getLogger(__name__)

SESSION_COOKIE = "kazma-session"
_DEFAULT_TTL = 14 * 24 * 3600  # 14 days


def use_opaque_sessions() -> bool:
    """Opaque sessions are default-on; set KAZMA_OPAQUE_SESSIONS=0 to disable.

    Multi-user mode always forces opaque sessions (cannot fall back to
    raw ``kazma-secret`` cookie as the session identity).
    """
    try:
        from kazma_core.security.platform_rbac import multi_user_enabled

        if multi_user_enabled():
            return True
    except Exception:
        pass
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
    tenant_id: str | None = None,
) -> str:
    """Mint a new opaque session id and persist its hash. Returns raw id for cookie."""
    from kazma_core.config_store import get_config_store

    sid = secrets.token_urlsafe(32)
    now = time.time()
    ttl = _ttl()
    tenant = (tenant_id or "").strip() or "default"
    payload: dict[str, Any] = {
        "created_at": now,
        "expires_at": now + ttl,
        "actor": actor,
        "username": username,
        "role": role or "admin",  # legacy single-operator = full admin
        "user_id": user_id,
        "tenant_id": tenant,
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


def purge_expired_sessions() -> int:
    """Delete every expired ``web_session.*`` record. Returns the count removed.

    Audit F-11: expiry was only ever enforced lazily, inside
    :func:`get_session_payload`, and only for the specific session being
    presented. A session never presented again was never removed, so the auth
    category of the ConfigStore grew without bound — one row per issued
    session, forever, and into every backup snapshot.

    Safe to call repeatedly; runs at startup and on the memory-ops cadence.
    """
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    now = time.time()
    removed = 0
    try:
        records = store.get_category("auth")
    except Exception:
        logger.warning("[web_sessions] purge skipped — cannot read auth category", exc_info=True)
        return 0

    for key, payload in list(records.items()):
        if not str(key).startswith(SESSION_KEY_PREFIX):
            continue
        expired = True
        if isinstance(payload, dict):
            try:
                expired = float(payload.get("expires_at") or 0) <= now
            except (TypeError, ValueError):
                expired = True  # unparseable expiry — treat as stale
        if not expired:
            continue
        try:
            store.delete(key)
            removed += 1
        except Exception:
            logger.debug("[web_sessions] could not delete %s", key, exc_info=True)

    if removed:
        logger.info("[web_sessions] purged %d expired session(s)", removed)
    return removed


def revoke_session(session_id: str | None) -> None:
    """Invalidate an opaque session."""
    if not session_id:
        return
    from kazma_core.config_store import get_config_store

    get_config_store().delete(f"web_session.{_hash(str(session_id).strip())}")
    logger.info("[web_sessions] revoked session")
