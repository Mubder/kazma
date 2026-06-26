"""Image generation tool — Generate AI images via pollinations.ai.

No API key required. Uses the public pollinations.ai endpoint:
    https://image.pollinations.ai/prompt/{encoded_prompt}?width={w}&height={h}

Images are saved to kazma-data/images/ and the file path is returned.

Usage:
    from kazma_core.tools.image_gen import generate_image
    result = await generate_image("a cat wearing sunglasses", width=512, height=512)
"""

from __future__ import annotations

import re
import time
import urllib.parse
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────

MAX_PROMPT_CHARS = 1000
MAX_WIDTH = 1792
MAX_HEIGHT = 1024
MIN_DIMENSION = 64
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024
IMAGE_DIR = Path("kazma-data/images")
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"


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
    # httpx-specific errors
    if "ConnectError" in exc_name:
        return "Error: Could not connect to image.pollinations.ai. Check your internet connection."
    if "TimeoutException" in exc_name:
        return "Error: Request to image.pollinations.ai timed out."
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
    description: str | None = None,
) -> str:
    """Generate an AI image via pollinations.ai and save it locally.

    Args:
        prompt:      Text description of the image to generate (max 1000 chars).
        width:       Image width in pixels (64–1792, default 1024).
        height:      Image height in pixels (64–1024, default 1024).
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

    # ── Build URL ───────────────────────────────────────────────────
    encoded = urllib.parse.quote(prompt, safe="")
    url = f"{POLLINATIONS_URL}{encoded}?width={width}&height={height}"

    # ── Determine description ───────────────────────────────────────
    desc = description if description else prompt[:120]

    # ── Fetch image ─────────────────────────────────────────────────
    try:
        import httpx
    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": "KazmaBot/1.0 (image generator)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            image_bytes = response.content
    except Exception as exc:
        return _friendly_error(exc)

    # ── Save to disk ────────────────────────────────────────────────
    timestamp = int(time.time())
    slug = _slugify(prompt)
    filename = f"{timestamp}_{slug}.png"
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
        f"  Dimensions:  {width}x{height}\n"
        f"  Size:        {size_kb:.1f} KB\n"
        f"  Saved to:    {filepath}"
    )
