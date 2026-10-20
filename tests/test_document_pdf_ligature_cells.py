"""A table cell with a ligature reads back as written.

Live 2026-10-20 (Telegram), test 18: asked to read back its own bilingual
weather PDF, Kazma quoted the English table's last cell as "frist rains of the
season" and doubted its own file. The page drew "first": IBM Plex's "fi" is
one glyph, and PyMuPDF's table reader -- used on a page with no Arabic --
put the ligature's "i" after the next glyph. Table cells are now read from
the glyphs on every page.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from kazma_core.documents.content_model import BodyBlock, ContentModel, TableBlock
from kazma_core.documents.engines.docx import DocxEngine
from kazma_core.documents.profile import DocProfile

CELLS = ["Gradual cooling; first rains of the season", "official figures", "Fifth office"]


def _soffice_available() -> bool:
    try:
        import fitz  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return bool(find_soffice())
    except Exception:
        return False


def _english_pdf(tmp_path: Path) -> Path:
    from kazma_core.documents.binaries import find_soffice, run_soffice_cli
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts

    model = ContentModel()
    model.add(BodyBlock(text="Kuwait has a hot desert climate."))
    model.add(TableBlock(headers=["Season", "Rain & wind"],
                         rows=[["Autumn", CELLS[0]], ["Note", CELLS[1]], ["Row", CELLS[2]]]))
    DocxEngine(DocProfile.for_content("Kuwait has a hot desert climate.")).render(model, tmp_path / "a.docx")
    give_soffice_the_bundled_fonts(tmp_path / "profile")
    run_soffice_cli((find_soffice(), "--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                     "--convert-to", "pdf", "--outdir", str(tmp_path), str(tmp_path / "a.docx"))[1:],
                    timeout=120, cwd=tmp_path)
    return tmp_path / "a.pdf"


def _table_text(path: Path) -> str:
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.parsers.common import ParseContext, sha256_path
    from kazma_core.documents.parsers.pdf import PdfParser

    config = DocumentConfig(storage_root=Path(tempfile.mkdtemp()), worker_timeout_seconds=60)
    context = ParseContext(config=config, source_sha256=sha256_path(path), mime_type="application/pdf",
                           extension=".pdf", parser_id="pdf", parser_version="1")
    ir = PdfParser().parse(path, context)
    return "\n".join(b.text for page in ir.pages for b in page.blocks
                     if getattr(b.block_type, "value", b.block_type) == "table")


@pytest.mark.skipif(not _soffice_available(), reason="needs LibreOffice and PyMuPDF")
def test_an_english_table_cell_with_a_ligature_reads_back_as_written(tmp_path, monkeypatch):
    pdf = _english_pdf(tmp_path)
    table = " ".join(_table_text(pdf).split())
    for cell in CELLS:
        assert cell in table, table

    # Negative control: PyMuPDF's own table reader on a page with no Arabic.
    from kazma_core.documents.parsers import pdf as pdf_parser

    monkeypatch.setattr(pdf_parser, "_logical_table", lambda table, matrix, glyphs, **_: matrix)
    old = " ".join(_table_text(pdf).split())
    assert any(cell not in old for cell in CELLS), old
