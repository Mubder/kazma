"""A table reads in its own language, whatever the document's.

Live 2026-10-20 (Telegram): a bilingual weather report -- an Arabic page, an
English page -- came out of the house-style route with the English table laid
out right to left ("Season" the rightmost column) and every range cell
reversed ("32 – 29" for 29–32): the table took the document's direction, and
a cell with no letter took it too. The agent then wrote its own HTML and
dropped the house style to get a readable table.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from kazma_core.documents import profile as profile_mod
from kazma_core.documents.content_model import BodyBlock, ContentModel, TableBlock
from kazma_core.documents.engines.docx import DocxEngine
from kazma_core.documents.engines.html import HtmlEngine
from kazma_core.documents.profile import DocProfile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ARABIC_INTRO = "مناخ الكويت صحراوي حار جاف: صيف طويل شديد الحرارة وشتاء قصير معتدل."
EN_TABLE = TableBlock(
    headers=["Season", "Months", "Avg high (°C)", "Avg low (°C)"],
    rows=[["Summer", "Jun – Sep", "43 – 46", "29 – 32"], ["Winter", "Dec – Feb", "19 – 22", "8 – 10"]],
)
AR_TABLE = TableBlock(
    headers=["الفصل", "الأشهر", "أعلى معدل"],
    rows=[["الصيف", "يونيو – سبتمبر", "43 – 46"]],
)


def _model() -> ContentModel:
    model = ContentModel()
    model.add(BodyBlock(text=ARABIC_INTRO))
    model.add(AR_TABLE)
    model.add(BodyBlock(text="Kuwait has a hot desert climate."))
    model.add(EN_TABLE)
    return model


def _profile() -> DocProfile:
    profile = DocProfile.for_content(ARABIC_INTRO)
    assert profile.rtl  # an Arabic document
    return profile


def test_the_table_direction_is_its_own():
    profile = _profile()
    assert profile.table_direction(EN_TABLE.headers, EN_TABLE.rows) == "ltr"
    assert profile.table_direction(AR_TABLE.headers, AR_TABLE.rows) == "rtl"
    # A cell with no letter takes its table's direction, not the document's.
    assert profile.block_direction("29 – 32", "ltr") == "ltr"
    assert profile.block_direction("29 – 32") == "rtl"


def _tables(tmp_path: Path):
    from lxml import etree

    out = tmp_path / "a.docx"
    DocxEngine(_profile()).render(_model(), out)
    with zipfile.ZipFile(out) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    return list(root.iter(f"{W}tbl"))


def _right_to_left(tbl) -> bool:
    return tbl.find(f"{W}tblPr/{W}bidiVisual") is not None


def _cell_bidi(tbl, text: str) -> str:
    for p in tbl.iter(f"{W}p"):
        if "".join(t.text or "" for t in p.iter(f"{W}t")).strip() == text:
            return p.find(f"{W}pPr/{W}bidi").get(f"{W}val")
    raise AssertionError(text)


def test_docx_lays_out_each_table_in_its_own_direction(tmp_path):
    ar, en = _tables(tmp_path)
    assert _right_to_left(ar) and not _right_to_left(en)
    assert _cell_bidi(en, "29 – 32") == "0"
    assert _cell_bidi(ar, "43 – 46") == "1"


def test_negative_control_the_documents_direction_mirrored_the_english_table(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_mod.DocProfile, "table_direction", lambda self, headers, rows: self.direction)
    ar, en = _tables(tmp_path)
    assert _right_to_left(en)
    assert _cell_bidi(en, "29 – 32") == "1"


def test_html_tables_carry_their_own_direction():
    html = HtmlEngine(_profile()).render(_model())
    assert html.count('<table dir="rtl">') == 1 and html.count('<table dir="ltr">') == 1
    assert html.index('<table dir="rtl">') < html.index('<table dir="ltr">')


def _soffice_available() -> bool:
    try:
        import pymupdf  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return bool(find_soffice())
    except Exception:
        return False


@pytest.mark.skipif(not _soffice_available(), reason="needs LibreOffice and PyMuPDF")
def test_on_the_page_the_english_table_reads_left_to_right(tmp_path):
    """Through LibreOffice, as Arabic documents are rendered: "Season" left of
    "Months", and 29 left of 32."""
    import pymupdf

    from kazma_core.documents.binaries import find_soffice, run_soffice_cli
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts

    DocxEngine(_profile()).render(_model(), tmp_path / "a.docx")
    give_soffice_the_bundled_fonts(tmp_path / "profile")
    run_soffice_cli((find_soffice(), "--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                     "--convert-to", "pdf", "--outdir", str(tmp_path), str(tmp_path / "a.docx"))[1:],
                    timeout=120, cwd=tmp_path)
    doc = pymupdf.open(str(tmp_path / "a.pdf"))
    words = [w for page in doc for w in page.get_text("words")]
    doc.close()

    def x(word: str) -> float:
        return next(w[0] for w in words if w[4] == word)

    assert x("Season") < x("Months")
    assert x("29") < x("32")
