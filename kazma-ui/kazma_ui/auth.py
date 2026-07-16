"""Authentication middleware for sensitive API endpoints.

When the ``KAZMA_SECRET`` environment variable is set, all sensitive API
endpoints (``/api/settings``, ``/api/swarm``, ``/api/mcp``, ``/api/skills``,
``/api/models``, ``/api/ollama``) require an ``X-Kazma-Secret`` request header
whose value matches the env var.  Comparison uses :func:`secrets.compare_digest`
for timing safety.

When ``KAZMA_SECRET`` is **not** set, every endpoint remains open (backward
compatible).

Read-only endpoints (``GET /api/status``, ``GET /api/telemetry``,
``GET /health``, ``/`` page routes, static assets) are **always** open
regardless of whether the secret is configured.

Usage (in ``app.py``)::

    from kazma_ui.auth import create_auth_middleware
    app.middleware("http")(create_auth_middleware())
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

#: Header name clients must send to authenticate.
SECRET_HEADER = "X-Kazma-Secret"

#: Environment variable holding the shared secret.  When empty/unset the
#: entire auth layer is bypassed (backward-compatible open mode).
SECRET_ENV_VAR = "KAZMA_SECRET"

#: Cookie name used to pass the secret in browser sessions (HttpOnly).
SECRET_COOKIE = "kazma-secret"


def _is_https(request: Request) -> bool:
    """Check if the request is over HTTPS (either direct or via proxy)."""
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )

#: API path prefixes that require authentication when the secret is set.
SENSITIVE_PREFIXES: tuple[str, ...] = (
    "/api/settings",
    "/api/swarm",
    "/api/mcp",
    "/api/skills",
    "/api/models",
    "/api/ollama",
    "/api/agents", "/api/providers", "/api/provider",
    "/api/connectors",
    "/api/chat", "/api/gateway",
    # Destructive / privileged routes that must be gated even when
    # other endpoints are open (sessions, HITL approval, system ops).
    "/api/sessions", "/api/session",
    "/api/approve", "/api/system",
    "/api/workspace", "/api/workspaces", "/api/memory",
    "/api/pending-approvals", "/api/metrics",
    "/api/telemetry/stream", "/api/telemetry/snapshot",
    "/api/telemetry/typing",
    "/api/alerts",
    "/metrics",
    # Sprint 19 / Phase 3 surfaces (chaos, migrations, workspace tooling)
    "/api/chaos",
    "/api/config",
    "/api/git",
    "/api/github",
    "/api/ide",          # IDE API: file read/write/delete, shell exec, git, swarm dispatch
    "/api/bookmarks",
    "/api/pipelines",
    "/v1/models",
    # Observability dashboard: unlike the SPA page shells (/, /chat,
    # /workspace), this route renders real cost/session/trace data
    # server-side into the HTML itself, not just via a later AJAX call —
    # so the page route and its JSON API both need to be gated.
    "/dashboard",
    "/api/dashboard",
)

#: Exact read-only paths that are always open regardless of secret config.
#  (Page routes like "/", "/chat", "/workspace" and static files are
#  handled separately — they never start with a sensitive prefix.)
ALWAYS_OPEN_PATHS: frozenset[str] = frozenset({
    "/",
    "/health",
    "/api/status",
    "/api/telemetry",
})

#: Path prefixes that are always open (browser-redirect targets that
#  cannot carry the X-Kazma-Secret header, e.g. the GitHub OAuth callback
#  which GitHub redirects to with only a ?code= query param).
ALWAYS_OPEN_PREFIXES: tuple[str, ...] = (
    "/api/github/oauth/callback",
    "/api/github/oauth/start",
)


# ── Helpers ──────────────────────────────────────────────────────────────

_generated_secret: str | None = None


def get_kazma_secret() -> str:
    """Return the configured ``KAZMA_SECRET`` (delegates to config_store).

    Single source of truth: :func:`kazma_core.config_store.get_kazma_secret`.
    Cached in-module for UI middleware hot path after first resolve.
    """
    global _generated_secret
    if _generated_secret is not None:
        return _generated_secret

    # Env override still short-circuits without store (and without caching empty)
    env_secret = os.environ.get(SECRET_ENV_VAR, "").strip()
    if env_secret:
        return env_secret

    try:
        from kazma_core.config_store import get_kazma_secret as _core_get

        secret = _core_get()
        # Only cache non-empty secrets so tests can still flip env/open mode
        if secret:
            _generated_secret = secret
        return secret
    except Exception as exc:
        logger.debug("[SECURITY] config_store get_kazma_secret failed: %s", exc)
        return ""


def is_sensitive_path(path: str) -> bool:
    """Return *True* if *path* falls under a sensitive API prefix."""
    # Normalise trailing slashes so "/api/settings/" matches the prefix.
    normalised = path.rstrip("/") or "/"
    for prefix in SENSITIVE_PREFIXES:
        if normalised == prefix or normalised.startswith(prefix + "/"):
            return True
    return False


def is_always_open(path: str) -> bool:
    """Return *True* for read-only/page/redirect routes that bypass auth."""
    if path in ALWAYS_OPEN_PATHS:
        return True
    return any(path == p or path.startswith(p + "/") for p in ALWAYS_OPEN_PREFIXES)


def verify_secret(provided: str, expected: str) -> bool:
    """Timing-safe comparison of *provided* against *expected*.

    Uses :func:`hmac.compare_digest` (alias of :func:`secrets.compare_digest`).
    Both arguments are coerced to ``str`` before comparison.
    """
    return hmac.compare_digest(provided, expected)


# ── FastAPI Dependency (for manual application) ─────────────────────────


def require_kazma_secret(
    x_kazma_secret: str = Header(default="", alias=SECRET_HEADER),
) -> None:
    """FastAPI dependency that enforces ``X-Kazma-Secret``.

    Raise ``HTTPException(401)`` when the secret is configured and the
    header is missing or incorrect.  When the secret is unset this is a
    no-op (backward compatible).
    """
    expected = get_kazma_secret()
    if not expected:
        return  # Auth disabled — open mode.
    if not verify_secret(x_kazma_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Kazma-Secret header",
            headers={"WWW-Authenticate": SECRET_HEADER},
        )


# ── Middleware Factory ──────────────────────────────────────────────────


def create_auth_middleware(
    secret: str | None = None,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Create an ASGI/Starlette HTTP middleware enforcing ``KAZMA_SECRET``.

    Args:
        secret: Optional explicit secret.  When ``None`` (default) the
            secret is read from the ``KAZMA_SECRET`` env var at **each
            request** so tests can monkeypatch ``os.environ`` dynamically.

    Returns:
        Middleware coroutine suitable for ``app.middleware("http")(...)``.
    """
    # Capture a static secret when provided; otherwise resolve per-request.
    static_secret = secret

    async def auth_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        expected = static_secret if static_secret is not None else get_kazma_secret()

        # Set HttpOnly cookie on all responses when secret is configured
        # (so browser-based JS can make authenticated API calls without
        # the secret being exposed in page source).
        response = await call_next(request)
        if expected and (not request.cookies.get(SECRET_COOKIE) or request.cookies.get(SECRET_COOKIE) != expected):
            response.set_cookie(
                key=SECRET_COOKIE,
                value=expected,
                httponly=True,
                samesite="strict",
                path="/",
                secure=_is_https(request),
            )
        return response

    # ── Separate gate for sensitive paths ────────────────────────────
    # We need to check auth BEFORE the route handler, but set cookies
    # AFTER. So we use a two-pass approach: check first, then set cookie
    # on the response.

    async def auth_middleware_with_gate(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        expected = static_secret if static_secret is not None else get_kazma_secret()

        # 1. Read-only & page routes always pass through (but still get cookie).
        if is_always_open(path):
            response = await call_next(request)
            if expected and (not request.cookies.get(SECRET_COOKIE) or request.cookies.get(SECRET_COOKIE) != expected):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="strict", path="/",
                    secure=_is_https(request),
                )
            return response

        # 2. Only sensitive prefixes are gated.
        if not is_sensitive_path(path):
            response = await call_next(request)
            if expected and (not request.cookies.get(SECRET_COOKIE) or request.cookies.get(SECRET_COOKIE) != expected):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="strict", path="/",
                    secure=_is_https(request),
                )
            return response

        # 3. No secret configured → open mode (backward compatible).
        if not expected:
            return await call_next(request)

        # 4. Verify the header or cookie using timing-safe comparison.
        provided = request.headers.get(SECRET_HEADER, "")
        if not provided:
            provided = request.cookies.get(SECRET_COOKIE, "")
        if not verify_secret(provided, expected):
            response = Response(
                content='{"detail":"Missing or invalid X-Kazma-Secret header"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": SECRET_HEADER},
            )
            if request.cookies.get(SECRET_COOKIE):
                response.delete_cookie(SECRET_COOKIE, path="/")
            return response

        response = await call_next(request)
        if not request.cookies.get(SECRET_COOKIE) or request.cookies.get(SECRET_COOKIE) != expected:
            response.set_cookie(
                key=SECRET_COOKIE, value=expected,
                httponly=True, samesite="strict", path="/",
                secure=_is_https(request),
            )
        return response

    return auth_middleware_with_gate


