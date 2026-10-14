"""One same-origin rule for the routers that add their own check (Scheduled audit SCH-S12).

The Scheduled, X and email routers require ``X-Requested-With`` on every
change and, when the browser names an Origin or Referer, that it is this
site. Each kept a copy of that check, and each compared the Origin's host with
the ``Host`` header only -- ignoring the origins the operator declared
(``KAZMA_PUBLIC_URL``, ``KAZMA_CORS_ORIGINS``), which the global CSRF
middleware and the WebSocket check trust (AGENTS §45). Behind a proxy that
rewrites ``Host``, every Scheduled change answered 403. This is now one
dependency with the middleware's rule: the served origin or a declared one.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from kazma_ui.browser_origins import configured_browser_origins, normalize_origin

__all__ = ["verify_same_origin"]


def _allowed_origins(request: Request) -> set[str]:
    allowed = set(configured_browser_origins())
    served = normalize_origin(str(request.url), allow_path=True)
    if served:
        allowed.add(served)
    host = request.headers.get("host") or ""
    if host:
        for scheme in ("http", "https"):
            same = normalize_origin(f"{scheme}://{host}")
            if same:
                allowed.add(same)
    return allowed


async def verify_same_origin(request: Request) -> None:
    """FastAPI dependency: 403 unless the request is this site's own page."""
    if request.headers.get("x-requested-with", "").lower() != "xmlhttprequest":
        raise HTTPException(status_code=403, detail="missing custom request header")
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    if not origin and not referer:
        return
    allowed = _allowed_origins(request)
    # The first one a browser sent, as the copies read it: only its scheme,
    # host and port are compared (a path is ignored, never trusted).
    normalized = normalize_origin(origin or referer, allow_path=True)
    if normalized is None or normalized not in allowed:
        raise HTTPException(status_code=403, detail="cross-origin request denied")
