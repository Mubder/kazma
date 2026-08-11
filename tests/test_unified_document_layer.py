"""Unification tests for the document layer (Stage 2).

These verify the architectural goal: the SAME payload produces a ContentModel +
DocProfile that BOTH the DOCX and PDF engines consume, and the direction /
alignment policy on the profile is the single place those decisions live. A
generated DOCX and a generated PDF are projections of one model under one
profile — one design, regardless of extension.
"""

from __future__ import annotations

from pathlib import Path


def test_shared_builder_drives_both_formats(tmp_path: Path) -> None:
    """One payload → one (model, profile) → both DOCX and PDF engines.

    The DOCX and PDF generators must route through the shared
    ``_build_model_and_profile`` so a design change applies to both formats at
    once. This guards against either path re-growing format-specific payload
    parsing.
    """
    from kazma_core.documents.renderer_worker import _build_model_and_profile

    payload = {
        "title": "منظومة كاظمة",
        "subtitle": "تقرير",
        "lang": "ar",
        "toc": True,
        "sections": [{"heading": "المقدمة", "body": "نص عربي للفقرة الرئيسية هنا."}],
        "tables": [{"heading": "جدول", "headers": ["أ", "ب"], "rows": [["1", "2"]]}],
        "citations": ["مصدر أول"],
    }
    model, profile = _build_model_and_profile(payload)

    # The model carries every block type the payload expressed.
    from kazma_core.documents.content_model import (
        BodyBlock, CitationBlock, HeadingBlock, TableBlock, TitleBlock, TOCBlock,
    )
    block_types = [type(b) for b in model.blocks]
    assert TitleBlock in block_types          # title + subtitle
    assert TOCBlock in block_types
    assert HeadingBlock in block_types
    assert BodyBlock in block_types
    assert TableBlock in block_types
    assert CitationBlock in block_types

    # The profile is RTL for Arabic and carries the design + chrome.
    assert profile.rtl is True
    assert profile.chrome["toc"] == "المحتويات"

    # Both engines accept this exact (model, profile) and produce non-empty files.
    from kazma_core.documents.engines.docx import DocxEngine
    from kazma_core.documents.engines.pdf import PdfEngine

    docx_out = tmp_path / "out.docx"
    DocxEngine(profile).render(model, docx_out)
    assert docx_out.is_file() and docx_out.stat().st_size > 2000

    pdf_out = tmp_path / "out.pdf"
    PdfEngine(profile, []).render(model, pdf_out)
    assert pdf_out.is_file() and pdf_out.stat().st_size > 500


def test_profile_alignment_policy_is_direction_aware() -> None:
    """The alignment policy maps an *intent* to format-native values per direction.

    DOCX: "start" → "start" (bidi-logical; physical right under RTL). The
    bidi/jc inversion (jc=right → physical left) must never be produced.
    PDF:  "start" → "TA_RIGHT" (RTL) / "TA_LEFT" (LTR); "justify" → "TA_JUSTIFY".
    """
    from kazma_core.documents.profile import DocProfile

    ar = DocProfile.for_content("نص عربي", rtl=True)
    en = DocProfile.for_content("English text", rtl=False)

    # DOCX policy — uses the bidi-logical keyword "start" for BOTH directions
    # (start = physical left under LTR, physical right under RTL). "start" never
    # becomes "right" (which under w:bidi maps to the physical LEFT — the bug).
    assert ar.docx_jc("start") == "start"
    assert en.docx_jc("start") == "start"
    assert ar.docx_jc("justify") == en.docx_jc("justify") == "both"
    assert ar.docx_jc("end") == en.docx_jc("end") == "end"

    # PDF policy — visual mapping per direction (ReportLab is a visual engine).
    assert ar.pdf_align("start") == "TA_RIGHT"
    assert ar.pdf_align("justify") == "TA_JUSTIFY"
    assert en.pdf_align("start") == "TA_LEFT"
    assert en.pdf_align("justify") == "TA_JUSTIFY"


