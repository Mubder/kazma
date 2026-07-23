"""DALL-E backend via the OpenAI Images API (v1/images/generations)."""

from __future__ import annotations

import base64
import os

from kazma_core.tools.image_backends.base import BackendError

# Map pixel dimensions → the sizes DALL-E 3 accepts. DALL-E 3 supports only
# 1024x1024, 1024x1792, 1792x1024. DALL-E 2 supports 256/512/1024 square.
_DALLE3_SIZES = {(1024, 1024), (1024, 1792), (1792, 1024)}


class DallEBackend:
    """OpenAI DALL-E image generation."""

    name = "dall-e"

    def __init__(self, api_key: str | None = None, model: str = "dall-e-3") -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._model = model

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        if not self._api_key:
            raise BackendError(
                "No OpenAI API key. Set OPENAI_API_KEY or configure the openai provider."
            )
        import httpx

        size = self._resolve_size(width, height)
        async with httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=120.0,
        ) as client:
            resp = await client.post(
                "/images/generations",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "response_format": "b64_json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            b64 = data["data"][0].get("b64_json")
            if not b64:
                # response_format=url fallback — fetch the URL.
                url = data["data"][0].get("url")
                if url:
                    img = await client.get(url)
                    img.raise_for_status()
                    return img.content
                raise BackendError("DALL-E returned no image data")
            return base64.b64decode(b64)

    def _resolve_size(self, width: int, height: int) -> str:
        """Snap requested dims to the nearest DALL-E-supported size."""
        if (width, height) in _DALLE3_SIZES:
            return f"{width}x{height}"
        if width > height:
            return "1792x1024"
        if height > width:
            return "1024x1792"
        return "1024x1024"
