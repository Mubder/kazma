"""Tests for Division Image Generator."""
from __future__ import annotations

import pytest

from almuhalab_custom_skills.branding.almuhalab_guidelines import BrandGuidelines
from almuhalab_custom_skills.asset_generation.image_generator import (
    DivisionImageGenerator,
    GeneratedImage,
)


@pytest.fixture
def mock_generator():
    return DivisionImageGenerator(backend="mock")


@pytest.fixture
def sample_inspection_report():
    """Minimal inspection report stub for testing."""

    class StubReport:
        report_id = "IR-20260620-0001"
        total_detections = 12
        critical_count = 2
        high_count = 3
        medium_count = 4
        low_count = 3

    return StubReport()


class TestLogoGeneration:
    """Test logo generation for all divisions and variants."""

    @pytest.mark.asyncio
    async def test_generate_standard_logo_gas_oil(self, mock_generator):
        img = await mock_generator.generate_logo("gas_oil", "standard")
        assert isinstance(img, GeneratedImage)
        assert img.division == "gas_oil"
        assert img.image_type == "logo"
        assert img.variant == "standard"
        assert img.width == 1024
        assert img.height == 1024
        assert img.data is not None
        assert len(img.data) > 0
        assert img.metadata["watermarked"] is True

    @pytest.mark.asyncio
    async def test_generate_icon_logo(self, mock_generator):
        img = await mock_generator.generate_logo("tourism", "icon")
        assert img.variant == "icon"
        assert "no text" in img.prompt_used.lower() or "icon" in img.prompt_used.lower()

    @pytest.mark.asyncio
    async def test_generate_horizontal_logo(self, mock_generator):
        img = await mock_generator.generate_logo("general_trading", "horizontal")
        assert img.variant == "horizontal"
        assert img.division == "general_trading"

    @pytest.mark.asyncio
    async def test_generate_arabic_logo(self, mock_generator):
        img = await mock_generator.generate_logo("gas_oil", "arabic")
        assert img.variant == "arabic"
        # Arabic logo prompt should contain Arabic text
        assert "تجارة الغاز والنفط" in img.prompt_used

    @pytest.mark.asyncio
    async def test_all_divisions_generate_logos(self, mock_generator):
        for div in BrandGuidelines.get_all_divisions():
            img = await mock_generator.generate_logo(div)
            assert img.division == div
            assert img.image_id.startswith(f"logo-{div}")

    @pytest.mark.asyncio
    async def test_custom_size(self, mock_generator):
        img = await mock_generator.generate_logo(
            "gas_oil", "standard", size=(512, 256)
        )
        assert img.width == 512
        assert img.height == 256

    @pytest.mark.asyncio
    async def test_unknown_division_raises(self, mock_generator):
        with pytest.raises(ValueError, match="Unknown division"):
            await mock_generator.generate_logo("nonexistent")

    @pytest.mark.asyncio
    async def test_unknown_variant_raises(self, mock_generator):
        with pytest.raises(ValueError, match="Unknown variant"):
            await mock_generator.generate_logo("gas_oil", "weird_variant")


class TestMarketingMaterial:
    """Test marketing material generation."""

    @pytest.mark.asyncio
    async def test_generate_banner(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "gas_oil", "banner", "Oil market overview"
        )
        assert img.image_type == "marketing"
        assert img.variant == "banner"
        assert img.width == 1920
        assert img.height == 600

    @pytest.mark.asyncio
    async def test_generate_social_media(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "tourism", "social_media", "Summer tourism campaign"
        )
        assert img.variant == "social_media"
        assert img.width == 1080
        assert img.height == 1080

    @pytest.mark.asyncio
    async def test_generate_brochure(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "general_trading", "brochure", "Q2 trading report"
        )
        assert img.variant == "brochure"
        assert img.width == 2480

    @pytest.mark.asyncio
    async def test_generate_presentation(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "gas_oil", "presentation", "Board meeting slides"
        )
        assert img.variant == "presentation"

    @pytest.mark.asyncio
    async def test_arabic_language(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "tourism", "banner", " tourism promotion", language="ar"
        )
        assert img.metadata["language"] == "ar"
        # Should contain Arabic tagline
        assert "اكتشف جمال الكويت" in img.prompt_used

    @pytest.mark.asyncio
    async def test_english_language(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "gas_oil", "social_media", "Oil futures update", language="en"
        )
        assert img.metadata["language"] == "en"
        assert "Powering Kuwait" in img.prompt_used

    @pytest.mark.asyncio
    async def test_watermarked(self, mock_generator):
        img = await mock_generator.generate_marketing_material(
            "general_trading", "banner", "Trade expo"
        )
        assert img.metadata["watermarked"] is True
        assert "watermark" in img.prompt_used.lower()

    @pytest.mark.asyncio
    async def test_unknown_material_type_raises(self, mock_generator):
        with pytest.raises(ValueError, match="Unknown material_type"):
            await mock_generator.generate_marketing_material(
                "gas_oil", "billboard", "test"
            )


class TestInspectionVisuals:
    """Test inspection visual generation."""

    @pytest.mark.asyncio
    async def test_technical_style(self, mock_generator, sample_inspection_report):
        img = await mock_generator.generate_inspection_visual(
            "gas_oil", sample_inspection_report, style="technical"
        )
        assert img.image_type == "inspection"
        assert img.variant == "technical"
        assert "technical" in img.prompt_used.lower()

    @pytest.mark.asyncio
    async def test_executive_style(self, mock_generator, sample_inspection_report):
        img = await mock_generator.generate_inspection_visual(
            "gas_oil", sample_inspection_report, style="executive"
        )
        assert img.variant == "executive"

    @pytest.mark.asyncio
    async def test_field_worker_style(self, mock_generator, sample_inspection_report):
        img = await mock_generator.generate_inspection_visual(
            "gas_oil", sample_inspection_report, style="field_worker"
        )
        assert img.variant == "field_worker"

    @pytest.mark.asyncio
    async def test_detection_counts_in_metadata(
        self, mock_generator, sample_inspection_report
    ):
        img = await mock_generator.generate_inspection_visual(
            "gas_oil", sample_inspection_report
        )
        assert img.metadata["total_detections"] == 12
        assert img.metadata["critical_count"] == 2
        assert img.metadata["report_id"] == "IR-20260620-0001"

    @pytest.mark.asyncio
    async def test_all_divisions(self, mock_generator, sample_inspection_report):
        for div in BrandGuidelines.get_all_divisions():
            img = await mock_generator.generate_inspection_visual(
                div, sample_inspection_report
            )
            assert img.division == div

    @pytest.mark.asyncio
    async def test_unknown_style_raises(self, mock_generator, sample_inspection_report):
        with pytest.raises(ValueError, match="Unknown style"):
            await mock_generator.generate_inspection_visual(
                "gas_oil", sample_inspection_report, style="cartoon"
            )


class TestGeneratedImageData:
    """Test GeneratedImage data properties."""

    @pytest.mark.asyncio
    async def test_size_bytes(self, mock_generator):
        img = await mock_generator.generate_logo("gas_oil")
        assert img.size_bytes > 0

    @pytest.mark.asyncio
    async def test_prompt_contains_brand_elements(self, mock_generator):
        img = await mock_generator.generate_logo("tourism", "standard")
        # Should mention tourism style elements
        assert "inviting" in img.prompt_used or "warm" in img.prompt_used

    @pytest.mark.asyncio
    async def test_metadata_has_colors(self, mock_generator):
        img = await mock_generator.generate_logo("gas_oil")
        assert "colors" in img.metadata
        assert img.metadata["colors"]["primary"] == "#1B365D"
