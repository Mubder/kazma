"""PDF engine for the unified document layer.

Consumes a :class:`~kazma_core.documents.content_model.ContentModel` under a
:class:`~kazma_core.documents.profile.DocProfile` and writes a ``.pdf`` via
ReportLab. Direction + alignment + theme come from the profile, so this engine
shares one design language with :class:`~kazma_core.documents.engines.docx.DocxEngine`.

ReportLab is a *visual* drawing engine: it does not shape Arabic (joining
forms) nor apply the Unicode BiDi algorithm. When ``profile.shape_arabic`` is
set, text is pre-shaped via
:func:`kazma_core.documents.rich_render.shape_for_pdf` /
:func:`shape_arabic_wrapped` before drawing. Markdown parsing is reused from
:mod:`kazma_core.documents.rich_render`.

Alignment is taken from the profile policy: ``profile.pdf_align("start")`` →
``TA_RIGHT`` (RTL) / ``TA_LEFT`` (LTR); ``"justify"`` → ``TA_JUSTIFY``. The
profile stays ReportLab-free (it returns the constant *name*; this engine
resolves it), so the direction logic has one home.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_core.documents.content_model import (
    BodyBlock,
    Block,
    CitationBlock,
    ContentModel,
    HeadingBlock,
    ImageBlock,
    TableBlock,
    TitleBlock,
    TOCBlock,
)
from kazma_core.documents.profile import DocProfile
from kazma_core.documents.rich_render import (
    inline_markdown_to_reportlab,
    pdf_flowables_from_body,
    shape_for_pdf,
)

logger = logging.getLogger(__name__)

__all__ = ["PdfEngine"]

# profile.pdf_align() returns these constant names; resolve to ReportLab values
# at use time so the profile module has no ReportLab dependency.
_TA: dict[str, Any] | None = None


def _ta(name: str) -> Any:
    global _TA
    if _TA is None:
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
        _TA = {
            "TA_LEFT": TA_LEFT, "TA_RIGHT": TA_RIGHT,
            "TA_CENTER": TA_CENTER, "TA_JUSTIFY": TA_JUSTIFY,
        }
    return _TA[name]


class PdfEngine:
    """Render a :class:`ContentModel` to ``.pdf`` under a :class:`DocProfile`.

    Instantiate per document: ``PdfEngine(profile, warnings).render(model, output)``.
    """

    def __init__(self, profile: DocProfile, warnings: list[str] | None = None) -> None:
        self.profile = profile
        self.theme = profile.theme
        self.warnings = warnings if warnings is not None else []
        self.shape_ar = profile.shape_arabic

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def render(self, model: ContentModel, output: Path | str, *,
               assets_dir: Any = None) -> None:
        """Render the model to PDF.

        For RTL (Arabic) content, prefer the **DOCX → LibreOffice → PDF** route:
        reportlab is a visual LTR engine and cannot correctly shape mixed
        Arabic+Latin+inline-markdown content (tokens jam, Latin splits across
        lines, line flow breaks). Routing through the DOCX engine (which Word
        and LibreOffice shape with a real bidi engine) yields professional
        Arabic PDFs — correct joining, proper token spacing, bold/code honoured,
        and RTL tables. Falls back to the reportlab path when LibreOffice is
        unavailable or the conversion fails.
        """
        output = Path(output)
        if self.profile.rtl and self._render_via_docx(model, output, assets_dir):
            return
        self._render_reportlab(model, output, assets_dir)

    def _render_via_docx(self, model: ContentModel, output: Path,
                         assets_dir: Any = None) -> bool:
        """DOCX → LibreOffice → PDF. Returns True on success, False to fall back."""
        import tempfile

        from kazma_core.documents.binaries import find_soffice, run_soffice_cli
        from kazma_core.documents.engines.docx import DocxEngine

        if not find_soffice():
            self.warnings.append(
                "Arabic PDF rendered via the limited reportlab path; install "
                "LibreOffice (soffice) for full-quality mixed-content Arabic PDFs"
            )
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="kazma_pdf_docx_") as tmp:
                tmp_dir = Path(tmp)
                docx_path = tmp_dir / (output.stem + ".docx")
                DocxEngine(self.profile).render(model, docx_path, assets_dir=assets_dir)
                run_soffice_cli(
                    ("--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={tmp_dir.as_uri()}",
                     "--convert-to", "pdf",
                     "--outdir", str(tmp_dir), str(docx_path)),
                    timeout=120, cwd=tmp_dir,
                )
                produced = tmp_dir / (output.stem + ".pdf")
                if not produced.is_file():
                    return False
                # Atomic move to the final output location.
                import shutil
                shutil.move(str(produced), str(output))
            return True
        except Exception:
            logger.debug("[pdf] DOCX→LibreOffice route failed; falling back to reportlab", exc_info=True)
            self.warnings.append(
                "Arabic PDF DOCX-route conversion failed; used the limited reportlab fallback"
            )
            return False

    # ================================================================== #
    # reportlab path (LTR, and RTL fallback when LibreOffice is absent)
    # ================================================================== #
    def _render_reportlab(self, model: ContentModel, output: Path | str,
                          assets_dir: Any = None) -> None:
        self._assets_dir = assets_dir
        from reportlab.lib import colors
        # Page size from the shared theme (single source) — keeps the reportlab
        # PDF geometry identical to the DOCX / DOCX-route PDF. ``A4`` stays a
        # (width, height) points tuple so the rest of this method is unchanged.
        from reportlab.lib.units import mm
        _ps_mm = self.theme.get("page_size_mm", (210.0, 297.0))
        A4 = (float(_ps_mm[0]) * mm, float(_ps_mm[1]) * mm)
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        from kazma_core.documents.style_theme import theme_colors_reportlab

        font, bold_font = self._setup_fonts(pdfmetrics, TTFont)
        th = theme_colors_reportlab()
        style = self.profile.theme  # raw THEME dict (sizes are floats here)

        def _size(name: str, default: float, low: float, high: float) -> float:
            try:
                return min(high, max(low, float(style.get(name, default))))
            except (TypeError, ValueError):
                self.warnings.append(f"Invalid {name} style token; deterministic default applied")
                return default

        title_size = _size("title_font_size", float(self.theme["title_size"]), 10, 36)
        heading_size = _size("heading_font_size", float(self.theme["h2_size"]), 8, 28)
        body_size = _size("body_font_size", float(self.theme["body_size"]), 6, 18)
        accent = th["accent"]

        rich_styles = self._build_styles(
            ParagraphStyle, th, font, bold_font,
            title_size, heading_size, body_size,
        )
        heading_fill = rich_styles["heading_fill"]
        body_size_actual = body_size

        def _bar(text_html: str, para_style: Any, *, fill: Any | None = None) -> Any:
            para = Paragraph(text_html, para_style)
            fill_c = fill if fill is not None else heading_fill
            tbl = Table([[para]], colWidths=["*"])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), fill_c),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            return tbl

        page_margin = float(self.theme.get("page_margin", 54))
        col_width = float(A4[0]) - 2 * page_margin

        # Stash chrome for the canvas callback (set per-render on the instance).
        self._model_header = model.header
        self._model_footer = model.footer
        self._page_numbers = model.page_numbers

        # Build the story from the content model.
        story: list[Any] = []
        for block in model.blocks:
            try:
                self._render_block(block, story, _bar, Paragraph, Spacer, PageBreak,
                                   Table, TableStyle, colors, th, rich_styles,
                                   font, bold_font, col_width, body_size_actual, accent)
            except Exception:
                logger.debug("[pdf] block render failed: %r", block, exc_info=True)

        # Reshaping library availability warning (only matters when shaping).
        if self.shape_ar:
            try:
                import arabic_reshaper  # noqa: F401
                from bidi.algorithm import get_display  # noqa: F401
            except ImportError:
                self.warnings.append(
                    "arabic_reshaper/python-bidi not installed — Arabic letters may appear "
                    "disconnected or reversed in PDF"
                )
        # Images are embedded only from the validated assets dir; warn when the
        # payload requested images but none can be embedded (no approved assets).
        if model.images_present and not getattr(self, "_assets_dir", None):
            self.warnings.append(
                "Images were omitted because no approved render assets were provided"
            )

        def decorate(canvas: Any, document: Any) -> None:
            self._decorate(canvas, document, A4, font, th)

        # Subclass that registers heading bars into a TableOfContents via
        # afterFlowable → notify. multiBuild (two-pass) gives the TOC real
        # page numbers (pass 1 records where headings land, pass 2 renders).
        class _TocDocTemplate(SimpleDocTemplate):
            def afterFlowable(self, flowable: Any) -> None:
                entry = getattr(flowable, "_toc_entry", None)
                if entry:
                    level, text = entry
                    self.notify("TOCEntry", (level - 1, text, self.page))

        _TocDocTemplate(
            str(output), pagesize=A4,
            leftMargin=page_margin, rightMargin=page_margin,
            topMargin=page_margin + 4, bottomMargin=page_margin,
        ).multiBuild(story, onFirstPage=decorate, onLaterPages=decorate)

    # ================================================================== #
    # font setup
    # ================================================================== #
    def _setup_fonts(self, pdfmetrics: Any, TTFont: Any) -> tuple[str, str]:
        # Lazy import to avoid a renderer_worker cycle (_font_paths lives there).
        from kazma_core.documents.renderer_worker import _font_paths

        regular, bold = _font_paths()
        font = "Helvetica"
        bold_font = "Helvetica-Bold"
        if regular:
            pdfmetrics.registerFont(TTFont("KazmaUnicode", str(regular)))
            font = "KazmaUnicode"
            if bold and bold.is_file():
                pdfmetrics.registerFont(TTFont("KazmaUnicodeBold", str(bold)))
                bold_font = "KazmaUnicodeBold"
            else:
                bold_font = font
        else:
            self.warnings.append(
                "Unicode font unavailable; PDF uses a limited deterministic fallback"
            )
        return font, bold_font

    # ================================================================== #
    # styles — every value preserved from the legacy _generate_pdf
    # ================================================================== #
    def _build_styles(
        self, ParagraphStyle: Any, th: dict[str, Any], font: str, bold_font: str,
        title_size: float, heading_size: float, body_size: float,
    ) -> dict[str, Any]:
        from kazma_core.documents.style_theme import THEME

        rtl = self.profile.rtl
        # Justified body in both languages. After shape_for_pdf() ReportLab draws
        # the *visual* string LTR — do NOT set wordWrap="RTL".
        wrap = "CJK" if rtl else "LTR"
        align = _ta(self.profile.pdf_align("start"))   # headings/title/bullets/cite
        body_align = _ta(self.profile.pdf_align("justify"))
        accent = th["accent"]
        heading_fill = th["heading_fill"]
        body_color = th["body"]

        title_style = ParagraphStyle(
            "KazmaTitle", fontName=bold_font, fontSize=title_size,
            leading=title_size * 1.35, textColor=th["heading_text"],
            alignment=align, wordWrap=wrap, spaceBefore=0, spaceAfter=0,
        )
        h1_bar = ParagraphStyle(
            "KazmaH1Bar", fontName=bold_font, fontSize=float(THEME["h1_size"]),
            leading=float(THEME["h1_size"]) * 1.35, textColor=th["heading_text"],
            alignment=align, wordWrap=wrap, spaceBefore=0, spaceAfter=0,
        )
        h2_bar = ParagraphStyle(
            "KazmaH2Bar", fontName=bold_font, fontSize=heading_size,
            leading=heading_size * 1.35, textColor=th["heading_text"],
            alignment=align, wordWrap=wrap, spaceBefore=0, spaceAfter=0,
        )
        h3_style = ParagraphStyle(
            "KazmaH3", fontName=bold_font, fontSize=float(THEME["h3_size"]),
            leading=float(THEME["h3_size"]) * 1.4, spaceBefore=10, spaceAfter=4,
            textColor=th["heading"], alignment=align, wordWrap=wrap,
        )
        body_style = ParagraphStyle(
            "KazmaBody", fontName=font, fontSize=body_size,
            leading=body_size * float(THEME["line_height"]), textColor=body_color,
            alignment=body_align, wordWrap=wrap, spaceAfter=8, firstLineIndent=0,
        )
        # Body paragraphs: ragged-right RTL for Arabic (v4 layout), full-column
        # justified for English. body_style (TA_JUSTIFY) stays parent/fallback.
        body_para_style = ParagraphStyle(
            "KazmaBodyPara", parent=body_style,
            alignment=(_ta("TA_RIGHT") if rtl else body_align),
        )
        # Citations/references: pin to start-of-reading-direction edge so RTL
        # Arabic citations align to the column right (x1 ~ 535), matching the
        # DOCX path. (DOCX uses jc=start under bidi = same physical edge.)
        cite_style = ParagraphStyle(
            "KazmaCite", parent=body_style, alignment=align,
            spaceBefore=0, spaceAfter=4,
        )
        bullet_style = ParagraphStyle(
            "KazmaBullet", parent=body_style,
            leftIndent=16, rightIndent=16, bulletIndent=0,
            alignment=align, spaceAfter=4,
        )
        number_style = ParagraphStyle("KazmaNumber", parent=bullet_style)
        quote_style = ParagraphStyle(
            "KazmaQuote", parent=body_style, textColor=th["quote"],
            leftIndent=12, rightIndent=12, backColor=th["bg_alt"],
            spaceBefore=4, spaceAfter=8,
        )
        code_style = ParagraphStyle(
            "KazmaCode", fontName=font, fontSize=8.5, leading=11,
            textColor=th["accent"], backColor=th["code_bg"],
            alignment=_ta("TA_LEFT"), wordWrap="CJK",
            leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=8,
        )
        return {
            "body": body_style, "body_para": body_para_style,
            "h1": h1_bar, "h2": h2_bar, "h3": h3_style,
            "bullet": bullet_style, "number": number_style,
            "quote": quote_style, "code": code_style, "cite": cite_style,
            "heading_fill": heading_fill, "title": title_style,
            "accent": accent,
        }

    # ================================================================== #
    # header / footer canvas
    # ================================================================== #
    def _decorate(self, canvas: Any, document: Any, A4: tuple, font: str, th: dict[str, Any]) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(th["muted"])
        canvas.setStrokeColor(th["border"])
        canvas.setLineWidth(0.8)
        canvas.line(document.leftMargin, A4[1] - 30, A4[0] - document.rightMargin, A4[1] - 30)
        canvas.line(document.leftMargin, 36, A4[0] - document.rightMargin, 36)

        header = self._model_header or ""
        footer = self._model_footer or ""
        hdr = shape_for_pdf(header) if self.shape_ar else header
        ftr = shape_for_pdf(footer) if self.shape_ar else footer
        if self.profile.rtl:
            canvas.drawRightString(A4[0] - document.rightMargin, A4[1] - 22, hdr)
            canvas.drawRightString(A4[0] - document.rightMargin, 22, ftr)
        else:
            canvas.drawString(document.leftMargin, A4[1] - 22, hdr)
            canvas.drawString(document.leftMargin, 22, ftr)
        if self._page_numbers:
            label = self.profile.chrome["page_fmt"].format(n=document.page)
            if self.shape_ar:
                label = shape_for_pdf(label)
            canvas.drawCentredString(A4[0] / 2, 22, label)
        canvas.restoreState()

    # cached model chrome (set at render start)
    _model_header: str | None = None
    _model_footer: str | None = None
    _page_numbers: bool = True

    # ================================================================== #
    # block dispatch
    # ================================================================== #
    def _render_block(
        self, block: Block, story: list[Any], _bar: Any, Paragraph: Any,
        Spacer: Any, PageBreak: Any, Table: Any, TableStyle: Any, colors: Any,
        th: dict[str, Any], styles: dict[str, Any], font: str, bold_font: str,
        col_width: float, body_size: float, accent: Any,
    ) -> None:
        if isinstance(block, TitleBlock):
            if block.level == 0:
                story.append(_bar(
                    inline_markdown_to_reportlab(block.text, shape_arabic=self.shape_ar),
                    styles["title"], fill=accent,
                ))
                story.append(Spacer(1, 14))
            else:
                # subtitle: direction-aligned sub-heading (mirrors DOCX level).
                story.append(Paragraph(
                    inline_markdown_to_reportlab(block.text, shape_arabic=self.shape_ar),
                    styles["h3"],
                ))
                story.append(Spacer(1, 10))
        elif isinstance(block, HeadingBlock):
            bar = _bar(
                inline_markdown_to_reportlab(block.text, shape_arabic=self.shape_ar),
                styles["h1"] if block.level <= 1 else styles["h2"],
            )
            bar._toc_entry = (block.level, block.text)  # for afterFlowable → TOC
            story.append(bar)
            story.append(Spacer(1, 8))
        elif isinstance(block, BodyBlock):
            story.extend(pdf_flowables_from_body(
                block.text, styles=styles, shape_arabic=self.shape_ar,
                Spacer=Spacer, Paragraph=Paragraph, colors=colors,
                Table=Table, TableStyle=TableStyle,
                font_name=font, bold_font_name=bold_font,
                col_width=col_width, font_size=body_size,
            ))
        elif isinstance(block, TOCBlock):
            story.append(_bar(
                inline_markdown_to_reportlab(self.profile.chrome["toc"], shape_arabic=self.shape_ar),
                styles["h2"],
            ))
            story.append(Spacer(1, 6))
            # Real TableOfContents flowable — multiBuild populates it with page
            # numbers + dot leaders from heading bars tagged via _toc_entry.
            from reportlab.lib.styles import ParagraphStyle as _PS
            from reportlab.platypus.tableofcontents import TableOfContents as _TOC
            toc = _TOC()
            toc.levelStyles = [
                _PS("TOC1", fontName=font, fontSize=11, leading=16,
                    leftIndent=20, firstLineIndent=-20, spaceBefore=4),
                _PS("TOC2", fontName=font, fontSize=10, leading=14,
                    leftIndent=40, firstLineIndent=-20, spaceBefore=2),
                _PS("TOC3", fontName=font, fontSize=10, leading=14,
                    leftIndent=60, firstLineIndent=-20, spaceBefore=2),
            ]
            story.append(toc)
            story.append(PageBreak())
        elif isinstance(block, TableBlock):
            if block.heading:
                story.append(_bar(
                    inline_markdown_to_reportlab(block.heading, shape_arabic=self.shape_ar),
                    styles["h2"],
                ))
                story.append(Spacer(1, 6))
            self._add_table(block, story, Table, TableStyle, th, font, bold_font, colors)
            story.append(Spacer(1, 10))
        elif isinstance(block, CitationBlock):
            story.append(_bar(
                inline_markdown_to_reportlab(self.profile.chrome["references"], shape_arabic=self.shape_ar),
                styles["h2"],
            ))
            story.append(Spacer(1, 6))
            for index, item in enumerate(block.items, 1):
                story.append(Paragraph(
                    inline_markdown_to_reportlab(f"{index}. {item}", shape_arabic=self.shape_ar),
                    styles["cite"],
                ))
        elif isinstance(block, ImageBlock):
            self._add_image(block, story, Spacer)

    def _add_image(self, block: ImageBlock, story: list[Any], Spacer: Any) -> None:
        """Embed an approved-asset image as a reportlab Image flowable (LTR path;
        the DOCX-route handles images via LibreOffice for RTL)."""
        from pathlib import Path

        if not getattr(self, "_assets_dir", None) or not block.name:
            return
        candidate = Path(str(self._assets_dir)) / Path(str(block.name)).name
        if not candidate.is_file():
            logger.debug("[pdf] image not in approved assets, skipped: %s", block.name)
            return
        try:
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Image

            iw, ih = ImageReader(str(candidate)).getSize()  # px
            target_w = block.width_in * 72.0  # in → pt
            img = Image(str(candidate), width=target_w, height=target_w * ih / iw)
            story.append(img)
            story.append(Spacer(1, 8))
        except Exception:
            logger.debug("[pdf] image embed failed: %s", block.name, exc_info=True)

    def _add_table(
        self, block: TableBlock, story: list[Any], Table: Any, TableStyle: Any,
        th: dict[str, Any], font: str, bold_font: str, colors: Any,
    ) -> None:
        def _cell(val: object) -> str:
            s = str(val)
            return shape_for_pdf(s) if self.shape_ar else s

        data = [[_cell(c) for c in block.headers]]
        data.extend([_cell(c) for c in row] for row in block.rows)
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTNAME", (0, 0), (-1, 0), bold_font),
            ("BACKGROUND", (0, 0), (-1, 0), th["table_header_bg"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), th["table_header_fg"]),
            ("BACKGROUND", (0, 1), (-1, -1), th["table_row_bg"]),
            ("TEXTCOLOR", (0, 1), (-1, -1), th["body"]),
            ("GRID", (0, 0), (-1, -1), 0.5, th["table_grid"]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT" if self.profile.rtl else "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
