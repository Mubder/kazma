"""Pollinations.ai backend — free, no API key required."""

from __future__ import annotations

import urllib.parse

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"


class PollinationsBackend:
    """Pollinations.ai image generation (public, keyless)."""

    name = "pollinations"

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        import httpx

        encoded = urllib.parse.quote(prompt, safe="")
        url = f"{POLLINATIONS_URL}{encoded}?width={width}&height={height}"
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": "KazmaBot/1.0 (image generator)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
