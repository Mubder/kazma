"""Bound native resolver lifetime for hosted/proxy HTTPX connections.

Only connect_tcp receives the literal IP. Logical pooling, CONNECT authority,
proxy credentials and both TLS SNI names remain httpcore's original values.
This compatibility seam mirrors ssrf_pin's network backend substitution.
"""
from __future__ import annotations

import asyncio
import httpcore
import httpx

from kazma_core.web_acquire import dns_worker
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


class _EndpointBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend, proxy_host):
        self.backend, self.proxy_host = backend, proxy_host

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if self.proxy_host is not None and host.lower() != self.proxy_host.lower():
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Proxy transport attempted an unexpected endpoint; direct fallback refused.')
        pin = await dns_worker.resolve_endpoint(host, trusted_proxy=self.proxy_host is not None)
        return await self.backend.connect_tcp(pin, port, timeout=timeout,
            local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AcquisitionError(FetchErrorCode.ROUTING, 'Acquisition endpoint transport requires TCP.')

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class EndpointAsyncTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, proxy=None, **kwargs):
        super().__init__(proxy=proxy, **kwargs)
        host = httpx.URL(proxy).host if proxy else None
        self._pool._network_backend = _EndpointBackend(self._pool._network_backend, host)


class _SyncEndpointBackend(httpcore.NetworkBackend):
    def __init__(self, backend, proxy_host):
        self.backend, self.proxy_host = backend, proxy_host

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if self.proxy_host is not None and host.lower() != self.proxy_host.lower():
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Proxy transport attempted an unexpected endpoint; direct fallback refused.')
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Synchronous proxy HTTP cannot run on an event loop; use the asynchronous client or an owned worker.')
        pin = asyncio.run(dns_worker.resolve_endpoint(host, trusted_proxy=self.proxy_host is not None))
        return self.backend.connect_tcp(pin, port, timeout=timeout,
            local_address=local_address, socket_options=socket_options)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AcquisitionError(FetchErrorCode.ROUTING, 'Acquisition endpoint transport requires TCP.')

    def sleep(self, seconds):
        self.backend.sleep(seconds)


class EndpointTransport(httpx.HTTPTransport):
    def __init__(self, *, proxy=None, **kwargs):
        super().__init__(proxy=proxy, **kwargs)
        host = httpx.URL(proxy).host if proxy else None
        self._pool._network_backend = _SyncEndpointBackend(self._pool._network_backend, host)
