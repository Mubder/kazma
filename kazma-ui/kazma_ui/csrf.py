"""CSRF defense: reject cross-origin mutating API requests from browsers.

Posture (audit M28). The email router keeps its stricter double check
(``X-Requested-With`` + Origin host match — ``email_api._verify_same_origin``);
this middleware extends origin checking to every mutating ``/api/`` route:

- Applies to non-GET/HEAD/OPTIONS requests under ``/api/``.
- Browser-based CSRF requests always carry an ``Origin`` (or ``Referer``)
  naming the attacker's site — when either is present and its host does
  not match the served host, the request is rejected with 403.
- Non-browser clients (curl, CLI, server-to-server webhooks) send no
  Origin/Referer and pass untouched. Requests carrying an explicit
  ``Authorization`` header are exempt — an explicit credential cannot be
  attached cross-site by a browser, so it is not CSRF-able.
- Proxied deployments: every ``X-Forwarded-Host`` value is accepted as an
  additional allowed host. Ports are intentionally NOT compared (a proxy's
  internal port differs from the public one); host equality is the
  CSRF-relevant boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["create_csrf_middleware"]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_of(url: str) -> str | None:
    try:
        netloc = urlsplit(url).netloc
        if not netloc:
            return None
        return (netloc.rsplit("@", 1)[-1].split(":")[0] or "").lower()
    except Exception:
        return None


def create_csrf_middleware() -> Callable[[Request], Awaitable[Response]]:
    """Build the cross-origin mutation guard (see module docstring)."""

    async def csrf_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            request.method in _SAFE_METHODS
            or not request.url.path.startswith("/api/")
            or request.headers.get("authorization")
        ):
            return await call_next(request)

        origin = request.headers.get("origin") or ""
        referer = request.headers.get("referer") or ""
        if not origin and not referer:
            # No browser context (curl/CLI/webhook) — nothing to check.
            return await call_next(request)

        # request.url.HOSTNAME — Starlette's URL has no `host` property
        # (netloc/hostname); the original `request.url.host` raised
        # AttributeError on the first real browser POST (every non-GET
        # /api/* request carrying Origin/Referer 500'd). TestClient requests
        # carry no Origin, which is why the test suites never hit it.
        allowed: set[str] = {(request.url.hostname or "").lower()}
        forwarded = request.headers.get("x-forwarded-host")
        if forwarded:
            allowed.update(
                h.split(":")[0].strip().lower() for h in forwarded.split(",") if h.strip()
            )

        for candidate in (origin, referer):
            if not candidate:
                continue
            host = _host_of(candidate)
            # Origin "null" (sandboxed frame) has no host — reject.
            if host is None or host not in allowed:
                logger.warning(
                    "[CSRF] Rejected cross-origin %s %s (origin=%r referer=%r)",
                    request.method,
                    request.url.path,
                    origin[:100],
                    referer[:100],
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin request rejected."},
                )

        return await call_next(request)

    return csrf_middleware
