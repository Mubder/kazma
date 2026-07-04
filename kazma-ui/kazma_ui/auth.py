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
    "/api/workspace", "/api/memory",
    "/api/pending-approvals", "/api/metrics",
    "/api/telemetry/stream", "/api/telemetry/snapshot",
    "/v1/models",
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


# ── Helpers ──────────────────────────────────────────────────────────────


def get_kazma_secret() -> str:
    """Return the configured ``KAZMA_SECRET`` (empty string when unset)."""
    return os.environ.get(SECRET_ENV_VAR, "").strip()


def is_sensitive_path(path: str) -> bool:
    """Return *True* if *path* falls under a sensitive API prefix."""
    # Normalise trailing slashes so "/api/settings/" matches the prefix.
    normalised = path.rstrip("/") or "/"
    for prefix in SENSITIVE_PREFIXES:
        if normalised == prefix or normalised.startswith(prefix + "/"):
            return True
    return False


def is_always_open(path: str) -> bool:
    """Return *True* for read-only and page routes that bypass auth."""
    return path in ALWAYS_OPEN_PATHS


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
        if expected and not request.cookies.get(SECRET_COOKIE):
            response.set_cookie(
                key=SECRET_COOKIE,
                value=expected,
                httponly=True,
                samesite="strict",
                path="/",
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
            if expected and not request.cookies.get(SECRET_COOKIE):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="strict", path="/",
                )
            return response

        # 2. Only sensitive prefixes are gated.
        if not is_sensitive_path(path):
            response = await call_next(request)
            if expected and not request.cookies.get(SECRET_COOKIE):
                response.set_cookie(
                    key=SECRET_COOKIE, value=expected,
                    httponly=True, samesite="strict", path="/",
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
            return Response(
                content='{"detail":"Missing or invalid X-Kazma-Secret header"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": SECRET_HEADER},
            )

        response = await call_next(request)
        if not request.cookies.get(SECRET_COOKIE):
            response.set_cookie(
                key=SECRET_COOKIE, value=expected,
                httponly=True, samesite="strict", path="/",
            )
        return response

    return auth_middleware_with_gate


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
]


# Silence unused-import warnings for re-exported symbols.
_ = (Any, status)
