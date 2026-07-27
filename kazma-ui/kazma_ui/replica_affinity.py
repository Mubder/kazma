"""Replica affinity cookie for multi-node SSE / in-process state.

Chat SSE and some swarm state are process-local. Behind a load balancer,
affinity keeps a browser on the same replica for the session lifetime.

Env:
  ``KAZMA_REPLICA_ID`` — sticky value (default: hostname)
  ``KAZMA_REPLICA_AFFINITY=0`` — disable cookie

Cookie: ``kazma-replica`` (HttpOnly, Path=/). Configure your LB to sticky
on this cookie, or use source-IP hash as an alternative.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

__all__ = ["COOKIE_NAME", "create_replica_affinity_middleware", "replica_id"]

COOKIE_NAME = "kazma-replica"


def replica_id() -> str:
    env = (os.environ.get("KAZMA_REPLICA_ID") or "").strip()
    if env:
        return env
    try:
        return socket.gethostname() or "kazma-node"
    except Exception:
        return "kazma-node"


def create_replica_affinity_middleware() -> Callable[
    [Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]
]:
    """HTTP middleware that sets/refreshes the replica affinity cookie."""

    disabled = (os.environ.get("KAZMA_REPLICA_AFFINITY") or "1").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    )
    rid = replica_id()

    async def replica_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if disabled:
            return response
        existing = request.cookies.get(COOKIE_NAME)
        if existing != rid:
            secure = request.url.scheme == "https"
            response.set_cookie(
                key=COOKIE_NAME,
                value=rid,
                path="/",
                httponly=True,
                samesite="lax",
                secure=secure,
                max_age=7 * 24 * 3600,
            )
            logger.debug("[replica] set affinity cookie=%s", rid)
        return response

    return replica_middleware
