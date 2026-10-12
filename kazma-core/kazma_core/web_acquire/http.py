"""Bounded public HTTP acquisition with validation and pinning on every GET.

This is the raw layer for links, robots and sitemaps, not a second extraction
ladder. Optional extraction services and LLM traffic must not enter it.
"""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

_PROXY_UNSET = object()

from kazma_core.security.ssrf import SSRFError, assert_peer_public, peer_ip_from_response, validate_url


@dataclass(frozen=True)
class HttpFetch:
    response: httpx.Response
    text: str
    url: str
    truncated: bool


def decompress_gzip(content: bytes, max_bytes: int) -> str:
    """Bound expansion of raw .xml.gz artifacts, including concatenated members."""
    import gzip
    import io

    with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Sitemap decompressed byte limit exceeded.")
    return data.decode("utf-8", errors="replace")


async def _bounded_body(response: httpx.Response, limit: int) -> tuple[bytearray, bool]:
    """Bound wire bytes AND decoder allocation, rather than trimming afterwards.

    HTTPX's aiter_bytes can expand a gzip bomb before yielding its first chunk.
    Use raw streaming and zlib's max_length instead. Brotli and stacked content
    encodings are deliberately refused; clients advertise only gzip/deflate.
    """
    if response.is_stream_consumed:
        # In-memory responses supplied by transports are already decoded.
        response.extensions["kazma_wire_bytes"] = None
        return bytearray(response.content[:limit]), len(response.content) > limit
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity", "gzip", "x-gzip", "deflate"):
        raise httpx.DecodingError(f"Unsupported bounded Content-Encoding: {encoding[:60]}")
    compressed = encoding not in ("", "identity")
    gzip = encoding in ("gzip", "x-gzip")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if gzip else zlib.MAX_WBITS) if compressed else None
    first = True
    prefix = bytearray()
    raw = bytearray()
    wire = 0
    response.extensions["kazma_wire_bytes"] = 0
    async for chunk in response.aiter_raw(65_536):
        wire += len(chunk)
        response.extensions["kazma_wire_bytes"] = wire
        if wire > limit + 65_536:
            return raw[:limit], True
        if not compressed:
            raw.extend(chunk[:limit + 1 - len(raw)])
        else:
            pending = chunk
            if encoding == "deflate" and first:
                prefix.extend(pending)
                if len(prefix) < 2:
                    continue
                # Distinguish zlib framing from raw DEFLATE before decoding;
                # a one-byte first network chunk must not defeat detection.
                framed = prefix[0] & 15 == 8 and (prefix[0] * 256 + prefix[1]) % 31 == 0
                decoder = zlib.decompressobj(zlib.MAX_WBITS if framed else -zlib.MAX_WBITS)
                pending = bytes(prefix)
                prefix.clear()
            while pending:
                if decoder is None:
                    if not gzip:
                        raise httpx.DecodingError("Trailing bytes in deflate response.")
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                try:
                    decoded = decoder.decompress(pending, limit + 1 - len(raw))
                except zlib.error as exc:
                    raise httpx.DecodingError("Invalid compressed response.") from exc
                first = False
                raw.extend(decoded)
                if len(raw) > limit:
                    return raw[:limit], True
                if decoder.eof:
                    pending = decoder.unused_data
                    decoder = None
                else:
                    pending = decoder.unconsumed_tail
        if len(raw) > limit:
            return raw[:limit], True
    if compressed and decoder is not None:
        raise httpx.DecodingError("Incomplete compressed response.")
    return raw, False


