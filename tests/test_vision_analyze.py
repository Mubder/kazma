"""Tests for the vision analysis tool (gw-054).

Covers:
  1. Base64 encoding of local images
  2. Unsupported format rejection
  3. Missing file handling
  4. Analysis with a custom question
  5. Analysis with no question (default prompt)
  6. URL image download path
  7. Large image resize (>20 MB)
  8. Fallback when provider lacks vision support
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.tools.vision_analyze import (
    DEFAULT_QUESTION,
    MAX_DOWNLOAD_BYTES,
    RESIZE_MAX_DIMENSION,
    _build_data_uri,
    _detect_format,
    _download_image,
    _load_local_image,
    _resize_image,
    analyze_image,
)

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_png_bytes(width: int = 8, height: int = 8) -> bytes:
    """Create a minimal valid PNG in memory."""
    try:
        from PIL import Image

        img = Image.new("RGB", (width, height), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Absolute fallback — tiny valid PNG
        return (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 100
        )


def _mock_llm_provider(response_text: str = "A red 8x8 image.", raise_exc=None):
    """Return a mock LLMProvider whose .chat() returns *response_text*."""
    provider = AsyncMock()
    provider.close = AsyncMock()

    if raise_exc:
        provider.chat = AsyncMock(side_effect=raise_exc)
    else:
        resp = MagicMock()
        resp.content = response_text
        resp.tool_calls = []
        resp.usage = {}
        resp.cost_usd = 0.0
        resp.duration_ms = 50.0
        provider.chat = AsyncMock(return_value=resp)

    return provider


# ═══════════════════════════════════════════════════════════════════
# Test 1: test_encode_image_base64
# ═══════════════════════════════════════════════════════════════════


class TestEncodeImageBase64:
    """Encode a local PNG file to a base64 data URI."""

    def test_data_uri_format(self, tmp_path):
        png = _make_png_bytes(4, 4)
        img_file = tmp_path / "test.png"
        img_file.write_bytes(png)

        raw, mime = _load_local_image(img_file)
        data_uri = _build_data_uri(raw, mime)

        assert data_uri.startswith("data:image/png;base64,")
        # Decode the base64 payload back and compare
        b64_part = data_uri.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == raw

    def test_detect_format_recognises_common_types(self):
        for ext in ("png", "jpeg", "jpg", "webp", "gif"):
            assert _detect_format(Path(f"img.{ext}")) == ext

    def test_detect_format_rejects_unknown(self):
        assert _detect_format(Path("img.bmp")) is None
        assert _detect_format(Path("img.tiff")) is None
        assert _detect_format(Path("img.svg")) is None


# ═══════════════════════════════════════════════════════════════════
# Test 2: test_unsupported_format
# ═══════════════════════════════════════════════════════════════════


class TestUnsupportedFormat:
    """Reject files with unsupported extensions."""

    @pytest.mark.asyncio
    async def test_bmp_rejected(self, tmp_path):
        bmp_file = tmp_path / "photo.bmp"
        bmp_file.write_bytes(b"BM" + b"\x00" * 10)

        result = await analyze_image(str(bmp_file))
        assert result.startswith("Error:")
        assert "Unsupported" in result

    @pytest.mark.asyncio
    async def test_no_extension_rejected(self, tmp_path):
        noext = tmp_path / "imagefile"
        noext.write_bytes(b"\x89PNG" + b"\x00" * 10)

        result = await analyze_image(str(noext))
        assert result.startswith("Error:")
        assert "Unsupported" in result


# ═══════════════════════════════════════════════════════════════════
# Test 3: test_image_not_found
# ═══════════════════════════════════════════════════════════════════


class TestImageNotFound:
    """Handle missing local files gracefully."""

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        result = await analyze_image("/tmp/this_file_does_not_exist_9999.png")
        assert result.startswith("Error:")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_directory_instead_of_file(self, tmp_path):
        result = await analyze_image(str(tmp_path))
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_empty_path(self):
        result = await analyze_image("")
        assert result.startswith("Error:")
        assert "No image path" in result


# ═══════════════════════════════════════════════════════════════════
# Test 4: test_analyze_with_question
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeWithQuestion:
    """Send a custom question alongside the image."""

    @pytest.mark.asyncio
    async def test_custom_question_forwarded(self, tmp_path):
        png = _make_png_bytes()
        img_file = tmp_path / "photo.png"
        img_file.write_bytes(png)

        mock_provider = _mock_llm_provider("The image shows a red square.")

        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=mock_provider,
        ):
            result = await analyze_image(
                str(img_file), question="What colour is this?"
            )

        assert result == "The image shows a red square."

        # Verify the question was included in the messages
        call_args = mock_provider.chat.call_args[0][0]
        user_content = call_args[0]["content"]
        text_parts = [p for p in user_content if p.get("type") == "text"]
        assert any("What colour" in p["text"] for p in text_parts)


# ═══════════════════════════════════════════════════════════════════
# Test 5: test_analyze_no_question
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeNoQuestion:
    """Use the default prompt when no question is given."""

    @pytest.mark.asyncio
    async def test_default_question_used(self, tmp_path):
        png = _make_png_bytes()
        img_file = tmp_path / "scene.png"
        img_file.write_bytes(png)

        mock_provider = _mock_llm_provider("A small red image.")

        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=mock_provider,
        ):
            result = await analyze_image(str(img_file))

        assert result == "A small red image."

        call_args = mock_provider.chat.call_args[0][0]
        text_parts = [
            p for p in call_args[0]["content"] if p.get("type") == "text"
        ]
        assert any(DEFAULT_QUESTION in p["text"] for p in text_parts)


# ═══════════════════════════════════════════════════════════════════
# Test 6: test_url_image
# ═══════════════════════════════════════════════════════════════════


class TestUrlImage:
    """Download an image from an HTTP URL and analyse it."""

    @pytest.mark.asyncio
    async def test_url_download_and_analyze(self):
        png_bytes = _make_png_bytes(16, 16)

        # Mock httpx streaming response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "image/png", "content-length": str(len(png_bytes))}
        mock_response.aiter_bytes = MagicMock(
            return_value=aiter_mock([png_bytes])
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
        mock_httpx.ConnectError = type("ConnectError", (Exception,), {})
        mock_httpx.TimeoutException = type("TimeoutException", (Exception,), {})

        mock_provider = _mock_llm_provider("A 16x16 red square from the web.")

        with patch.dict("sys.modules", {"httpx": mock_httpx}), \
             patch("kazma_core.http_pool.get_http_client", return_value=mock_client), \
             patch(
                 "kazma_core.tools.vision_analyze._get_llm_provider",
                 return_value=mock_provider,
             ):
            result = await analyze_image("https://example.com/image.png")

        assert result == "A 16x16 red square from the web."

    @pytest.mark.asyncio
    async def test_url_download_failure(self):
        """Graceful error when URL download fails."""
        mock_httpx = MagicMock()
        mock_client = AsyncMock()

        import httpx as real_httpx

        # Simulate ConnectError on stream context entry
        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=real_httpx.ConnectError("Connection refused")
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        with patch.dict("sys.modules", {"httpx": mock_httpx}), \
             patch("kazma_core.http_pool.get_http_client", return_value=mock_client):
            # Patch the real httpx classes used in the except blocks
            with patch("kazma_core.tools.vision_analyze.httpx", mock_httpx, create=True):
                result = await analyze_image("https://broken.example.com/img.png")

        assert result.startswith("Error:")


# ═══════════════════════════════════════════════════════════════════
# Test 7: test_large_image_resize
# ═══════════════════════════════════════════════════════════════════


class TestLargeImageResize:
    """Images exceeding MAX_IMAGE_BYTES are auto-resized."""

    def test_resize_brings_image_under_limit(self):
        """Create an image whose bytes exceed the limit, then verify resize."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        # Create a large image (4000x4000 RGB ≈ 48 MB uncompressed → likely >20 MB PNG)
        img = Image.new("RGB", (4000, 4000), color=(0, 128, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        large_bytes = buf.getvalue()

        # If the PNG is already under the limit, compress it less
        # (raw pixels would be 48 MB, but PNG compresses well)
        # Instead, test the resize function directly
        resized = _resize_image(large_bytes, max_dim=RESIZE_MAX_DIMENSION)

        from PIL import Image as PILImage

        result_img = PILImage.open(io.BytesIO(resized))
        w, h = result_img.size
        assert max(w, h) <= RESIZE_MAX_DIMENSION

    def test_small_image_not_resized(self):
        """Images already under the limit pass through unchanged (same content)."""
        try:
            from PIL import Image as PILImage
        except ImportError:
            pytest.skip("Pillow not installed")
        png = _make_png_bytes(8, 8)
        # _resize_image still normalises the format, so sizes may differ slightly
        # but dimensions should remain 8x8
        resized = _resize_image(png, max_dim=RESIZE_MAX_DIMENSION)

        result_img = PILImage.open(io.BytesIO(resized))
        assert result_img.size == (8, 8)

    @pytest.mark.asyncio
    async def test_analyze_auto_resizes_large_file(self, tmp_path):
        """analyze_image auto-resizes when file exceeds MAX_IMAGE_BYTES."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")

        # Create a file that pretends to be over the limit by patching the constant
        png = _make_png_bytes(16, 16)
        img_file = tmp_path / "big.png"
        img_file.write_bytes(png)

        mock_provider = _mock_llm_provider("Resized and analysed.")

        # Patch MAX_IMAGE_BYTES to a tiny value so our small file triggers resize
        with patch("kazma_core.tools.vision_analyze.MAX_IMAGE_BYTES", 10), \
             patch(
                 "kazma_core.tools.vision_analyze._get_llm_provider",
                 return_value=mock_provider,
             ):
            result = await analyze_image(str(img_file))

        assert result == "Resized and analysed."
        # Verify the image passed to the LLM was PNG (resize always outputs PNG)
        call_args = mock_provider.chat.call_args[0][0]
        image_url = call_args[0]["content"][0]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")


# ═══════════════════════════════════════════════════════════════════
# Test 8: test_vision_not_available
# ═══════════════════════════════════════════════════════════════════


class TestVisionNotAvailable:
    """Graceful fallback when the provider lacks vision support."""

    @pytest.mark.asyncio
    async def test_provider_returns_empty_response(self, tmp_path):
        """Empty model response triggers a helpful error message."""
        png = _make_png_bytes()
        img_file = tmp_path / "photo.png"
        img_file.write_bytes(png)

        mock_provider = _mock_llm_provider("")  # empty response

        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=mock_provider,
        ):
            result = await analyze_image(str(img_file))

        assert result.startswith("Error:")
        assert "empty" in result.lower() or "vision" in result.lower()

    @pytest.mark.asyncio
    async def test_provider_raises_vision_error(self, tmp_path):
        """Error mentioning 'vision' yields a model-switch suggestion."""
        png = _make_png_bytes()
        img_file = tmp_path / "photo.png"
        img_file.write_bytes(png)

        mock_provider = _mock_llm_provider(
            raise_exc=Exception("model does not support vision")
        )

        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=mock_provider,
        ):
            result = await analyze_image(str(img_file))

        assert result.startswith("Error:")
        assert "vision" in result.lower()

    @pytest.mark.asyncio
    async def test_provider_unavailable(self, tmp_path):
        """No LLM provider at all yields a clear unavailability message."""
        png = _make_png_bytes()
        img_file = tmp_path / "photo.png"
        img_file.write_bytes(png)

        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=None,
        ):
            result = await analyze_image(str(img_file))

        assert result.startswith("Error:")
        assert "unavailable" in result.lower()

# ═══════════════════════════════════════════════════════════════════
# Test 9: Stream-based download size cap (gw-064 BUG 2)
# ═══════════════════════════════════════════════════════════════════


def _make_mock_stream_response(
    data: bytes,
    content_type: str = "image/png",
    content_length: str | None = None,
):
    """Build a mock httpx streaming response."""
    resp = AsyncMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    headers = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length
    resp.headers = headers
    resp.aiter_bytes = MagicMock(
        return_value=aiter_mock([data])
    )
    return resp


async def aiter_mock(chunks):
    """Async iterator yielding byte chunks."""
    for chunk in chunks:
        yield chunk


class TestStreamDownloadSizeCap:
    """Tests for stream-based download size enforcement (gw-064)."""

    @pytest.mark.asyncio
    async def test_content_length_too_large_raises(self):
        """Rejects download when Content-Length exceeds MAX_DOWNLOAD_BYTES."""
        small_data = b"\x89PNG" + b"\x00" * 100

        mock_resp = _make_mock_stream_response(
            small_data,
            content_length=str(MAX_DOWNLOAD_BYTES + 1),
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kazma_core.http_pool.get_http_client", return_value=mock_client):
            with pytest.raises(ValueError, match="too large"):
                await _download_image("https://example.com/huge.png")

    @pytest.mark.asyncio
    async def test_stream_bytes_exceed_limit_raises(self):
        """Rejects download when streamed bytes exceed MAX_DOWNLOAD_BYTES."""
        # Create data that's small but we patch MAX_DOWNLOAD_BYTES to be tiny
        data = b"x" * 200

        mock_resp = _make_mock_stream_response(data)

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kazma_core.http_pool.get_http_client", return_value=mock_client), \
             patch("kazma_core.tools.vision_analyze.MAX_DOWNLOAD_BYTES", 100):
            with pytest.raises(ValueError, match="exceeds"):
                await _download_image("https://example.com/stealth.png")

    @pytest.mark.asyncio
    async def test_no_content_length_small_file_succeeds(self):
        """Succeeds when Content-Length is absent but file is small."""
        png_data = _make_png_bytes(4, 4)

        mock_resp = _make_mock_stream_response(
            png_data,
            content_type="image/png",
            content_length=None,  # no Content-Length header
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kazma_core.http_pool.get_http_client", return_value=mock_client):
            image_bytes, mime = await _download_image("https://example.com/small.png")

        assert image_bytes == png_data
        assert mime == "image/png"

    @pytest.mark.asyncio
    async def test_content_length_within_limit_succeeds(self):
        """Succeeds when Content-Length is present and within limit."""
        png_data = _make_png_bytes(8, 8)

        mock_resp = _make_mock_stream_response(
            png_data,
            content_type="image/jpeg",
            content_length=str(len(png_data)),
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kazma_core.http_pool.get_http_client", return_value=mock_client):
            image_bytes, mime = await _download_image("https://example.com/photo.jpg")

        assert image_bytes == png_data
        assert mime == "image/jpeg"
