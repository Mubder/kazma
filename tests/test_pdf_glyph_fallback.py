"""A PDF draws every letter of a transliteration, and bold is bold (2026-10-31).

Live: Kazma's Kuwait history paper came out of ``generate_pdf`` with "Kāẓima"
read back as "K ima". IBM Plex Sans Arabic, the house face, has no Latin
Extended-A or Additional, and ReportLab draws a letter its face lacks blank;
the agent then stripped the transliteration from its own paper. The same
render drew every inline ``**bold**`` in the regular face: the house face was
never registered as a family, so ``<b>`` had no bold to find.
"""
from __future__ import annotations

import pytest

from kazma_core.documents.content_model import BodyBlock, ContentModel, HeadingBlock, TableBlock, TitleBlock
from kazma_core.documents.profile import DocProfile

pymupdf = pytest.importorskip("pymupdf")

WORDS = ["Kāẓima", "Ṣabāḥ", "ʿAbd", "Fīlaka", "Ḍūḥa"]


def _render(tmp_path, monkeypatch):
    from kazma_core.documents.engines import pdf as pdfmod

    monkeypatch.setattr(pdfmod.PdfEngine, "_render_via_docx", lambda self, *a, **k: False)
    model = ContentModel()
    model.add(TitleBlock(text="A History of Kuwait", level=0, fill="3b82f6"))
    model.add(HeadingBlock(text="The Āl Ṣabāḥ and Kāẓima", level=1))
    model.add(BodyBlock(text=(
        "The bay of **Kāẓima** was settled before the **Banī ʿUtūb** arrived; "
        "ʿAbd Allāh and Fīlaka appear in the records.\n\n"
        "| Place | Note |\n|---|---|\n| Ḍūḥa | a cove |\n"
    )))
    model.add(TableBlock(headers=["Ruler", "Years"], rows=[["Ṣabāḥ I", "1752"]]))
    out = tmp_path / "paper.pdf"
    pdfmod.PdfEngine(DocProfile.for_content("A History of Kuwait"), []).render(model, out)
    return out


def _chars(path):
    """(char, font) for every drawn character."""
    with pymupdf.open(path) as doc:
        return [(ch["c"], span["font"])
                for page in doc
                for block in page.get_text("rawdict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                for ch in span["chars"]]


def _text(path):
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def test_every_letter_of_a_transliteration_is_drawn(tmp_path, monkeypatch):
    text = _text(_render(tmp_path, monkeypatch))
    for word in WORDS + ["Āl Ṣabāḥ", "Banī ʿUtūb", "ʿAbd Allāh"]:
        assert word in text, word


def test_a_letter_the_house_face_lacks_is_drawn_in_a_face_that_has_it(tmp_path, monkeypatch):
    from kazma_core.documents.fonts import resolve_fonts
    from kazma_core.documents.glyph_fallback import _cmap

    plex = _cmap(str(resolve_fonts(arabic=False).regular))
    lacking = [(c, font) for c, font in _chars(_render(tmp_path, monkeypatch))
               if c.strip() and ord(c) not in plex]
    assert lacking, "the paper holds letters the house face lacks"
    assert not [c for c, font in lacking if font.startswith("IBMPlexSansArabic")]


def test_bold_text_is_drawn_in_a_bold_face(tmp_path, monkeypatch):
    chars = _chars(_render(tmp_path, monkeypatch))
    ink = "".join(c for c, font in chars if "Bold" in font)
    assert "Kāẓima" in ink and "Banī ʿUtūb" in ink
    body = "".join(c for c, font in chars if "Bold" not in font)
    assert "was settled before the" in body  # the rest stays regular


def test_without_the_fallback_the_letters_are_lost(tmp_path, monkeypatch):
    """Negative control: the paragraph markup as it was."""
    from kazma_core.documents.glyph_fallback import GlyphFallback

    monkeypatch.setattr(GlyphFallback, "markup", lambda self, markup, bold=False: markup)
    monkeypatch.setattr(GlyphFallback, "covers", lambda self, text: True)
    text = _text(_render(tmp_path, monkeypatch))
    assert "Kāẓima" not in text and "Ṣabāḥ" not in text


def test_without_the_family_bold_is_regular(tmp_path, monkeypatch):
    """Negative control: each face its own family, bold and italic included,
    as registering a font makes it, and nothing more (as before)."""
    from reportlab.lib import fonts

    real = fonts.addMapping
    monkeypatch.setattr(fonts, "addMapping", lambda face, bold, italic, psname:
                        real(face, bold, italic, psname) if face.lower() == psname.lower() else None)
    monkeypatch.setattr(fonts, "_tt2ps_map", {k: v for k, v in fonts._tt2ps_map.items()
                                               if not k[0].startswith(("kazmaunicode", "kazmaglyph"))})
    monkeypatch.setattr(fonts, "_ps2tt_map", {k: v for k, v in fonts._ps2tt_map.items()
                                               if not k.startswith(("kazmaunicode", "kazmaglyph"))})
    ink = "".join(c for c, font in _chars(_render(tmp_path, monkeypatch)) if "Bold" in font)
    assert "Banī" not in ink  # the body's **Banī ʿUtūb** came out regular (the heading is set bold)


def test_markup_keeps_tags_and_entities(tmp_path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from kazma_core.documents.fonts import resolve_fonts
    from kazma_core.documents.glyph_fallback import GlyphFallback

    fallback = GlyphFallback(resolve_fonts(arabic=False).regular, pdfmetrics, TTFont)
    plain = 'A &amp; B <link href="https://example.test/ā">x</link>'
    assert fallback.markup(plain) == plain  # a URL in a tag is never touched
    marked = fallback.markup("<b>Ṣa</b>b")
    assert marked.startswith("<b><font name=") and 'Bold">Ṣ</font>a</b>b' in marked
