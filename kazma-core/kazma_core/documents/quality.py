"""Per-page extraction quality assessment for selective OCR routing."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .models import DocumentIR, DocumentPage

__all__ = ["PageQuality", "assess_document_quality", "assess_page_quality"]


@dataclass(frozen=True, slots=True)
class PageQuality:
    """A deterministic decision about whether a page needs OCR."""

    page_number: int
    needs_ocr: bool
    reasons: tuple[str, ...]
    confidence: float
    text_chars: int
    character_quality: float
    text_density: float

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "needs_ocr": self.needs_ocr,
            "reasons": list(self.reasons),
            "confidence": self.confidence,
            "text_chars": self.text_chars,
            "character_quality": self.character_quality,
            "text_density": self.text_density,
        }


def _character_quality(text: str) -> float:
    significant = [char for char in text if not char.isspace()]
    if not significant:
        return 0.0
    acceptable = 0
    for char in significant:
        category = unicodedata.category(char)
        if char != "\ufffd" and category not in {"Cc", "Cs", "Co", "Cn"}:
            acceptable += 1
    return acceptable / len(significant)


def assess_page_quality(
    page: DocumentPage,
    *,
    min_text_chars: int = 40,
) -> PageQuality:
    """Assess native extraction without invoking OCR.

    Density is measured in characters per 1,000 page-coordinate square units.
    It is only a supporting signal because some parsers do not expose geometry.
    """

    text = "\n".join(block.text for block in page.blocks if block.text).strip()
    text_chars = len(text)
    character_quality = _character_quality(text)
    area = (page.width or 0.0) * (page.height or 0.0)
    text_density = text_chars * 1_000.0 / area if area > 0 else float(text_chars)
    image_count = int(page.metadata.get("image_count", 0) or 0)
    image_only = bool(
        page.metadata.get("image_only")
        or page.metadata.get("kind") == "image"
        or (image_count > 0 and not text)
    )

    reasons: list[str] = []
    if not text:
        reasons.append("no_native_text")
    elif text_chars < min_text_chars and image_count > 0:
        reasons.append("low_text_density")
    if text and character_quality < 0.85:
        reasons.append("poor_character_quality")
    if image_only:
        reasons.append("image_or_scanned_page")
    elif image_count > 0 and text_chars < min_text_chars:
        reasons.append("image_dominant_page")

    needs_ocr = bool(reasons)
    if not needs_ocr:
        confidence = 0.98
    elif image_only:
        confidence = 0.98
    elif "poor_character_quality" in reasons:
        confidence = 0.92
    elif image_count > 0:
        confidence = 0.9
    else:
        confidence = 0.72
    return PageQuality(
        page_number=page.page_number,
        needs_ocr=needs_ocr,
        reasons=tuple(reasons),
        confidence=confidence,
        text_chars=text_chars,
        character_quality=round(character_quality, 6),
        text_density=round(text_density, 6),
    )


def assess_document_quality(
    document: DocumentIR,
    *,
    min_text_chars: int = 40,
) -> tuple[PageQuality, ...]:
    return tuple(
        assess_page_quality(page, min_text_chars=min_text_chars)
        for page in document.pages
    )
