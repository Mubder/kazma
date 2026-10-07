"""Bounded adapter downloads: stop reading before a body exceeds its cap."""
from __future__ import annotations

import httpx


async def public_attachment_download(url: str, *, slack_token: str = "", max_bytes: int = 20 * 1024 * 1024) -> bytes:
    """Fetch a public URL with pinned DNS, bounded bytes and vetted redirects.

    The Slack credential is sent only to HTTPS hosts controlled by Slack.
    A fresh client avoids carrying a platform client's default credentials to
    an arbitrary attachment host. Redirects never inherit authorization.
    """
    if max_bytes < 1:
        raise ValueError("Download size limit must be positive")
    import asyncio
    from urllib.parse import urljoin, urlsplit

    from kazma_core.security.ssrf import assert_peer_public, validate_url
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport
    from kazma_core.http_tls import shared_ssl_context

    pins: dict[str, str] = {}
    transport = PinHostAsyncTransport(pins, verify=shared_ssl_context())
    async with httpx.AsyncClient(transport=transport, verify=shared_ssl_context(), trust_env=False, follow_redirects=False, timeout=30) as client:
        current = url
        for hop in range(4):
            target = urlsplit(current)
            if target.username is not None or target.password is not None:
                raise ValueError("Attachment URLs cannot carry credentials")
            ips = await asyncio.to_thread(validate_url, current, block_unresolved=True)
            if not ips:
                raise ValueError("Attachment host has no verified public address")
            host = (target.hostname or "").lower()
            pins[host] = ips[0]
            headers = {"Accept-Encoding": "identity"}
            if hop == 0 and slack_token and target.scheme == "https" and (host == "slack.com" or host.endswith(".slack.com")):
                headers["Authorization"] = f"Bearer {slack_token}"
            async with client.stream("GET", current, headers=headers) as response:
                assert_peer_public(response, url=current, validated_ips=ips)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Attachment redirect has no location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise ValueError("Compressed attachment downloads are not supported")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise ValueError("Attachment exceeds the download size limit")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=min(65536, max_bytes + 1)):
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError("Attachment exceeds the download size limit")
                    body.extend(chunk)
                return bytes(body)
    raise ValueError("Attachment URL exceeded its redirect limit")


async def bounded_download(client: httpx.AsyncClient, url: str, max_bytes: int) -> bytes:
    """Stream a fixed adapter URL; redirects and excessive bodies are refused."""
    if max_bytes < 1:
        raise ValueError("Download size limit must be positive")
    async with client.stream(
        "GET", url, follow_redirects=False, headers={"Accept-Encoding": "identity"},
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise ValueError("Compressed attachment downloads are not supported")
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise ValueError("Attachment exceeds the download size limit")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=min(65536, max_bytes + 1)):
            if len(body) + len(chunk) > max_bytes:
                raise ValueError("Attachment exceeds the download size limit")
            body.extend(chunk)
        return bytes(body)
