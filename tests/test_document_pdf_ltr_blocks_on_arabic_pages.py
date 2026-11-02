"""English blocks on a page with Arabic read as on a page without (FA-004).

The 2026-11 functional audit: a DOCX Kazma made, converted to PDF by Kazma
and read back, came out with its title line twice, a cut word ("The audit
codew") and a borderless table as "QuarterValue / Q1120 / Q2135". The minimal
repro: two English lines and a row "Q1" / "120" read correctly; add one
Arabic word anywhere on the page and the first line repeats and the row
reads "Q1120". On a page with right-to-left glyphs every block is re-read
from its glyphs (``pdf_layout.extract_pymupdf_page_text``), and

* ``pdf_rtl.text_in_rect`` took a glyph whose baseline lay up to half a font
  size above the block, so the line above joined it;
* ``logical_line``'s left-to-right return joined glyphs with no word gaps.

Through the real parser (``DocumentService``, in the document worker).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kazma_core.documents.parsers.pdf_rtl import Glyph, logical_line, text_in_rect

FONT = Path(__file__).resolve().parents[1] / "kazma-core/kazma_core/documents/assets/fonts"


def _plex() -> Path:
    found = sorted(FONT.glob("IBMPlexSansArabic-Regular*"))
    if not found:
        pytest.skip("the bundled Arabic face is not here")
    return found[0]


def _minimal_pdf(path: Path, *, arabic: bool) -> Path:
    """Two English lines and a row "Q1" / "120" a column apart; with
    *arabic*, one Arabic word lower on the page."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="plex", fontfile=str(_plex()))
    page.insert_text((72, 100), "Kazma audit report", fontname="plex", fontsize=14)
    page.insert_text((72, 122), "The audit codeword is QUILL-HARBOR-58.", fontname="plex", fontsize=11)
    page.insert_text((72, 160), "Q1", fontname="plex", fontsize=11)
    page.insert_text((200, 160), "120", fontname="plex", fontsize=11)
    if arabic:
        page.insert_text((300, 300), "تقرير", fontname="plex", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _read(path: Path) -> str:
    from kazma_core.documents.service import DocumentService

    ir = DocumentService().parse_ingested_blob(path)
    return "\n".join(b.text for p in ir.pages for b in p.blocks)


def _english(text: str) -> list[str]:
    """The lines with no Arabic letter (the Arabic word is drawn by PyMuPDF's
    plain text insertion, unshaped: its reading is not under test here)."""
    return [line for line in text.splitlines()
            if line.strip() and not any("؀" <= ch <= "ۿ" for ch in line)]


def test_one_arabic_word_does_not_change_how_the_english_reads(tmp_path):
    plain = _read(_minimal_pdf(tmp_path / "plain.pdf", arabic=False))
    mixed = _read(_minimal_pdf(tmp_path / "mixed.pdf", arabic=True))
    # The same words in the same order: the plain reader puts the two cells
    # on lines of their own, the glyph reader on one line, a space apart.
    assert " ".join(_english(mixed)).split() == " ".join(_english(plain)).split()
    assert mixed.count("Kazma audit report") == 1
    assert "codeword is QUILL-HARBOR-58." in mixed
    assert "Q1120" not in mixed and "Q1" in mixed and "120" in mixed


def test_the_old_rules_read_the_row_as_one_number(monkeypatch):
    """Negative control: the old left-to-right return (no word gaps) and the
    old half-size slack."""
    from kazma_core.documents.parsers import pdf_rtl

    row = [Glyph("Q", 72, 79, 160, 11), Glyph("1", 79, 85, 160, 11), Glyph("1", 200, 206, 160, 11)]
    monkeypatch.setattr(pdf_rtl, "_with_word_gaps", lambda glyphs: glyphs)
    assert logical_line(row) == "Q11"
    monkeypatch.setattr(pdf_rtl, "_BASELINE_SLACK", 0.5)
    above = [Glyph("T", 72, 78, 100.0, 11.0)]
    line = [Glyph("B", 72, 78, 116.0, 11.0)]
    assert text_in_rect([above, line], (60, 104.5, 400, 119)) == "T\nB"


def test_a_block_takes_only_glyphs_whose_baseline_is_inside_it():
    """The glyph rule, on a block whose top sits just under the line above
    (a baseline 0.4 size above the block was taken)."""
    above = [Glyph(c, 72 + 6 * i, 77 + 6 * i, 100.0, 11.0) for i, c in enumerate("Title")]
    line = [Glyph(c, 72 + 6 * i, 77 + 6 * i, 116.0, 11.0) for i, c in enumerate("Body")]
    assert text_in_rect([above, line], (60, 104.5, 400, 119)) == "Body"


def test_a_left_to_right_line_keeps_its_word_gaps():
    row = [Glyph("Q", 72, 79, 160, 11), Glyph("1", 79, 85, 160, 11),
           Glyph("1", 200, 206, 160, 11), Glyph("2", 206, 212, 160, 11), Glyph("0", 212, 218, 160, 11)]
    assert logical_line(row) == "Q1 120"


def test_the_round_trip_of_a_kazma_document_reads_its_table(tmp_path):
    """A DOCX Kazma makes, converted to PDF by Kazma (the LibreOffice route,
    because the page has Arabic), read back by Kazma."""
    if not (shutil.which("soffice") or Path("C:/Program Files/LibreOffice/program/soffice.exe").exists()):
        pytest.skip("LibreOffice is not installed (the Arabic PDF route)")
    from kazma_core.documents.content_model import BodyBlock, ContentModel, TableBlock, TitleBlock
    from kazma_core.documents.engines.pdf import PdfEngine
    from kazma_core.documents.profile import DocProfile

    model = ContentModel()
    model.add(TitleBlock(text="Kazma audit report", level=0))
    model.add(BodyBlock(text="The audit codeword is QUILL-HARBOR-58."))
    model.add(BodyBlock(text="ملاحظة: هذا التقرير تجريبي."))
    model.add(TableBlock(headers=["Quarter", "Value"], rows=[["Q1", "120"], ["Q2", "135"]]))
    profile = DocProfile.for_content("Kazma audit report ملاحظة", language="en")
    pdf = tmp_path / "report.pdf"
    PdfEngine(profile, []).render(model, pdf)
    text = _read(pdf)
    assert text.count("Kazma audit report") == 1
    assert "QUILL-HARBOR-58" in text
    assert "Q1 | 120" in text and "Q2 | 135" in text
    assert "Q1120" not in text
