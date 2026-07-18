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


def _client_host(request: Request) -> str:
    if request.client is None:
        return ""
    return (request.client.host or "").strip().lower()


def _is_loopback_client(request: Request) -> bool:
    """True when the TCP peer is loopback (local single-operator use)."""
    host = _client_host(request)
    return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


def _is_private_lan_client(request: Request) -> bool:
    """True for RFC1918 / link-local peers (home lab, WSL, Docker bridge)."""
    host = _client_host(request)
    if not host:
        return False
    try:
        import ipaddress

        ip = ipaddress.ip_address(host.split("%")[0])  # drop IPv6 zone id
        return bool(ip.is_private or ip.is_link_local)
    except ValueError:
        return False


def _trust_lan_enabled() -> bool:
    """Auto-auth private LAN when secret is set (default on for self-hosted DX).

    Set ``KAZMA_TRUST_LAN=0`` on untrusted networks so only loopback + login
    receive the session cookie.
    """
    raw = (os.environ.get("KAZMA_TRUST_LAN") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _should_auto_issue_cookie(request: Request, expected: str) -> bool:
    """Whether to Set-Cookie the secret without an explicit login.

    - Loopback clients: yes.
    - Private LAN (when KAZMA_TRUST_LAN=1, default): yes — fixes WSL/LAN UI.
    - Remote clients with a valid X-Kazma-Secret header: yes.
    - Public internet clients: no — must use /login.
    """
    if not expected:
        return False
    if _is_loopback_client(request):
        return True
    if _trust_lan_enabled() and _is_private_lan_client(request):
        return True
    provided = request.headers.get(SECRET_HEADER, "")
    return bool(provided and verify_secret(provided, expected))


def _wants_html_response(request: Request) -> bool:
    """True when the client expects an HTML document (browser nav / soft-nav)."""
    if request.headers.get("Kazma-Soft-Nav"):
        return True
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return True
    # Top-level page navigations (not /api/*)
    if request.method == "GET" and not request.url.path.startswith("/api/"):
        return True
    return False


def _unauthorized_response(request: Request) -> Response:
    """401 JSON for APIs; redirect browsers to /login?next=… for HTML pages."""
    from fastapi.responses import RedirectResponse

    if _wants_html_response(request):
        nxt = request.url.path
        if request.url.query:
            nxt = f"{nxt}?{request.url.query}"
        # Only same-origin relative next
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        from urllib.parse import quote

        return RedirectResponse(
            url=f"/login?next={quote(nxt, safe='/?&=')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = Response(
        content='{"detail":"Missing or invalid X-Kazma-Secret header"}',
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": SECRET_HEADER},
    )
    if request.cookies.get(SECRET_COOKIE):
        response.delete_cookie(SECRET_COOKIE, path="/")
    return response

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
    # Explicit auth bootstrap (remote clients cannot use loopback auto-cookie)
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
})

#: Path prefixes that are always open (browser-redirect targets that
#  cannot carry the X-Kazma-Secret header, e.g. the GitHub OAuth callback
#  which GitHub redirects to with only a ?code= query param).
ALWAYS_OPEN_PREFIXES: tuple[str, ...] = (
    "/api/github/oauth/callback",
    "/api/github/oauth/start",
    "/api/auth/",
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
    try:
        return hmac.compare_digest(str(provided or ""), str(expected or ""))
    except Exception:
        return False


def verify_api_token(provided: str) -> bool:
    """Return True when *provided* is a valid Account API token (``kazma_…``).

    Tokens are created in Settings → Account. Only the SHA-256 hash is stored
    (never the raw token). Accepts the raw token string from the create dialog.
    """
    if not provided or not str(provided).startswith("kazma_"):
        return False
    try:
        import hashlib
        import json
        from datetime import UTC, datetime

        from kazma_core.config_store import get_config_store

        token_hash = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        raw = get_config_store().get("account.tokens", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if not isinstance(raw, list):
            return False
        now = datetime.now(UTC)
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if entry.get("token_hash") != token_hash:
                continue
            expires_days = entry.get("expires_days")
            created = entry.get("created_at") or ""
            if expires_days and created:
                try:
                    created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=UTC)
                    if (now - created_dt).days > int(expires_days):
                        return False
                except Exception:
                    pass
            return True
        return False
    except Exception:
        logger.debug("[SECURITY] API token verify failed", exc_info=True)
        return False


def extract_provided_credential(request: Request) -> str:
    """Pull auth material from headers/cookie (secret or API token).

    Order:
      1. ``X-Kazma-Secret``
      2. ``X-Api-Token`` / ``X-Kazma-Token``
      3. ``Authorization: Bearer …``
      4. ``kazma-secret`` cookie (browser sessions)
    """
    provided = (request.headers.get(SECRET_HEADER) or "").strip()
    if provided:
        return provided
    provided = (
        request.headers.get("X-Api-Token")
        or request.headers.get("X-Kazma-Token")
        or ""
    ).strip()
    if provided:
        return provided
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.cookies.get(SECRET_COOKIE) or "").strip()


