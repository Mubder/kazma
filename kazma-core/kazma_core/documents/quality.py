"""Per-page extraction quality assessment for selective OCR routing.

Also provides a lightweight **extraction score** used by multi-engine PDF
selection (PyMuPDF vs pdfplumber vs pypdf) so the parser can keep the better
text layer without always running every backend.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from .models import BlockType, DocumentIR, DocumentPage

__all__ = [
    "PageQuality",
    "assess_document_quality",
    "assess_page_quality",
    "presentation_form_ratio",
    "score_document_extraction",
    "score_extracted_text",
]

# Arabic Presentation Forms-A/B — common when extractors dump visual glyphs.
_PRESENTATION_RE = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
_CID_RE = re.compile(r"\(cid:\d+\)", re.IGNORECASE)

# Small rank bonus so equal content scores prefer engines known to keep
# logical-order Arabic (PyMuPDF first). Never overrides a clearly better extract.
_EXTRACTOR_RANK_BONUS: dict[str, float] = {
    "pymupdf": 0.04,
    "pypdfium2": 0.035,  # PDFium peer; optional bake-off engine
    "pdfplumber": 0.015,
    "pypdf": 0.0,
    "PyPDF2": 0.0,
}


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
    presentation_form_ratio: float = 0.0
    extraction_score: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "needs_ocr": self.needs_ocr,
            "reasons": list(self.reasons),
            "confidence": self.confidence,
            "text_chars": self.text_chars,
            "character_quality": self.character_quality,
            "text_density": self.text_density,
            "presentation_form_ratio": self.presentation_form_ratio,
            "extraction_score": self.extraction_score,
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


def presentation_form_ratio(text: str) -> float:
    """Fraction of non-space chars that are Arabic presentation forms."""

    significant = [char for char in text if not char.isspace()]
    if not significant:
        return 0.0
    hits = sum(1 for char in significant if _PRESENTATION_RE.fullmatch(char))
    return hits / len(significant)


def _cid_density(text: str) -> float:
    """Rough density of ``(cid:N)`` placeholders in the extract."""

    if not text:
        return 0.0
    matches = _CID_RE.findall(text)
    if not matches:
        return 0.0
    # Each cid token is garbage; scale by token count vs whitespace tokens.
    tokens = max(1, len(text.split()))
    return min(1.0, len(matches) / tokens)


def score_extracted_text(
    text: str,
    *,
    table_blocks: int = 0,
    extractor: str | None = None,
) -> float:
    """Score a text extract in ``[0, 1]`` for multi-engine comparison.

    Higher is better. Empty extracts score near zero (tables can add a little).
    Presentation forms and cid-mangling are penalised so visual-order dumps lose
    to logical-order Unicode when both engines return content.
    """

    stripped = (text or "").strip()
    table_bonus = min(0.12, max(0, table_blocks) * 0.04)
    rank = _EXTRACTOR_RANK_BONUS.get((extractor or "").lower(), 0.0)
    if not stripped:
        return round(min(0.18, table_bonus + rank), 6)

    char_q = _character_quality(stripped)
    pres = presentation_form_ratio(stripped)
    cid = _cid_density(stripped)
    # Length helps prefer fuller extracts, with diminishing returns.
    length_factor = min(1.0, math.log1p(len(stripped)) / math.log1p(4000))
    content = char_q * (1.0 - 0.75 * pres) * (1.0 - 0.55 * cid)
    base = 0.55 * content + 0.30 * length_factor + table_bonus
    return round(min(1.0, max(0.0, base + rank)), 6)


def score_document_extraction(document: DocumentIR) -> float:
    """Aggregate extraction score across pages (mean of per-page scores)."""

    if not document.pages:
        return 0.0
    scores = [
        score_extracted_text(
            "\n".join(block.text for block in page.blocks if block.text),
            table_blocks=sum(
                1 for block in page.blocks if block.block_type is BlockType.TABLE
            ),
            extractor=str(
                page.metadata.get("extractor")
                or document.metadata.get("extractor")
                or ""
            ),
        )
        for page in document.pages
    ]
    return round(sum(scores) / len(scores), 6)


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
    pres_ratio = presentation_form_ratio(text)
    cid = _cid_density(text)
    table_blocks = sum(1 for block in page.blocks if block.block_type is BlockType.TABLE)
    extractor = str(page.metadata.get("extractor") or "")
    extraction_score = score_extracted_text(
        text, table_blocks=table_blocks, extractor=extractor
    )
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
    # Visual / shaped dumps that OCR can often recover better.
    if text and pres_ratio >= 0.12:
        reasons.append("high_presentation_forms")
    if text and cid >= 0.08:
        reasons.append("cid_mangled_text")
    if image_only:
        reasons.append("image_or_scanned_page")
    elif image_count > 0 and text_chars < min_text_chars:
        reasons.append("image_dominant_page")

    needs_ocr = bool(reasons)
    if not needs_ocr:
        confidence = 0.98
    elif image_only:
        confidence = 0.98
    elif "high_presentation_forms" in reasons or "cid_mangled_text" in reasons:
        confidence = 0.93
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
        presentation_form_ratio=round(pres_ratio, 6),
        extraction_score=extraction_score,
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
