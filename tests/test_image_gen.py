"""Tests for image generation tool via pollinations.ai.

Covers:
  - basic generation with mocked HTTP
  - empty prompt validation
  - long prompt validation (>1000 chars)
  - invalid dimension validation
  - URL encoding correctness
  - file creation on disk
"""

from __future__ import annotations

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.tools.image_gen import (
    MAX_HEIGHT,
    MAX_PROMPT_CHARS,
    MAX_WIDTH,
    MIN_DIMENSION,
    _slugify,
    _validate_dimensions,
    generate_image,
)

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_httpx():
    """Mock httpx at the top-level package so the lazy import inside
    generate_image() picks up the mocked AsyncClient."""
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal PNG header + padding

    mock_httpx_mod = MagicMock()
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = fake_png
    mock_response.raise_for_status = MagicMock()
    mock_client.__aenter__.return_value.get.return_value = mock_response
    mock_httpx_mod.AsyncClient.return_value = mock_client

    with patch.dict("sys.modules", {"httpx": mock_httpx_mod}):
        yield mock_httpx_mod


@pytest.fixture
def temp_image_dir(tmp_path, monkeypatch):
    """Redirect IMAGE_DIR to a temp path so tests don't write to real kazma-data."""
    import kazma_core.tools.image_gen as mod

    monkeypatch.setattr(mod, "IMAGE_DIR", tmp_path / "images")
    return tmp_path / "images"


# ═══════════════════════════════════════════════════════════════════
# Unit tests — helpers
# ═══════════════════════════════════════════════════════════════════


class TestSlugify:
    def test_basic(self):
        assert _slugify("a cat wearing sunglasses") == "a-cat-wearing-sunglasses"

    def test_special_chars(self):
        assert _slugify("Hello!!! World???") == "hello-world"

    def test_empty_fallback(self):
        assert _slugify("!!!") == "image"

    def test_truncation(self):
        long = "a" * 200
        result = _slugify(long)
        assert len(result) <= 60


class TestValidateDimensions:
    def test_valid(self):
        assert _validate_dimensions(1024, 1024) is None

    def test_below_minimum(self):
        err = _validate_dimensions(32, 32)
        assert err is not None
        assert f"at least {MIN_DIMENSION}" in err

    def test_width_exceeds_max(self):
        err = _validate_dimensions(2000, 1024)
        assert err is not None
        assert str(MAX_WIDTH) in err

    def test_height_exceeds_max(self):
        err = _validate_dimensions(1024, 2000)
        assert err is not None
        assert str(MAX_HEIGHT) in err

    def test_non_integer(self):
        err = _validate_dimensions("abc", 512)  # type: ignore[arg-type]
        assert err is not None
        assert "must be integers" in err


# ═══════════════════════════════════════════════════════════════════
# Integration tests — generate_image
# ═══════════════════════════════════════════════════════════════════