def test_shape_arabic_flag_reflects_content() -> None:
    """PDF engines pre-shape Arabic; the flag is RTL-or-Arabic-present.

    An LTR doc with embedded Arabic fragments must still shape (so glyphs join),
    matching the legacy ``shape_ar = rtl or is_arabic_dominant(sample)`` rule.
    """
    from kazma_core.documents.profile import DocProfile

    assert DocProfile.for_content("نص عربي واضح هنا", rtl=True).shape_arabic is True
    assert DocProfile.for_content("Plain English only", rtl=False).shape_arabic is False
    # LTR forced, but Arabic present → still shapes.
    assert DocProfile.for_content("مقدمة عربية", rtl=False).shape_arabic is True


def test_same_payload_yields_consistent_direction_across_overrides() -> None:
    """Explicit lang/rtl overrides resolve identically for both engines because
    they share one profile. Catches drift if either engine re-detects direction."""
    from kazma_core.documents.renderer_worker import _build_model_and_profile

    # Explicit rtl=False on Arabic content.
    _, prof1 = _build_model_and_profile({"title": "نص", "rtl": False})
    # Same via lang=ltr.
    _, prof2 = _build_model_and_profile({"title": "نص", "lang": "ltr"})
    assert prof1.direction == prof2.direction == "ltr"
    # And both still shape (Arabic present) — consistent PDF behavior.
    assert prof1.shape_arabic is True and prof2.shape_arabic is True


def test_html_engine_shares_profile_direction_theme_and_chrome(tmp_path: Path) -> None:
    """Stage 3: HTML export consumes the DocProfile, so an Arabic HTML doc
    carries dir=rtl + the shared THEME + localized chrome — matching the DOCX/PDF
    design. EN is dir=ltr. Guards against the old profile-free ``_markdown_html``
    regressing (it emitted no dir/lang/theme/chrome)."""
    from kazma_core.documents.renderer_worker import _generate_html
    from kazma_core.documents.style_theme import THEME

    ar = tmp_path / "ar.html"
    en = tmp_path / "en.html"
    payload_ar = {
        "title": "منظومة كاظمة", "lang": "ar", "toc": True,
        "sections": [{"heading": "المقدمة", "body": "نص عربي مع **تنسيق** و REST."}],
        "citations": ["مصدر أول"],
    }
    payload_en = {
        "title": "Kazma Platform", "lang": "en", "toc": True,
        "sections": [{"heading": "Intro", "body": "English body with **bold**."}],
        "citations": ["First source"],
    }
    _generate_html(ar, payload_ar)
    _generate_html(en, payload_en)
    ar_html = ar.read_text(encoding="utf-8")
    en_html = en.read_text(encoding="utf-8")

    # Direction + language from the profile.
    assert '<html lang="ar" dir="rtl">' in ar_html
    assert '<html lang="en" dir="ltr">' in en_html

    # Shared THEME token present in both (one design language).
    assert THEME["heading_fill"].lstrip("#") in ar_html
    assert THEME["heading_fill"].lstrip("#") in en_html

    # Localized chrome: Arabic TOC heading + references, English equivalents.
    assert "المحتويات" in ar_html
    assert "المراجع" in ar_html
    assert "Contents" in en_html
    assert "References" in en_html


def test_markdown_uses_localized_chrome_labels() -> None:
    """The Markdown emitter localizes Contents/References via the profile, so an
    Arabic .md matches the HTML/DOCX/PDF chrome (not hardcoded English)."""
    from kazma_core.documents.renderer_worker import _markdown

    ar_md = _markdown({"title": "تقرير", "lang": "ar", "toc": True,
                       "sections": [{"heading": "قسم", "body": "نص."}], "citations": ["مصدر"]})
    en_md = _markdown({"title": "Report", "toc": True,
                       "sections": [{"heading": "Sec", "body": "text."}], "citations": ["src"]})
    assert "## المحتويات" in ar_md and "## المراجع" in ar_md
    assert "## Contents" in en_md and "## References" in en_md


