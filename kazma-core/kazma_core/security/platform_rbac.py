"""Platform multi-user RBAC (admin / operator / viewer) — Phase 4.4.

Distinct from division RBAC (``kazma_core.rbac`` enterprise divisions).
This module gates *product* capabilities for the web UI / API.

Roles (least → most privilege):
  * viewer   — read-only APIs, no approve/write/exec
  * operator — day-to-day chat, HITL approve, IDE (default operator)
  * admin    — settings, users, secrets, system

Enable with users in ConfigStore key ``platform.users`` or Postgres
``kazma_platform_users``. Optional OIDC: see ``kazma_core.security.oidc``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PlatformRole",
    "PlatformUser",
    "ROLE_RANK",
    "authenticate_local_user",
    "create_local_user",
    "get_user",
    "list_users",
    "require_role",
    "role_allows",
]

logger = logging.getLogger(__name__)

PlatformRole = str  # "viewer" | "operator" | "admin"

ROLE_RANK: dict[str, int] = {
    "viewer": 10,
    "operator": 50,
    "admin": 100,
}

# Path prefixes that require at least admin
_ADMIN_PREFIXES = (
    "/api/settings",
    "/api/system",
    "/api/mcp",
    "/api/connectors",
    "/api/providers",
    "/api/provider",
    "/api/config",
    "/api/chaos",
    "/settings",
)

# Paths viewer may access (read-ish); everything else needs operator+
_VIEWER_OK_PREFIXES = (
    "/api/status",
    "/api/telemetry",
    "/api/sessions",
    "/api/session",
    "/api/chat",
    "/api/memory",
    "/api/dashboard",
    "/dashboard",
    "/chat",
    "/health",
    "/",
)


@dataclass
class PlatformUser:
    user_id: str
    username: str
    role: str = "operator"
    enabled: bool = True
    meta: dict[str, Any] | None = None

    def has_at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(role, 999)


def role_allows(role: str, path: str, method: str = "GET") -> bool:
    """Return True if *role* may access *path* with *method*."""
    rank = ROLE_RANK.get(role, 0)
    if rank >= ROLE_RANK["admin"]:
        return True
    m = method.upper()
    # Admin-only surfaces
    for p in _ADMIN_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return rank >= ROLE_RANK["admin"]
    # Mutations need operator+
    if m in ("POST", "PUT", "PATCH", "DELETE"):
        # Approve / chat / ide mutations
        return rank >= ROLE_RANK["operator"]
    # GET: viewer ok for read surfaces; operator for rest
    if rank >= ROLE_RANK["operator"]:
        return True
    # viewer
    for p in _VIEWER_OK_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def require_role(user: PlatformUser | None, minimum: str) -> bool:
    if user is None or not user.enabled:
        return False
    return user.has_at_least(minimum)


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, hexdigest = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        check = _hash_password(password, salt=salt)
        return hmac.compare_digest(check, stored)
    except Exception:
        return False


def _load_users_from_store() -> list[dict[str, Any]]:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get("platform.users", [])
        if isinstance(raw, list):
            return [u for u in raw if isinstance(u, dict)]
    except Exception:
        pass
    return []


def _save_users_to_store(users: list[dict[str, Any]]) -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set("platform.users", users, category="auth")


def list_users() -> list[PlatformUser]:
    out: list[PlatformUser] = []
    for u in _load_users_from_store():
        out.append(
            PlatformUser(
                user_id=str(u.get("user_id") or u.get("username") or ""),
                username=str(u.get("username") or ""),
                role=str(u.get("role") or "operator"),
                enabled=bool(u.get("enabled", True)),
                meta=dict(u.get("meta") or {}),
            )
        )
    return out


def get_user(username: str) -> PlatformUser | None:
    for u in list_users():
        if u.username.lower() == username.lower():
            return u
    return None


def create_local_user(
    username: str,
    password: str,
    *,
    role: str = "operator",
    user_id: str | None = None,
) -> PlatformUser:
    """Create or update a local username/password user."""
    if role not in ROLE_RANK:
        raise ValueError(f"Invalid role {role!r}; use viewer|operator|admin")
    users = _load_users_from_store()
    uid = user_id or secrets.token_hex(8)
    ph = _hash_password(password)
    found = False
    for u in users:
        if str(u.get("username", "")).lower() == username.lower():
            u["password_hash"] = ph
            u["role"] = role
            u["enabled"] = True
            uid = str(u.get("user_id") or uid)
            found = True
            break
    if not found:
        users.append(
            {
                "user_id": uid,
                "username": username,
                "password_hash": ph,
                "role": role,
                "enabled": True,
                "meta": {},
            }
        )
    _save_users_to_store(users)
    logger.warning("[platform_rbac] user upsert username=%s role=%s", username, role)
    return PlatformUser(user_id=uid, username=username, role=role, enabled=True)


def authenticate_local_user(username: str, password: str) -> PlatformUser | None:
    """Validate local credentials; return PlatformUser or None."""
    for u in _load_users_from_store():
        if str(u.get("username", "")).lower() != username.lower():
            continue
        if not u.get("enabled", True):
            return None
        if _verify_password(password, str(u.get("password_hash") or "")):
            return PlatformUser(
                user_id=str(u.get("user_id") or username),
                username=str(u.get("username")),
                role=str(u.get("role") or "operator"),
                enabled=True,
                meta=dict(u.get("meta") or {}),
            )
    return None


def multi_user_enabled() -> bool:
    """True when platform users exist or KAZMA_MULTI_USER=1."""
    if (os.environ.get("KAZMA_MULTI_USER") or "").strip().lower() in (
        "1", "true", "on", "yes",
    ):
        return True
    return bool(_load_users_from_store())
