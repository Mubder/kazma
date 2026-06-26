"""Vision analysis tool — Analyze images using a vision-capable LLM.

Sends an image (local file or URL) to the configured LLM's vision endpoint
and returns the model's text description / analysis.

Supported formats: PNG, JPEG, WebP, GIF.
Large images (>20 MB) are automatically downscaled before sending.

Usage:
    from kazma_core.tools.vision_analyze import analyze_image
    result = await analyze_image("/path/to/photo.jpg", question="What's in this image?")
    result = await analyze_image("https://example.com/cat.png")
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

SUPPORTED_FORMATS: set[str] = {"png", "jpeg", "jpg", "webp", "gif"}
MIME_MAP: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB — images above this are resized
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB — hard cap for URL downloads
DEFAULT_QUESTION = "Describe this image in detail."
RESIZE_MAX_DIMENSION = 2048  # px — longest side after downscale
REQUEST_TIMEOUT = 60.0


# ── Helpers ────────────────────────────────────────────────────────────


def _detect_format(path: Path) -> str | None:
    """Detect image format from file extension. Returns lowercase ext or None."""
    ext = path.suffix.lstrip(".").lower()
    if ext in SUPPORTED_FORMATS:
        return ext
    return None


def _ext_to_mime(ext: str) -> str:
    """Map a file extension to a MIME type."""
    return MIME_MAP.get(ext, "image/png")


def _resize_image(image_bytes: bytes, max_dim: int = RESIZE_MAX_DIMENSION) -> bytes:
    """Downscale an image so its longest side is at most *max_dim* pixels.

    Returns the (possibly resized) image bytes as PNG.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed — returning original image bytes")
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes))

    # Convert palette / RGBA modes to RGB for broadest API compat
    if img.mode in ("P", "LA", "PA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        # Composite on white background for JPEG-friendly output
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Only resize if needed
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        logger.info("Resized image from %dx%d to %dx%d", w, h, *new_size)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _build_data_uri(image_bytes: bytes, mime: str) -> str:
    """Base64-encode *image_bytes* and return a ``data:`` URI."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _load_local_image(path: Path) -> tuple[bytes, str]:
    """Read a local image file and return ``(raw_bytes, mime_type)``.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the format is unsupported.
    """
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    fmt = _detect_format(path)
    if fmt is None:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise ValueError(
            f"Unsupported image format: '{path.suffix}'. "
            f"Supported: {supported}"
        )

    raw = path.read_bytes()
    mime = _ext_to_mime(fmt)
    return raw, mime



def _is_safe_url(url: str) -> bool:
    """Check if URL is safe to fetch (no SSRF to private/internal hosts)."""
    import ipaddress
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        except ValueError:
            if host.endswith(".local") or host.endswith(".internal"):
                return False
        if "169.254.169.254" in host or "metadata.google" in host:
            return False
        return True
    except Exception:
        return False


async def _download_image(url: str) -> tuple[bytes, str]:
    """Download an image from *url* and return ``(image_bytes, mime_type)``.

    Raises:
        ValueError: if the download fails or the content type is unsupported.
    """
    if not _is_safe_url(url):
        raise ValueError(f"Blocked potentially unsafe URL: {url}")
    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "KazmaBot/1.0 (vision analyzer)"},
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()

                # Check Content-Length header first (may be absent)
                content_length = int(resp.headers.get("content-length", 0))
                if content_length > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image too large ({content_length / 1_048_576:.1f} MB). "
                        f"Max {MAX_DOWNLOAD_BYTES / 1_048_576:.0f} MB."
                    )

                # Stream-based size check — protects against missing Content-Length
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"Image download exceeds {MAX_DOWNLOAD_BYTES / 1_048_576:.0f} MB limit "
                            f"(downloaded {total_bytes / 1_048_576:.1f} MB so far)."
                        )
                    chunks.append(chunk)
                image_bytes = b"".join(chunks)

            # Detect MIME from Content-Type header, fallback to PNG
            content_type = resp.headers.get("content-type", "image/png")
            mime = content_type.split(";")[0].strip().lower()
            if mime not in MIME_MAP.values():
                mime = "image/png"  # best-effort fallback

            return image_bytes, mime

    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"Failed to download image: HTTP {exc.response.status_code}"
        ) from exc
    except httpx.ConnectError as exc:
        raise ValueError(
            f"Could not connect to {url}. Check the URL and your connection."
        ) from exc
    except httpx.TimeoutException as exc:
        raise ValueError(f"Request to {url} timed out.") from exc


def _build_vision_messages(
    data_uri: str,
    question: str,
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible vision chat messages."""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                },
                {
                    "type": "text",
                    "text": question,
                },
            ],
        },
    ]


