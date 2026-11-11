"""PPTX engine for the unified document layer — branded layout.

Slides don't share the document :class:`ContentModel` (they have their own
slides/bullets shape), so this engine consumes the native ``slides`` payload
**plus** a :class:`~kazma_core.documents.profile.DocProfile`. The profile
supplies the shared theme (accent / heading colours, fonts) and direction.

The deck is built on a blank layout with explicit shapes so the look is fully
controlled and consistent with the DOCX/PDF/HTML family:

  - 16:9 widescreen (modern default).
  - Title slide: full-width accent band with the title (white, centred).
  - Content slides: a **heading bar** (accent fill + white title) — the same
    heading-bar motif as DOCX/PDF/HTML — followed by the bullet body and a
    footer (brand + page number).
  - Direction per paragraph (``DocProfile.block_direction``, §19 I): an
    Arabic paragraph carries ``rtl="1"`` AND ``algn="r"`` (rtl alone does not
    right-align; the DrawingML attribute is ``algn``, not ``al``), an English
    one in an Arabic deck ``rtl="0"``/``algn="l"``. The footer follows the deck.
  - The house face in every slot (``engines/office_fonts.py``): each run, its
    paragraph's defaults and end mark name IBM Plex Sans Arabic in ``a:latin``,
    ``a:ea`` and ``a:cs``; so do the themes, the master's text styles, the
    notes master and the default text style; bullets are "•" in that face; and
    the face is embedded (``engines/pptx_fonts.py``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_core.documents.payloads import slide_heading
from kazma_core.documents.profile import DocProfile

logger = logging.getLogger(__name__)

__all__ = ["PptxEngine"]


class PptxEngine:
    """Render a slides payload to a branded ``.pptx`` under a :class:`DocProfile`."""

    def __init__(self, profile: DocProfile) -> None:
        self.profile = profile
        self.theme = profile.theme

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def render(self, payload: dict[str, Any], output: Path | str) -> None:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Inches

        t = self.theme
        colors = {
            "accent": str(t["accent"]).lstrip("#"),
            "heading": str(t["heading_fill"]).lstrip("#"),
            "body": str(t["body"]).lstrip("#"),
            "muted": str(t["muted"]).lstrip("#"),
        }

        presentation = Presentation()
        # 16:9 widescreen.
        presentation.slide_width = Inches(13.333)
        presentation.slide_height = Inches(7.5)
        # python-pptx leaves a stale type="screen4x3" label after resizing;
        # correct it so viewers categorise the deck as 16:9 (cosmetic but right).
        try:
            presentation._sldSz.set("type", "screen16x9")
        except Exception:
            logger.debug("[pptx] could not set sldSz type", exc_info=True)
        slide_w = presentation.slide_width
        slide_h = presentation.slide_height
        blank = presentation.slide_layouts[6]

        # File core properties (title/author/subject).
        try:
            cp = presentation.core_properties
            cp.title = str(payload.get("title") or "Presentation")
            cp.author = str(payload.get("author") or "Kazma")
            if payload.get("subject"):
                cp.subject = str(payload.get("subject"))
            if payload.get("keywords"):
                cp.keywords = str(payload.get("keywords"))
        except Exception:
            logger.debug("[pptx] core properties failed", exc_info=True)

        # ── Title slide ─────────────────────────────────────────────────── #
        title_slide = presentation.slides.add_slide(blank)
        band = title_slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, slide_w, Inches(2.8),
        )
        self._fill(band, colors["accent"])
        self._shape_text(
            band, str(payload.get("title", "Presentation")),
            color_hex="FFFFFF", bold=True, size=36, align="ctr", band=True,
        )
        subtitle = payload.get("subtitle")
        if subtitle:
            sub = title_slide.shapes.add_textbox(
                Inches(0.8), Inches(3.1), slide_w - Inches(1.6), Inches(0.8),
            )
            self._shape_text(sub, str(subtitle), color_hex=colors["muted"],
                             size=20, align="ctr")
        # Optional speaker notes on the title slide.
        self._set_notes(title_slide, payload.get("notes"))

        # ── Content slides ─────────────────────────────────────────────── #
        slides = payload.get("slides") if isinstance(payload.get("slides"), list) else []
        page_num = 1
        for value in slides:
            if not isinstance(value, dict):
                continue
            page_num += 1
            slide = presentation.slides.add_slide(blank)

            # Heading bar (mirrors DOCX/PDF/HTML heading bars).
            bar = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, 0, Inches(0.25), slide_w, Inches(0.95),
            )
            self._fill(bar, colors["heading"])
            # A slide's title is its "heading"; "title" is read too: the tool
            # named no key until 2026-11-12, and the model sent "title" for
            # every slide of a deck, all dropped (documents/payloads.py).
            self._shape_text(
                bar, slide_heading(value),
                color_hex="FFFFFF", bold=True, size=26, align=None, band=True,
            )

            # Bullet body.
            body = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.55),
                slide_w - Inches(1.2), slide_h - Inches(2.5),
            )
            body.fill.background()  # transparent
            body.line.fill.background()
            self._bullet_body(body, value, colors["body"])

            # Footer (brand + page number).
            footer = slide.shapes.add_textbox(
                Inches(0.4), slide_h - Inches(0.45), slide_w - Inches(0.8), Inches(0.3),
            )
            chrome = self.profile.chrome
            page_fmt = str(chrome.get("page_fmt") or "{n}")
            self._shape_text(
                footer,
                f'{chrome["brand"]}    ·    {page_fmt.format(n=page_num)}',
                color_hex=colors["muted"], size=10, align=None, rtl=self.profile.rtl,
            )
            # Optional speaker notes for this slide.
            self._set_notes(slide, value.get("notes"))

        # The notes master exists even with no notes, so notes a person adds
        # later start from the house face too (PowerPoint makes a missing one
        # from its own Calibri template).
        _ = presentation.notes_master
        self._house_package(presentation)
        self._embed_house_fonts(presentation)
        presentation.save(str(output))

    @staticmethod
    def _house_package(presentation: Any) -> None:
        """Every theme, master, layout, note and slide of the deck in the house face.

        The themes' font schemes (``house_theme``), and every DrawingML text
        property, list level and bullet in the other XML parts
        (``house_drawingml_tree``): the master's ``p:txStyles``, the notes
        master's ``p:notesStyle``, ``p:defaultTextStyle``, the layouts'
        list styles, and any run not styled one by one.
        """
        from pptx.opc.constants import CONTENT_TYPE as CT
        from pptx.opc.package import XmlPart

        from .office_fonts import house_drawingml_tree, house_theme

        for part in list(presentation.part.package.iter_parts()):
            if part.content_type == CT.OFC_THEME:
                if isinstance(part, XmlPart):
                    from pptx.oxml import parse_xml

                    part._element = parse_xml(house_theme(part.blob))
                else:
                    part.blob = house_theme(part.blob)
            elif isinstance(part, XmlPart):
                house_drawingml_tree(part._element)

    def _embed_house_fonts(self, presentation: Any) -> None:
        """Put the house face inside the deck (``engines/pptx_fonts.py``).

        Opened where IBM Plex Sans Arabic is not installed, PowerPoint draws
        the embedded face instead of substituting. A deck still saves without
        it if embedding fails, said at WARNING.
        """
        import struct

        from kazma_core.documents.fonts import _bundled_pair

        from .pptx_fonts import embed_fonts

        try:
            pair = _bundled_pair(arabic=True)
            if pair is None:
                return
            regular, bold = pair
            families = {str(self.theme.get("font_latin") or ""), str(self.theme.get("font_arabic") or "")}
            faces: dict[str, dict[str, Path]] = {}
            for family in families:
                if family and family.replace(" ", "").lower() in regular.stem.replace("-", "").lower():
                    faces[family] = {"regular": regular, **({"bold": bold} if bold else {})}
            if faces:
                embed_fonts(presentation, faces)
        except (OSError, ValueError, KeyError, struct.error) as exc:
            logger.warning("[pptx] house fonts not embedded (%s)", type(exc).__name__, exc_info=True)

    def _set_notes(self, slide: Any, notes: Any) -> None:
        """Attach speaker notes to a slide (creates the notes slide if needed).

        ``notes`` may be a string or a list of bullet strings. Each paragraph
        takes its own direction and the house face, like the slide's text:
        ``notes_text_frame.text`` alone wrote runs with no font at all.
        """
        if not notes:
            return
        lines = notes if isinstance(notes, list) else [str(notes)]
        tf = slide.notes_slide.notes_text_frame
        for index, line in enumerate(lines):
            para = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
            self._style_paragraph(para, text=str(line))

    # ================================================================== #
    # shape helpers
    # ================================================================== #
    def _fill(self, shape: Any, hex_color: str) -> None:
        from pptx.dml.color import RGBColor

        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(hex_color)
        shape.line.fill.background()  # no border

    def _shape_text(self, shape: Any, text: str, *, color_hex: str, bold: bool = False,
                    size: float = 18, align: str | None = None, band: bool = False,
                    rtl: bool | None = None) -> None:
        """Put themed text in a shape and style its single paragraph.

        ``align`` defaults to the paragraph's reading start; ``band`` tightens
        the internal margins and vertically centres text for full-width bars.
        """
        from pptx.enum.text import MSO_ANCHOR
        from pptx.util import Inches

        tf = shape.text_frame
        tf.word_wrap = True
        if band:
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
                setattr(tf, side, Inches(0.15))
        self._style_paragraph(tf.paragraphs[0], text=text, color_hex=color_hex, bold=bold,
                              size=size, align=align, rtl=rtl)

    def _bullet_body(self, shape: Any, value: dict[str, Any], body_hex: str) -> None:
        """Fill a body shape with bullets (or body lines), each its own direction.

        A bullet is the paragraph's own (``a:buChar`` "•" in the house face,
        hanging on the reading-start side), not a "•" typed into the text.
        """
        tf = shape.text_frame
        tf.word_wrap = True
        bullets = value.get("bullets")
        lines = bullets if isinstance(bullets, list) else str(value.get("body", "")).splitlines()
        for index, line in enumerate(lines):
            para = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
            self._style_paragraph(para, text=str(line), color_hex=body_hex, size=18, bullet=True)

    #: A bullet's hanging indent (the slide master's first level).
    _BULLET_INDENT_EMU = 342900

    def _style_paragraph(self, paragraph: Any, *, text: str | None = None,
                         color_hex: str | None = None, bold: bool = False,
                         size: float | None = None, align: str | None = None,
                         rtl: bool | None = None, bullet: bool = False) -> None:
        """Set a paragraph's text, direction, alignment, runs and defaults.

        RTL needs ``rtl="1"`` (reading order) AND ``algn`` alignment. ``rtl`` alone
        does not right-align — the paragraph inherits left from the layout, so
        Arabic looks LTR. DrawingML's attribute is ``algn`` (NOT ``al``).
        Direction is the paragraph's own unless *rtl* says (the footer's
        chrome follows the deck). The text is written as one run per script
        (``arabic.script_runs``), each with its language. Every run, the
        paragraph's defaults (``a:defRPr``) and its end mark
        (``a:endParaRPr``, what the caret and new text take) name the house
        face in all three slots.
        """
        from pptx.dml.color import RGBColor
        from pptx.text.text import Font
        from pptx.util import Pt

        from kazma_core.documents.arabic import has_rtl, script_runs

        from .office_fonts import house_bullet, house_text_properties

        if rtl is None:
            rtl = self.profile.block_is_rtl(
                text if text is not None else "".join(r.text for r in paragraph.runs))
        if text is not None:
            paragraph.clear()
            for index, line in enumerate(text.split("\n")):
                if index:
                    paragraph.add_line_break()
                for chunk, chunk_rtl in script_runs(line, rtl=rtl):
                    run = paragraph.add_run()
                    run.text = chunk
                    run._r.get_or_add_rPr().set("lang", "ar-SA" if chunk_rtl else "en-US")
        pPr = paragraph._p.get_or_add_pPr()
        pPr.set("rtl", "1" if rtl else "0")
        if align is None:
            align = "r" if rtl else "l"
        pPr.set("algn", align)
        if bullet:
            pPr.set("marL", str(self._BULLET_INDENT_EMU))
            pPr.set("indent", str(-self._BULLET_INDENT_EMU))
            house_bullet(pPr)

        def themed(props: Any, *, lang: str) -> None:
            font = Font(props)
            if size is not None:
                font.size = Pt(size)
            font.bold = bold
            if color_hex is not None:
                font.color.rgb = RGBColor.from_string(color_hex)
            props.set("lang", props.get("lang") or lang)
            house_text_properties(props)  # last: the faces go after the fill

        for run in paragraph.runs:
            themed(run._r.get_or_add_rPr(), lang="ar-SA" if has_rtl(run.text) else "en-US")
        lang = "ar-SA" if rtl else "en-US"
        themed(pPr.get_or_add_defRPr(), lang=lang)
        themed(paragraph._p.get_or_add_endParaRPr(), lang=lang)
