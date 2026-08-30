"""Layout and typography gate for generated documents.

Separate from ``test_arabic_text.py``: that file guards Arabic *text policy*
(direction, folding, shaping). This one guards how a document is *set on the
page*, which applies to English too — type scale, table geometry, widow and
orphan control, and page breaks.

Everything here is measured against a real rendered PDF with PyMuPDF rather
than asserted against the source, because the defects it covers were all
invisible at the code level and obvious the moment anyone looked at a page.

Findings covered:

* The PDF engine ignored the complex-script type scale that DOCX (``w:szCs``)
  and HTML (``_css``) both apply, so the same Arabic document was set at 11pt
  one way and 16pt the other and paginated to 6 pages versus 13.
* PDF tables carried no ``FONTSIZE`` at all and fell through to ReportLab's
  10pt default.
* RTL tables were not column-reversed in the PDF path, so column 1 sat on the
  left of an Arabic table while DOCX (``w:bidiVisual``) and HTML (``dir=rtl``)
  put it on the right — the same document, mirrored.
* The Arabic line-wrap safety margin was a 20pt fudge left over from measuring
  logical instead of shaped text, which showed up as an ~83pt left margin
  against a ~56pt right margin.
* Paragraph widows and orphans were uncontrolled in both PDF and HTML.
"""

from __future__ import annotations

import pathlib

import pytest
from kazma_core.documents import arabic
from kazma_core.documents.content_model import (
    BodyBlock,
    ContentModel,
    HeadingBlock,
    TableBlock,
    TitleBlock,
)
from kazma_core.documents.profile import DocProfile
from kazma_core.documents.style_theme import THEME, theme_cs_size

AR_SENTENCE = (
    "تسجل المنصة كل عملية عبور للحدود حتى يتمكن المشغل من إعادة بناء ما حدث "
    "دون الحاجة إلى قراءة الشيفرة المصدرية. "
)
EN_SENTENCE = (
    "The platform records every boundary crossing so an operator can "
    "reconstruct what happened without reading the source. "
)

_PDF_SOURCE = pathlib.Path("kazma-core/kazma_core/documents/engines/pdf.py")
_HTML_SOURCE = pathlib.Path("kazma-core/kazma_core/documents/engines/html.py")


def _render_pdf(model, profile, tmp_path, *, force_reportlab: bool):
    """Render to PDF, optionally pinning the reportlab fallback route.

    The fallback is the route that has to be tested explicitly: it is the only
    one available on a host without LibreOffice, and it is where every defect
    above lived.
    """
    from kazma_core.documents.engines import pdf as pdfmod

    original = pdfmod.PdfEngine._render_via_docx
    if force_reportlab:
        pdfmod.PdfEngine._render_via_docx = lambda self, *a, **k: False
    try:
        out = tmp_path / "doc.pdf"
        pdfmod.PdfEngine(profile, []).render(model, out)
        return out
    finally:
        pdfmod.PdfEngine._render_via_docx = original


def _spans(path):
    """Every non-blank text span as ``(text, size, bbox)``."""
    import pymupdf

    doc = pymupdf.open(path)
    try:
        return [
            (span["text"], round(span["size"], 1), span["bbox"])
            for page in doc
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip()
        ]
    finally:
        doc.close()


def _arabic_doc():
    body = AR_SENTENCE * 6
    model = ContentModel()
    model.add(TitleBlock(text="تقرير التدقيق الفني", level=0, fill="3b82f6"))
    model.add(HeadingBlock(text="المقدمة", level=1))
    model.add(BodyBlock(text=body))
    return model, DocProfile.for_content("تقرير التدقيق الفني\n" + body)


def _staggered_doc(lang: str, sections: int = 12):
    """A multi-page document whose section lengths vary.

    Uniform sections put every heading at the same offset, which tests nothing.
    Staggering the lengths lands headings at many different distances from the
    page foot, so at least one would be stranded without a keep-with-next rule.
    """
    sentence = AR_SENTENCE if lang == "ar" else EN_SENTENCE
    title = "تقرير التدقيق" if lang == "ar" else "Audit Report"
    model = ContentModel()
    model.add(TitleBlock(text=title, level=0, fill="3b82f6"))
    sample = [title]
    headings: set[str] = set()
    for index in range(sections):
        heading = f"القسم {index + 1}" if lang == "ar" else f"Section {index + 1}"
        body = sentence * (3 + (index * 5) % 17)
        model.add(HeadingBlock(text=heading, level=1))
        model.add(BodyBlock(text=body))
        headings.add(heading)
        sample += [heading, body[:200]]
    return model, DocProfile.for_content("\n".join(sample)), headings


