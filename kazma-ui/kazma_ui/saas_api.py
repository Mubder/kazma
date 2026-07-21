"""SaaS multi-user admin API — users, tenants, backend status."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["create_saas_router"]


def create_saas_router() -> APIRouter:
    router = APIRouter(prefix="/api/saas", tags=["saas"])

    def _require_admin(request: Request) -> JSONResponse | None:
        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        principal = get_request_principal(request) or {}
        # Shared secret = full admin; multi-user needs admin role
        if principal.get("source") == "secret":
            return None
        if principal.get("role") != "admin":
            return JSONResponse({"error": "Admin role required"}, status_code=403)
        return None

    @router.get("/status")
    async def saas_status(request: Request) -> JSONResponse:
        from kazma_core.db.backend import get_backend, get_database_url, is_postgres
        from kazma_core.security.platform_rbac import list_users, multi_user_enabled
        from kazma_core.security.oidc import oidc_configured
        from kazma_ui.auth import get_request_principal

        principal = get_request_principal(request) or {}
        return JSONResponse({
            "backend": get_backend().value,
            "postgres": is_postgres(),
            "database_url_set": bool(get_database_url()),
            "multi_user": multi_user_enabled(),
            "user_count": len(list_users()),
            "oidc": oidc_configured(),
            "principal": {
                "username": principal.get("username"),
                "role": principal.get("role"),
                "source": principal.get("source"),
            },
        })

    @router.get("/users")
    async def list_platform_users(request: Request) -> JSONResponse:
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
            }
            for u in list_users()
        ]
        return JSONResponse({"users": users})

    @router.post("/users")
    async def create_platform_user(request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
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
        try:
            from kazma_core.security.platform_rbac import create_local_user

            user = create_local_user(username, password, role=role)
            return JSONResponse({
                "status": "ok",
                "user": {
                    "user_id": user.user_id,
                    "username": user.username,
                    "role": user.role,
                    "enabled": user.enabled,
                },
            })
        except Exception as exc:
            logger.exception("[saas] create user failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.delete("/users/{username}")
    async def delete_platform_user(username: str, request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        from kazma_core.config_store import get_config_store
        from kazma_core.security.platform_rbac import _load_users_from_store, _save_users_to_store

        users = _load_users_from_store()
        new_users = [u for u in users if str(u.get("username", "")).lower() != username.lower()]
        if len(new_users) == len(users):
            return JSONResponse({"error": "User not found"}, status_code=404)
        _save_users_to_store(new_users)
        return JSONResponse({"status": "ok", "deleted": username})

    @router.patch("/users/{username}")
    async def patch_platform_user(username: str, request: Request) -> JSONResponse:
        denied = _require_admin(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            body = {}
        from kazma_core.security.platform_rbac import (
            _load_users_from_store,
            _save_users_to_store,
            _hash_password,
        )

        users = _load_users_from_store()
        found = None
        for u in users:
            if str(u.get("username", "")).lower() == username.lower():
                found = u
                break
        if not found:
            return JSONResponse({"error": "User not found"}, status_code=404)
        if "role" in body:
            role = str(body["role"]).lower()
            if role not in ("viewer", "operator", "admin"):
                return JSONResponse({"error": "invalid role"}, status_code=400)
            found["role"] = role
        if "enabled" in body:
            found["enabled"] = bool(body["enabled"])
        if body.get("password"):
            pw = str(body["password"])
            if len(pw) < 8:
                return JSONResponse({"error": "password min 8 chars"}, status_code=400)
            found["password_hash"] = _hash_password(pw)
        _save_users_to_store(users)
        return JSONResponse({
            "status": "ok",
            "user": {
                "user_id": found.get("user_id"),
                "username": found.get("username"),
                "role": found.get("role"),
                "enabled": found.get("enabled", True),
            },
        })

    @router.get("/tenants")
    async def list_tenants(request: Request) -> JSONResponse:
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
        denied = _require_admin(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        tid = str(body.get("id") or "").strip().lower().replace(" ", "-")
        name = str(body.get("name") or tid).strip()
        if not tid or tid == "default":
            return JSONResponse({"error": "valid non-default id required"}, status_code=400)
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        raw = cs.get("saas.tenants", [])
        tenants: list[Any] = list(raw) if isinstance(raw, list) else []
        for t in tenants:
            if isinstance(t, dict) and t.get("id") == tid:
                return JSONResponse({"error": "tenant exists"}, status_code=409)
            if t == tid:
                return JSONResponse({"error": "tenant exists"}, status_code=409)
        tenants.append({"id": tid, "name": name})
        cs.set("saas.tenants", tenants, category="saas")
        return JSONResponse({"status": "ok", "tenant": {"id": tid, "name": name}})

    return router
