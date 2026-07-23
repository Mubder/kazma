"""Image-backend router — provider resolution + backend instantiation.

Mirrors the email_manager router pattern: ``resolve_provider("auto")``
returns the first credentialed backend, else ``"pollinations"`` (the
keyless default). ``get_backend(provider)`` returns an instance, never
raising — it falls back to :class:`PollinationsBackend` when a chosen
backend's credentials are missing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def detect_available_provider() -> str:
    """Return first credentialed provider, else the keyless default."""
    if os.getenv("OPENAI_API_KEY"):
        return "dall-e"
    if os.getenv("STABILITY_API_KEY"):
        return "stability"
    if os.getenv("FAL_KEY"):
        return "flux"
    return "pollinations"


def resolve_provider(provider: str | None = None) -> str:
    """Normalize a provider request to a concrete backend name."""
    p = (provider or os.getenv("KAZMA_IMAGE_PROVIDER", "auto") or "auto").strip().lower()
    if p in ("", "auto"):
        return detect_available_provider()
    return p


def get_backend(provider: str | None = None) -> Any:
    """Return an image-backend instance for *provider*.

    Falls back to :class:`PollinationsBackend` when a requested backend
    lacks credentials, so callers always get a usable backend.
    """
    name = resolve_provider(provider)

    if name == "dall-e":
        if os.getenv("OPENAI_API_KEY"):
            from kazma_core.tools.image_backends.dall_e import DallEBackend

            return DallEBackend()
        logger.info("[image] dall-e requested but no OPENAI_API_KEY — using pollinations")
        name = "pollinations"

    if name == "stability":
        if os.getenv("STABILITY_API_KEY"):
            from kazma_core.tools.image_backends.stability import StabilityBackend

            return StabilityBackend()
        logger.info("[image] stability requested but no STABILITY_API_KEY — using pollinations")
        name = "pollinations"

    if name == "flux":
        if os.getenv("FAL_KEY"):
            from kazma_core.tools.image_backends.flux import FluxBackend

            return FluxBackend()
        logger.info("[image] flux requested but no FAL_KEY — using pollinations")
        name = "pollinations"

    # Default / fallback: pollinations (always available, keyless)
    from kazma_core.tools.image_backends.pollinations import PollinationsBackend

    return PollinationsBackend()
