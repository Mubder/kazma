"""Image generation tool — multi-provider AI image generation.

Backends are selected via the router in ``image_backends/router.py``:

* ``pollinations`` (default, keyless) — https://pollinations.ai
* ``dall-e``   — OpenAI DALL-E 3 (needs OPENAI_API_KEY)
* ``stability`` — Stability SDXL (needs STABILITY_API_KEY)
* ``flux``     — Flux via FAL.ai (needs FAL_KEY)

``provider="auto"`` picks the first credentialed backend, else pollinations.
Images are saved to kazma-data/images/ and the file path is returned.

Usage:
    from kazma_core.tools.image_gen import generate_image
    result = await generate_image("a cat wearing sunglasses", width=512, height=512)
    result = await generate_image("a sunset", provider="dall-e")
"""

from __future__ import annotations

import re
import time
from pathlib import Path

__all__ = ["DEFAULT_HEIGHT", "DEFAULT_WIDTH", "IMAGE_DIR", "MAX_HEIGHT", "MAX_PROMPT_CHARS", "MAX_WIDTH", "MIN_DIMENSION", "generate_image"]

# ── Constants ──────────────────────────────────────────────────────────

MAX_PROMPT_CHARS = 1000
MAX_WIDTH = 1792
MAX_HEIGHT = 1024
MIN_DIMENSION = 64
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024
IMAGE_DIR = Path("kazma-data/images")


def _slugify(text: str, max_len: int = 60) -> str:
    """Create a filesystem-safe slug from prompt text."""
    slug = text.lower().strip()
    # Keep only alphanumeric, spaces, hyphens
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    # Collapse whitespace and hyphens
    slug = re.sub(r"[\s-]+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    if not slug:
        slug = "image"
    return slug[:max_len]


def _validate_dimensions(width: int, height: int) -> str | None:
    """Validate width/height. Returns error string or None if valid."""
    if not isinstance(width, int) or not isinstance(height, int):
        return f"Error: width and height must be integers, got {type(width).__name__}/{type(height).__name__}"
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        return f"Error: Dimensions must be at least {MIN_DIMENSION}x{MIN_DIMENSION}, got {width}x{height}"
    if width > MAX_WIDTH:
        return f"Error: Width exceeds maximum of {MAX_WIDTH}, got {width}"
    if height > MAX_HEIGHT:
        return f"Error: Height exceeds maximum of {MAX_HEIGHT}, got {height}"
    return None


def _friendly_error(exc: Exception) -> str:
    """Map low-level exceptions to user-friendly messages."""
    exc_name = type(exc).__name__
    if "ConnectError" in exc_name:
        return "Error: Could not connect to the image provider. Check your internet connection."
    if "TimeoutException" in exc_name:
        return "Error: Image generation request timed out."
    if "HTTPStatusError" in exc_name:
        status = getattr(exc, "response", None)
        code = getattr(status, "status_code", "unknown")
        return f"Error: Image generation server returned HTTP {code}."
    if isinstance(exc, OSError):
        return f"Error: Network or file error — {exc}"
    return f"Error: Image generation failed — {exc}"


async def generate_image(
    prompt: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    *,
    provider: str = "auto",
    description: str | None = None,
) -> str:
    """Generate an AI image and save it locally.

    Args:
        prompt:      Text description of the image to generate (max 1000 chars).
        width:       Image width in pixels (64–1792, default 1024).
        height:      Image height in pixels (64–1024, default 1024).
        provider:    Image backend — ``"auto"`` (first credentialed),
                     ``"pollinations"`` (keyless default), ``"dall-e"``,
                     ``"stability"``, or ``"flux"``.
        description: Optional human-readable label for what was generated.
                     If omitted, derived from the prompt.

    Returns:
        Success message with file path and description, or a friendly error.
    """
    # ── Validate prompt ─────────────────────────────────────────────
    if not prompt or not prompt.strip():
        return "Error: No prompt provided. Please describe the image you want to generate."

    prompt = prompt.strip()

    if len(prompt) > MAX_PROMPT_CHARS:
        return f"Error: Prompt too long ({len(prompt)} chars). Maximum is {MAX_PROMPT_CHARS}."

    # ── Validate dimensions ─────────────────────────────────────────
    dim_error = _validate_dimensions(width, height)
    if dim_error:
        return dim_error

    # ── Determine description ───────────────────────────────────────
    desc = description if description else prompt[:120]

    # ── Resolve backend ─────────────────────────────────────────────
    try:
        from kazma_core.tools.image_backends.router import get_backend, resolve_provider

        backend = get_backend(provider)
        chosen = resolve_provider(provider)
    except Exception as exc:  # noqa: BLE001
        return f"Error: Could not initialise image backend — {exc}"

    # ── Generate ────────────────────────────────────────────────────
    try:
        image_bytes = await backend.generate(prompt, width, height)
    except Exception as exc:
        return _friendly_error(exc)

    if not image_bytes:
        return "Error: Image backend returned no image data."

    # ── Save to disk ────────────────────────────────────────────────
    timestamp = int(time.time())
    slug = _slugify(prompt)
    ext = "png"
    filename = f"{timestamp}_{slug}.{ext}"
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = IMAGE_DIR / filename

    try:
        filepath.write_bytes(image_bytes)
    except OSError as exc:
        return f"Error: Could not save image to {filepath} — {exc}"

    size_kb = len(image_bytes) / 1024
    return (
        f"Image generated successfully.\n"
        f"  Description: {desc}\n"
        f"  Provider:    {chosen}\n"
        f"  Dimensions:  {width}x{height}\n"
        f"  Size:        {size_kb:.1f} KB\n"
        f"  Saved to:    {filepath}"
    )
