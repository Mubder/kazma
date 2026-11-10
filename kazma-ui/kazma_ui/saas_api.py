"""SaaS multi-user admin API — users, tenants, backend status."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.errors import safe_error
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

logger = logging.getLogger(__name__)

__all__ = ["create_saas_router"]


def create_saas_router() -> APIRouter:
    router = APIRouter(prefix="/api/saas", tags=["saas"])

    def _require_admin(request: Request) -> JSONResponse | None:
        # Open mode (no secret) passes inside admin_decision: gating status
        # there blanked Settings → Ops on a no-secret install (audit 2026-09-17).
        from kazma_ui.auth import admin_decision

        decision = admin_decision(request)
        if decision == "ok":
            return None
        if decision == "unauthorized":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse({"error": "Admin role required"}, status_code=403)

    @router.get("/status")
    def saas_status(request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        from kazma_core.db.backend import get_backend, get_database_url, is_postgres
        from kazma_core.security.platform_rbac import (
            PlatformUsersUnavailable,
            list_users,
            multi_user_enabled,
        )
        from kazma_core.security.oidc import oidc_configured
        from kazma_ui.auth import get_request_principal

        principal = get_request_principal(request) or {}
        try:
            multi_user: bool | None = multi_user_enabled()
        except PlatformUsersUnavailable:
            # Unknown, said as unknown (audit 2026-10-26 AUD-123).
            multi_user = None
        return JSONResponse({
            "backend": get_backend().value,
            "postgres": is_postgres(),
            "database_url_set": bool(get_database_url()),
            "multi_user": multi_user,
            "user_count": len(list_users()),
            "oidc": oidc_configured(),
            "principal": {
                "username": principal.get("username"),
                "role": principal.get("role"),
                "source": principal.get("source"),
            },
        })

    @router.get("/users")
    def list_platform_users(request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        from kazma_core.security.platform_rbac import list_users

        users = [
            {
                "user_id": u.user_id,
                "username": u.username,
                "role": u.role,
                "enabled": u.enabled,
                "tenant_id": u.tenant_id,
            }
            for u in list_users()
        ]
        return JSONResponse({"users": users})

    @router.post("/users")
    async def create_platform_user(request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_require_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        role = str(body.get("role") or "operator").strip().lower()
        if not username or len(password) < 8:
            return JSONResponse(
                {"error": "username required; password min 8 chars"},
                status_code=400,
            )
        if role not in ("viewer", "operator", "admin"):
            return JSONResponse({"error": "role must be viewer|operator|admin"}, status_code=400)
        tenant_id = str(body.get("tenant_id") or "").strip() or None
        try:
            from kazma_core.security.platform_rbac import create_local_user, valid_tenant_id

            if tenant_id is not None and not valid_tenant_id(tenant_id):
                return JSONResponse(
                    {"error": "tenant_id: letters, digits, '.', '_' or '-', up to 64"},
                    status_code=400,
                )
            # A user-store write and a PBKDF2 hash (audit 2026-10-26 AUD-111).
            user = await asyncio.to_thread(
                create_local_user, username, password, role=role, tenant_id=tenant_id
            )
            return JSONResponse({
                "status": "ok",
                "user": {
                    "user_id": user.user_id,
                    "username": user.username,
                    "role": user.role,
                    "enabled": user.enabled,
                    "tenant_id": user.tenant_id,
                },
            })
        except Exception as exc:
            logger.exception("[saas] create user failed")
            # AUD-102: a 500 answers with a code and reference, never the
            # exception's text (§26F).
            return JSONResponse({"error": safe_error(exc, log=False)}, status_code=500)

    @router.delete("/users/{username}")
    def delete_platform_user(username: str, request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        from kazma_core.security.platform_rbac import (
            PlatformUsersUnavailable,
            _load_users_from_store,
            _save_users_to_store,
        )

        # Strict read: an unreadable list is not "no users" (audit 2026-10-26
        # AUD-123); read as empty, this answered 404 over a user who exists.
        try:
            users = _load_users_from_store()
        except PlatformUsersUnavailable:
            logger.warning("[saas] delete user: platform users unreadable", exc_info=True)
            return JSONResponse({"error": "users_unavailable"}, status_code=503)
        gone = [u for u in users if str(u.get("username", "")).lower() == username.lower()]
        new_users = [u for u in users if str(u.get("username", "")).lower() != username.lower()]
        if not gone:
            return JSONResponse({"error": "User not found"}, status_code=404)
        _save_users_to_store(new_users)
        # A deleted user's sessions end with them, the asking admin's too
        # when they delete themselves (security audit 2026-11-10).
        ended = _end_sessions(gone[0], keep=None)
        if ended is None:
            return _sessions_not_ended()
        return JSONResponse({"status": "ok", "deleted": username, "sessions_ended": ended})

    @router.patch("/users/{username}")
    async def patch_platform_user(username: str, request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_require_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request, allow_empty=True)
        except InvalidJsonBody:
            return invalid_json_response()
        # The user store round trip and the password hash stay off the loop.
        from kazma_core.security.web_sessions import SESSION_COOKIE

        return await asyncio.to_thread(
            _patch_user, username, body, request.cookies.get(SESSION_COOKIE)
        )

    def _end_sessions(user: dict[str, Any], *, keep: str | None) -> int | None:
        """End *user*'s sessions but *keep*; ``None`` when that failed."""
        from kazma_core.security import web_sessions

        try:
            return web_sessions.revoke_user_sessions(
                user_id=str(user.get("user_id") or ""),
                username=str(user.get("username") or ""),
                keep=keep,
            )
        except Exception:
            logger.warning("[saas] could not end the sessions of %s", user.get("username"),
                           exc_info=True)
            return None

    def _sessions_not_ended() -> JSONResponse:
        # The change is saved; the sessions it should have ended are not.
        return JSONResponse(
            {"error": "sessions_not_ended",
             "detail": "Saved, but the user's sessions could not be ended. Try again."},
            status_code=503,
        )

    def _patch_user(username: str, body: dict[str, Any], keep: str | None = None) -> JSONResponse:
        from kazma_core.security.platform_rbac import (
            PlatformUsersUnavailable,
            _hash_password,
            _load_users_from_store,
            _save_users_to_store,
        )

        # Strict read (audit 2026-10-26 AUD-123), as for delete.
        try:
            users = _load_users_from_store()
        except PlatformUsersUnavailable:
            logger.warning("[saas] update user: platform users unreadable", exc_info=True)
            return JSONResponse({"error": "users_unavailable"}, status_code=503)
        found = None
        for u in users:
            if str(u.get("username", "")).lower() == username.lower():
                found = u
                break
        if not found:
            return JSONResponse({"error": "User not found"}, status_code=404)
        # A session carries the role it was minted with, so a role change, a
        # disable or a password reset must end the sessions that predate it
        # (security audit 2026-11-10: they kept working). Only a password
        # reset keeps the asking admin's own session; a changed role or a
        # disabled account would otherwise live on in it.
        role_changed = disabled = password_reset = False
        if "role" in body:
            role = str(body["role"]).lower()
            if role not in ("viewer", "operator", "admin"):
                return JSONResponse({"error": "invalid role"}, status_code=400)
            role_changed = role != str(found.get("role") or "operator").lower()
            found["role"] = role
        if "enabled" in body:
            disabled = not bool(body["enabled"]) and found.get("enabled", True) is not False
            found["enabled"] = bool(body["enabled"])
        if body.get("password"):
            pw = str(body["password"])
            if len(pw) < 8:
                return JSONResponse({"error": "password min 8 chars"}, status_code=400)
            found["password_hash"] = _hash_password(pw)
            password_reset = True
        _save_users_to_store(users)
        ended = 0
        if role_changed or disabled or password_reset:
            keep_mine = keep if (password_reset and not role_changed and not disabled) else None
            result = _end_sessions(found, keep=keep_mine)
            if result is None:
                return _sessions_not_ended()
            ended = result
        return JSONResponse({
            "status": "ok",
            "user": {
                "user_id": found.get("user_id"),
                "username": found.get("username"),
                "role": found.get("role"),
                "enabled": found.get("enabled", True),
            },
            "sessions_ended": ended,
        })

    @router.get("/tenants")
    def list_tenants(request: Request) -> JSONResponse:
        """List known tenant ids (from config + default)."""
        denied = _require_admin(request)
        if denied:
            return denied
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        raw = cs.get("saas.tenants", [])
        tenants: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, dict) and t.get("id"):
                    tenants.append({"id": t["id"], "name": t.get("name") or t["id"]})
                elif isinstance(t, str):
                    tenants.append({"id": t, "name": t})
        if not any(t["id"] == "default" for t in tenants):
            tenants.insert(0, {"id": "default", "name": "Default"})
        return JSONResponse({"tenants": tenants})

    @router.post("/tenants")
    async def create_tenant(request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_require_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        tid = str(body.get("id") or "").strip().lower().replace(" ", "-")
        name = str(body.get("name") or tid).strip()
        if not tid or tid == "default":
            return JSONResponse({"error": "valid non-default id required"}, status_code=400)
        from kazma_core.config_store import get_config_store

        class _Exists(Exception):
            pass

        def _add(raw: Any) -> list[Any]:
            tenants: list[Any] = list(raw) if isinstance(raw, list) else []
            for t in tenants:
                if (isinstance(t, dict) and t.get("id") == tid) or t == tid:
                    raise _Exists
            tenants.append({"id": tid, "name": name})
            return tenants

        # One read-modify-write (two admins adding at once both land), off
        # the event loop: on Postgres it is a locked round trip.
        try:
            await asyncio.to_thread(
                get_config_store().atomic_update, "saas.tenants", _add, category="saas",
            )
        except _Exists:
            return JSONResponse({"error": "tenant exists"}, status_code=409)
        return JSONResponse({"status": "ok", "tenant": {"id": tid, "name": name}})

    return router
