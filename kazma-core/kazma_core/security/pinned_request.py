"""One HTTP request to an address another party chose, pinned to the checked address.

For a call that is not page acquisition -- an OAuth discovery, registration
or token request at an address an MCP server named -- and so must not go
through the scraping proxy, robots rules or the crawl's origin scheduler.
A page or file download uses ``web_acquire.http.fetch_public`` instead; both
are the pinned path (AGENTS.md §32).

What a request gets:

* ``https`` only, unless the caller allows plain ``http``;
* every address the name resolves to checked public by the owned resolver
  (``web_acquire.dns_worker.validate_public_url``): no private, loopback,
  link-local, metadata or reserved address, no credentials in the URL;
* the socket pinned to the checked address (``PinHostAsyncTransport``), so a
  name that answers differently a moment later is never asked again;
* the connected peer checked before the body is read;
* no redirect followed: a 3xx comes back as the answer, for the caller to
  refuse;
* no ambient proxy (``trust_env=False``), a bounded body and one deadline for
  the whole request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from kazma_core.security.ssrf import SSRFError

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_BYTES", "AnswerTooLarge", "pinned_request"]

#: Large enough for any OAuth metadata or token answer.
DEFAULT_MAX_BYTES = 1024 * 1024


class AnswerTooLarge(ValueError):
    """The answer's body was larger than the caller's limit."""


async def pinned_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: float = 20.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    require_https: bool = True,
) -> httpx.Response:
    """Send one request to the public address *url* names; never follow a redirect.

    Returns the response with its body read (bounded). Raises
    :class:`~kazma_core.security.ssrf.SSRFError` (or the acquisition layer's
    refusal) before anything is sent when the URL is not ``https`` (unless
    *require_https* is False) or any address the name resolves to is not
    public, and when the connected peer is not the checked address;
    :class:`AnswerTooLarge` when the body passes *max_bytes*.
    """
    from kazma_core.http_tls import shared_ssl_context
    from kazma_core.security.ssrf import assert_peer_public
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport
    from kazma_core.web_acquire import dns_worker
    from kazma_core.web_acquire.http import read_response_capped

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme != "https" and (require_https or scheme != "http"):
        raise SSRFError("Blocked URL: only https addresses are allowed here.")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFError("Blocked URL: the address names no host.")
    async with asyncio.timeout(timeout):
        ips = tuple(await dns_worker.validate_public_url(url))
        if not ips:
            raise SSRFError("Blocked URL: the host has no checked public address.")
        tls = shared_ssl_context()
        transport = PinHostAsyncTransport({host: ips[0]}, verify=tls)
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=timeout,
            verify=tls,
        ) as client:
            async with client.stream(
                method, url, headers=headers, data=data, json=json_body,
            ) as response:
                assert_peer_public(response, url=url, validated_ips=ips, require_peer=True)
                answer, _text, truncated = await read_response_capped(response, max_bytes)
    if truncated:
        raise AnswerTooLarge(f"The answer was larger than {max_bytes} bytes.")
    return answer
