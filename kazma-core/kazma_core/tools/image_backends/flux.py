"""Flux backend via FAL.ai queue API (no fal-client dependency)."""

from __future__ import annotations

import asyncio
import os

from kazma_core.tools.image_backends.base import BackendError

_MODEL = os.getenv("FAL_FLUX_MODEL", "fal-ai/flux/schnell")


class FluxBackend:
    """Flux image generation hosted on FAL.ai."""

    name = "flux"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FAL_KEY", "")
        self._model = model or _MODEL

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        if not self._api_key:
            raise BackendError("No FAL_KEY set. Get one at https://fal.ai/dashboard/keys.")
        import httpx

        async with httpx.AsyncClient(
            base_url="https://queue.fal.run",
            headers={
                "Authorization": f"Key {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=180.0,
        ) as client:
            # 1. Submit
            sub = await client.post(
                f"/{self._model}",
                json={
                    "prompt": prompt,
                    "image_size": {"width": width, "height": height},
                },
            )
            sub.raise_for_status()
            req_id = sub.json().get("request_id")
            if not req_id:
                raise BackendError("FAL submit returned no request_id")

            # 2. Poll status until COMPLETED/FAILED
            for _ in range(120):
                status = await client.get(f"/{self._model}/requests/{req_id}/status")
                if status.status_code == 200:
                    st = status.json().get("status")
                    if st == "COMPLETED":
                        break
                    if st == "FAILED":
                        raise BackendError("FAL generation failed")
                await asyncio.sleep(1.5)

            # 3. Fetch result
            result = await client.get(f"/{self._model}/requests/{req_id}")
            result.raise_for_status()
            images = (result.json().get("images") or [])
            if not images:
                raise BackendError("FAL returned no images")
            url = images[0].get("url")
            if not url:
                raise BackendError("FAL image had no url")
            img = await client.get(url)
            img.raise_for_status()
            return img.content
