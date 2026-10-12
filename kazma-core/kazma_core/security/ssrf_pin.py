"""Connect-to-IP / Host+SNI-original transport for SSRF pin-IP (audit H-7).

Used only on the **direct** scraping path (no proxy). A proxy CONNECT
already terminates on the proxy host; pin-IP there would break the proxy
and is skipped by ``get_scraping_client``.

The pin map is a live dict so redirect hops can add hosts without
rebuilding the client.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpcore
import httpx

from kazma_core.security.ssrf import SSRFError, _is_blocked_ip

__all__ = ["PinHostAsyncTransport"]


def _validated_pin(pins: dict[str, str], host: str) -> str:
    pin = pins.get(host.lower())
    if not pin:
        raise SSRFError(f"Blocked URL: no validated IP pin for '{host}'.")
    try:
        address = ipaddress.ip_address(pin)
    except ValueError as exc:
        raise SSRFError("Blocked URL: invalid IP pin.") from exc
    if _is_blocked_ip(address):
        raise SSRFError("Blocked URL: private IP pin.")
    return str(address)


class _PinnedStream(httpcore.AsyncNetworkStream):
    """Keep socket provenance across TLS wrapping and connection reuse."""

    def __init__(self, stream: httpcore.AsyncNetworkStream, pin: str) -> None:
        self.stream = stream
        self.pin = pin

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self.stream.read(max_bytes, timeout=timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self.stream.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self.stream.aclose()

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = await self.stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=timeout,
        )
        return _PinnedStream(stream, self.pin)

    def get_extra_info(self, info: str) -> Any:
        if info == "kazma_pinned_ip":
            return self.pin
        return self.stream.get_extra_info(info)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, pins: dict[str, str], backend: httpcore.AsyncNetworkBackend):
        self.pins = pins
        self.backend = backend

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        pin = _validated_pin(self.pins, host)
        stream = await self.backend.connect_tcp(
            pin, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
        )
        return _PinnedStream(stream, pin)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise SSRFError("Blocked URL: Unix sockets cannot enforce public IP pins.")

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class PinHostAsyncTransport(httpx.AsyncHTTPTransport):
    """Pin the socket destination while retaining the logical pool/TLS origin.

    ``pins`` maps hostname (lowercased) → IPv4/IPv6 string. Missing hosts
    are refused; DNS fallback would defeat the validated-address boundary.
    """

    def __init__(self, pins: dict[str, str], **kwargs: Any) -> None:
        if kwargs.get("proxy") is not None or kwargs.get("uds") is not None:
            raise SSRFError("IP pinning requires a direct TCP transport.")
        super().__init__(**kwargs)
        self.pins = pins
        # HTTPX exposes no network_backend parameter. This is the single
        # compatibility seam to its httpcore pool; do not rewrite request URLs:
        # doing so merges different TLS origins sharing an IP into one pool.
        self._pool._network_backend = _PinnedBackend(pins, self._pool._network_backend)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        _validated_pin(self.pins, host)
        headers = httpx.Headers(request.headers)
        # The URL remains the logical origin. Preserve its HTTP authority
        # (including a non-default port and IPv6 brackets) independently of
        # the connect IP; TLS SNI below uses only the hostname.
        if "host" not in headers:
            headers["host"] = httpx.Request(request.method, request.url).headers["host"]
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = request.url.host or host
        pinned = httpx.Request(
            request.method,
            request.url,
            headers=headers,
            extensions=extensions,
            stream=request.stream,
        )
        response = await super().handle_async_request(pinned)
        stream = response.extensions.get("network_stream")
        if stream is not None:
            # Attest the connection's actual pin, never a newer map entry on
            # a reused socket. Live peer checks still take precedence.
            pin = stream.get_extra_info("kazma_pinned_ip")
            if pin:
                response.extensions["kazma_pinned_ip"] = pin
        return response
