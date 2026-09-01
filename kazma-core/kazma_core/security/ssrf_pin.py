"""Connect-to-IP / Host+SNI-original transport for SSRF pin-IP (audit H-7).

Used only on the **direct** scraping path (no proxy). A proxy CONNECT
already terminates on the proxy host; pin-IP there would break the proxy
and is skipped by ``get_scraping_client``.

The pin map is a live dict so redirect hops can add hosts without
rebuilding the client.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = ["PinHostAsyncTransport"]


class PinHostAsyncTransport(httpx.AsyncHTTPTransport):
    """Rewrite the connect host to a pre-validated IP; keep Host + SNI.

    ``pins`` maps hostname (lowercased) → IPv4/IPv6 string. Missing hosts
    fall through to normal DNS (then ``assert_peer_public`` still runs).
    """

    def __init__(self, pins: dict[str, str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pins = pins

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        pin = self.pins.get(host) if host else None
        if not pin:
            return await super().handle_async_request(request)
        headers = httpx.Headers(request.headers)
        headers["host"] = request.url.host or host
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = request.url.host or host
        try:
            new_url = request.url.copy_with(host=pin)
        except Exception:
            logger.debug("[ssrf-pin] copy_with host failed for %s -> %s", host, pin)
            return await super().handle_async_request(request)
        pinned = httpx.Request(
            request.method,
            new_url,
            headers=headers,
            extensions=extensions,
            stream=request.stream,
        )
        return await super().handle_async_request(pinned)
