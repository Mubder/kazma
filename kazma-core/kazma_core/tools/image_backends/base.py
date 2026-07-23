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