async def read_capped(
    client: Any,
    url: str,
    max_bytes: int,
    *,
    validated_ips: tuple[str, ...] = (),
    via_proxy: bool = False,
    require_peer: bool = False,
    observe_response: Callable[[httpx.Response], None] | None = None,
    method: str = "GET",
    request_headers: dict[str, str] | None = None,
    request_body: bytes | None = None,
) -> tuple[httpx.Response, str, bool]:
    """Check the live peer before reading, cap decoded bytes, preserve evidence."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    from kazma_core.web_acquire.origins import origin_scheduler

    from kazma_core.web_acquire.robots import require_robots, active_robots, USER_AGENT

    await require_robots(url)
    context = active_robots.get()
    if context is not None and context.enforce():
        request_headers = {key: value for key, value in (request_headers or {}).items() if key.lower() != 'user-agent'}
        request_headers['User-Agent'] = USER_AGENT
    scheduler = origin_scheduler()
    if request_body is not None and len(request_body) > 1024**2:
        raise ValueError('Browser request body exceeds 1MiB')
    body_kwargs = {'content': request_body} if request_body is not None else {}
    async with scheduler.request(url):
        async with client.stream(method, url, headers=request_headers, **body_kwargs) as response:
            assert_peer_public(response, url=url, validated_ips=validated_ips,
                               via_proxy=via_proxy, require_peer=require_peer)
            if observe_response is not None:
                observe_response(response)
            from kazma_core.web_acquire.crawl_jobs import call
            await call(scheduler.observe, url, response.status_code, response.headers)
            extensions = {**response.extensions, "kazma_via_proxy": via_proxy}
            peer = peer_ip_from_response(response)
            if peer:
                extensions["kazma_peer_ip"] = peer
            response.extensions.update(extensions)
            return await read_response_capped(response, max_bytes)


async def read_response_capped(
    response: httpx.Response, max_bytes: int,
) -> tuple[httpx.Response, str, bool]:
    """Bound an OPEN response's wire/decoded body before buffering it.

    This is a decoder, not an egress gate. Public target callers must use
    read_capped/fetch_public, which validate the live peer first. Hosted API
    callers apply their provider policy and endpoint controls before opening
    the stream. The caller owns/ closes the response context in every case.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    # HEAD/204/304 have no entity body. Their Content-Encoding/Length may
    # describe a representation, not bytes that should be read or decoded.
    # Redirect bodies are irrelevant; never buffer them to discover a hop.
    raw = bytearray()
    truncated = False
    if response.request.method == "HEAD" or response.status_code in (204, 304):
        response.extensions.update(kazma_wire_bytes=0, kazma_decoded_bytes=0)
    elif response.status_code not in (301, 302, 303, 307, 308):
        raw, truncated = await _bounded_body(response, max_bytes)
        response.extensions["kazma_decoded_bytes"] = len(raw)
    encoding = response.charset_encoding or "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        encoding = "utf-8"
        text = raw.decode("utf-8", errors="replace")
    # Do not ask httpx to decompress already-decoded bytes a second time.
    out = httpx.Response(response.status_code, headers=response.headers,
                         request=response.request, extensions=dict(response.extensions))
    out._content = bytes(raw)
    out.encoding = encoding
    return out, text, truncated


async def fetch_public(
    url: str,
    *,
    max_bytes: int = 5_000_000,
    timeout: float = 30.0,
    max_redirects: int = 5,
    scope: Callable[[str], bool] | None = None,
    headers: dict[str, str] | None = None,
    capacity_pool=None,
    proxy_url=_PROXY_UNSET,
) -> HttpFetch:
    """Fetch the actual GET chain; never infer GET redirects from HEAD.

    The timeout covers DNS, all redirects, and body streaming together. Scope
    is checked before every request, including redirected URLs.
    """
    from kazma_core.proxy.client import get_active_proxy_url, get_scraping_client
    from kazma_core.web_acquire import resources

    pins: dict[str, str] = {}
    current = url
    seen: set[str] = set()
    async with asyncio.timeout(timeout), await (capacity_pool or resources.acquisition_capacity()).acquire():
        if proxy_url is _PROXY_UNSET:
            proxy_url = get_active_proxy_url()
        via_proxy = bool(proxy_url)
        async with get_scraping_client(
            follow_redirects=False, timeout=timeout,
            headers={"Accept-Encoding": "gzip, deflate", **(headers or {})},
            rotate_ua=True, pin_hosts=pins if not via_proxy else None,
            trust_env=False, proxy=proxy_url,
        ) as client:
            for hop in range(max_redirects + 1):
                if scope is not None and not scope(current):
                    raise SSRFError("Redirect or URL is outside the crawl scope.")
                if current in seen:
                    raise SSRFError("Redirect cycle detected.")
                seen.add(current)
                from kazma_core.web_acquire import dns_worker

                ips = await dns_worker.validate_public_url(current, validator=validate_url)
                host = (urlsplit(current).hostname or "").lower()
                if not ips:
                    raise SSRFError("No validated public address for acquisition.")
                if not via_proxy:
                    pins[host] = ips[0]
                response, text, truncated = await read_capped(
                    client, current, max_bytes, validated_ips=ips,
                    via_proxy=via_proxy, require_peer=True,
                )
                if response.status_code not in (301, 302, 303, 307, 308):
                    return HttpFetch(response, text, current, truncated)
                location = response.headers.get("location")
                if not location:
                    raise SSRFError("Redirect has no Location header.")
                if hop == max_redirects:
                    raise SSRFError("Redirect limit exceeded.")
                current = urljoin(current, location)
    raise SSRFError("Redirect limit exceeded.")
