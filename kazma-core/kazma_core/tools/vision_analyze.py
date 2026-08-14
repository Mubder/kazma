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

import httpx

__all__ = ["DEFAULT_QUESTION", "MAX_DOWNLOAD_BYTES", "MAX_IMAGE_BYTES", "MIME_MAP", "REQUEST_TIMEOUT", "RESIZE_MAX_DIMENSION", "SUPPORTED_FORMATS", "analyze_image"]

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
    """Check if URL is safe to fetch (no SSRF to private/internal hosts).

    Resolves the hostname and blocks if **any** resolved IP is private,
    loopback, link-local (incl. cloud metadata 169.254.169.254), or
    reserved. Also rejects ``localhost``, ``0.0.0.0``, ``::1``, and
    ``.local``/``.internal`` hostnames.
    """
    try:
        from kazma_core.security.ssrf import validate_url

        validate_url(url)
    except Exception:
        return False
    return True


async def _download_image(url: str) -> tuple[bytes, str]:
    """Download an image from *url* and return ``(image_bytes, mime_type)``.

    Raises:
        ValueError: if the download fails or the content type is unsupported.
    """
    if not _is_safe_url(url):
        raise ValueError(f"Blocked potentially unsafe URL: {url}")
    from kazma_core.http_pool import get_http_client

    try:
        client = get_http_client()
        async with client.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "KazmaBot/1.0 (vision analyzer)"},
        ) as resp:
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
    """Return a vision-capable LLM provider, or ``None`` with a reason.

    Selection order (see :mod:`kazma_core.vision_capability`):

    1. The *active* model if it is vision-capable.
    2. Any other configured, vision-capable model (one-off client; the
       active profile is not changed).
    3. ``None`` — no vision model available; the caller surfaces a clear,
       actionable error *before* making any API call.

    Returns ``(provider, model_id, reason)``. When ``provider`` is ``None``
    the reason explains why (``"no-vision-model"``, ``"registry-unavailable"``).
    """
    try:
        from kazma_core.model_registry import get_model_registry
        from kazma_core.vision_capability import get_vision_client

        registry = get_model_registry()
        provider, model_id, reason = get_vision_client(registry)
        return provider, model_id, reason
    except Exception as exc:  # noqa: BLE001 — registry/imports are best-effort
        logger.debug("[vision_analyze] provider lookup failed: %s", exc)
        return None, None, "registry-unavailable"


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
            # Honor the same path-policy SoT as file_read/file_write so an
            # operator-granted extra root or a session path grant applies to
            # image analysis too. Previously this checked only the raw
            # workspace + allow_absolute flag, so a granted path was rejected
            # here while file_read accepted it (inconsistent UX) (audit finding).
            try:
                from kazma_core.workspace.path_policy import check_path_access, denied_message

                _access = check_path_access(str(path), "read")
                if not _access.allowed:
                    return denied_message(str(path), "read", result=_access)
            except Exception:
                # If the workspace module is unavailable, deny by default (fail-closed)
                return "Safety: workspace module unavailable — image access denied."
            
            image_bytes, mime = _load_local_image(path)
    except FileNotFoundError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.debug("[vision_analyze] Failed to load image: %s", exc, exc_info=True)
        return "Error: Failed to load image. Check the path and format."

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
    provider, chosen_model, reason = _get_llm_provider()
    if provider is None:
        # Fail BEFORE making any API call. Give a clear, actionable message
        # so the agent/user knows exactly what to fix instead of a cryptic
        # "400 unknown variant image_url" from the active text-only model.
        if reason == "no-vision-model":
            # chosen_model holds the active model id in the no-vision-model
            # case (returned by get_vision_client). Use it directly rather
            # than re-querying the registry.
            active_note = f" (active model: {chosen_model})" if chosen_model else ""
            return (
                "Error: Image analysis needs a vision-capable model, but the active"
                f" model{active_note} cannot process images, and no vision-capable "
                "model is configured. Add one in Settings > Models (e.g. gpt-4o, "
                "gpt-4o-mini, Gemini, or Claude 3.5 Sonnet) and it will be used "
                "automatically."
            )
        return (
            "Error: Vision analysis unavailable — LLM provider module not found. "
            "Ensure kazma_core.llm_provider is installed."
        )

    logger.info(
        "[vision_analyze] using model=%s (reason=%s)", chosen_model, reason
    )

    # ── Call the vision model ──────────────────────────────────────
    messages = _build_vision_messages(data_uri, question)

    try:
        response = await provider.chat(messages)
    except Exception as exc:
        exc_name = type(exc).__name__
        if "vision" in str(exc).lower() or "image" in str(exc).lower():
            return (
                "Error: The configured model does not appear to support vision. "
                "Switch to a vision-capable model (e.g. gpt-4o, gpt-4o-mini, "
                "claude-3.5-sonnet)."
            )
        logger.debug("[vision_analyze] LLM call failed: %s", exc, exc_info=True)
        return "Error: LLM call failed. Please check your model configuration."
    finally:
        # Close only ONE-OFF clients. reason == "active-model" is the shared
        # registry singleton the whole agent uses — closing it killed every
        # concurrent/next LLM call on that client ("client closed") and
        # forced constant re-creation.
        if reason != "active-model":
            try:
                await provider.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

    content = (response.content or "").strip()
    if not content:
        return (
            "Error: The model returned an empty response. "
            "It may not support image inputs — try a vision-capable model."
        )

    return content
