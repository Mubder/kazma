"""Proxy provider abstraction for bulletproof web scraping.

A ``ProxyProvider`` translates user config (host/port/credentials/flags) into an
outbound proxy URL that ``httpx`` consumes via ``proxy=``. Providers are
pluggable: the scraper talks to the interface, so adding a new proxy service
(BrightData, Oxylabs, …) is one class under this package + one registry line.

This is **opt-in and scraping-scoped**: it is never applied to LLM API calls
(those use the separate ``http_pool.py``), and defaults to ``NullProvider``
(direct, no proxy) so non-users see zero change.

Config lives in ConfigStore under ``proxy.*`` keys (mirrors the connector
pattern); ``proxy.password`` is auto-encrypted by the vault.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["ProxyProvider", "NullProvider"]

logger = logging.getLogger(__name__)


class ProxyProvider:
    """Interface for outbound proxy providers.

    Implementations read their config from ConfigStore on each call (live config
    — a Settings change takes effect without restart, mirroring HITL's
    ``get_hitl_config``). ``None`` means credentials are not configured; active
    route resolution refuses a selected unusable provider instead of switching
    to direct access. Configuration failures raise a typed acquisition error.
    """

    name: str = "none"

    def get_proxy_url(self) -> str | None:
        """Return an httpx proxy URL (``http://user:pass@host:port``) or None.

        ``None`` means "no proxy / direct" — callers must treat it as direct.
        """
        return None

    def is_configured(self) -> bool:
        """True when the provider has enough config to actually proxy."""
        return False

    async def test(self) -> dict[str, Any]:
        """Health-check the proxy. Returns ``{success, exit_ip?, error?}``.

        Default implementation: fetch ``https://api.ipify.org`` through the
        proxy and return the exit IP. Subclasses may override for provider-
        specific health endpoints.
        """
        import asyncio

        from kazma_core.web_acquire.policy import acquisition_policy
        from kazma_core.web_acquire.result import AcquisitionError

        if not acquisition_policy().permits('proxy'):
            return {'success': False, 'error': 'Proxy testing is disabled by acquisition policy.'}
        try:
            proxy_url = await asyncio.to_thread(self.get_proxy_url)
        except AcquisitionError as exc:
            return {'success': False, 'error': str(exc)}
        if not proxy_url:
            return {'success': False, 'error': 'Proxy not configured.'}
        return await probe_proxy(proxy_url)


async def probe_proxy(proxy_url: str | None) -> dict[str, Any]:
    """Probe a resolved route without re-reading provider/account settings."""
    import asyncio
    import ipaddress

    from kazma_core.errors import redact_secrets
    from kazma_core.proxy.client import get_scraping_client
    from kazma_core.web_acquire.http import read_response_capped
    from kazma_core.web_acquire.policy import acquisition_policy
    from kazma_core.web_acquire.resources import acquisition_capacity
    from kazma_core.web_acquire.result import AcquisitionError

    if not acquisition_policy().permits('proxy'):
        return {'success': False, 'error': 'Proxy testing is disabled by acquisition policy.'}
    if proxy_url is None:
        return {'success': True, 'message': 'Direct mode (no proxy).'}
    try:
        async with asyncio.timeout(15.0), await acquisition_capacity().acquire(), get_scraping_client(
            proxy=proxy_url, timeout=15.0, follow_redirects=False,
        ) as client:
            async with client.stream('GET', 'https://api.ipify.org') as response:
                resp, text, truncated = await read_response_capped(response, 128)
            if resp.status_code != 200:
                return {'success': False, 'error': f'Exit check HTTP {resp.status_code}'}
            if truncated:
                return {'success': False, 'error': 'Proxy exit check exceeded the response limit.'}
            try:
                exit_ip = str(ipaddress.ip_address(text.strip()))
            except ValueError:
                return {'success': False, 'error': 'Proxy exit check did not return a valid IP address.'}
            return {'success': True, 'exit_ip': exit_ip}
    except AcquisitionError as exc:
        return {'success': False, 'error': str(exc), 'error_code': exc.code}
    except TimeoutError:
        return {'success': False, 'error': 'Proxy exit check deadline exceeded (15s).', 'error_code': 'acquisition_deadline'}
    except Exception as exc:
        return {'success': False, 'error': redact_secrets(f'Proxy connection failed: {exc}')[:300]}


class NullProvider(ProxyProvider):
    """The default provider — direct fetching, no proxy."""

    name = "none"

    def get_proxy_url(self) -> str | None:
        return None

    def is_configured(self) -> bool:
        return False

    async def test(self) -> dict[str, Any]:
        return {"success": True, "message": "Direct mode (no proxy)."}
