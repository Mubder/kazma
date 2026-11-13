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
anywhere else, no page frames anything, workers, fonts and the manifest are
this server's. A response that sets its own policy (chat file downloads,
sandboxed) keeps it.

Scripts and connections (2026-11-11, inventoried again: every ``https://``
a page script or template loads or fetches). Scripts are this server's own
files and the pages' inline scripts and handlers (``'unsafe-inline'``), with
``'unsafe-eval'`` for Alpine, which compiles every ``x-*`` expression with
``new Function`` -- and no other host: a ``<script>`` with an outside address
is refused. The LiveKit browser client was the one script loaded from
another host (a CDN, unpinned); it ships with Kazma now
(``static/vendor/livekit``, ``scripts/vendor_livekit.py``). Connections are
this server's (``'self'``, and its own ``ws:``/``wss:`` origin named for
browsers that read ``'self'`` as http only) plus the LiveKit server the
owner configured (``LIVEKIT_URL`` or ``voice.livekit.url``, when duplex is
on: its ``wss``/``https`` origin, and ``*.livekit.cloud`` for LiveKit
Cloud, whose client moves to a regional host). That server is read off the
loop and kept :data:`_LiveKitSources.TTL_S` seconds (:func:`_livekit_sources`);
a page applies the policy it was loaded with, so a LiveKit address saved in
Settings reaches pages opened after the save (Settings says so). No
``default-src``: every kind a page uses is named.

``Cross-Origin-Opener-Policy: same-origin`` (2026-11-11): a page Kazma opens
in a window of another site -- or a site that opens Kazma -- shares no
``window.opener`` with it. It waited for the GitHub sign-in, which ran in a
popup answering its opener with ``postMessage``; every sign-in now runs in
the same tab (``window.kazmaStartSignIn``, ``kazma_ui.oauth_sign_in``), and
``tests/test_no_popup_sign_in.py`` keeps it so. The one window a page still
opens for a sign-in is an MCP server's authorization page, opened
``noopener`` with its callback on a loopback listener of its own.

A pure ASGI middleware, so streamed responses (SSE) pass through untouched.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["CSP", "SECURITY_HEADERS", "SecurityHeadersMiddleware", "pin_static_types"]

#: Only this server's images, audio and video; no plugins; no <base>
#: rewrite; forms post here; no page frames a Kazma page.
#: Styles, fonts, workers and the manifest are this server's own; nothing
#: is framed. Scripts are this server's, inline, and Alpine's expressions;
#: ``connect-src`` is added per response (:func:`_policy_for`).
CSP = (
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "frame-src 'none'; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

#: Only over HTTPS (see :func:`_is_https_scope`), never on plain loopback http.
HSTS = b"max-age=31536000"

#: Added to a response that does not already carry the header; the policy
#: itself is :func:`_policy_for`'s.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    # Voice input needs the microphone; nothing else is used.
    (b"permissions-policy", b"camera=(), geolocation=(), microphone=(self)"),
)


def _is_https_scope(scope: dict[str, Any]) -> bool:
    """Whether auth's rule (direct TLS or a declared trusted proxy) says HTTPS."""
    try:
        from starlette.requests import Request

        from kazma_ui.auth import _is_https

        return bool(_is_https(Request(scope)))
    except Exception:  # an undecidable request gets no HSTS, never a 500
        return False


#: An origin as it may stand in a source list: a scheme, a host of letters,
#: digits, dots and dashes (or an IP literal) and a port -- nothing that
#: could end the directive or name another (``;``, ``,``, a space, ``*``).
_ORIGIN = re.compile(r"^(https?)://(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:\d{1,5})?$")


def _http_origin(url: str) -> str:
    """*url*'s origin as ``http(s)://host[:port]``, safe in a source list, or ``""``.

    ``ws:`` / ``wss:`` read as ``http:`` / ``https:`` (a LiveKit address is
    usually written ``wss://``).
    """
    from kazma_ui.browser_origins import normalize_origin

    text = (url or "").strip()
    scheme, sep, rest = text.partition("://")
    if not sep:
        return ""
    scheme = {"ws": "http", "wss": "https"}.get(scheme.lower(), scheme.lower())
    origin = normalize_origin(f"{scheme}://{rest}", allow_path=True) or ""
    return origin if _ORIGIN.match(origin) else ""


def _socket_origin(origin: str) -> str:
    """The ``ws:`` / ``wss:`` twin of an ``http(s):`` origin."""
    return ("wss" + origin[len("https"):]) if origin.startswith("https:") else ("ws" + origin[len("http"):])


