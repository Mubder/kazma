"""Opaque web session tokens (audit H1 / Phase 4).

Browser cookies hold a random session id — never the shared KAZMA_SECRET.
Server stores SHA-256(session_id) → expiry in ConfigStore.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
from typing import Any

__all__ = [
    "SESSION_COOKIE",
    "SESSION_KEY_PREFIX",
    "create_session",
    "list_sessions",
    "revoke_session",
    "revoke_user_sessions",
    "validate_session",
    "use_opaque_sessions",
    "purge_expired_sessions",
]

#: ConfigStore key prefix for session records.
SESSION_KEY_PREFIX = "web_session."

logger = logging.getLogger(__name__)

SESSION_COOKIE = "kazma-session"
_DEFAULT_TTL = 14 * 24 * 3600  # 14 days

# Read-through cache of session payloads (and misses), keyed by the stored
# key. Every authenticated request resolves its session, and it did so with a
# ConfigStore read -- a Postgres round trip on the event loop -- per request:
# five loop-stall dumps caught it there. A revoke in this process evicts at
# once, expiry is re-checked on every hit, and a revoke made by ANOTHER
# process is seen within _CACHE_TTL_S.
_CACHE_TTL_S = 30.0
_CACHE_MAX = 2048
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_revoked: dict[str, float] = {}  # key -> until: no reader may re-seed it
_cache_lock = threading.Lock()


def _cache_get(key: str) -> tuple[bool, dict[str, Any] | None]:
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None or hit[0] < time.monotonic():
        return False, None
    return True, hit[1]


def _cache_put(key: str, payload: dict[str, Any] | None) -> None:
    with _cache_lock:
        if payload is not None and _revoked.get(key, 0.0) > time.monotonic():
            return  # read before the revoke landed; do not resurrect it
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + _CACHE_TTL_S, payload)


def _cache_evict(key: str, *, revoked: bool = False) -> None:
    with _cache_lock:
        _cache.pop(key, None)
        if revoked:
            now = time.monotonic()
            for k in [k for k, until in _revoked.items() if until < now]:
                del _revoked[k]
            _revoked[key] = now + _CACHE_TTL_S


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
    role: str,
    actor: str = "web",
    username: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    """Mint a new opaque session id and persist its hash. Returns raw id for cookie.

    ``role`` is REQUIRED and has no default.

    It used to be ``role: str | None = None`` stored as ``role or "admin"`` —
    a caller that did not think about authority silently minted a full admin
    session. That was defensible while the payload described one operator,
    but it already carries ``user_id`` and ``tenant_id``: the moment sessions
    are per-user, "forgot to pass a role" becomes "issued an admin session",
    and it fails open, quietly, at the one place that is supposed to decide
    authority.

    Every existing caller kept exactly the authority it already had — this
    changes no behaviour today. What it removes is the inheritance: the next
    caller has to say what it means, and a caller that says nothing does not
    compile (TypeSafe audit follow-up, 2026-09-19).
    """
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
        "role": role,
        "user_id": user_id,
        "tenant_id": tenant,
    }
    key = f"web_session.{_hash(sid)}"
    get_config_store().set(key, payload, category="auth")
    _cache_put(key, dict(payload))
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
    cached, raw = _cache_get(key)
    if not cached:
        raw = get_config_store().get(key)
        raw = raw if isinstance(raw, dict) else None
        _cache_put(key, raw)
    if raw is None:
        return None
    exp = raw.get("expires_at")
    try:
        if exp is not None and time.time() > float(exp):
            _cache_evict(key)
            get_config_store().delete(key)
            return None
    except (TypeError, ValueError):
        return None
    return dict(raw)


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
            _cache_evict(str(key))
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

    key = f"web_session.{_hash(str(session_id).strip())}"
    _cache_evict(key, revoked=True)  # tombstone first: no racing reader re-seeds it
    get_config_store().delete(key)
    logger.info("[web_sessions] revoked session")


def _live_records() -> list[tuple[str, dict[str, Any]]]:
    """Every unexpired ``web_session.*`` record: ``(key, payload)``."""
    from kazma_core.config_store import get_config_store

    now = time.time()
    out: list[tuple[str, dict[str, Any]]] = []
    for key, payload in get_config_store().get_category("auth").items():
        if not str(key).startswith(SESSION_KEY_PREFIX) or not isinstance(payload, dict):
            continue
        try:
            if float(payload.get("expires_at") or 0) <= now:
                continue
        except (TypeError, ValueError):
            continue
        out.append((str(key), payload))
    return out


def _belongs_to(payload: dict[str, Any], *, user_id: str, username: str) -> bool:
    if user_id:
        return str(payload.get("user_id") or "") == user_id
    return bool(username) and str(payload.get("username") or "") == username


def list_sessions(*, user_id: str = "", username: str = "", current: str | None = None) -> list[dict[str, Any]]:
    """The live web sessions of one signed-in person, newest first: when each
    began and ends, and which is *current* (the raw cookie value).

    Settings -> Account listed ``account.sessions``, a key nothing wrote, so
    it always said "no active sessions" (2026-11 functional audit). Never the
    session id itself: ``id`` is the start of its hash."""
    mine = f"{SESSION_KEY_PREFIX}{_hash(str(current).strip())}" if current else ""
    rows = [
        {
            "id": key[len(SESSION_KEY_PREFIX):][:12],
            "platform": str(payload.get("actor") or "web"),
            "started_at": float(payload.get("created_at") or 0),
            "expires_at": float(payload.get("expires_at") or 0),
            "current": key == mine,
        }
        for key, payload in _live_records()
        if _belongs_to(payload, user_id=user_id, username=username)
    ]
    return sorted(rows, key=lambda r: r["started_at"], reverse=True)


def revoke_user_sessions(*, user_id: str = "", username: str = "", keep: str | None = None) -> int:
    """End every live session of one person but *keep* (the raw cookie value
    of the session asking): after a password change the old one must not
    stay signed in elsewhere. Returns how many were ended."""
    from kazma_core.config_store import get_config_store

    kept = f"{SESSION_KEY_PREFIX}{_hash(str(keep).strip())}" if keep else ""
    ended = 0
    for key, payload in _live_records():
        if key == kept or not _belongs_to(payload, user_id=user_id, username=username):
            continue
        _cache_evict(key, revoked=True)
        get_config_store().delete(key)
        ended += 1
    if ended:
        logger.info("[web_sessions] ended %d other session(s) of one user", ended)
    return ended
