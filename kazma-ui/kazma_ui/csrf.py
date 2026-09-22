"""CSRF defense: reject cross-origin mutating API requests from browsers.

Posture (audit M28). The email router keeps its stricter double check
(``X-Requested-With`` + Origin host match — ``email_api._verify_same_origin``);
this middleware extends origin checking to every mutating ``/api/`` route:

- Applies to non-GET/HEAD/OPTIONS requests under ``/api/``.
- Browser-based CSRF requests always carry an ``Origin`` (or ``Referer``)
  naming the attacker's site — scheme, hostname and effective port must
  match the request origin or an explicitly configured browser origin.
- Non-browser clients (curl, CLI, server-to-server webhooks) send no
  Origin/Referer and pass untouched. Requests carrying an explicit
  ``Authorization`` header are exempt — an explicit credential cannot be
  attached cross-site by a browser, so it is not CSRF-able.
- Proxied deployments declare their external origin in ``KAZMA_PUBLIC_URL``.
  Forwarded headers never enlarge the trusted-origin set. Additional clients
  may be explicitly trusted through ``KAZMA_CORS_ORIGINS``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from kazma_ui.browser_origins import configured_browser_origins, normalize_origin

logger = logging.getLogger(__name__)

__all__ = ["create_csrf_middleware"]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_csrf_middleware() -> Callable[[Request], Awaitable[Response]]:
    """Build the cross-origin mutation guard (see module docstring)."""
    configured = frozenset(configured_browser_origins())

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

        allowed = set(configured)
        served_origin = normalize_origin(str(request.url), allow_path=True)
        if served_origin:
            allowed.add(served_origin)

        for candidate, allow_path in ((origin, False), (referer, True)):
            if not candidate:
                continue
            candidate_origin = normalize_origin(candidate, allow_path=allow_path)
            # Origin "null" (sandboxed frame) has no host — reject.
            if candidate_origin is None or candidate_origin not in allowed:
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
