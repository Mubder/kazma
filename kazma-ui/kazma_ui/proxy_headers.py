"""Apply ``X-Forwarded-*`` from declared proxies inside the app, keeping the TCP peer.

Live, every boot since 2026-09-23: Cloudflare Tunnel (``cloudflared`` on
127.0.0.1, correctly declared in ``KAZMA_TRUSTED_PROXIES``) forwarded a
visitor's request. uvicorn -- told by ``serve.py`` to honour forwarded headers
from the declared proxies -- replaced ``scope["client"]`` with the visitor's
address before the app saw it. The undeclared-proxy detector
(:func:`kazma_ui.auth._note_forwarded_headers`) reads the TCP peer from that
field, so it saw the VISITOR's address carrying ``X-Forwarded-For`` and
concluded an undeclared proxy was in front::

    [SECURITY] x-forwarded-for/x-forwarded-proto arrived from peer
    46.186.228.227, which is not in KAZMA_TRUSTED_PROXIES ...
    Set KAZMA_TRUSTED_PROXIES=46.186.228.227 and restart.

A false alarm on the first tunnelled request of every process, whose advice
would have made a client address a trusted proxy. The same substitution made
:func:`kazma_ui.auth._peer_trust_allowed` see the visitor instead of the
proxy, so what kept a tunnelled visitor away from peer trust was the false
alarm itself.

So the server no longer rewrites anything (every entry point passes
``proxy_headers=False``; ``tests/test_forwarded_headers_peer.py`` gates it),
and this middleware, outermost in the app, does it instead: it records the
TCP peer on the scope first (:data:`kazma_ui.auth.TCP_PEER_SCOPE_KEY`), then
runs uvicorn's own ``ProxyHeadersMiddleware`` for the declared proxies. Every
middleware and route below sees the same client address and scheme as before;
the peer checks see the peer.
"""

from __future__ import annotations

import logging
from typing import Any

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kazma_ui.auth import TCP_PEER_SCOPE_KEY, trusted_proxies

logger = logging.getLogger(__name__)

__all__ = ["ForwardedHeadersMiddleware"]


class ForwardedHeadersMiddleware:
    """Record the TCP peer, then apply forwarded headers from declared proxies.

    ``KAZMA_TRUSTED_PROXIES`` is read per request (as everywhere in
    :mod:`kazma_ui.auth`), and the uvicorn middleware is rebuilt only when it
    changes.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._trusted: frozenset[str] = frozenset()
        self._rewrite: ProxyHeadersMiddleware | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        scope[TCP_PEER_SCOPE_KEY] = (client[0] if client else "") or ""
        trusted = trusted_proxies()
        if not trusted:
            await self.app(scope, receive, send)
            return
        if trusted != self._trusted or self._rewrite is None:
            self._rewrite = ProxyHeadersMiddleware(self.app, trusted_hosts=sorted(trusted))
            self._trusted = trusted
        await self._rewrite(scope, receive, send)
