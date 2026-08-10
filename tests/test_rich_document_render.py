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
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert any("وثيقة" in t for t in texts)
    assert any("واحد" in t or "اثنان" in t for t in texts)
    # At least one paragraph should have bidi flag when RTL
    from docx.oxml.ns import qn

    has_bidi = False
    for p in doc.paragraphs:
        p_pr = p._p.pPr
        if p_pr is not None and p_pr.find(qn("w:bidi")) is not None:
            has_bidi = True
            break
    assert has_bidi


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