def test_convert_markdown_html_is_direction_aware() -> None:
    """convert:markdown:html (raw Markdown source, no payload) builds a profile
    from the text, so an Arabic Markdown source renders as dir=rtl HTML."""
    from kazma_core.documents.renderer_worker import _markdown_html

    ar_html = _markdown_html("# عنوان\n\nنص عربي واضح هنا في الفقرة الرئيسية للوثيقة.")
    en_html = _markdown_html("# Title\n\nPlain English markdown document body.")
    assert 'dir="rtl"' in ar_html and 'lang="ar"' in ar_html
    assert 'dir="ltr"' in en_html and 'lang="en"' in en_html


def test_xlsx_engine_is_themed_and_direction_aware(tmp_path: Path) -> None:
    """Stage 4: XLSX consumes the DocProfile — Arabic sheet view is right-to-left
    and header cells carry the shared theme fill; English is LTR. Both themed."""
    import zipfile

    from kazma_core.documents.renderer_worker import _generate_xlsx

    rows = [["الميزة", "القيمة"], ["المعالجة", "سريعة"], ["اللغة", "عربية"]]
    ar = tmp_path / "ar.xlsx"
    en = tmp_path / "en.xlsx"
    _generate_xlsx(ar, {"title": "تقرير", "lang": "ar",
                        "sheets": [{"name": "البيانات", "rows": rows}]})
    _generate_xlsx(en, {"title": "Report", "lang": "en",
                        "sheets": [{"name": "Data", "rows": [["Feature", "Value"], ["Speed", "Fast"]]}]})

    def sheet_xml(p: Path) -> str:
        return zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()

    def styles_xml(p: Path) -> str:
        return zipfile.ZipFile(p).read("xl/styles.xml").decode()

    # RTL sheet view for Arabic (rightToLeft="1"), not active for English.
    # openpyxl serializes rightToLeft="0" when False, so check the value.
    assert 'rightToLeft="1"' in sheet_xml(ar), "AR xlsx missing active rightToLeft sheet view"
    assert 'rightToLeft="1"' not in sheet_xml(en), "EN xlsx unexpectedly right-to-left"

    # Shared theme fill present in both (header band colour).
    assert "1e3a5f" in styles_xml(ar).lower()
    assert "1e3a5f" in styles_xml(en).lower()


def test_pptx_engine_is_themed_and_direction_aware(tmp_path: Path) -> None:
    """Stage 4: PPTX consumes the DocProfile — Arabic paragraphs carry rtl=1 and
    the title slide uses the shared accent/heading colour; English has no rtl."""
    import zipfile

    from kazma_core.documents.renderer_worker import _generate_pptx

    ar = tmp_path / "ar.pptx"
    en = tmp_path / "en.pptx"
    _generate_pptx(ar, {"title": "منظومة كاظمة", "lang": "ar",
                        "slides": [{"heading": "المقدمة", "bullets": ["نقطة أولى", "نقطة ثانية"]}]})
    _generate_pptx(en, {"title": "Kazma", "slides": [{"heading": "Intro", "bullets": ["one", "two"]}]})

    def slide_xml(p: Path, n: int) -> str:
        return zipfile.ZipFile(p).read(f"ppt/slides/slide{n}.xml").decode()

    # Arabic content slide paragraphs are rtl=1 AND right-aligned (algn="r").
    # rtl alone does not right-align (inherits left from the layout); the
    # DrawingML alignment attr is "algn" (NOT "al" — that's silently ignored),
    # so algn="r" is required for Arabic to render right-aligned.
    ar_slide2 = slide_xml(ar, 2)
    assert 'rtl="1"' in ar_slide2, "AR pptx missing rtl paragraphs"
    assert 'algn="r"' in ar_slide2, "AR pptx missing right alignment (algn=r) on content"
    assert 'rtl="1"' not in slide_xml(en, 2), "EN pptx unexpectedly rtl"

    # Shared accent/heading colour on the title slide for both.
    assert ("1e3a5f" in slide_xml(ar, 1).lower() or "0f172a" in slide_xml(ar, 1).lower())
    assert ("1e3a5f" in slide_xml(en, 1).lower() or "0f172a" in slide_xml(en, 1).lower())
