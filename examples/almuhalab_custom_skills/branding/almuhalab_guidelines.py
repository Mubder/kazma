"""Brand Guidelines Engine — ALMuhalab division branding definitions.

Defines color palettes, typography, logo elements, imagery styles,
and taglines for Gas & Oil Trading, Tourism, and General Trading divisions.
Provides prompt prefix generation for diffusion-based image generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DivisionBranding:
    """Immutable branding spec for a single division."""

    name_en: str
    name_ar: str
    colors: dict[str, str]
    fonts: list[str]
    logo_elements: list[str]
    imagery_style: str
    tagline_ar: str
    tagline_en: str


class BrandGuidelines:
    """ALMuhalab division branding guidelines.

    Central registry of branding specs for all three divisions:
    - gas_oil: Gas & Oil Trading (-trade gas oil)
    - tourism: Tourism (tourism)
    - general_trading: General Trading (general trade)

    Provides:
    - Exact hex color palettes
    - Typography (Arabic + Latin fonts)
    - Logo element keywords
    - Imagery style keywords for diffusion prompts
    - Division taglines in Arabic and English
    """

    DIVISIONS: dict[str, DivisionBranding] = {
        "gas_oil": DivisionBranding(
            name_en="Gas & Oil Trading",
            name_ar="تجارة الغاز والنفط",
            colors={
                "primary": "#1B365D",       # Deep navy
                "secondary": "#C8102E",     # Red accent
                "accent": "#FFD700",        # Gold
                "background": "#FFFFFF",    # White
            },
            fonts=["Arial", "Noto Sans Arabic"],
            logo_elements=["flame", "pipeline", "barrel"],
            imagery_style="industrial, professional, high-tech",
            tagline_ar="طاقة تخدم الكويت",
            tagline_en="Powering Kuwait",
        ),
        "tourism": DivisionBranding(
            name_en="Tourism",
            name_ar="السياحة",
            colors={
                "primary": "#0077B6",       # Ocean blue
                "secondary": "#00B4D8",     # Light blue
                "accent": "#FFB703",        # Sun yellow
                "background": "#FFFFFF",    # White
            },
            fonts=["Arial", "Noto Sans Arabic"],
            logo_elements=["dhow", "palm", "sun"],
            imagery_style="inviting, warm, cultural heritage",
            tagline_ar="اكتشف جمال الكويت",
            tagline_en="Discover Kuwait's Beauty",
        ),
        "general_trading": DivisionBranding(
            name_en="General Trading",
            name_ar="التجارة العامة",
            colors={
                "primary": "#2D6A4F",       # Forest green
                "secondary": "#40916C",     # Light green
                "accent": "#D4A373",        # Sand
                "background": "#FFFFFF",    # White
            },
            fonts=["Arial", "Noto Sans Arabic"],
            logo_elements=["globe", "handshake", "chart"],
            imagery_style="global, trustworthy, growth-oriented",
            tagline_ar="تجارة عالمية بثقة",
            tagline_en="Global Trade, Trusted",
        ),
    }

    # Watermark text applied to all generated assets
    WATERMARK_TEXT = "ALMuhalab"

    @classmethod
    def get_palette(cls, division: str) -> dict[str, str]:
        """Get color palette for division.

        Args:
            division: Division identifier ('gas_oil', 'tourism', 'general_trading').

        Returns:
            Dict with 'primary', 'secondary', 'accent', 'background' hex colors.

        Raises:
            KeyError: If division is not recognized.
        """
        return dict(cls.DIVISIONS[division].colors)

    @classmethod
    def get_branding(cls, division: str) -> DivisionBranding:
        """Get full branding spec for a division.

        Args:
            division: Division identifier.

        Returns:
            DivisionBranding dataclass.
        """
        return cls.DIVISIONS[division]

    @classmethod
    def get_style_prompt(cls, division: str) -> str:
        """Get diffusion prompt prefix for division style.

        Produces a prompt segment incorporating the division's colors,
        imagery style, and branding elements suitable for diffusion models.

        Args:
            division: Division identifier.

        Returns:
            Prompt string for diffusion model prefix.
        """
        brand = cls.DIVISIONS[division]
        color_names = ", ".join(
            f"{k} ({v})" for k, v in brand.colors.items()
        )
        return (
            f"{brand.imagery_style} style, "
            f"color palette: {color_names}, "
            f"featuring {', '.join(brand.logo_elements)}, "
            f"professional corporate branding, "
            f"ALMuhalab watermark in corner"
        )

    @classmethod
    def get_logo_elements(cls, division: str) -> list[str]:
        """Get logo element keywords for a division.

        Args:
            division: Division identifier.

        Returns:
            List of element keyword strings.
        """
        return list(cls.DIVISIONS[division].logo_elements)

    @classmethod
    def get_tagline(cls, division: str, language: str = "ar") -> str:
        """Get division tagline.

        Args:
            division: Division identifier.
            language: 'ar' for Arabic, 'en' for English.

        Returns:
            Tagline string.
        """
        brand = cls.DIVISIONS[division]
        return brand.tagline_ar if language == "ar" else brand.tagline_en

    @classmethod
    def get_fonts(cls, division: str) -> list[str]:
        """Get approved fonts for a division.

        Args:
            division: Division identifier.

        Returns:
            List of font name strings.
        """
        return list(cls.DIVISIONS[division].fonts)

    @classmethod
    def validate_hex_color(cls, hex_color: str) -> bool:
        """Validate that a string is a proper hex color code.

        Args:
            hex_color: String to validate (e.g. '#1B365D').

        Returns:
            True if valid hex color.
        """
        return bool(re.match(r"^#[0-9A-Fa-f]{6}$", hex_color))

    @classmethod
    def get_all_divisions(cls) -> list[str]:
        """Get list of all division identifiers.

        Returns:
            List of division ID strings.
        """
        return list(cls.DIVISIONS.keys())

    @classmethod
    def get_marketing_dimensions(cls, material_type: str) -> dict[str, int]:
        """Get recommended pixel dimensions for marketing material types.

        Args:
            material_type: 'banner', 'social_media', 'brochure', 'presentation'.

        Returns:
            Dict with 'width' and 'height' in pixels.
        """
        dimensions = {
            "banner": {"width": 1920, "height": 600},
            "social_media": {"width": 1080, "height": 1080},
            "brochure": {"width": 2480, "height": 3508},  # A4 at 300dpi
            "presentation": {"width": 1920, "height": 1080},
        }
        return dimensions.get(material_type, {"width": 1024, "height": 1024})

    @classmethod
    def get_logo_placement(cls) -> dict[str, any]:
        """Get logo placement rules.

        Returns:
            Dict with placement position and minimum area fraction.
        """
        return {
            "position": "bottom_right",
            "min_area_fraction": 0.10,
        }
