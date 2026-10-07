"""Bounded adapter downloads: stop reading before a body exceeds its cap."""
from __future__ import annotations

import httpx


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
