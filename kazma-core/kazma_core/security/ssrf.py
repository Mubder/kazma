"""SSRF (Server-Side Request Forgery) protection utilities.

Provides :func:`validate_url` which resolves a URL's hostname to its
IP addresses and rejects any that point to private, loopback, link-local,
or other non-public ranges.

Blocked ranges include:
  - RFC 1918 private: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
  - Loopback: 127.0.0.0/8, ::1
  - Link-local / cloud metadata: 169.254.0.0/16 (incl. 169.254.169.254), fe80::/10
  - Reserved / unspecified: 0.0.0.0, ::
  - IPv6 unique-local: fc00::/7
  - IPv4-mapped IPv6 that resolve to private IPv4

Usage::

    from kazma_core.security.ssrf import validate_url, SSRFError

    try:
        validate_url("http://169.254.169.254/latest/meta-data/")
    except SSRFError as exc:
        print(exc)  # "Blocked URL ..."

The resolver walks **all** A/AAAA records and blocks if **any** resolved IP is
non-public. That defeats a *split* record set (one public A record alongside an
internal one) — it does **not** defeat true DNS rebinding, where a low-TTL
record answers public to this validator and private to the socket that connects
a moment later (audit F-08/O7: this docstring previously claimed otherwise).

Two rules follow from that limitation:

* Validate the URL, then connect **immediately**. The wider the gap, the wider
  the rebinding window.
* Never pair ``validate_url`` with ``follow_redirects=True``. The guard sees
  only the URL you passed it; use :func:`resolve_redirects` to validate every
  hop, then fetch the final URL with redirects disabled.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SSRFError(ValueError):
    """Raised when a URL resolves to a disallowed (private/internal) host."""


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """Return *True* if *ip* is private, loopback, link-local, reserved, or unspecified.

    This covers all the ranges called out in the feature spec plus a few
    closely related ones (unspecified, multicast) that no outbound fetch
    should ever target.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        # Map IPv4-in-IPv6 forms (rare from ip_address, but be safe).
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        )
    # IPv6
    # is_private on IPv6 covers ::1, fc00::/7, fe80::/10, and more.
    # We also flag IPv4-mapped addresses that point to private IPv4.
    if getattr(ip, "ipv4_mapped", None) is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def _resolve_host_ips(host: str) -> list[str]:
    """Resolve *host* to a list of IP address strings (A + AAAA records).

    Returns an empty list when resolution fails so callers can decide
    whether to treat an unresolvable host as blocked.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and len(sockaddr) >= 1:
            ip = sockaddr[0]
            if ip not in ips:
                ips.append(ip)
    return ips


def validate_url(url: str, *, block_unresolved: bool = False, allow_private: bool = False) -> tuple[str, ...]:
    """Validate that *url* points at a public, externally reachable host.

    Args:
        url: The URL to validate (must use ``http`` or ``https``).
        block_unresolved: When *True*, hosts that fail DNS resolution are
            rejected. When *False* (default) an unresolvable host is allowed
            so the eventual HTTP request can surface a normal connection
            error instead of an SSRF block.
        allow_private: When *True*, private/loopback/local addresses are
            permitted (e.g. for LM Studio, Ollama, or other local providers
            the user explicitly configured). Defaults to *False*.

    Raises:
        SSRFError: If the URL scheme is not http/https, the hostname is a
            bare private IP, the hostname is ``localhost``/``0.0.0.0``, any
            resolved IP is private/loopback/link-local/reserved, or (when
            ``block_unresolved`` is set) the host cannot be resolved.
        ValueError: If the URL is empty or has no hostname.
    """
    if not url or not url.strip():
        raise ValueError("No URL provided.")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(
            f"Blocked URL '{url}': only http and https schemes are allowed."
        )

    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no hostname: {url}")

    host_lower = host.lower()

    # When private addresses are explicitly permitted, skip the checks.
    if allow_private:
        return ()

    # Fast textual rejects for common internal hostnames.
    if host_lower in ("localhost", "0.0.0.0", "::1"):
        raise SSRFError(f"Blocked URL '{url}': hostname '{host}' is not allowed.")
    if host_lower.endswith(".local") or host_lower.endswith(".internal"):
        raise SSRFError(
            f"Blocked URL '{url}': hostname '{host}' is not publicly resolvable."
        )

    # If the host is already a literal IP, validate it directly without DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP; fall through to DNS resolution below.
        pass
    else:
        if _is_blocked_ip(ip):
            raise SSRFError(
                f"Blocked URL '{url}': host IP '{ip}' is private or reserved."
            )
        return (str(ip),)  # Literal public IP — allowed.

    # Resolve the hostname and check every returned address. Blocking on
    # ANY private IP mitigates DNS-rebinding where the first A record is
    # public and later ones are internal.
    resolved_ips = _resolve_host_ips(host)
    if not resolved_ips:
        if block_unresolved:
            raise SSRFError(
                f"Blocked URL '{url}': hostname '{host}' could not be resolved."
            )
        # Allow the request to proceed; the HTTP client will fail with a
        # normal connection error if the host is genuinely unreachable.
        logger.debug("SSRF: host '%s' did not resolve; allowing request", host)
        return ()

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Unexpected address family — be conservative and block.
            raise SSRFError(
                f"Blocked URL '{url}': host resolved to unparseable address '{ip_str}'."
            )
        if _is_blocked_ip(ip):
            raise SSRFError(
                f"Blocked URL '{url}': host '{host}' resolves to private/"
                f"reserved address '{ip}'."
            )
    return tuple(resolved_ips)


def peer_ip_from_response(response: Any) -> str | None:
    """Return the connected peer IP from an httpx response, if exposed.

    Uses ``response.extensions["network_stream"].get_extra_info("peername")``
    (httpcore). Returns None when the stream is missing (mocks, some proxies).
    """
    ext = getattr(response, "extensions", None) or {}
    stream = ext.get("network_stream") or ext.get("stream")
    if stream is None:
        return None
    getter = getattr(stream, "get_extra_info", None)
    if not callable(getter):
        return None
    try:
        peer = getter("peername")
    except Exception:
        return None
    if isinstance(peer, tuple) and peer:
        ip = str(peer[0])
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        return ip
    if isinstance(peer, str) and peer:
        return peer
    return None


def assert_peer_public(
    response: Any,
    *,
    url: str,
    validated_ips: tuple[str, ...] | set[str] | None = None,
    via_proxy: bool = False,
) -> None:
    """Abort if the connected peer is private or not in *validated_ips* (H-7).

    When *via_proxy* is True the peer is the proxy, not the origin — only
    the private-range check runs. Missing peer info is a no-op (cannot
    inspect) rather than a fail-open skip of validate_url, which already ran.
    """
    peer = peer_ip_from_response(response)
    if not peer:
        return
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        raise SSRFError(
            f"Blocked URL '{url}': connected peer '{peer}' is unparseable."
        ) from None
    if _is_blocked_ip(ip):
        raise SSRFError(
            f"Blocked URL '{url}': connected peer '{peer}' is private or reserved."
        )
    if via_proxy or not validated_ips:
        return
    allowed = {str(a) for a in validated_ips}
    if peer not in allowed:
        raise SSRFError(
            f"Blocked URL '{url}': connected peer '{peer}' was not in the "
            f"validated address set (DNS rebinding)."
        )


def is_url_safe(url: str) -> bool:
    """Return *True* when :func:`validate_url` accepts *url*.

    Convenience wrapper for callers that want a boolean instead of an
    exception (mirrors the old ``_is_safe_url`` helper signature).
    """
    try:
        validate_url(url)
    except (SSRFError, ValueError):
        return False
    return True


#: Maximum redirect hops followed by :func:`resolve_redirects`.
MAX_REDIRECT_HOPS = 5

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


async def resolve_redirects(
    client: Any,
    url: str,
    *,
    max_hops: int = MAX_REDIRECT_HOPS,
    **request_kwargs: Any,
) -> str:
    """Walk a redirect chain, validating **every** hop, and return the final URL.

    ``follow_redirects=True`` is unsafe on an SSRF-guarded fetch: the guard
    runs once against the URL the caller supplied, and an attacker-controlled
    public URL can then 302 to ``169.254.169.254`` or an internal service,
    which httpx follows without a second check (audit F-08).

    Use this to resolve the destination first, then issue the real request
    against the returned URL with ``follow_redirects=False``.

    Args:
        client: An ``httpx.AsyncClient``.
        url: Starting URL. Validated before the first request.
        max_hops: Redirect budget; exceeding it raises :class:`SSRFError`.
        **request_kwargs: Passed to each ``client.request`` call.

    Returns:
        The final, validated URL.

    Raises:
        SSRFError: if any hop resolves to a blocked address, or the chain is
            longer than *max_hops*.
    """
    import httpx

    validate_url(url)
    current = url
    for _ in range(max_hops):
        resp = await client.request(
            "HEAD", current, follow_redirects=False, **request_kwargs
        )
        if resp.status_code not in _REDIRECT_STATUSES:
            return current
        location = resp.headers.get("location", "")
        if not location:
            return current
        current = str(httpx.URL(current).join(location))
        validate_url(current)  # every hop, not just the first
    raise SSRFError(
        f"Blocked URL '{url}': more than {max_hops} redirects."
    )


__all__: list[str] = [
    "SSRFError",
    "validate_url",
    "is_url_safe",
    "resolve_redirects",
    "MAX_REDIRECT_HOPS",
    "peer_ip_from_response",
    "assert_peer_public",
]
