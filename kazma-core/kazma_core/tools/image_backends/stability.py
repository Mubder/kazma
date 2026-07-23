"""Stability AI backend (Stable Diffusion) via the Stability Images API."""

from __future__ import annotations

import os

from kazma_core.tools.image_backends.base import BackendError

# Stability engine id for SDXL 1.0. Users can override via env.
_DEFAULT_ENGINE = os.getenv("STABILITY_ENGINE_ID", "stable-diffusion-xl-1024-v1-0")
_API_BASE = "https://api.stability.ai"


class StabilityBackend:
    """Stability AI image generation (Stable Diffusion XL)."""

    name = "stability"

    def __init__(self, api_key: str | None = None, engine_id: str | None = None) -> None:
        self._api_key = api_key or os.getenv("STABILITY_API_KEY", "")
        self._engine_id = engine_id or _DEFAULT_ENGINE

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        if not self._api_key:
            raise BackendError(
                "No Stability API key. Set STABILITY_API_KEY."
            )
        import base64
        import httpx

        if width > height:
            bucket_w, bucket_h = 1024, 768
        elif height > width:
            bucket_w, bucket_h = 768, 1024
        else:
            bucket_w, bucket_h = 1024, 1024

        async with httpx.AsyncClient(
            base_url=_API_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=120.0,
        ) as client:
            resp = await client.post(
                f"/v1/generation/{self._engine_id}/text-to-image",
                json={
                    "text_prompts": [{"text": prompt, "weight": 1.0}],
                    "cfg_scale": 7,
                    "width": bucket_w,
                    "height": bucket_h,
                    "samples": 1,
                    "step": 30,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            artifacts = data.get("artifacts") or []
            if not artifacts:
                raise BackendError("Stability returned no artifacts")
            b64 = artifacts[0].get("base64")
            if not b64:
                raise BackendError("Stability artifact had no base64 payload")
            return base64.b64decode(b64)
