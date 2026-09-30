"""Security headers on every HTTP response (audit 2026-09-30, AUD-018).

The pages sent no Content-Security-Policy, no framing rule, no ``nosniff`` and
no referrer policy. The one that mattered: the markdown renderer loaded an
image from any host a model reply named, and a reply is steerable by any
untrusted text the agent reads, so a prompt injection could make the browser
send conversation data to an outside server just by rendering. The renderer
now links such images instead (``streaming.js`` ``isSameOriginPath``); this
policy is the second layer, so no future renderer path can load one either.

What the policy restricts is what the pages provably use (inventoried
2026-09-30): every image, audio and video is this server's (or a ``data:``/
``blob:`` URL the page made), nothing frames a Kazma page, no form posts
anywhere else. Scripts and styles are not restricted here: Alpine evaluates
expressions with ``new Function`` and the templates use inline handlers, so a
script policy would need ``unsafe-inline`` and ``unsafe-eval`` and add
nothing. A response that sets its own policy (chat file downloads, sandboxed)
keeps it.

A pure ASGI middleware, so streamed responses (SSE) pass through untouched.
"""

from __future__ import annotations

import mimetypes
from typing import Any

__all__ = ["CSP", "SECURITY_HEADERS", "SecurityHeadersMiddleware", "pin_static_types"]

#: Only this server's images, audio and video; no plugins; no <base>
#: rewrite; forms post here; no page frames a Kazma page.
CSP = (
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

#: Added to a response that does not already carry the header.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
)


class SecurityHeadersMiddleware:
    """Add :data:`SECURITY_HEADERS` to every HTTP response start."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {bytes(name).lower() for name, _ in headers}
                headers.extend(pair for pair in SECURITY_HEADERS if pair[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


#: The static types a page needs executed or rendered.
_STATIC_TYPES = (
    (".js", "application/javascript"),
    (".mjs", "application/javascript"),
    (".css", "text/css"),
    (".json", "application/json"),
    (".map", "application/json"),
    (".svg", "image/svg+xml"),
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".wasm", "application/wasm"),
)


def pin_static_types() -> None:
    """Serve scripts and styles with their real types, whatever the OS says.

    Python reads MIME types from the Windows registry, which on some machines
    maps ``.js`` to ``text/plain``; under ``nosniff`` a browser then refuses
    to run the script and every page is blank.
    """
    for ext, media_type in _STATIC_TYPES:
        mimetypes.add_type(media_type, ext)
