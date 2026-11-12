"""One TLS context for every httpx client, built once and off the event loop.

``httpx.AsyncClient()`` with the default ``verify=True`` builds a fresh
``ssl.SSLContext`` and loads the whole CA bundle into it -- synchronously, in
the constructor. Built inside an ``async def``, that CA load runs on the event
loop that serves every chat stream, once per client. It is normally a few
milliseconds; on a starved machine two loop-stall dumps caught it holding the
loop (the X poller, 2026-09-23), and 131 async call sites paid it per request.

:func:`shared_ssl_context` returns the same context httpx would have built
(``httpx.create_ssl_context()`` -- certifi, plus ``SSL_CERT_FILE`` /
``SSL_CERT_DIR`` when set), created once. :func:`prewarm` builds it in a
thread at boot so no event-loop caller ever pays for it. An ``SSLContext`` is
safe to share between clients and threads.

The LLM provider keeps its own context (``llm_provider._shared_ssl_context``,
the system store) -- a different trust root on purpose, untouched here.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["open_download", "prewarm", "shared_ssl_context"]

_CONTEXT: Any = None
_LOCK = threading.Lock()


def shared_ssl_context() -> Any:
    """The process-wide httpx TLS context (``True`` if it cannot be built).

    ``True`` is httpx's own "verify normally" default, so a failure here
    degrades to per-client contexts -- never to disabled verification.
    """
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT
    with _LOCK:
        if _CONTEXT is None:
            try:
                import httpx

                _CONTEXT = httpx.create_ssl_context()
            except (OSError, ValueError):  # ssl.SSLError is an OSError; a bad SSL_CERT_FILE too
                logger.warning("[http_tls] shared TLS context unavailable", exc_info=True)
                return True
    return _CONTEXT


def open_download(url: str, *, timeout: float) -> Any:
    """``urllib.request.urlopen`` for a download setup makes, verified
    against certifi's roots (this module's context), never the machine's
    store alone.

    Python's default context on Windows reads only the roots already in the
    machine's store, and a fresh Windows adds a root only when Windows itself
    builds a chain that needs it: on the clean drill VM (2026-11-12) every
    GitHub release download setup made failed "unable to get local issuer
    certificate" while ``Invoke-WebRequest`` to the same host worked."""
    import ssl
    import urllib.request

    context = shared_ssl_context()
    if not isinstance(context, ssl.SSLContext):
        context = ssl.create_default_context()
    return urllib.request.urlopen(url, timeout=timeout, context=context)  # noqa: S310 -- https, verified


def prewarm() -> None:
    """Build the context now (call from a worker thread at startup)."""
    shared_ssl_context()
