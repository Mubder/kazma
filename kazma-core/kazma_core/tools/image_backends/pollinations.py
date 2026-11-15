"""Pollinations.ai backend — free, no API key required."""

from __future__ import annotations

import urllib.parse

from kazma_core.tools.image_backends.base import fetch_generated_image

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"


class PollinationsBackend:
    """Pollinations.ai image generation (public, keyless)."""

    name = "pollinations"

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        encoded = urllib.parse.quote(prompt, safe="")
        url = f"{POLLINATIONS_URL}{encoded}?width={width}&height={height}"
        # Pollinations answers with redirects; each hop is checked and pinned
        # (it followed them unchecked until 2026-11-15).
        return await fetch_generated_image(url, timeout=60.0)
