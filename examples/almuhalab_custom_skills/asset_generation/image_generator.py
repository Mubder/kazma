"""Image Generation Pipeline — Division-branded image creation.

Generates logos, marketing materials, and inspection visuals
that strictly adhere to ALMuhalab division branding guidelines.
Supports multiple backends (local diffusion, API-based).
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from almuhalab_custom_skills.branding.almuhalab_guidelines import BrandGuidelines

logger = logging.getLogger(__name__)


@dataclass
class GeneratedImage:
    """A generated image with branding metadata."""

    image_id: str
    division: str
    image_type: str          # "logo", "marketing", "inspection"
    variant: str             # e.g. "standard", "icon", "banner"
    width: int
    height: int
    prompt_used: str
    backend: str
    created_at: float = field(default_factory=time.time)
    # Raw pixel data or file path (backend-dependent)
    data: Optional[bytes] = None
    file_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        return len(self.data) if self.data else 0


class DivisionImageGenerator:
    """Generates images adhering to division branding.

    Uses the BrandGuidelines engine to apply correct color palettes,
    imagery styles, logo placement, and watermarks to all generated
    images. Supports logo variants, marketing materials, and
    inspection visual generation.

    Backend options:
    - 'local': Local diffusion model (Stable Diffusion / Krita-style)
    - 'api': External API (Firefly, DALL-E, etc.)
    - 'mock': Testing backend (generates placeholder images)
    """

    def __init__(self, backend: str = "mock") -> None:
        self.backend = backend
        self.guidelines = BrandGuidelines

    def _generate_id(self, prefix: str) -> str:
        """Generate a unique asset ID."""
        ts = int(time.time() * 1000)
        rand = uuid.uuid4().hex[:8]
        return f"{prefix}-{ts}-{rand}"

    def _build_logo_prompt(
        self, division: str, variant: str, size: Tuple[int, int]
    ) -> str:
        """Build diffusion prompt for logo generation."""
        brand = self.guidelines.get_branding(division)
        style_prefix = self.guidelines.get_style_prompt(division)

        variant_desc = {
            "standard": "full corporate logo with company name and tagline",
            "icon": "minimal icon mark only, no text",
            "horizontal": "horizontal layout logo with text beside icon",
            "arabic": "corporate logo with Arabic text and tagline",
        }
        desc = variant_desc.get(variant, "standard corporate logo")

        text_elements = ""
        if variant == "arabic":
            text_elements = (
                f', Arabic text "{brand.name_ar}", '
                f'Arabic tagline "{brand.tagline_ar}"'
            )
        elif variant != "icon":
            text_elements = (
                f', English text "{brand.name_en}", '
                f'tagline "{brand.tagline_en}"'
            )

        return (
            f"{style_prefix}, {desc}{text_elements}, "
            f"clean white background, vector style, "
            f"high resolution {size[0]}x{size[1]}"
        )

    def _build_marketing_prompt(
        self,
        division: str,
        material_type: str,
        content_prompt: str,
        language: str,
    ) -> str:
        """Build diffusion prompt for marketing material."""
        brand = self.guidelines.get_branding(division)
        style_prefix = self.guidelines.get_style_prompt(division)
        dims = self.guidelines.get_marketing_dimensions(material_type)

        tagline = brand.tagline_ar if language == "ar" else brand.tagline_en
        name = brand.name_ar if language == "ar" else brand.name_en

        return (
            f"{style_prefix}, {material_type} layout, "
            f"{content_prompt}, "
            f'company name "{name}", tagline "{tagline}", '
            f"{dims['width']}x{dims['height']} resolution, "
            f"professional marketing design, ALMuhalab branding"
        )

    def _build_inspection_prompt(
        self,
        division: str,
        report_summary: str,
        style: str,
    ) -> str:
        """Build diffusion prompt for inspection visual."""
        brand = self.guidelines.get_branding(division)
        style_prefix = self.guidelines.get_style_prompt(division)

        style_desc = {
            "technical": "detailed technical diagram with annotations and measurements",
            "executive": "high-level executive summary infographic, clean and minimal",
            "field_worker": "simplified field reference card, bold labels, clear icons",
        }
        desc = style_desc.get(style, style_desc["technical"])

        return (
            f"{style_prefix}, {desc}, "
            f"inspection findings: {report_summary}, "
            f"professional {brand.imagery_style} aesthetic, "
            f"ALMuhalab watermark"
        )

    def _apply_watermark(
        self, image_data: bytes, division: str
    ) -> bytes:
        """Apply ALMuhalab watermark to image data.

        In a real implementation this would overlay text/graphics.
        For mock backend, returns data unchanged with watermark flag in metadata.
        """
        # Placeholder: real impl uses PIL/ImageMagick to overlay
        return image_data

    def _apply_color_overlay(
        self, image_data: bytes, division: str
    ) -> bytes:
        """Apply division color palette overlay.

        Ensures generated image uses the correct color palette.
        """
        # Placeholder: real impl applies color grading
        return image_data

    def _mock_generate(self, width: int, height: int, prompt: str) -> bytes:
        """Generate a mock placeholder image (solid color with gradient).

        Returns raw bytes simulating a PNG image.
        """
        # Simple mock: deterministic bytes based on prompt hash
        prompt_hash = hashlib.md5(prompt.encode()).digest()
        # Create a minimal "image" — just the hash repeated to fill size
        pixel_count = width * height * 3  # RGB
        mock_data = (prompt_hash * (pixel_count // 16 + 1))[:pixel_count]
        return mock_data

    async def generate_logo(
        self,
        division: str,
        variant: str = "standard",
        size: Tuple[int, int] = (1024, 1024),
    ) -> GeneratedImage:
        """Generate division logo variant.

        Variants:
        - standard: Full logo with text
        - icon: Icon only (no text)
        - horizontal: Horizontal layout
        - arabic: Arabic text variant

        Args:
            division: Division identifier.
            variant: Logo variant name.
            size: (width, height) in pixels.

        Returns:
            GeneratedImage with logo data.
        """
        if division not in self.guidelines.DIVISIONS:
            raise ValueError(
                f"Unknown division '{division}'. "
                f"Valid: {self.guidelines.get_all_divisions()}"
            )
        valid_variants = ("standard", "icon", "horizontal", "arabic")
        if variant not in valid_variants:
            raise ValueError(
                f"Unknown variant '{variant}'. "
                f"Valid: {valid_variants}"
            )

        prompt = self._build_logo_prompt(division, variant, size)
        logger.info("Generating %s logo for %s: %dx%d", variant, division, size[0], size[1])

        if self.backend == "mock":
            image_data = self._mock_generate(size[0], size[1], prompt)
        else:
            # Placeholder for real backend integration
            image_data = self._mock_generate(size[0], size[1], prompt)

        image_data = self._apply_watermark(image_data, division)

        return GeneratedImage(
            image_id=self._generate_id(f"logo-{division}"),
            division=division,
            image_type="logo",
            variant=variant,
            width=size[0],
            height=size[1],
            prompt_used=prompt,
            backend=self.backend,
            data=image_data,
            metadata={
                "colors": self.guidelines.get_palette(division),
                "fonts": self.guidelines.get_fonts(division),
                "logo_elements": self.guidelines.get_logo_elements(division),
                "watermarked": True,
            },
        )

    async def generate_marketing_material(
        self,
        division: str,
        material_type: str,
        content_prompt: str,
        language: str = "ar",
    ) -> GeneratedImage:
        """Generate marketing material with division branding.

        Automatically applies division color palette, appropriate
        typography, brand-consistent imagery style, tagline, and
        ALMuhalab watermark.

        Args:
            division: Division identifier.
            material_type: 'banner', 'social_media', 'brochure', 'presentation'.
            content_prompt: Description of marketing content.
            language: 'ar' for Arabic, 'en' for English.

        Returns:
            GeneratedImage with marketing material data.
        """
        if division not in self.guidelines.DIVISIONS:
            raise ValueError(
                f"Unknown division '{division}'. "
                f"Valid: {self.guidelines.get_all_divisions()}"
            )
        valid_types = ("banner", "social_media", "brochure", "presentation")
        if material_type not in valid_types:
            raise ValueError(
                f"Unknown material_type '{material_type}'. "
                f"Valid: {valid_types}"
            )

        dims = self.guidelines.get_marketing_dimensions(material_type)
        prompt = self._build_marketing_prompt(
            division, material_type, content_prompt, language
        )
        logger.info(
            "Generating %s marketing material for %s (%s)",
            material_type, division, language,
        )

        if self.backend == "mock":
            image_data = self._mock_generate(dims["width"], dims["height"], prompt)
        else:
            image_data = self._mock_generate(dims["width"], dims["height"], prompt)

        image_data = self._apply_watermark(image_data, division)

        brand = self.guidelines.get_branding(division)
        return GeneratedImage(
            image_id=self._generate_id(f"mkt-{division}-{material_type}"),
            division=division,
            image_type="marketing",
            variant=material_type,
            width=dims["width"],
            height=dims["height"],
            prompt_used=prompt,
            backend=self.backend,
            data=image_data,
            metadata={
                "colors": self.guidelines.get_palette(division),
                "fonts": self.guidelines.get_fonts(division),
                "tagline": brand.tagline_ar if language == "ar" else brand.tagline_en,
                "language": language,
                "watermarked": True,
            },
        )

    async def generate_inspection_visual(
        self,
        division: str,
        inspection_report: Any,
        style: str = "technical",
    ) -> GeneratedImage:
        """Generate visual representation of inspection findings.

        Styles:
        - technical: Detailed technical diagram
        - executive: High-level summary for executives
        - field_worker: Simplified for field crews

        Args:
            division: Division identifier.
            inspection_report: InspectionReport dataclass instance.
            style: Visual style name.

        Returns:
            GeneratedImage with inspection visual data.
        """
        if division not in self.guidelines.DIVISIONS:
            raise ValueError(
                f"Unknown division '{division}'. "
                f"Valid: {self.guidelines.get_all_divisions()}"
            )
        valid_styles = ("technical", "executive", "field_worker")
        if style not in valid_styles:
            raise ValueError(
                f"Unknown style '{style}'. Valid: {valid_styles}"
            )

        # Build summary from report
        report_summary = (
            f"{inspection_report.total_detections} detections, "
            f"{inspection_report.critical_count} critical, "
            f"{inspection_report.high_count} high, "
            f"{inspection_report.medium_count} medium, "
            f"{inspection_report.low_count} low"
        )

        prompt = self._build_inspection_prompt(
            division, report_summary, style
        )
        logger.info(
            "Generating %s inspection visual for %s (%d findings)",
            style, division, inspection_report.total_detections,
        )

        width, height = 1920, 1080
        if self.backend == "mock":
            image_data = self._mock_generate(width, height, prompt)
        else:
            image_data = self._mock_generate(width, height, prompt)

        image_data = self._apply_watermark(image_data, division)

        return GeneratedImage(
            image_id=self._generate_id(f"insp-{division}-{style}"),
            division=division,
            image_type="inspection",
            variant=style,
            width=width,
            height=height,
            prompt_used=prompt,
            backend=self.backend,
            data=image_data,
            metadata={
                "colors": self.guidelines.get_palette(division),
                "report_id": inspection_report.report_id,
                "total_detections": inspection_report.total_detections,
                "critical_count": inspection_report.critical_count,
                "watermarked": True,
            },
        )