def is_authenticated(request: Request, expected_secret: str = "") -> bool:
    """True if request carries a valid KAZMA_SECRET or Account API token."""
    provided = extract_provided_credential(request)
    if not provided:
        return False
    expected = expected_secret or get_kazma_secret()
    if expected and verify_secret(provided, expected):
        return True
    if verify_api_token(provided):
        return True
    return False


# ── FastAPI Dependency (for manual application) ─────────────────────────


def require_kazma_secret(
    x_kazma_secret: str = Header(default="", alias=SECRET_HEADER),
) -> None:
    """FastAPI dependency that enforces ``X-Kazma-Secret`` or Account API token.

    Raise ``HTTPException(401)`` when the secret is configured and the
    header is missing or incorrect.  When the secret is unset this is a
    no-op (backward compatible).
    """
    expected = get_kazma_secret()
    if not expected:
        return  # Auth disabled — open mode.
    if expected and verify_secret(x_kazma_secret, expected):
        return
    if verify_api_token(x_kazma_secret):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid X-Kazma-Secret header (or Account API token)",
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

        # 1. Read-only & page routes always pass through.
        # Cookie auto-issue only for loopback or when secret header is present
        # (never mint auth cookie for anonymous remote visitors — C2 fix).
        if is_always_open(path):
            response = await call_next(request)
            if (
                expected
                and _should_auto_issue_cookie(request, expected)
                and (
                    not request.cookies.get(SECRET_COOKIE)
                    or request.cookies.get(SECRET_COOKIE) != expected
                )
            ):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="lax", path="/",
                    secure=_is_https(request),
                )
            return response

        # 2. Only sensitive prefixes are gated.
        if not is_sensitive_path(path):
            response = await call_next(request)
            if (
                expected
                and _should_auto_issue_cookie(request, expected)
                and (
                    not request.cookies.get(SECRET_COOKIE)
                    or request.cookies.get(SECRET_COOKIE) != expected
                )
            ):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="lax", path="/",
                    secure=_is_https(request),
                )
            return response

        # 3. No secret configured → open mode UNLESS the caller presented an
        #    Account API token (still validate those when present).
        provided = extract_provided_credential(request)
        if not expected:
            # Open mode: still accept valid API tokens; otherwise pass through.
            if provided and provided.startswith("kazma_") and not verify_api_token(provided):
                return _unauthorized_response(request)
            return await call_next(request)

        # 4. Verify KAZMA_SECRET (header/cookie/Bearer) OR Account API token.
        if not is_authenticated(request, expected):
            return _unauthorized_response(request)

        response = await call_next(request)
        # Only mint/refresh the shared secret cookie for secret auth (not API tokens).
        if expected and verify_secret(provided, expected):
            if not request.cookies.get(SECRET_COOKIE) or request.cookies.get(SECRET_COOKIE) != expected:
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="lax", path="/",  # Lax: works for IP/LAN + OAuth return
                    secure=_is_https(request),
                )
        return response

    return auth_middleware_with_gate


def extract_tenant_from_jwt(token: str) -> str | None:
    """Extract tenant_id only from a *verified* JWT.

    Unverified base64 payload decoding is disabled (forgery risk). Set
    ``KAZMA_JWT_SECRET`` to enable HS256 verification; otherwise returns None.
    """
    secret = os.environ.get("KAZMA_JWT_SECRET", "").strip()
    if not secret:
        logger.debug("[TENANT] JWT tenant extraction disabled (KAZMA_JWT_SECRET unset)")
        return None
    try:
        import jwt as _jwt  # PyJWT
        payload = _jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
        if isinstance(payload, dict):
            tid = payload.get("tenant_id") or payload.get("tenant")
            return str(tid) if tid else None
    except Exception as exc:
        logger.debug("[TENANT] JWT verification failed: %s", exc)
    return None


def create_tenant_middleware() -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Create an HTTP middleware that extracts X-Tenant-ID and propagates it using ContextVar.

    Extracts from (in order):
    1. Header: X-Tenant-ID or x-tenant-id
    2. Cookie: X-Tenant-ID, x-tenant-id, or tenant_id
    3. Authorization Bearer JWT — only if KAZMA_JWT_SECRET is set (verified)
    4. JWT Cookie fallback — same verification requirement
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

        # 3. Check Authorization Bearer JWT (verified only)
        if not tenant_id:
            auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
                tenant_id = extract_tenant_from_jwt(token)

        # 4. Check Cookie JWT fallback (verified only)
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
    "verify_api_token",
    "extract_provided_credential",
    "is_authenticated",
    "create_tenant_middleware",
]


# Silence unused-import warnings for re-exported symbols.
_ = (Any, status, Awaitable)
