"""A bilingual report: its title once, its Arabic bold drawn bold.

Live 2026-10-20 (Telegram), the first house-style report after the bullet
fix. Two faults on page 1:

- the title twice: the source began "# طقس الكويت عبر الفصول — Kuwait's
  Weather by Season" and the document's title was the same two halves in the
  other order, which ``headings_equivalent`` did not take for a repeat;
- Arabic bold drawn regular: the Arabic half of the title and the bold
  lead-ins of the Arabic notes. Word and LibreOffice draw Arabic bold by
  ``w:bCs``, which only Arabic runs of right-to-left paragraphs had.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

TITLE = "Kuwait's Weather by Season — طقس الكويت عبر الفصول"
SOURCE = """# طقس الكويت عبر الفصول — Kuwait's Weather by Season

## القسم العربي

تقع الكويت في الرأس الشمالي للخليج العربي وتصنَّف ضمن المناخ الصحراوي الحار.

**ثلاث ملاحظات:**

- **الذروة الحرارية:** سُجّلت في المطربة أعلى قراءة موثوقة في آسيا.
- **الغبار:** تكثر العواصف الترابية في الربيع والصيف.

## English Section

Kuwait has a hot desert climate.

- **Peak heat:** Mitribah recorded 53.9 °C on 21 July 2016.
"""
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(tmp_path: Path, monkeypatch=None, *, cs_emphasis: bool = True) -> Path:
    from kazma_core.documents.engines.docx import DocxEngine
    from kazma_core.documents.renderer_worker import _build_model_and_profile

    model, profile = _build_model_and_profile({"title": TITLE, "sections": [{"heading": "", "body": SOURCE}]})
    engine = DocxEngine(profile)
    if not cs_emphasis:
        engine._complex_script_emphasis = lambda document: None  # the old engine
    engine.render(model, tmp_path / "a.docx")
    return tmp_path / "a.docx"


def test_every_bold_and_italic_run_carries_its_complex_script_twin(tmp_path):
    from lxml import etree

    with zipfile.ZipFile(_docx(tmp_path)) as z:
        for part in ("word/document.xml", "word/styles.xml"):
            root = etree.fromstring(z.read(part))
            for r_pr in root.iter(f"{W}rPr"):
                if r_pr.find(f"{W}b") is not None:
                    assert r_pr.find(f"{W}bCs") is not None, (part, etree.tostring(r_pr)[:300])
                if r_pr.find(f"{W}i") is not None:
                    assert r_pr.find(f"{W}iCs") is not None, (part, etree.tostring(r_pr)[:300])


def test_the_swapped_bilingual_title_is_one_heading():
    from kazma_core.documents.heading_text import headings_equivalent

    assert headings_equivalent("طقس الكويت عبر الفصول — Kuwait's Weather by Season", TITLE)
    assert not headings_equivalent("Kuwait's Weather — Rainfall", TITLE)
    assert not headings_equivalent("Well-being", "being Well")


def _soffice_available() -> bool:
    try:
        import fitz  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return bool(find_soffice())
    except Exception:
        return False


def _pdf(tmp_path: Path, **kw) -> Path:
    from kazma_core.documents.binaries import find_soffice, run_soffice_cli
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts

    docx = _docx(tmp_path, **kw)
    give_soffice_the_bundled_fonts(tmp_path / "profile")
    run_soffice_cli((find_soffice(), "--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                     "--convert-to", "pdf", "--outdir", str(tmp_path), str(docx))[1:],
                    timeout=120, cwd=tmp_path)
    return tmp_path / "a.pdf"


def _arabic_bold_faces(pdf: Path) -> set[str]:
    """The faces that draw the Arabic bold lead-in."""
    import fitz

    faces: set[str] = set()
    with fitz.open(pdf) as doc:
        for page in doc:
            for block in page.get_text("rawdict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        text = "".join(c["c"] for c in span["chars"])
                        if "لحرار" in text or "غبار" in text:
                            faces.add(span["font"].split("+")[-1])
    return faces


@pytest.mark.skipif(not _soffice_available(), reason="needs LibreOffice and PyMuPDF")
def test_on_the_page_the_title_is_once_and_arabic_bold_is_bold(tmp_path):
    import fitz

    pdf = _pdf(tmp_path)
    with fitz.open(pdf) as doc:
        text = "".join(page.get_text() for page in doc)
    assert text.count("Kuwait's Weather by Season") == 1, text[:400]
    assert any("Bold" in face for face in _arabic_bold_faces(pdf)), _arabic_bold_faces(pdf)

    # Negative control: without the twins the Arabic lead-ins are regular.
    old = tmp_path / "old"
    old.mkdir()
    assert not any("Bold" in face for face in _arabic_bold_faces(_pdf(old, cs_emphasis=False)))
