"""A bullet list is drawn in the house face.

Live 2026-10-20 (Telegram), the first house-style weather report: IBM Plex
throughout, header and footer, tables right -- and every list bullet in
OpenSymbol. The template's bullet levels name the Symbol font and its private
glyph, and LibreOffice draws those in OpenSymbol ("house every thing").
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from kazma_core.documents.content_model import BodyBlock, ContentModel
from kazma_core.documents.engines.docx import DocxEngine
from kazma_core.documents.profile import DocProfile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ARABIC = "سجلت المطربة نحو 53.9 م في يوليو 2016، وهي من أعلى الدرجات الموثقة عالمياً."
BULLETS = f"- {ARABIC}\n- Annual rainfall averages roughly 110 mm.\n- Figures are approximate."


def _render(tmp_path: Path, *, house: bool = True) -> Path:
    model = ContentModel()
    model.add(BodyBlock(text="مناخ الكويت صحراوي حار جاف."))
    model.add(BodyBlock(text=BULLETS))
    engine = DocxEngine(DocProfile.for_content(ARABIC))
    if not house:
        engine._house_bullets = lambda document: None  # the old engine
    engine.render(model, tmp_path / "a.docx")
    return tmp_path / "a.docx"


def _bullet_levels(docx: Path) -> list:
    from lxml import etree

    with zipfile.ZipFile(docx) as z:
        root = etree.fromstring(z.read("word/numbering.xml"))
    return [lvl for lvl in root.iter(f"{W}lvl")
            if lvl.find(f"{W}numFmt") is not None and lvl.find(f"{W}numFmt").get(f"{W}val") == "bullet"]


def test_every_bullet_level_is_the_house_bullet_in_the_house_face(tmp_path):
    from kazma_core.documents.style_theme import theme_fonts

    levels = _bullet_levels(_render(tmp_path))
    assert levels
    house = theme_fonts(rtl=True)["latin"]
    for lvl in levels:
        assert lvl.find(f"{W}lvlText").get(f"{W}val") == "\u2022"
        fonts = lvl.find(f"{W}rPr/{W}rFonts")
        assert {fonts.get(f"{W}ascii"), fonts.get(f"{W}hAnsi"), fonts.get(f"{W}cs")} == {house}


def test_negative_control_the_template_names_symbol(tmp_path):
    fonts = {lvl.find(f"{W}rPr/{W}rFonts").get(f"{W}ascii")
             for lvl in _bullet_levels(_render(tmp_path, house=False))
             if lvl.find(f"{W}rPr/{W}rFonts") is not None}
    assert fonts - {"IBM Plex Sans Arabic"}, fonts


def _soffice_available() -> bool:
    try:
        import fitz  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return bool(find_soffice())
    except Exception:
        return False


def _pdf_fonts(tmp_path: Path, *, house: bool) -> set[str]:
    import fitz
    from kazma_core.documents.binaries import find_soffice, run_soffice_cli
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts

    docx = _render(tmp_path, house=house)
    give_soffice_the_bundled_fonts(tmp_path / "profile")
    run_soffice_cli((find_soffice(), "--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                     "--convert-to", "pdf", "--outdir", str(tmp_path), str(docx))[1:],
                    timeout=120, cwd=tmp_path)
    with fitz.open(tmp_path / "a.pdf") as pdf:
        return {f[3].split("+")[-1] for page in pdf for f in page.get_fonts()}


@pytest.mark.skipif(not _soffice_available(), reason="needs LibreOffice and PyMuPDF")
def test_on_the_page_only_the_house_face_is_embedded(tmp_path):
    fonts = _pdf_fonts(tmp_path, house=True)
    assert fonts and all(name.startswith("IBMPlexSansArabic") for name in fonts), fonts
    old = _pdf_fonts(tmp_path / "old", house=False) if (tmp_path / "old").mkdir() is None else set()
    assert any(not name.startswith("IBMPlexSansArabic") for name in old), old