def extract_tenant_from_jwt(token: str) -> str | None:
    """Extract tenant_id or tenant claim from a base64url-encoded JWT token."""
    try:
        parts = token.split(".")
        if len(parts) == 3:
            payload_b64 = parts[1]
            # Add padding if needed for base64 decoding
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            import base64
            import json
            decoded = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
            payload = json.loads(decoded)
            if isinstance(payload, dict):
                return payload.get("tenant_id") or payload.get("tenant")
    except Exception as exc:
        logger.debug("[TENANT] Failed to extract tenant from JWT: %s", exc)
    return None


def create_tenant_middleware() -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Create an HTTP middleware that extracts X-Tenant-ID and propagates it using ContextVar.
    
    Extracts from:
    1. Header: X-Tenant-ID or x-tenant-id
    2. Cookie: X-Tenant-ID, x-tenant-id, or tenant_id
    3. Authorization header: Bearer JWT (containing tenant_id or tenant claim)
    4. JWT Cookie fallback: jwt, token, x-tenant-id-jwt, or tenant_jwt
    """
    from kazma_core.tenant_context import set_current_tenant_id, reset_current_tenant_id

    async def tenant_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 1. Check header
        tenant_id = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")

        # 2. Check cookie fallback
        if not tenant_id:
            tenant_id = (
                request.cookies.get("X-Tenant-ID")
                or request.cookies.get("x-tenant-id")
                or request.cookies.get("tenant_id")
            )

        # 3. Check Authorization Bearer JWT
        if not tenant_id:
            auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
                tenant_id = extract_tenant_from_jwt(token)

        # 4. Check Cookie JWT fallback
        if not tenant_id:
            for cookie_name in ("jwt", "token", "x-tenant-id-jwt", "tenant_jwt"):
                token = request.cookies.get(cookie_name)
                if token:
                    tenant_id = extract_tenant_from_jwt(token)
                    if tenant_id:
                        break

        # Log active tenant context if found
        if tenant_id:
            logger.debug("[TENANT] Inbound request scoped to tenant_id: %s", tenant_id)

        # Set tenant context variable
        token = set_current_tenant_id(tenant_id)
        try:
            response = await call_next(request)
            # Propagate tenant_id cookie on response for subsequent requests
            if tenant_id:
                if request.cookies.get("X-Tenant-ID") != tenant_id:
                    response.set_cookie(
                        key="X-Tenant-ID",
                        value=tenant_id,
                        httponly=True,
                        samesite="strict",
                        path="/",
                        secure=_is_https(request),
                    )
            return response
        finally:
            reset_current_tenant_id(token)

    return tenant_middleware


__all__: list[str] = [
    "SECRET_HEADER",
    "SECRET_COOKIE",
    "SECRET_ENV_VAR",
    "SENSITIVE_PREFIXES",
    "ALWAYS_OPEN_PATHS",
    "create_auth_middleware",
    "get_kazma_secret",
    "is_sensitive_path",
    "is_always_open",
    "require_kazma_secret",
    "verify_secret",
    "create_tenant_middleware",
]


# Silence unused-import warnings for re-exported symbols.
_ = (Any, status, Awaitable)
