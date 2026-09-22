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
_installed: set[int] = set()


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
    key = id(context)
    if key in _installed:
        return
    context.route("**/*", _guard_sync)
    _installed.add(key)


async def install_async_browser_egress(context) -> None:
    """Install the guard once on an async Playwright browser context."""
    key = id(context)
    if key in _installed:
        return
    await context.route("**/*", _guard_async)
    _installed.add(key)
