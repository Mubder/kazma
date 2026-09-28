"""Block private-network requests from every Kazma-driven browser.

Chromium follows redirects and loads subresources on its own. Checking only
the first URL leaves that path outside the direct scraper's SSRF rules.
A route installed on the browser context sees each of those requests before
Chromium connects, and aborts anything ``validate_url`` would reject.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

__all__ = [
    "browser_request_allowed",
    "install_async_browser_egress",
    "install_sync_browser_egress",
]

_LOCAL_SCHEMES = frozenset({"about", "blob", "data"})
#: Marks a context that has the guard. The mark lives ON the context: a set
#: of ``id(context)`` did not work -- ``read_url`` and the Knowledge crawler
#: close their context after each fetch, CPython gives the closed context's
#: id to the next one, and the next context was taken for guarded and left
#: without the route (2026-09-28).
_GUARD_MARK = "_kazma_browser_egress"


def _already_guarded(context) -> bool:
    return bool(getattr(context, _GUARD_MARK, False))


def _mark_guarded(context) -> None:
    """Record the guard on *context*, after its route is in place.

    A context that cannot carry the mark gets the route again next time: a
    second route is only extra work, a missing one is the hole.
    """
    try:
        setattr(context, _GUARD_MARK, True)
    except (AttributeError, TypeError):
        logger.debug("[browser-egress] context cannot be marked; it will be guarded again")


def browser_request_allowed(url: str) -> tuple[bool, str]:
    """Return whether Chromium may open *url*.

    ``data:``, ``blob:`` and ``about:`` never leave the page. Every other
    scheme is refused. ``http`` and ``https`` must pass the same public-host
    check as direct fetches, including literal IPv4 and IPv6.
    """
    if not url or not url.strip():
        return False, "empty URL"
    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        return False, str(exc)
    scheme = (parsed.scheme or "").lower()
    if scheme in _LOCAL_SCHEMES:
        return True, ""
    if scheme not in {"http", "https"}:
        return False, f"scheme {scheme or '(none)'} is not allowed"
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url, block_unresolved=True)
    except (SSRFError, ValueError) as exc:
        return False, str(exc)
    return True, ""


def _guard_sync(route) -> None:
    allowed, reason = browser_request_allowed(route.request.url)
    if not allowed:
        logger.info("[browser-egress] blocked %s (%s)", route.request.url, reason)
        route.abort("blockedbyclient")
        return
    route.continue_()


async def _guard_async(route) -> None:
    allowed, reason = browser_request_allowed(route.request.url)
    if not allowed:
        logger.info("[browser-egress] blocked %s (%s)", route.request.url, reason)
        await route.abort("blockedbyclient")
        return
    await route.continue_()


def install_sync_browser_egress(context) -> None:
    """Install the guard once on a sync Playwright context or page context."""
    if _already_guarded(context):
        return
    context.route("**/*", _guard_sync)
    _mark_guarded(context)


async def install_async_browser_egress(context) -> None:
    """Install the guard once on an async Playwright browser context."""
    if _already_guarded(context):
        return
    await context.route("**/*", _guard_async)
    _mark_guarded(context)