class TestUnifiedTypeScale:
    """One design regardless of extension — measured, not claimed."""

    def test_arabic_pdf_uses_the_complex_script_body_size(self, tmp_path):
        pytest.importorskip("pymupdf")
        model, profile = _arabic_doc()
        assert profile.rtl

        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)
        sizes = sorted({size for _, size, _ in _spans(path)})
        expected = theme_cs_size(float(THEME["body_size"]))
        assert expected == pytest.approx(16.0)
        assert expected in sizes, (
            f"Arabic PDF body must be set at {expected}pt, as DOCX and HTML do; "
            f"sizes present: {sizes}"
        )

    def test_english_pdf_type_scale_is_untouched(self, tmp_path):
        """The complex-script scale must not leak into Latin documents."""
        pytest.importorskip("pymupdf")
        body = EN_SENTENCE * 8
        model = ContentModel()
        model.add(TitleBlock(text="Technical Audit", level=0, fill="3b82f6"))
        model.add(BodyBlock(text=body))
        profile = DocProfile.for_content("Technical Audit\n" + body)
        assert not profile.rtl

        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)
        sizes = sorted({size for _, size, _ in _spans(path)})
        assert float(THEME["body_size"]) in sizes, f"sizes present: {sizes}"

    def test_arabic_leading_differs_from_latin(self):
        """Arabic needs more leading at the same measure; the theme carries both."""
        assert float(THEME["line_height_ar"]) > float(THEME["line_height"])
        source = _PDF_SOURCE.read_text(encoding="utf-8")
        assert "line_height_ar" in source, (
            "the PDF engine must pick between the two leadings like the others"
        )

    def test_pdf_and_html_resolve_the_table_size_the_same_way(self):
        pdf_source = _PDF_SOURCE.read_text(encoding="utf-8")
        html_source = _HTML_SOURCE.read_text(encoding="utf-8")
        assert "theme_cs_size(10)" in pdf_source
        assert "theme_cs_size(10)" in html_source
        assert theme_cs_size(10) > 10

    def test_pdf_tables_set_an_explicit_size(self):
        """Falling through to ReportLab's 10pt default is not a design choice."""
        assert "FONTSIZE" in _PDF_SOURCE.read_text(encoding="utf-8")


class TestTableGeometry:
    def test_rtl_table_columns_read_right_to_left(self, tmp_path):
        """Matching ``w:bidiVisual`` (DOCX) and ``dir=rtl`` (HTML)."""
        pytest.importorskip("pymupdf")
        model = ContentModel()
        model.add(TitleBlock(text="جدول الأسعار", level=0, fill="3b82f6"))
        model.add(
            TableBlock(
                headers=["البند", "الكمية", "السعر"],
                rows=[["ترخيص", "10", "1250"]],
            )
        )
        profile = DocProfile.for_content("جدول الأسعار البند الكمية السعر ترخيص")
        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)

        wanted = {"البند", "الكمية", "السعر"}
        cells = [
            (box[0], arabic.to_logical(text).strip())
            for text, _, box in _spans(path)
            if arabic.to_logical(text).strip() in wanted
        ]
        cells.sort(key=lambda item: -item[0])  # rightmost column first
        assert [label for _, label in cells] == ["البند", "الكمية", "السعر"]

    def test_ltr_table_columns_are_not_reversed(self, tmp_path):
        pytest.importorskip("pymupdf")
        model = ContentModel()
        model.add(TitleBlock(text="Price Table", level=0, fill="3b82f6"))
        model.add(
            TableBlock(headers=["Item", "Qty", "Price"], rows=[["License", "10", "1250"]])
        )
        profile = DocProfile.for_content("Price Table Item Qty Price License")
        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)

        wanted = {"Item", "Qty", "Price"}
        cells = sorted(
            ((box[0], text.strip()) for text, _, box in _spans(path) if text.strip() in wanted),
            key=lambda item: item[0],
        )
        assert [label for _, label in cells] == ["Item", "Qty", "Price"]


