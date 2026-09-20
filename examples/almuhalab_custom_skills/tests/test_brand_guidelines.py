"""Tests for Brand Guidelines Engine."""
from __future__ import annotations

from almuhalab_custom_skills.branding.almuhalab_guidelines import BrandGuidelines, DivisionBranding


class TestBrandGuidelinesDivisions:
    """Test division registry completeness."""

    def test_all_divisions_registered(self):
        divisions = BrandGuidelines.get_all_divisions()
        assert set(divisions) == {"gas_oil", "tourism", "general_trading"}

    def test_division_count(self):
        assert len(BrandGuidelines.DIVISIONS) == 3

    def test_get_branding_returns_division_branding(self):
        for div in BrandGuidelines.get_all_divisions():
            brand = BrandGuidelines.get_branding(div)
            assert isinstance(brand, DivisionBranding)


class TestColorPalettes:
    """Test color palette definitions."""

    def test_gas_oil_palette(self):
        palette = BrandGuidelines.get_palette("gas_oil")
        assert palette["primary"] == "#1B365D"
        assert palette["secondary"] == "#C8102E"
        assert palette["accent"] == "#FFD700"
        assert palette["background"] == "#FFFFFF"

    def test_tourism_palette(self):
        palette = BrandGuidelines.get_palette("tourism")
        assert palette["primary"] == "#0077B6"
        assert palette["secondary"] == "#00B4D8"
        assert palette["accent"] == "#FFB703"
        assert palette["background"] == "#FFFFFF"

    def test_general_trading_palette(self):
        palette = BrandGuidelines.get_palette("general_trading")
        assert palette["primary"] == "#2D6A4F"
        assert palette["secondary"] == "#40916C"
        assert palette["accent"] == "#D4A373"
        assert palette["background"] == "#FFFFFF"

    def test_palette_returns_copy(self):
        p1 = BrandGuidelines.get_palette("gas_oil")
        p2 = BrandGuidelines.get_palette("gas_oil")
        p1["primary"] = "#000000"
        assert p2["primary"] == "#1B365D"  # Original unchanged

    def test_all_palettes_have_required_keys(self):
        required = {"primary", "secondary", "accent", "background"}
        for div in BrandGuidelines.get_all_divisions():
            palette = BrandGuidelines.get_palette(div)
            assert required.issubset(set(palette.keys())), (
                f"{div} missing keys: {required - set(palette.keys())}"
            )


class TestHexColorValidation:
    """Test hex color validation."""

    def test_valid_colors(self):
        assert BrandGuidelines.validate_hex_color("#1B365D")
        assert BrandGuidelines.validate_hex_color("#FFFFFF")
        assert BrandGuidelines.validate_hex_color("#000000")
        assert BrandGuidelines.validate_hex_color("#ff77aa")

    def test_invalid_colors(self):
        assert not BrandGuidelines.validate_hex_color("1B365D")
        assert not BrandGuidelines.validate_hex_color("#GGGGGG")
        assert not BrandGuidelines.validate_hex_color("#12345")
        assert not BrandGuidelines.validate_hex_color("#1234567")
        assert not BrandGuidelines.validate_hex_color("red")

    def test_all_palette_colors_are_valid(self):
        for div in BrandGuidelines.get_all_divisions():
            palette = BrandGuidelines.get_palette(div)
            for name, color in palette.items():
                assert BrandGuidelines.validate_hex_color(color), (
                    f"{div}.{name} has invalid color: {color}"
                )


class TestStylePrompts:
    """Test diffusion prompt generation."""

    def test_style_prompt_contains_imagery_style(self):
        for div in BrandGuidelines.get_all_divisions():
            prompt = BrandGuidelines.get_style_prompt(div)
            brand = BrandGuidelines.get_branding(div)
            assert brand.imagery_style in prompt

    def test_style_prompt_contains_colors(self):
        prompt = BrandGuidelines.get_style_prompt("gas_oil")
        assert "#1B365D" in prompt
        assert "#C8102E" in prompt

    def test_style_prompt_contains_logo_elements(self):
        prompt = BrandGuidelines.get_style_prompt("tourism")
        assert "dhow" in prompt
        assert "palm" in prompt
        assert "sun" in prompt

    def test_style_prompt_contains_watermark(self):
        for div in BrandGuidelines.get_all_divisions():
            prompt = BrandGuidelines.get_style_prompt(div)
            assert "watermark" in prompt.lower()


