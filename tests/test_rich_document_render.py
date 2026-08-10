"""Rich PDF/DOCX formatting + Arabic shaping for document generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.documents.rich_render import (
    inline_markdown_to_reportlab,
    is_arabic_dominant,
    parse_rich_blocks,
    shape_for_pdf,
)


def test_unified_theme_en_ar_pdf_share_heading_bars(tmp_path: Path) -> None:
    """EN and AR PDFs must use the same visual theme (bars + brand chrome)."""
    from kazma_core.documents.renderer_worker import _generate_pdf
    from kazma_core.documents.style_theme import THEME, localized_chrome
    from kazma_core.skills.exporter import generate_pdf_html_document

    body = (
        "## Overview\n\n"
        "A professional paragraph with enough words to exercise justified layout "
        "in both language modes of the shared document theme.\n\n"
        "| Feature | Status |\n|---|---|\n| Unified theme | Yes |\n"
    )
    body_ar = (
        "## نظرة عامة\n\n"
        "فقرة مهنية عربية طويلة بما يكفي لاختبار التبرير ونفس قالب التصميم.\n\n"
        "| الميزة | الحالة |\n|---|---|\n| قالب موحّد | نعم |\n"
    )
    w: list[str] = []
    en = tmp_path / "en.pdf"
    ar = tmp_path / "ar.pdf"
    _generate_pdf(
        en,
        {"title": "What is Kazma?", "lang": "en", "sections": [{"heading": "Main", "body": body}]},
        w,
    )
    _generate_pdf(
        ar,
        {"title": "ما هي كاظمة؟", "lang": "ar", "sections": [{"heading": "رئيسي", "body": body_ar}]},
        w,
    )
    assert en.stat().st_size > 2000 and ar.stat().st_size > 2000
    # HTML exporter shares THEME tokens
    html_en = generate_pdf_html_document(body, title="What is Kazma?", rtl=False)
    html_ar = generate_pdf_html_document(body_ar, title="ما هي كاظمة؟", rtl=True)
    assert THEME["heading_fill"] in html_en and THEME["heading_fill"] in html_ar
    assert 'dir="ltr"' in html_en and 'dir="rtl"' in html_ar
    assert localized_chrome(rtl=False)["brand"] in html_en or "Kazma" in html_en
    assert localized_chrome(rtl=True)["brand"] in html_ar or "كاظمة" in html_ar


def test_is_arabic_dominant() -> None:
    assert is_arabic_dominant("هذا نص عربي طويل بما يكفي") is True
    assert is_arabic_dominant("Hello English only document") is False
    assert is_arabic_dominant("Hello مرحبا mixed") is True  # enough AR letters


def test_parse_rich_blocks_headings_lists_bold() -> None:
    body = """# Main

Intro **bold** and *italic*.

## Details

- first
- second

1. one
2. two

