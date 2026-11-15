"""Image-generation backend protocol + shared types.

Each backend implements ``generate(prompt, width, height) -> bytes`` (raw
PNG/JPEG bytes). The router in ``router.py`` selects a backend by provider
name or auto-detects the first credentialed one. ``PollinationsBackend`` is
the always-available fallback (no API key required).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ImageBackend(Protocol):
    """Structural interface for image-generation backends."""

    name: str

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image and return its raw bytes."""
        ...


class BackendError(Exception):
    """Raised when a backend cannot produce an image (auth, network, quota)."""


#: The largest generated image Kazma downloads.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


async def fetch_generated_image(url: str, *, timeout: float = 60.0) -> bytes:
    """Download a generated image on the pinned path, with no credentials.

    The address is the provider's answer (DALL-E's ``url``, FAL's image
    ``url``) or a redirect Pollinations chooses, so it goes through
    ``web_acquire.http.fetch_public`` (AGENTS.md §32): every hop checked
    public, the connection pinned to the checked address, the body bounded.
    No ``Authorization`` header goes with it: a provider's key is sent only
    to that provider's own API host, never to the host its answer names.
    """
    from kazma_core.web_acquire.http import fetch_public

    got = await fetch_public(url, max_bytes=MAX_IMAGE_BYTES, timeout=timeout)
    status = got.response.status_code
    if status >= 400:
        raise BackendError(f"Image generation server returned HTTP {status}.")
    if got.truncated:
        raise BackendError(
            f"The generated image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
        )
    return got.response.content