class TestLogoElements:
    """Test logo element retrieval."""

    def test_gas_oil_elements(self):
        elements = BrandGuidelines.get_logo_elements("gas_oil")
        assert "flame" in elements
        assert "pipeline" in elements
        assert "barrel" in elements

    def test_tourism_elements(self):
        elements = BrandGuidelines.get_logo_elements("tourism")
        assert "dhow" in elements
        assert "palm" in elements
        assert "sun" in elements

    def test_general_trading_elements(self):
        elements = BrandGuidelines.get_logo_elements("general_trading")
        assert "globe" in elements
        assert "handshake" in elements
        assert "chart" in elements

    def test_returns_copy(self):
        e1 = BrandGuidelines.get_logo_elements("gas_oil")
        e2 = BrandGuidelines.get_logo_elements("gas_oil")
        e1.append("extra")
        assert "extra" not in e2


class TestTaglines:
    """Test tagline retrieval."""

    def test_gas_oil_taglines(self):
        assert BrandGuidelines.get_tagline("gas_oil", "ar") == "طاقة تخدم الكويت"
        assert BrandGuidelines.get_tagline("gas_oil", "en") == "Powering Kuwait"

    def test_tourism_taglines(self):
        assert BrandGuidelines.get_tagline("tourism", "ar") == "اكتشف جمال الكويت"
        assert BrandGuidelines.get_tagline("tourism", "en") == "Discover Kuwait's Beauty"

    def test_general_trading_taglines(self):
        assert BrandGuidelines.get_tagline("general_trading", "ar") == "تجارة عالمية بثقة"
        assert BrandGuidelines.get_tagline("general_trading", "en") == "Global Trade, Trusted"

    def test_default_language_is_arabic(self):
        for div in BrandGuidelines.get_all_divisions():
            tagline = BrandGuidelines.get_tagline(div)
            brand = BrandGuidelines.get_branding(div)
            assert tagline == brand.tagline_ar


class TestFonts:
    """Test font retrieval."""

    def test_all_divisions_use_noto_sans_arabic(self):
        for div in BrandGuidelines.get_all_divisions():
            fonts = BrandGuidelines.get_fonts(div)
            assert "Noto Sans Arabic" in fonts

    def test_all_divisions_use_arial(self):
        for div in BrandGuidelines.get_all_divisions():
            fonts = BrandGuidelines.get_fonts(div)
            assert "Arial" in fonts


class TestMarketingDimensions:
    """Test marketing material dimensions."""

    def test_banner_dimensions(self):
        dims = BrandGuidelines.get_marketing_dimensions("banner")
        assert dims["width"] == 1920
        assert dims["height"] == 600

    def test_social_media_dimensions(self):
        dims = BrandGuidelines.get_marketing_dimensions("social_media")
        assert dims["width"] == 1080
        assert dims["height"] == 1080

    def test_brochure_dimensions(self):
        dims = BrandGuidelines.get_marketing_dimensions("brochure")
        assert dims["width"] == 2480
        assert dims["height"] == 3508  # A4 at 300dpi

    def test_presentation_dimensions(self):
        dims = BrandGuidelines.get_marketing_dimensions("presentation")
        assert dims["width"] == 1920
        assert dims["height"] == 1080

    def test_unknown_type_returns_default(self):
        dims = BrandGuidelines.get_marketing_dimensions("unknown")
        assert dims["width"] == 1024
        assert dims["height"] == 1024


class TestLogoPlacement:
    """Test logo placement rules."""

    def test_placement_position(self):
        placement = BrandGuidelines.get_logo_placement()
        assert placement["position"] == "bottom_right"

    def test_min_area_fraction(self):
        placement = BrandGuidelines.get_logo_placement()
        assert placement["min_area_fraction"] == 0.10


class TestWatermark:
    """Test watermark configuration."""

    def test_watermark_text(self):
        assert BrandGuidelines.WATERMARK_TEXT == "ALMuhalab"