class TestMargins:
    def test_rtl_body_margins_are_near_symmetric(self, tmp_path):
        """A 20pt wrap fudge used to leave the left margin ~27pt wider."""
        pytest.importorskip("pymupdf")
        import pymupdf

        model, profile = _arabic_doc()
        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)
        spans = _spans(path)
        doc = pymupdf.open(path)
        page_width = doc[0].rect.width
        doc.close()

        left = min(box[0] for _, _, box in spans)
        right = page_width - max(box[2] for _, _, box in spans)
        assert abs(left - right) < 20, (
            f"asymmetric body margins: left={left:.0f}pt right={right:.0f}pt"
        )

    def test_wrap_safety_margin_is_not_a_fudge_factor(self):
        from kazma_core.documents.rich_render import _ARABIC_BODY_LINE_SAFETY_PT

        assert _ARABIC_BODY_LINE_SAFETY_PT <= 8.0, (
            "the wrapper measures shaped text now; a large margin just eats the page"
        )

    def test_no_shaped_line_overflows_its_column(self):
        """If a line overflowed, ReportLab would re-split it mid-word."""
        import html as html_lib

        pytest.importorskip("reportlab")
        from kazma_core.documents.fonts import resolve_fonts
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        choice = resolve_fonts(arabic=True)
        if choice.regular is None or not choice.arabic_ready:
            pytest.skip("no Arabic-capable font available")
        pdfmetrics.registerFont(TTFont("KazmaLayoutProbe", str(choice.regular)))

        column = float(THEME["page_size_mm"][0]) * 72 / 25.4 - 2 * float(
            THEME["page_margin"]
        )
        rendered = inline_markdown_to_reportlab(
            AR_SENTENCE * 8,
            col_width=column,
            font_name="KazmaLayoutProbe",
            font_size=theme_cs_size(float(THEME["body_size"])),
        )
        widths = [
            pdfmetrics.stringWidth(
                html_lib.unescape(line),
                "KazmaLayoutProbe",
                theme_cs_size(float(THEME["body_size"])),
            )
            for line in rendered.split("<br/>")
        ]
        assert widths and max(widths) <= column, (
            f"a shaped line is {max(widths):.1f}pt wide in a {column:.1f}pt column"
        )


class TestPagination:
    """A heading must never be the last thing on a page."""

    @pytest.mark.parametrize("lang", ["en", "ar"])
    def test_no_heading_is_stranded_at_a_page_foot(self, lang, tmp_path):
        pymupdf = pytest.importorskip("pymupdf")

        model, profile, headings = _staggered_doc(lang)
        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)

        doc = pymupdf.open(path)
        try:
            assert len(doc) > 1, "the fixture must span several pages"
            stranded = []
            for number, page in enumerate(doc, start=1):
                if number == len(doc):
                    continue  # nothing follows the last page
                body = [
                    block
                    for block in page.get_text("blocks")
                    if (block[4] or "").strip()
                    and block[1] < page.rect.height - 60  # skip the footer band
                ]
                if not body:
                    continue
                last = " ".join(arabic.to_logical(body[-1][4]).split())
                if any(last == h or (len(last) < 40 and h in last) for h in headings):
                    stranded.append((number, last))
            assert not stranded, f"heading left alone at a page foot: {stranded}"
        finally:
            doc.close()

    def test_widow_and_orphan_control_is_declared(self):
        pdf_source = _PDF_SOURCE.read_text(encoding="utf-8")
        html_source = _HTML_SOURCE.read_text(encoding="utf-8")
        assert "allowWidows=0" in pdf_source and "allowOrphans=0" in pdf_source
        assert "orphans: 3" in html_source and "widows: 3" in html_source

    def test_heading_keep_with_next_exists_in_both_engines(self):
        docx_source = pathlib.Path(
            "kazma-core/kazma_core/documents/engines/docx.py"
        ).read_text(encoding="utf-8")
        assert "w:keepNext" in docx_source and "w:widowControl" in docx_source
        assert "_keep_headings_with_body" in _PDF_SOURCE.read_text(encoding="utf-8")


# ── bilingual documents ──────────────────────────────────────────────────

EN_TWEETS = [
    (
        "Kazma's major features (EN)",
        "Most AI runs in someone else's cloud, on someone else's data. Kazma "
        "runs on your machine, under your control. #Privacy #LocalAI",
    ),
    (
        "Local-first architecture (EN)",
        "Your data never leaves the box. Swarm orchestration, document "
        "intelligence and memory all run locally. #LocalFirst",
    ),
]
AR_TWEETS = [
    (
        "معظم الذكاء الاصطناعي يعمل في سحابة غيرك (AR)",
        "معظم الذكاء الاصطناعي يعمل في سحابة غيرك، على بيانات غيرك. كاظمة "
        "تعمل على جهازك، تحت سيطرتك الكاملة. #الخصوصية",
    ),
    (
        "بنية محلية أولاً (AR)",
        "بياناتك لا تغادر جهازك أبداً. تنسيق الأسراب وذكاء المستندات والذاكرة "
        "تعمل جميعها محلياً على جهازك. #محلي_أولاً",
    ),
]


def _bilingual_doc():
    """A tweet archive that is roughly half English and half Arabic.

    Modelled on a real generated document in which every Arabic block came out
    left-to-right. Its RTL ratio sits near the dominance threshold, which is
    the point: whichever way a single document-level direction resolved, one
    language was aligned backwards.
    """
    model = ContentModel()
    model.add(
        TitleBlock(text="Kazma (@KazmaAI) — Tweet Archive", level=0, fill="3b82f6")
    )
    sample = ["Kazma (@KazmaAI) — Tweet Archive"]
    model.add(
        BodyBlock(
            text="Complete archive of all tweets posted through the Kazma X "
            "connector. Account: @KazmaAI"
        )
    )
    for index, (heading, body) in enumerate(EN_TWEETS + AR_TWEETS, start=1):
        model.add(HeadingBlock(text=f"{index}. {heading}", level=1))
        model.add(BodyBlock(text=body))
        sample += [heading, body]
    return model, DocProfile.for_content("\n".join(sample))


