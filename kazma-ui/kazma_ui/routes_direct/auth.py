"""Login, logout, OIDC, and auth-status endpoints.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse as _JSONResponse
from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)

__all__ = ["register_auth_routes"]


def register_auth_routes(self: Any) -> None:
    """Register the auth routes onto ``self.app``."""
    @self.app.get("/api/auth/status")
    async def _auth_status(request: Request) -> dict[str, Any]:
        """Whether auth is enabled and whether this request is authenticated."""
        from kazma_ui.auth import (
            _is_loopback_client,
            get_kazma_secret,
            get_request_principal,
            is_authenticated,
            proxy_health,
        )

        expected = get_kazma_secret()
        oidc = False
        multi_user = False
        try:
            from kazma_core.security.oidc import oidc_configured
            from kazma_core.security.platform_rbac import multi_user_enabled

            oidc = oidc_configured()
            multi_user = multi_user_enabled()
        except Exception:
            pass
        if not expected:
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "open",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        # Public demo mode: report as open/authenticated so the client skips
        # the login redirect. Matches the middleware bypass in auth.py.
        import os as _os
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "demo",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        ok = is_authenticated(request, expected)
        principal = get_request_principal(request) if ok else None
        return {
            "auth_enabled": True,
            "authenticated": ok,
            "loopback": _is_loopback_client(request),
            "mode": "secret",
            "oidc": oidc,
            "multi_user": multi_user,
            "principal": principal,
            # Makes a mis-set KAZMA_TRUSTED_PROXIES visible without reading
            # logs — the F-01 class otherwise fails silently.
            "proxy": proxy_health(),
        }
    # Login brute-force throttle (audit M3) — in-process sliding window per IP.
    # Keyed on the proxy-aware client address (audit F-12): keying on the raw
    # TCP peer collapsed every client behind a reverse proxy into one bucket,
    # so 10 deliberate failures locked out every operator.
    _login_failures: dict[str, list[float]] = {}
    _LOGIN_WINDOW_S = 300.0
    _LOGIN_MAX_FAILS = 10
    # addresses and never trips the per-address limit.
    _login_failures_global: list[float] = []
    _LOGIN_MAX_FAILS_GLOBAL = 200
    @self.app.post("/api/auth/login")
    async def _auth_login(request: Request) -> Response:
        """Exchange KAZMA_SECRET for an HttpOnly session cookie."""
        import time as _time

        from kazma_ui.auth import (
            SECRET_COOKIE,
            _is_https,
            client_address,
            get_kazma_secret,
            verify_secret,
        )

        client_ip = client_address(request) or "unknown"
        now = _time.time()
        # Bound the per-IP map: unique attacking IPs used to leave stale
        # keys forever (pruning only ever ran for the retrying same IP).
        if len(_login_failures) > 1000:
            for _ip in list(_login_failures):
                if not any(now - t < _LOGIN_WINDOW_S for t in _login_failures[_ip]):
                    del _login_failures[_ip]
        recent = [
            t for t in _login_failures.get(client_ip, [])
            if now - t < _LOGIN_WINDOW_S
        ]
        _login_failures[client_ip] = recent
        _login_failures_global[:] = [
            t for t in _login_failures_global if now - t < _LOGIN_WINDOW_S
        ]
        if (
            len(recent) >= _LOGIN_MAX_FAILS
            or len(_login_failures_global) >= _LOGIN_MAX_FAILS_GLOBAL
        ):
            logger.warning("[auth] login rate limit hit for %s", client_ip)
            return _JSONResponse(
                {"detail": "Too many failed login attempts — try again later"},
                status_code=429,
            )

        expected = get_kazma_secret()
        if not expected:
            return _JSONResponse(
                {"status": "ok", "message": "Auth disabled (no KAZMA_SECRET)"},
                status_code=200,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        secret = str(body.get("secret") or "").strip()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "").strip()
        session_user = None
        session_role = "admin"
        session_uid = None
        authenticated = False

        # Path A: multi-user local username + password (Phase 4.4)
        if username and password:
            try:
                from kazma_core.security.platform_rbac import authenticate_local_user

                pu = authenticate_local_user(username, password)
                if pu is not None:
                    authenticated = True
                    session_user = pu.username
                    session_role = pu.role
                    session_uid = pu.user_id
            except Exception:
                logger.debug("[auth] local user auth failed", exc_info=True)

        # Path B: shared operator secret
        if not authenticated:
            check = secret or password
            if check and verify_secret(check, expected):
                authenticated = True
                session_user = "operator"
                session_role = "admin"
                session_uid = "shared-secret"

        if not authenticated:
            recent.append(now)
            _login_failures[client_ip] = recent
            _login_failures_global.append(now)
            # One message for every failure mode (bad secret, unknown user,
            # wrong password) so the response cannot confirm which usernames
            # exist (audit F-12).
            return _JSONResponse(
                {"detail": "Invalid credentials"},
                status_code=401,
            )

        # Success — clear failures for this IP
        _login_failures.pop(client_ip, None)

        resp = _JSONResponse({
            "status": "ok",
            "authenticated": True,
            "username": session_user,
            "role": session_role,
        })
        # Always mint an opaque session — never put KAZMA_SECRET in a cookie.
        try:
            from kazma_core.security.web_sessions import SESSION_COOKIE, create_session

            sid = create_session(
                actor="login",
                username=session_user,
                role=session_role,
                user_id=session_uid,
            )
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            resp.delete_cookie(SECRET_COOKIE, path="/")
            return resp
        except Exception:
            logger.warning("[auth] opaque session create failed — refusing raw secret cookie", exc_info=True)
            return _JSONResponse(
                {"error": "session_create_failed", "detail": "Could not mint a login session."},
                status_code=503,
            )
    @self.app.get("/api/auth/oidc/start")
    async def _oidc_start(request: Request) -> Response:
        """Redirect browser to configured OIDC IdP (Phase 4.4)."""
        from fastapi.responses import RedirectResponse

        try:
            from kazma_core.security.oidc import build_authorize_url, oidc_configured

            if not oidc_configured():
                return _JSONResponse(
                    {"error": "OIDC not configured (KAZMA_OIDC_ISSUER + CLIENT_ID)"},
                    status_code=503,
                )
            info = await build_authorize_url()
            return RedirectResponse(url=info["url"], status_code=302)
        except Exception as exc:
            logger.exception("[oidc] start failed")
            return _JSONResponse({"error": safe_error(exc)}, status_code=500)
    @self.app.get("/api/auth/oidc/callback")
    async def _oidc_callback(request: Request) -> Response:
        """OIDC callback — mint opaque session from IdP claims."""
        from fastapi.responses import RedirectResponse

        from kazma_ui.auth import SESSION_COOKIE, _is_https

        code = request.query_params.get("code") or ""
        state = request.query_params.get("state") or ""
        if not code or not state:
            return _JSONResponse({"error": "Missing code/state"}, status_code=400)
        try:
            from kazma_core.security.oidc import exchange_code
            from kazma_core.security.web_sessions import create_session, use_opaque_sessions

            result = await exchange_code(code, state)
            if not use_opaque_sessions():
                return _JSONResponse(
                    {"error": "Opaque sessions required for OIDC"},
                    status_code=500,
                )
            sid = create_session(
                actor="oidc",
                username=result.get("username"),
                role=result.get("role") or "operator",
                user_id=result.get("user_id"),
            )
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            return resp
        except Exception as exc:
            logger.exception("[oidc] callback failed")
            return _JSONResponse({"error": safe_error(exc)}, status_code=400)
    @self.app.get("/api/auth/me")
    async def _auth_me(request: Request) -> Response:
        """Return current principal (role/username) for UI chrome."""
        import os as _os

        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

        # Public demo mode: report as an authenticated demo visitor.
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return _JSONResponse({"authenticated": True, "source": "demo", "role": "admin"})
        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return _JSONResponse({"authenticated": False}, status_code=401)
        principal = get_request_principal(request) or {}
        return _JSONResponse({"authenticated": True, **principal})
    @self.app.post("/api/auth/logout")
    async def _auth_logout(request: Request) -> Response:
        """Clear auth cookies and revoke opaque session."""
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        try:
            from kazma_core.security.web_sessions import revoke_session

            sid = request.cookies.get(SESSION_COOKIE) or ""
            if sid:
                revoke_session(sid)
        except Exception:
            pass
        resp = _JSONResponse({"status": "ok", "authenticated": False})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp
