"""PPTX engine for the unified document layer.

Slides don't share the document :class:`ContentModel` (they have their own
slides/bullets shape), so this engine consumes the native ``slides`` payload
**plus** a :class:`~kazma_core.documents.profile.DocProfile`. The profile
supplies the shared theme (accent / heading colours, fonts) and direction
(``a:pPr rtl=1`` on every paragraph for Arabic), so an Arabic deck matches the
Arabic DOCX/PDF/HTML/XLSX in design and direction.

python-pptx does not expose RTL directly, so paragraph direction is set on the
underlying ``<a:pPr>`` element (mirrors the DOCX ``w:bidi`` approach).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_core.documents.profile import DocProfile

logger = logging.getLogger(__name__)

__all__ = ["PptxEngine"]


class PptxEngine:
    """Render a slides payload to a themed ``.pptx`` under a :class:`DocProfile`."""

    def __init__(self, profile: DocProfile) -> None:
        self.profile = profile
        self.theme = profile.theme

    def render(self, payload: dict[str, Any], output: Path | str) -> None:
        from pptx import Presentation
        from pptx.util import Pt

        t = self.theme
        accent_hex = str(t["accent"]).lstrip("#")
        heading_hex = str(t["heading_fill"]).lstrip("#")
        body_hex = str(t["body"]).lstrip("#")
        muted_hex = str(t["muted"]).lstrip("#")
        font_name = "Calibri"

        presentation = Presentation()

        # ── Title slide ─────────────────────────────────────────────────── #
        title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
        title_text = str(payload.get("title", "Presentation"))
        if title_slide.shapes.title is not None:
            tf = title_slide.shapes.title.text_frame
            self._set_paragraph(tf.paragraphs[0], title_text, color=accent_hex,
                                bold=True, size=40, font=font_name, align="ctr")
        subtitle = payload.get("subtitle")
        if subtitle and len(title_slide.placeholders) > 1:
            sub_tf = title_slide.placeholders[1].text_frame
            self._set_paragraph(sub_tf.paragraphs[0], str(subtitle), color=muted_hex,
                                size=20, font=font_name, align="ctr")

        # ── Content slides ──────────────────────────────────────────────── #
        slides = payload.get("slides")
        if isinstance(slides, list):
            for value in slides:
                if not isinstance(value, dict):
                    continue
                slide = presentation.slides.add_slide(presentation.slide_layouts[1])
                heading = str(value.get("heading", ""))
                if slide.shapes.title is not None:
                    self._set_paragraph(
                        slide.shapes.title.text_frame.paragraphs[0], heading,
                        color=heading_hex, bold=True, size=30, font=font_name,
                    )
                if len(slide.placeholders) > 1:
                    frame = slide.placeholders[1].text_frame
                    frame.word_wrap = True
                    bullets = value.get("bullets")
                    lines = bullets if isinstance(bullets, list) else str(value.get("body", "")).splitlines()
                    for index, line in enumerate(lines):
                        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                        self._set_paragraph(para, str(line), color=body_hex,
                                            size=20, font=font_name)

        presentation.save(str(output))

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _set_paragraph(self, paragraph: Any, text: str, *, color: str, bold: bool = False,
                       size: float = 18, font: str = "Calibri",
                       align: str | None = None) -> None:
        """Set a paragraph's text + themed run + RTL direction (profile-driven).

        RTL needs TWO things on ``<a:pPr>``: ``rtl="1"`` (reading order) AND an
        explicit ``algn`` alignment (the DrawingML attribute is ``algn``, NOT
        ``al`` — the wrong name is silently ignored). ``rtl`` alone does not
        right-align the text — the paragraph inherits left alignment from the
        default slide layouts, so Arabic renders left-aligned (looks LTR). For
        RTL we force ``algn="r"``; callers can override (e.g. ``ctr`` for titles).
        """
        from pptx.dml.color import RGBColor
        from pptx.util import Pt

        paragraph.text = text
        # Direction + alignment on the paragraph properties.
        pPr = paragraph._p.get_or_add_pPr()
        pPr.set("rtl", "1" if self.profile.rtl else "0")
        if align is None:
            align = "r" if self.profile.rtl else "l"
        pPr.set("algn", align)
        # Theme the run.
        if paragraph.runs:
            run = paragraph.runs[0]
            run.font.size = Pt(size)
            run.font.name = font
            run.font.bold = bold
            run.font.color.rgb = RGBColor.from_string(color)
