"""Proxy-aware httpx client factory + rotating User-Agent pool.

This is the single injection point the scraper uses instead of raw
``httpx.AsyncClient(...)``. It transparently applies the configured proxy
provider (opt-in) and can rotate the User-Agent so consecutive requests don't
share a fingerprint.

Scoped to SCRAPING ONLY — never used for LLM API calls (those go through the
separate ``http_pool.py`` so provider API keys are never routed through a
third-party proxy).
"""

from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from kazma_core.proxy.registry import get_proxy_provider

__all__ = ["get_scraping_client", "random_user_agent", "USER_AGENT_POOL"]

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


def get_scraping_client(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an httpx client wired to the active proxy provider + optional UA rotation.

    Args:
        follow_redirects: passed to httpx.
        timeout: passed to httpx.
        headers: base headers; if ``rotate_ua`` is True a random UA is injected
            (overriding any ``User-Agent`` in *headers*).
        rotate_ua: inject a random User-Agent so requests vary their fingerprint.
        **kwargs: forwarded to ``httpx.AsyncClient``.

    When the proxy provider is unconfigured (the default), this returns a plain
    client — identical behavior to before, so non-users see no change.
    """
    provider = get_proxy_provider()
    proxy_url = provider.get_proxy_url() if provider.is_configured() else None

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
        logger.debug("[proxy] scraping client via %s (%s)", provider.name, proxy_url.split("@")[-1])
    return httpx.AsyncClient(**client_kwargs, **kwargs)