> a quote
"""
    blocks = parse_rich_blocks(body)
    types = [b["type"] for b in blocks]
    assert "heading" in types
    assert "paragraph" in types
    assert "list" in types
    assert "quote" in types
    lists = [b for b in blocks if b["type"] == "list"]
    assert any(not b["ordered"] and len(b["items"]) == 2 for b in lists)
    assert any(b["ordered"] and len(b["items"]) == 2 for b in lists)


def test_shape_for_pdf_joins_arabic() -> None:
    raw = "مرحبا"
    shaped = shape_for_pdf(raw)
    # Shaping must change presentation forms (joined glyphs) or at least reorder
    assert shaped != raw or "م" not in shaped  # either reshaped forms or reordered
    assert len(shaped) >= 3
    # No reverse of logical without reshape libraries would leave isolated letters;
    # with libs, shaped string should not equal simple reverse alone necessarily.
    # Smoke: English unchanged
    assert shape_for_pdf("Hello") == "Hello"


def test_inline_markdown_to_reportlab_escapes_and_bold() -> None:
    html = inline_markdown_to_reportlab("Say **hi** & more", shape_arabic=False)
    assert "<b>" in html
    assert "hi" in html
    assert "&amp;" in html


def test_generate_pdf_arabic_and_markdown(tmp_path: Path) -> None:
    from kazma_core.documents.renderer_worker import _generate_pdf

    out = tmp_path / "ar.pdf"
    warnings: list[str] = []
    _generate_pdf(
        out,
        {
            "title": "تقرير الاختبار",
            "lang": "ar",
            "page_numbers": True,
            "sections": [
                {
                    "heading": "المقدمة",
                    "body": (
                        "## نظرة عامة\n\n"
                        "هذا **فقرة** عربية مبررة مع قائمة:\n\n"
                        "- البند الأول\n"
                        "- البند الثاني\n\n"
                        "ونص إنجليزي mixed: ISO/IEC 27001."
                    ),
                }
            ],
        },
        warnings,
    )
    assert out.is_file()
    assert out.stat().st_size > 800
    # Should not warn about missing reshape when deps present
    assert not any("arabic_reshaper" in w for w in warnings)


def test_generate_docx_arabic_rtl(tmp_path: Path) -> None:
    from docx import Document

    from kazma_core.documents.renderer_worker import _generate_docx

    out = tmp_path / "ar.docx"
    _generate_docx(
        out,
        {
            "title": "وثيقة اختبار",
            "lang": "ar",
            "sections": [
                {
                    "heading": "القسم الأول",
                    "body": (
                        "### فرعي\n\n"
                        "نص **عريض** مع نقاط:\n\n"
                        "- واحد\n"
                        "- اثنان\n"
                    ),
                }
            ],
        },
    )
    assert out.is_file()
    doc = Document(str(out))
    # Title/headings live in filled single-cell tables (PDF-parity bars)
    all_text = " ".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                all_text += " " + cell.text
    assert "وثيقة" in all_text
    assert "واحد" in all_text or "اثنان" in all_text
    assert len(doc.tables) >= 2  # title bar + section/heading bars
    from docx.oxml.ns import qn

    has_bidi = False
    for p in doc.paragraphs:
        p_pr = p._p.pPr
        if p_pr is not None and p_pr.find(qn("w:bidi")) is not None:
            has_bidi = True
            break
    # Also check table cell paragraphs (title bar is RTL)
    if not has_bidi:
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        p_pr = p._p.pPr
                        if p_pr is not None and p_pr.find(qn("w:bidi")) is not None:
                            has_bidi = True
    assert has_bidi
    # Document must open as RTL (section bidi), not LTR shell with Arabic text
    sect = doc.sections[0]._sectPr
    assert sect.find(qn("w:bidi")) is not None
    assert sect.find(qn("w:rtlGutter")) is not None
    tfl = doc.settings.element.find(qn("w:themeFontLang"))
    assert tfl is not None and tfl.get(qn("w:bidi")) == "ar-SA"
    for table in doc.tables:
        assert table._tbl.tblPr is not None
        assert table._tbl.tblPr.find(qn("w:bidiVisual")) is not None


def test_markdown_table_and_docx_justify_shading(tmp_path: Path) -> None:
    from docx import Document
    from docx.oxml.ns import qn

    from kazma_core.documents.renderer_worker import _generate_docx, _generate_pdf
    from kazma_core.documents.rich_render import parse_rich_blocks

    body = (
        "## Overview\n\n"
        "A longer paragraph that must be justified in the DOCX output so spacing "
        "looks even across full lines of professional body text for export tests.\n\n"
        "| Feature | Status |\n"
        "|---|---|\n"
        "| Tables | Yes |\n"
        "| Justify | Yes |\n"
    )
    blocks = parse_rich_blocks(body)
    assert any(b["type"] == "table" for b in blocks)

    pdf = tmp_path / "t.pdf"
    docx = tmp_path / "t.docx"
    warnings: list[str] = []
    payload = {
        "title": "Parity Test",
        "lang": "en",
        "sections": [{"heading": "Main", "body": body}],
        "tables": [{"heading": "Extra", "headers": ["A", "B"], "rows": [["1", "2"]]}],
    }
    _generate_pdf(pdf, payload, warnings)
    _generate_docx(docx, payload)
    assert pdf.is_file() and pdf.stat().st_size > 500
    doc = Document(str(docx))
    # title bar + section bar + markdown table + payload table (+ heading bars)
    assert len(doc.tables) >= 3
    found_jc = False
    found_bar_fill = False
    for p in doc.paragraphs:
        if "longer paragraph" in p.text and p._p.pPr is not None:
            jc = p._p.pPr.find(qn("w:jc"))
            if jc is not None and jc.get(qn("w:val")) == "both":
                found_jc = True
    for table in doc.tables:
        cell = table.rows[0].cells[0]
        tc_pr = cell._tc.tcPr
        if tc_pr is not None:
            shd = tc_pr.find(qn("w:shd"))
            if shd is not None and shd.get(qn("w:fill")):
                found_bar_fill = True
                break
    assert found_jc
    assert found_bar_fill

    # Collapsed one-line table (as shipped in Telegram DOCX) becomes real table
    collapsed = (
        "| Feature | Kazma | LangChain | |---|---|---| "
        "| Multi-agent | Native | Add-on | | HITL | Triple | Manual |"
    )
    from kazma_core.documents.rich_render import try_parse_pipe_table_blob

    parsed = try_parse_pipe_table_blob(collapsed)
    assert parsed is not None
    assert parsed["headers"][0] == "Feature"
    assert len(parsed["rows"]) >= 2


def test_generate_pdf_english_lists(tmp_path: Path) -> None:
    from kazma_core.documents.renderer_worker import _generate_pdf

    out = tmp_path / "en.pdf"
    warnings: list[str] = []
    _generate_pdf(
        out,
        {
            "title": "English Report",
            "lang": "en",
            "sections": [
                {
                    "heading": "Overview",
                    "body": (
                        "## Goals\n\n"
                        "A **strong** start.\n\n"
                        "- alpha\n"
                        "- beta\n\n"
                        "1. first\n"
                        "2. second\n"
                    ),
                }
            ],
        },
        warnings,
    )
    assert out.is_file() and out.stat().st_size > 500
