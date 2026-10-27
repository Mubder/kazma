"""A body block that cannot be rendered fails the render; it is never dropped.

Audit 2026-10-26 AUD-025: the DOCX, HTML and PDF engines rendered each body
block inside ``try: ... except Exception: logger.debug(...)``. A table or
paragraph that hit a render bug was missing from the generated document, with
no warning and the artifact reported ready -- exactly what AGENTS.md §19's
``DocumentRenderError`` rule forbids ("never a document that looks
successful but is wrong").

Now each engine raises ``DocumentRenderError`` naming the block. Negative
control: the old loop, replayed, returns a document without the table.
"""

from __future__ import annotations

import pytest
from kazma_core.documents.content_model import TableBlock
from kazma_core.documents.errors import DocumentRenderError
from kazma_core.documents.renderer_worker import _build_model_and_profile

_PAYLOAD = {
    "title": "Quarterly report",
    "lang": "en",
    "sections": [{"heading": "Summary", "body": "Revenue rose in every region."}],
    "tables": [{"heading": "Figures", "headers": ["Region", "Revenue"], "rows": [["North", "12"]]}],
}


def _break_tables(monkeypatch, engine_cls) -> None:
    real = engine_cls._render_block

    def render_block(self, *args, **kwargs):
        if any(isinstance(arg, TableBlock) for arg in args):
            raise ValueError("a render bug in the table path")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(engine_cls, "_render_block", render_block)


def test_docx_refuses_to_drop_a_table(tmp_path, monkeypatch):
    from kazma_core.documents.engines.docx import DocxEngine

    model, profile = _build_model_and_profile(dict(_PAYLOAD))
    _break_tables(monkeypatch, DocxEngine)
    with pytest.raises(DocumentRenderError, match="TableBlock"):
        DocxEngine(profile).render(model, tmp_path / "out.docx")


def test_html_refuses_to_drop_a_table(monkeypatch):
    from kazma_core.documents.engines.html import HtmlEngine

    model, profile = _build_model_and_profile(dict(_PAYLOAD))
    _break_tables(monkeypatch, HtmlEngine)
    with pytest.raises(DocumentRenderError, match="TableBlock") as caught:
        HtmlEngine(profile).render(model)
    assert caught.value.code == "document_render_failed"


def test_pdf_refuses_to_drop_a_table(tmp_path, monkeypatch):
    from kazma_core.documents.engines.pdf import PdfEngine

    model, profile = _build_model_and_profile(dict(_PAYLOAD))
    _break_tables(monkeypatch, PdfEngine)
    with pytest.raises(DocumentRenderError, match="TableBlock"):
        PdfEngine(profile, []).render(model, tmp_path / "out.pdf")


def test_a_bilingual_pdf_holds_the_blocks_of_both_directions(tmp_path, monkeypatch):
    # What the dropped-block loop hid: ``_mirror_styles`` cloned every entry
    # of the style set, the accent colour too (a reportlab ``Color.clone``
    # takes no name), so on the ReportLab route every block whose direction
    # differs from the document's raised and was dropped.
    pymupdf = pytest.importorskip("pymupdf")
    from kazma_core.documents.content_model import BodyBlock, ContentModel, HeadingBlock, TitleBlock
    from kazma_core.documents.engines import pdf as pdfmod
    from kazma_core.documents.profile import DocProfile

    english = "The quarterly figures rose in every region we serve."
    arabic = "ارتفعت الأرقام الفصلية في كل منطقة نخدمها هذا العام."
    model = ContentModel()
    model.add(TitleBlock(text="Report", level=0))
    model.add(HeadingBlock(text="Summary", level=1))
    model.add(BodyBlock(text=english))
    model.add(HeadingBlock(text="الملخص", level=1))
    model.add(BodyBlock(text=arabic))
    profile = DocProfile.for_content("\n".join([english, english, "Summary", arabic]))
    monkeypatch.setattr(pdfmod.PdfEngine, "_render_via_docx", lambda self, *a, **k: False)
    out = tmp_path / "bilingual.pdf"

    pdfmod.PdfEngine(profile, []).render(model, out)

    from kazma_core.documents import arabic as arabic_text

    doc = pymupdf.open(out)
    try:
        text = arabic_text.to_logical(" ".join(page.get_text() for page in doc))
    finally:
        doc.close()
    assert "quarterly figures" in text
    assert "الفصلية" in text, "the other direction's blocks are missing from the PDF"


def test_an_unbroken_render_still_holds_the_table(monkeypatch):
    from kazma_core.documents.engines.html import HtmlEngine

    model, profile = _build_model_and_profile(dict(_PAYLOAD))
    assert "North" in HtmlEngine(profile).render(model)


def test_negative_control_the_old_loop_returns_a_document_without_the_table(monkeypatch):
    from kazma_core.documents.engines.html import HtmlEngine

    model, profile = _build_model_and_profile(dict(_PAYLOAD))
    _break_tables(monkeypatch, HtmlEngine)
    engine = HtmlEngine(profile)
    engine._assets_dir = None
    engine._last_heading_text = ""
    parts = []
    for block in model.blocks:  # the old loop: every failure dropped at DEBUG
        try:
            chunk = engine._render_block(block)
            if chunk:
                parts.append(chunk)
        except Exception:  # noqa: BLE001 -- the behaviour under test
            pass
    body = "\n".join(parts)
    assert "Revenue rose" in body and "North" not in body