def _get_llm_provider():
    """Return an LLMProvider configured from the environment / kazma.yaml.

    Returns None if the provider module or config is unavailable.
    """
    try:
        from kazma_core.llm_provider import LLMConfig, LLMProvider
    except ImportError:
        return None

    # Try loading from ConfigStore (kazma.yaml + DB overrides)
    try:
        from kazma_core.config_store import ConfigStore

        store = ConfigStore()
        llm_cfg = store.get_category("llm") or {}
        config = LLMConfig.from_dict({
            "base_url": llm_cfg.get("llm.base_url", llm_cfg.get("base_url", "")),
            "api_key": llm_cfg.get("llm.api_key", llm_cfg.get("api_key", "")),
            "model": llm_cfg.get("llm.model", llm_cfg.get("model", "")),
        })
    except Exception:
        config = LLMConfig()  # defaults

    return LLMProvider(config=config)


# ── Main entry point ───────────────────────────────────────────────────


async def analyze_image(
    image_path: str,
    question: str | None = None,
) -> str:
    """Analyze an image using a vision-capable LLM.

    Args:
        image_path:  Local file path or HTTP(S) URL to the image.
        question:    What to ask about the image.  If omitted, a general
                     description is requested.

    Returns:
        The model's text analysis of the image, or an error message.
    """
    if not image_path or not image_path.strip():
        return "Error: No image path or URL provided."

    image_path = image_path.strip()
    question = (question or DEFAULT_QUESTION).strip()

    # ── Load image (local or URL) ──────────────────────────────────
    is_url = image_path.startswith(("http://", "https://"))

    try:
        if is_url:
            image_bytes, mime = await _download_image(image_path)
        else:
            path = Path(image_path).expanduser().resolve()
            image_bytes, mime = _load_local_image(path)
    except FileNotFoundError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: Failed to load image — {exc}"

    # ── Resize if needed ───────────────────────────────────────────
    if len(image_bytes) > MAX_IMAGE_BYTES:
        logger.info(
            "Image is %.1f MB (>%d MB) — resizing",
            len(image_bytes) / 1_048_576,
            MAX_IMAGE_BYTES / 1_048_576,
        )
        image_bytes = _resize_image(image_bytes)
        mime = "image/png"  # _resize_image always outputs PNG

    # ── Build data URI ─────────────────────────────────────────────
    data_uri = _build_data_uri(image_bytes, mime)

    # ── Get LLM provider ───────────────────────────────────────────
    provider = _get_llm_provider()
    if provider is None:
        return (
            "Error: Vision analysis unavailable — LLM provider module not found. "
            "Ensure kazma_core.llm_provider is installed."
        )

    # ── Call the vision model ──────────────────────────────────────
    messages = _build_vision_messages(data_uri, question)

    try:
        response = await provider.chat(messages)
    except Exception as exc:
        exc_name = type(exc).__name__
        if "vision" in str(exc).lower() or "image" in str(exc).lower():
            return (
                f"Error: The configured model does not appear to support vision. "
                f"Switch to a vision-capable model (e.g. gpt-4o, gpt-4o-mini, "
                f"claude-3.5-sonnet). Details: {exc}"
            )
        return f"Error: LLM call failed — {exc}"
    finally:
        await provider.close()

    content = (response.content or "").strip()
    if not content:
        return (
            "Error: The model returned an empty response. "
            "It may not support image inputs — try a vision-capable model."
        )

    return content