class TestBilingualBlockDirection:
    """Direction is per block, not per document.

    A bilingual archive has English blocks and Arabic blocks. Resolving
    direction once for the whole document meant the minority language was laid
    out backwards on every one of its blocks — Arabic text left-aligned with no
    ``w:bidi`` in a mostly-English document, and English text right-aligned had
    the ratio tipped the other way.
    """

    def test_profile_resolves_each_block_separately(self):
        _, profile = _bilingual_doc()
        assert profile.block_direction(EN_TWEETS[0][1]) == "ltr"
        assert profile.block_direction(AR_TWEETS[0][1]) == "rtl"

    def test_blocks_without_strong_characters_inherit_the_document(self):
        """A divider, a bare number or a date has no direction of its own."""
        _, profile = _bilingual_doc()
        for neutral in ("────────────────", "2026-08-28", "18", "   "):
            assert profile.block_direction(neutral) == profile.direction

    def test_docx_stamps_bidi_per_paragraph(self, tmp_path):
        import re
        import zipfile

        from kazma_core.documents.engines.docx import DocxEngine

        model, profile = _bilingual_doc()
        out = tmp_path / "archive.docx"
        DocxEngine(profile).render(model, out)
        xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")

        rtl = ltr = 0
        mismatches = []
        for para in re.findall(r"<w:p\b.*?</w:p>", xml, re.S):
            text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para))
            if not text.strip() or arabic.first_strong(text) is None:
                continue
            found = re.search(r'<w:bidi w:val="(\d)"', para)
            value = found.group(1) if found else None
            wanted = "1" if arabic.direction_of(text) == "rtl" else "0"
            rtl += value == "1"
            ltr += value == "0"
            if value != wanted:
                mismatches.append((wanted, value, text[:40]))

        assert not mismatches, f"paragraph direction mismatches: {mismatches}"
        assert rtl >= 2 and ltr >= 2, (
            f"expected both directions in one document, got {rtl} RTL / {ltr} LTR"
        )

    def test_ltr_paragraphs_are_stamped_not_left_to_inherit(self):
        """In an RTL document an unmarked English paragraph inherits RTL.

        The Normal style and the section both carry ``w:bidi``, so silence is
        not neutral — the LTR branch has to say so explicitly.
        """
        source = pathlib.Path(
            "kazma-core/kazma_core/documents/engines/docx.py"
        ).read_text(encoding="utf-8")
        assert 'bidi.set(qn("w:val"), "1" if block_rtl else "0")' in source

    def test_pdf_puts_each_block_on_its_own_edge(self, tmp_path):
        pymupdf = pytest.importorskip("pymupdf")

        model, profile = _bilingual_doc()
        path = _render_pdf(model, profile, tmp_path, force_reportlab=True)

        doc = pymupdf.open(path)
        try:
            width = doc[0].rect.width
            wrong = []
            checked = 0
            for page in doc:
                for block in page.get_text("blocks"):
                    text = arabic.to_logical(block[4] or "").strip()
                    if len(text) < 25 or arabic.first_strong(text) is None:
                        continue
                    near_left = block[0] < 90
                    near_right = (width - block[2]) < 90
                    if near_left and near_right:
                        continue  # spans the measure; edge tells us nothing
                    got = "ltr" if near_left else "rtl"
                    checked += 1
                    if got != arabic.direction_of(text):
                        wrong.append((arabic.direction_of(text), got, text[:40]))
            assert checked >= 4, "not enough one-edge blocks to judge"
            assert not wrong, f"blocks on the wrong edge: {wrong}"
        finally:
            doc.close()

    def test_html_marks_only_the_blocks_that_differ(self):
        from kazma_core.documents.engines.html import HtmlEngine

        model, profile = _bilingual_doc()
        html = HtmlEngine(profile).render(model)
        opposite = "rtl" if profile.direction == "ltr" else "ltr"
        assert f'dir="{opposite}"' in html, "minority-direction blocks are unmarked"

    def test_uniform_documents_get_no_wrapper(self):
        """A single-language document must emit exactly what it did before."""
        from kazma_core.documents.engines.html import HtmlEngine

        body = EN_SENTENCE * 4
        model = ContentModel()
        model.add(TitleBlock(text="Audit Report", level=0, fill="3b82f6"))
        model.add(HeadingBlock(text="Summary", level=1))
        model.add(BodyBlock(text=body))
        profile = DocProfile.for_content("Audit Report\nSummary\n" + body)
        html = HtmlEngine(profile).render(model)
        assert 'dir="rtl"' not in html
