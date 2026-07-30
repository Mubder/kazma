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
    ``get_hitl_config``). They must degrade gracefully: if misconfigured, return
    ``None`` from :meth:`get_proxy_url` so the caller falls back to direct.
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
        import httpx

        proxy_url = self.get_proxy_url()
        if not proxy_url:
            return {"success": False, "error": "Proxy not configured."}
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0) as client:
                resp = await client.get("https://api.ipify.org")
                if resp.status_code == 200:
                    return {"success": True, "exit_ip": resp.text.strip()}
                return {"success": False, "error": f"Exit check HTTP {resp.status_code}"}
        except Exception as exc:  # noqa: BLE001 — best-effort health check
            return {"success": False, "error": f"Proxy connection failed: {exc}"}


class NullProvider(ProxyProvider):
    """The default provider — direct fetching, no proxy."""

    name = "none"

    def get_proxy_url(self) -> str | None:
        return None

    def is_configured(self) -> bool:
        return False

    async def test(self) -> dict[str, Any]:
        return {"success": True, "message": "Direct mode (no proxy)."}
