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
from kazma_core.proxy.config import proxy_config_scope
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

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


def _validate_proxy_url(url: str) -> str:
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            raise ValueError
        parsed = urlparse(url)
        if (parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parsed.hostname
                or parsed.path not in ('', '/') or parsed.params or parsed.query or parsed.fragment):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        for value in (parsed.username, parsed.password):
            if value and (unquote(value).startswith('vault://') or any(ord(c) < 32 or ord(c) == 127 for c in unquote(value))):
                raise ValueError
        httpx.URL(url)  # syntax only; no DNS, socket or provider call
    except (ValueError, TypeError, httpx.InvalidURL):
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'Invalid scraping proxy endpoint or credentials. Repair Proxy Provider settings; direct fallback was refused.') from None
    return url


def get_active_proxy_url() -> str | None:
    """Resolve one coherent route; only policy-off or none means direct."""
    from kazma_core.web_acquire.policy import acquisition_policy

    if not acquisition_policy().permits("proxy"):
        return None
    try:
        with proxy_config_scope():
            provider = get_proxy_provider()
            if provider.name == 'none':
                return None
            if not provider.is_configured():
                raise AcquisitionError(FetchErrorCode.ROUTING,
                    f'Scraping proxy {provider.name} is selected but its credentials are unavailable. '
                    'Complete Proxy Provider settings or select none; direct fallback was refused.')
            url = provider.get_proxy_url()
            if not url:
                raise AcquisitionError(FetchErrorCode.ROUTING,
                    'The selected scraping proxy did not supply a route; direct fallback was refused.')
            return _validate_proxy_url(url)
    except AcquisitionError:
        raise
    except Exception:
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'The selected scraping proxy failed to resolve its route; direct fallback was refused. '
            'Repair Proxy Provider settings before retrying.') from None



def playwright_proxy() -> dict[str, str] | None:
    """Playwright proxy fields, with the same validated/fail-closed route."""
    proxy_url = get_active_proxy_url()
    if not proxy_url:
        return None
    p = urlparse(_validate_proxy_url(proxy_url))
    # Brackets are required for IPv6 proxy endpoints.
    host = f'[{p.hostname}]' if ':' in p.hostname else p.hostname
    server = f'{p.scheme}://{host}'
    if p.port:
        server += f':{p.port}'
    out = {'server': server}
    if p.username:
        out['username'] = unquote(p.username)
    if p.password:
        out['password'] = unquote(p.password)
    return out


def _client_common_kwargs(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    proxy_url = kwargs.get("proxy") if "proxy" in kwargs else get_active_proxy_url()
    if proxy_url:
        from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy

        if not acquisition_policy().permits("proxy"):
            raise AcquisitionPolicyError("Scraping proxy refused by acquisition policy.")
    if proxy_url is not None:
        _validate_proxy_url(proxy_url)
    from kazma_core.http_tls import shared_ssl_context
    final_headers = dict(headers) if headers else {}
    if rotate_ua:
        final_headers["User-Agent"] = random_user_agent()

    client_kwargs: dict[str, Any] = {
        "follow_redirects": follow_redirects,
        "timeout": timeout,
        "headers": final_headers or None,
        # An ambient HTTPS_PROXY must not bypass target IP pinning or route
        # acquisition through an undeclared service.
        "trust_env": False,
        "verify": shared_ssl_context(),
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
        _validate_proxy_url(proxy_url)
        # Never resolve the provider again just for logging: Settings may have
        # changed since the route snapshot. Credentials are never logged.
        endpoint = urlparse(proxy_url)
        logger.debug('[proxy] scraping client via %s:%s', endpoint.hostname, endpoint.port)
    client_kwargs.update(kwargs)
    # No ambient proxy can evade the explicit acquisition-policy gate.
    client_kwargs["trust_env"] = False
    return client_kwargs


def get_scraping_client(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    pin_hosts: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an async httpx client wired to the active proxy + optional UA rotation.

    When no proxy provider is selected (the default), this returns a plain
    client — identical behavior to before, so non-users see no change.

    ``pin_hosts`` (audit H-7) is a live hostname→IP map used only on the
    **direct** path. A configured proxy skips the pin transport so CONNECT
    still targets the proxy. Callers still run ``assert_peer_public``.
    """
    common = _client_common_kwargs(
        follow_redirects=follow_redirects,
        timeout=timeout,
        headers=headers,
        rotate_ua=rotate_ua,
        **kwargs,
    )
    if (
        pin_hosts is not None
        and "transport" not in common
        and not common.get("proxy")
    ):
        from kazma_core.security.ssrf_pin import PinHostAsyncTransport

        from kazma_core.http_tls import shared_ssl_context

        common["transport"] = PinHostAsyncTransport(pin_hosts, verify=shared_ssl_context())
    proxy_route = common.get('proxy')
    try:
        if common.get('proxy'):
            from kazma_core.web_acquire import accounting
            from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport

            accounting.attach_hooks(common, 'proxy', common['proxy'], asynchronous=True)
            if 'transport' in common or common.get('mounts'):
                raise AcquisitionError(FetchErrorCode.ROUTING, 'Custom proxy transports/mounts cannot bypass the owned endpoint boundary.')
            # Put the proxy on this transport, rather than a client mount
            # that would bypass its bounded endpoint DNS backend.
            common['transport'] = EndpointAsyncTransport(proxy=common.pop('proxy'), verify=common['verify'])
        return httpx.AsyncClient(**common)
    except (ImportError, ValueError, httpx.InvalidURL):
        if not proxy_route:
            raise
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'Scraping proxy client could not initialize. Check endpoint settings and required proxy dependencies.') from None


def get_scraping_client_sync(
    *,
    follow_redirects: bool = True,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    rotate_ua: bool = False,
    **kwargs: Any,
) -> httpx.Client:
    """Sync httpx client (Bing / Wikipedia SERP, smoke scripts) with the same proxy."""
    common = _client_common_kwargs(
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=headers,
            rotate_ua=rotate_ua,
            **kwargs,
        )
    proxy_route = common.get('proxy')
    try:
        if common.get('proxy'):
            from kazma_core.web_acquire import accounting
            from kazma_core.web_acquire.endpoint_transport import EndpointTransport

            accounting.attach_hooks(common, 'proxy', common['proxy'], asynchronous=False)
            if 'transport' in common or common.get('mounts'):
                raise AcquisitionError(FetchErrorCode.ROUTING, 'Custom proxy transports/mounts cannot bypass the owned endpoint boundary.')
            common['transport'] = EndpointTransport(proxy=common.pop('proxy'), verify=common['verify'])
        return httpx.Client(**common)
    except (ImportError, ValueError, httpx.InvalidURL):
        if not proxy_route:
            raise
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'Scraping proxy client could not initialize. Check endpoint settings and required proxy dependencies.') from None