class TestGenerateImageBasic:
    """Test 1: Basic generation with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_generates_and_saves_image(self, mock_httpx, temp_image_dir):
        result = await generate_image("a sunset over mountains", width=512, height=512)

        assert "Image generated successfully" in result
        assert "Description: a sunset over mountains" in result
        assert "512x512" in result
        assert "Saved to:" in result
        # Verify file was actually written
        files = list(temp_image_dir.glob("*.png"))
        assert len(files) == 1
        assert files[0].suffix == ".png"
        assert files[0].stat().st_size > 0


class TestGenerateImageEmptyPrompt:
    """Test 2: Empty prompt returns clear error."""

    @pytest.mark.asyncio
    async def test_empty_string(self):
        result = await generate_image("")
        assert result.startswith("Error: No prompt provided")

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        result = await generate_image("   ")
        assert result.startswith("Error: No prompt provided")


class TestGenerateImageLongPrompt:
    """Test 3: Prompt exceeding max length is rejected."""

    @pytest.mark.asyncio
    async def test_too_long(self):
        long_prompt = "x" * (MAX_PROMPT_CHARS + 1)
        result = await generate_image(long_prompt)
        assert "Error: Prompt too long" in result
        assert str(MAX_PROMPT_CHARS) in result

    @pytest.mark.asyncio
    async def test_at_limit_accepted(self, mock_httpx, temp_image_dir):
        exact_prompt = "x" * MAX_PROMPT_CHARS
        result = await generate_image(exact_prompt, width=64, height=64)
        assert "Image generated successfully" in result


class TestGenerateImageInvalidDimensions:
    """Test 4: Invalid dimensions rejected before any HTTP call."""

    @pytest.mark.asyncio
    async def test_width_too_large(self):
        result = await generate_image("a cat", width=2000, height=512)
        assert "Error" in result
        assert str(MAX_WIDTH) in result

    @pytest.mark.asyncio
    async def test_height_too_large(self):
        result = await generate_image("a cat", width=512, height=2000)
        assert "Error" in result
        assert str(MAX_HEIGHT) in result

    @pytest.mark.asyncio
    async def test_too_small(self):
        result = await generate_image("a cat", width=32, height=32)
        assert "Error" in result
        assert f"at least {MIN_DIMENSION}" in result


class TestGenerateImageUrlEncoding:
    """Test 5: Prompt is correctly URL-encoded in the request URL."""

    @pytest.mark.asyncio
    async def test_special_characters_encoded(self, mock_httpx, temp_image_dir):
        result = await generate_image("sunset & moon? #vibe!", width=256, height=256)

        assert "Image generated successfully" in result
        # Verify the URL was built with encoded prompt
        client_instance = mock_httpx.AsyncClient.return_value
        called_url = client_instance.__aenter__.return_value.get.call_args[0][0]

        # The prompt should appear encoded, not raw
        assert "sunset" in urllib.parse.unquote(called_url)
        assert "&" not in called_url.split("?")[0]  # & should be encoded as %26
        assert urllib.parse.quote("sunset & moon? #vibe!", safe="") in called_url

    @pytest.mark.asyncio
    async def test_unicode_characters_encoded(self, mock_httpx, temp_image_dir):
        result = await generate_image("caf\u00e9 \u4e2d\u6587", width=256, height=256)

        assert "Image generated successfully" in result
        client_instance = mock_httpx.AsyncClient.return_value
        called_url = client_instance.__aenter__.return_value.get.call_args[0][0]
        # Unicode should be percent-encoded
        decoded = urllib.parse.unquote(called_url)
        assert "caf\u00e9 \u4e2d\u6587" in decoded


class TestGenerateImageFileCreation:
    """Test 6: Generated image is saved to disk with correct naming."""

    @pytest.mark.asyncio
    async def test_file_naming(self, mock_httpx, temp_image_dir):
        result = await generate_image("a friendly robot", width=128, height=128)

        assert "Image generated successfully" in result
        files = list(temp_image_dir.glob("*.png"))
        assert len(files) == 1

        filename = files[0].name
        # Pattern: {timestamp}_{slug}.png
        assert "_" in filename
        parts = filename.rsplit("_", 1)
        assert parts[0].isdigit(), f"Expected timestamp prefix, got {parts[0]}"
        assert "friendly-robot" in parts[1]

    @pytest.mark.asyncio
    async def test_description_parameter(self, mock_httpx, temp_image_dir):
        result = await generate_image(
            "a complex prompt about something",
            width=256,
            height=256,
            description="Minimalist logo design",
        )
        assert "Description: Minimalist logo design" in result

    @pytest.mark.asyncio
    async def test_description_falls_back_to_prompt(self, mock_httpx, temp_image_dir):
        result = await generate_image("watercolor landscape painting", width=256, height=256)
        assert "Description: watercolor landscape painting" in result

    @pytest.mark.asyncio
    async def test_multiple_generations_unique_filenames(self, mock_httpx, temp_image_dir):
        await generate_image("first image", width=64, height=64)
        # Small delay so timestamp differs
        import asyncio

        await asyncio.sleep(0.1)
        await generate_image("second image", width=64, height=64)

        files = list(temp_image_dir.glob("*.png"))
        assert len(files) == 2
        assert files[0].name != files[1].name
