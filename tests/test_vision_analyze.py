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

from tests._module_stubs import stub_modules
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

@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """The download's SSRF check resolves the image's host: answered here
    (a public address), never by the network -- example.com was looked up
    for real (tests/_egress_guard.py, 2026-10-15). The HTTP client is each
    test's own stand-in."""
    monkeypatch.setattr("kazma_core.security.ssrf._resolve_host_ips", lambda _host: ["93.184.216.34"])


def _fetched(
    data: bytes,
    content_type: str = "image/png",
    content_length: str | None = None,
    *,
    truncated: bool = False,
    status: int = 200,
    raises: Exception | None = None,
):
    """Stand in for ``web_acquire.http.fetch_public``, the download's seam
    since the security audit of 2026-11-10 (it validates and pins every hop
    and bounds the body; its own tests hold that). *truncated* is what it
    reports for a body past ``max_bytes``."""
    import httpx
    from kazma_core.web_acquire.http import HttpFetch

    headers = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length

    async def _fetch(url: str, **kwargs):
        if raises is not None:
            raise raises
        response = httpx.Response(status, headers=headers, content=data,
                                  request=httpx.Request("GET", url))
        return HttpFetch(response, "", url, truncated)

    return patch("kazma_core.web_acquire.http.fetch_public", _fetch)


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
            return_value=(mock_provider, "test-active-model", "active-model"),
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
            return_value=(mock_provider, "test-active-model", "active-model"),
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
        mock_provider = _mock_llm_provider("A 16x16 red square from the web.")

        with _fetched(png_bytes, "image/png", str(len(png_bytes))), \
             patch(
                "kazma_core.tools.vision_analyze._get_llm_provider",
                return_value=(mock_provider, "test-active-model", "active-model"),
             ):
            result = await analyze_image("https://example.com/image.png")

        assert result == "A 16x16 red square from the web."

    @pytest.mark.asyncio
    async def test_url_download_failure(self):
        """Graceful error when URL download fails."""
        import httpx as real_httpx

        with _fetched(b"", raises=real_httpx.ConnectError("Connection refused")):
            result = await analyze_image("https://broken.example.com/img.png")

        assert result.startswith("Error:") and "Could not connect" in result

    @pytest.mark.asyncio
    async def test_a_refused_redirect_is_blocked(self):
        """A redirect to an internal address (refused by the acquisition
        layer, audit F-08) is the same "Blocked" answer as a private URL."""
        from kazma_core.security.ssrf import SSRFError

        with _fetched(b"", raises=SSRFError("Blocked URL 'http://169.254.169.254/'")):
            with pytest.raises(ValueError, match="Blocked"):
                await _download_image("https://example.com/redirects-to-metadata.png")

    @pytest.mark.asyncio
    async def test_an_http_error_is_said(self):
        with _fetched(b"nope", "text/plain", status=404):
            with pytest.raises(ValueError, match="HTTP 404"):
                await _download_image("https://example.com/missing.png")


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

        # There are two thresholds, not one: `analyze_image` rejects outright
        # above MAX_IMAGE_BYTES * 2 and resizes between 1x and 2x. Patching the
        # limit to a flat 10 put this 16x16 PNG past the *reject* line, so the
        # test asserted a resize and got "Image file too large (0.0 MB). Max
        # 0 MB" -- the product behaving correctly under an impossible limit.
        # Derive the limit from the file so it always lands in the resize band.
        resize_band = len(png) - 1  # file > 1x (resize) but < 2x (no reject)
        with patch("kazma_core.tools.vision_analyze.MAX_IMAGE_BYTES", resize_band), \
             patch(
                "kazma_core.tools.vision_analyze._get_llm_provider",
                return_value=(mock_provider, "test-active-model", "active-model"),
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
            return_value=(mock_provider, "test-active-model", "active-model"),
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
            return_value=(mock_provider, "test-active-model", "active-model"),
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

        # _get_llm_provider always returns a 3-tuple; the "unavailable"
        # case is signalled by provider=None with a reason string.
        with patch(
            "kazma_core.tools.vision_analyze._get_llm_provider",
            return_value=(None, None, "registry-unavailable"),
        ):
            result = await analyze_image(str(img_file))

        assert result.startswith("Error:")
        assert "unavailable" in result.lower()

# ═══════════════════════════════════════════════════════════════════
# Test 9: Stream-based download size cap (gw-064 BUG 2)
# ═══════════════════════════════════════════════════════════════════


class TestStreamDownloadSizeCap:
    """Tests for download size enforcement (gw-064), through the bounded
    acquisition layer since 2026-11-10."""

    @pytest.mark.asyncio
    async def test_content_length_too_large_raises(self):
        """Rejects download when Content-Length exceeds MAX_DOWNLOAD_BYTES."""
        small_data = b"\x89PNG" + b"\x00" * 100

        with _fetched(small_data, content_length=str(MAX_DOWNLOAD_BYTES + 1)):
            with pytest.raises(ValueError, match="too large"):
                await _download_image("https://example.com/huge.png")

    @pytest.mark.asyncio
    async def test_stream_bytes_exceed_limit_raises(self):
        """Rejects download when the body ran past the cap (the bounded
        reader stopped and reported it cut)."""
        with _fetched(b"x" * 100, truncated=True):
            with pytest.raises(ValueError, match="exceeds"):
                await _download_image("https://example.com/stealth.png")

    @pytest.mark.asyncio
    async def test_the_cap_reaches_the_reader(self):
        """The bounded reader is asked for MAX_DOWNLOAD_BYTES, no more."""
        from kazma_core.web_acquire.http import HttpFetch

        import httpx

        asked: dict = {}

        async def _fetch(url: str, **kwargs):
            asked.update(kwargs)
            response = httpx.Response(200, headers={"content-type": "image/png"},
                                      content=_make_png_bytes(4, 4),
                                      request=httpx.Request("GET", url))
            return HttpFetch(response, "", url, False)

        with patch("kazma_core.web_acquire.http.fetch_public", _fetch):
            await _download_image("https://example.com/small.png")
        assert asked["max_bytes"] == MAX_DOWNLOAD_BYTES

    @pytest.mark.asyncio
    async def test_no_content_length_small_file_succeeds(self):
        """Succeeds when Content-Length is absent but file is small."""
        png_data = _make_png_bytes(4, 4)

        with _fetched(png_data, "image/png", None):
            image_bytes, mime = await _download_image("https://example.com/small.png")

        assert image_bytes == png_data
        assert mime == "image/png"

    @pytest.mark.asyncio
    async def test_content_length_within_limit_succeeds(self):
        """Succeeds when Content-Length is present and within limit."""
        png_data = _make_png_bytes(8, 8)

        with _fetched(png_data, "image/jpeg", str(len(png_data))):
            image_bytes, mime = await _download_image("https://example.com/photo.jpg")

        assert image_bytes == png_data
        assert mime == "image/jpeg"
