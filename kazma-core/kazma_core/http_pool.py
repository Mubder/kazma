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
                _client = httpx.AsyncClient(
                    limits=limits,
                    timeout=timeout,
                    follow_redirects=True,
                )
                logger.info("[HTTPPool] Initialized global persistent httpx.AsyncClient pool")
    return _client


async def close_http_client() -> None:
    """Closes the global persistent httpx.AsyncClient instance if initialized."""
    global _client
    if _client is not None:
        with _client_lock:
            if _client is not None:
                client_to_close = _client
                _client = None
                try:
                    await client_to_close.aclose()
                    logger.info("[HTTPPool] Closed global persistent httpx.AsyncClient pool")
                except Exception as exc:
                    logger.warning("[HTTPPool] Error closing httpx.AsyncClient: %s", exc)
