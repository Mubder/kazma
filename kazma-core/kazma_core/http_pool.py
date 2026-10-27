"""Centralized, thread-safe HTTP Connection Pool for Kazma.

Provides a globally shared, persistent httpx.AsyncClient instance with
standard retry, timeout, and pool configuration to optimize hot paths.
"""

from __future__ import annotations

import logging
import threading

import httpx

__all__ = ["close_http_client", "get_http_client"]

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_lock = threading.Lock()


def get_http_client() -> httpx.AsyncClient:
    """Get or create the global persistent httpx.AsyncClient instance.

    Thread-safe initialization. Reuses connection pool for efficiency.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                # Configure the client with standard enterprise settings:
                # 10s connect timeout, 30s overall timeout, 100 max connections, 20 max keepalive.
                limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
                timeout = httpx.Timeout(30.0, connect=10.0)
                from kazma_core.http_tls import shared_ssl_context

                # Redirects are OFF by default (audit 2026-10-26 AUD-099): a
                # request through the shared pool followed any 3xx to any host,
                # a hop no caller had checked; a caller that wants a redirect
                # chain resolves it with every hop checked
                # (``security.ssrf.resolve_redirects``). The TLS context is the
                # process's shared one, like every other async client (§35).
                _client = httpx.AsyncClient(
                    limits=limits,
                    timeout=timeout,
                    follow_redirects=False,
                    verify=shared_ssl_context(),
                )
                logger.info("[HTTPPool] Initialized global persistent httpx.AsyncClient pool")
    return _client


async def close_http_client() -> None:
    """Closes the global persistent httpx.AsyncClient instance if initialized."""
    global _client
    client_to_close = None
    with _client_lock:
        if _client is not None:
            client_to_close = _client
            _client = None
    if client_to_close is not None:
        try:
            await client_to_close.aclose()
            logger.info("[HTTPPool] Closed global persistent httpx.AsyncClient pool")
        except Exception as exc:
            logger.warning("[HTTPPool] Error closing httpx.AsyncClient: %s", exc)
