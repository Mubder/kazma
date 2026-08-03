"""Proxy-aware httpx client factory + rotating User-Agent pool.

This is the single injection point the scraper uses instead of raw
``httpx.AsyncClient(...)``. It transparently applies the configured proxy
provider (opt-in) and can rotate the User-Agent so consecutive requests don't
share a fingerprint.

Also exposes Playwright proxy dicts and a sync httpx client so KB discover,
SERP scrapes, and Chromium recovery share the same ConfigStore setting.

Scoped to SCRAPING ONLY — never used for LLM API calls (those go through the
separate ``http_pool.py`` so provider API keys are never routed through a
third-party proxy).
"""

from __future__ import annotations

import logging
import random
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from kazma_core.proxy.registry import get_proxy_provider

__all__ = [
    "get_scraping_client",
    "get_scraping_client_sync",
    "get_active_proxy_url",
    "playwright_proxy",
    "random_user_agent",
    "USER_AGENT_POOL",
]

logger = logging.getLogger(__name__)

# A curated pool of current, plausible browser User-Agents across Chrome/Firefox/
# Edge/Safari and OSes. Rotating these defeats naive UA-based blocking.
USER_AGENT_POOL = (
    # Chrome (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Firefox (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Firefox (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0",
    # Edge (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Safari (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    # Chrome (Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)


def random_user_agent() -> str:
    """Return a random browser User-Agent from the pool."""
    return random.choice(USER_AGENT_POOL)


def get_active_proxy_url() -> str | None:
    """Return the live proxy URL, or ``None`` when direct (unconfigured)."""
    try:
        provider = get_proxy_provider()
        if not provider.is_configured():
            return None
        url = provider.get_proxy_url()
        return url if url else None
    except Exception:
        logger.debug("[proxy] get_active_proxy_url failed", exc_info=True)
        return None


def playwright_proxy() -> dict[str, str] | None:
    """Playwright ``proxy=`` dict for ``chromium.launch``, or ``None`` if direct.

    Parses ``http://user:pass@host:port`` from the active provider into
    Playwright's ``{server, username?, password?}`` shape.
    """
    proxy_url = get_active_proxy_url()
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)
        if not p.hostname:
            return None
        scheme = p.scheme or "http"
        server = f"{scheme}://{p.hostname}"
        if p.port:
            server = f"{server}:{p.port}"
        out: dict[str, str] = {"server": server}
        if p.username:
            out["username"] = unquote(p.username)
        if p.password:
            out["password"] = unquote(p.password)
        logger.debug(
            "[proxy] Playwright via %s",
            f"{p.hostname}:{p.port}" if p.port else p.hostname,
        )
        return out
    except Exception:
        logger.debug("[proxy] playwright_proxy parse failed", exc_info=True)
        return None


def _client_common_kwargs(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    proxy_url = get_active_proxy_url()
    final_headers = dict(headers) if headers else {}
    if rotate_ua:
        final_headers["User-Agent"] = random_user_agent()

    client_kwargs: dict[str, Any] = {
        "follow_redirects": follow_redirects,
        "timeout": timeout,
        "headers": final_headers or None,
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
        try:
            provider = get_proxy_provider()
            name = getattr(provider, "name", "?")
        except Exception:
            name = "?"
        logger.debug(
            "[proxy] scraping client via %s (%s)",
            name,
            proxy_url.split("@")[-1],
        )
    client_kwargs.update(kwargs)
    return client_kwargs


def get_scraping_client(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an async httpx client wired to the active proxy + optional UA rotation.

    When the proxy provider is unconfigured (the default), this returns a plain
    client — identical behavior to before, so non-users see no change.
    """
    return httpx.AsyncClient(
        **_client_common_kwargs(
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=headers,
            rotate_ua=rotate_ua,
            **kwargs,
        )
    )


def get_scraping_client_sync(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> httpx.Client:
    """Sync httpx client (Bing / Wikipedia SERP, smoke scripts) with the same proxy."""
    return httpx.Client(
        **_client_common_kwargs(
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=headers,
            rotate_ua=rotate_ua,
            **kwargs,
        )
    )