def _livekit_connect_sources(url: str) -> tuple[str, ...]:
    """The sources a browser needs to reach the LiveKit server at *url*.

    Its WebSocket (signalling) and its HTTPS origin (the client's checks);
    for LiveKit Cloud also ``*.livekit.cloud``, where the client moves to a
    regional host. An address that is not a plain origin gives nothing.
    """
    origin = _http_origin(url)
    if not origin:
        return ()
    sources = [_socket_origin(origin), origin]
    host = origin.split("://", 1)[1].split(":", 1)[0]
    if host == "livekit.cloud" or host.endswith(".livekit.cloud"):
        sources += ["wss://*.livekit.cloud", "https://*.livekit.cloud"]
    return tuple(sources)


def _policy_for(scope: dict[str, Any], livekit: tuple[str, ...] = ()) -> bytes:
    """The Content-Security-Policy for one response: :data:`CSP` and its
    ``connect-src`` -- this server (``'self'``, and its own ``ws``/``wss``
    origin, which older browsers do not read into ``'self'``) and the
    configured LiveKit server's *livekit* sources."""
    sources = ["'self'"]
    try:
        from starlette.requests import Request

        served = _http_origin(str(Request(scope).url))
    except (KeyError, TypeError, ValueError):  # an unreadable Host: 'self' still covers this server
        served = ""
    if served:
        sources.append(_socket_origin(served))
    sources.extend(livekit)
    return f"{CSP}; connect-src {' '.join(dict.fromkeys(sources))}".encode("ascii")


class _LiveKitSources:
    """The configured LiveKit server's sources, read off the loop and kept a while.

    The server is the environment's ``LIVEKIT_URL`` or the setting
    ``voice.livekit.url`` (``kazma_core.voice.livekit.get_livekit_config``),
    only while duplex is on. A settings read is a database round trip: never
    on the loop (:func:`_livekit_sources`).
    """

    TTL_S = 15.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: tuple[str, ...] | None = None
        self._read_at = 0.0
        self._warned = ""
        self.refreshing = False
        #: Bumped by :meth:`forget`: a background read begun before it reads
        #: nothing (a test's settings, a changed setting are read fresh).
        self.generation = 0

    def cached(self) -> tuple[tuple[str, ...] | None, bool]:
        """What was read last (None: nothing yet) and whether it is due again."""
        value = self._value
        return value, value is None or time.monotonic() - self._read_at > self.TTL_S

    def read(self, generation: int | None = None) -> tuple[str, ...]:
        """Read the configured server now. Blocking: call from a thread. A
        background refresh passes the *generation* it began in: one a
        :meth:`forget` came after reads nothing (2026-11-13: an earlier app's
        refresh, still queued in its thread, read a later test's settings)."""
        with self._lock:
            if generation is not None and generation != self.generation:
                return self._value or ()
            try:
                # Never raises by its contract (a settings store that cannot
                # answer reads as unset); a broken import or value still must
                # not fail a response.
                from kazma_core.voice.livekit import get_livekit_config

                cfg = get_livekit_config()
                value = _livekit_connect_sources(cfg.url) if cfg.enabled else ()
                if cfg.enabled and not value and cfg.url != self._warned:
                    self._warned = cfg.url
                    logger.warning("[csp] The LiveKit address is not a plain origin "
                                   "(scheme://host[:port]); the pages will not reach it.")
            except (ImportError, OSError, RuntimeError, ValueError):
                # Kept, never widened: the last sources read stand.
                logger.warning("[csp] The LiveKit address could not be read", exc_info=True)
                value = self._value or ()
            self._value, self._read_at = value, time.monotonic()
            self.refreshing = False
            return value

    def forget(self) -> None:
        """Read again at the next response (tests, and a changed setting)."""
        with self._lock:
            self._value, self._read_at, self.refreshing = None, 0.0, False
            self.generation += 1


_LIVEKIT = _LiveKitSources()


async def _livekit_sources() -> tuple[str, ...]:
    """The LiveKit sources for this response, without holding the loop.

    The first response waits for one read in a thread; after that a stale
    value is used while one background read brings it up to date.
    """
    value, due = _LIVEKIT.cached()
    if value is None:
        return await asyncio.to_thread(_LIVEKIT.read)
    if due and not _LIVEKIT.refreshing:
        from kazma_core.background import spawn_background

        _LIVEKIT.refreshing = True
        spawn_background(asyncio.to_thread(_LIVEKIT.read, _LIVEKIT.generation), name="csp-livekit-sources")
    return value


class SecurityHeadersMiddleware:
    """Add :data:`SECURITY_HEADERS` and :func:`_policy_for` to every HTTP response start."""

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
                if b"content-security-policy" not in present:
                    headers.append((b"content-security-policy",
                                    _policy_for(scope, await _livekit_sources())))
                headers.extend(pair for pair in SECURITY_HEADERS if pair[0] not in present)
                if b"strict-transport-security" not in present and _is_https_scope(scope):
                    headers.append((b"strict-transport-security", HSTS))
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
