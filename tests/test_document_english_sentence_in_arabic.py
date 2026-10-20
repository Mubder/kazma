"""An English sentence ending an Arabic paragraph keeps its full stop.

Live 2026-10-20 (Telegram): the report's closing line, Arabic then English in
one right-to-left paragraph, drew the English sentence's full stop on the far
side of its last line (".long-term climatological ranges ..."). The marks
``arabic.isolate_ltr_runs`` puts around an English run in Arabic text stopped
at the last letter; the full stop outside took the paragraph's direction.
The sentence's own punctuation is now inside the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.documents import arabic

LRM = "\u200e"
LINE = ("المصدر: بيانات مناخية عامة لأغراض الاسترشاد. — Source: general ranges for guidance; "
        "official figures: Kuwait Meteorological Department.")


def test_the_sentence_punctuation_is_inside_the_run():
    marked = arabic.isolate_ltr_runs(LINE)
    assert marked.endswith("Department." + LRM), marked[-40:]
    # An Arabic comma after an English word is Arabic punctuation: outside.
    assert arabic.isolate_ltr_runs("استخدم Kazma، ثم") == "استخدم " + LRM + "Kazma" + LRM + "، ثم"


def _soffice_available() -> bool:
    try:
        import fitz  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return bool(find_soffice())
    except Exception:
        return False


def _stop_and_last_letter_x(tmp_path: Path) -> tuple[float, float]:
    import fitz
    from kazma_core.documents.binaries import find_soffice, run_soffice_cli
    from kazma_core.documents.content_model import BodyBlock, ContentModel
    from kazma_core.documents.engines.docx import DocxEngine
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts
    from kazma_core.documents.profile import DocProfile

    model = ContentModel()
    model.add(BodyBlock(text=LINE))
    DocxEngine(DocProfile.for_content("مناخ الكويت صحراوي حار")).render(model, tmp_path / "a.docx")
    give_soffice_the_bundled_fonts(tmp_path / "profile")
    run_soffice_cli((find_soffice(), "--headless", "--nologo", "--norestore",
                     f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                     "--convert-to", "pdf", "--outdir", str(tmp_path), str(tmp_path / "a.docx"))[1:],
                    timeout=120, cwd=tmp_path)
    with fitz.open(tmp_path / "a.pdf") as doc:
        page = doc[0]
        words = page.get_text("words")
        last = next(w for w in words if w[4].startswith("Department"))
        chars = [c for b in page.get_text("rawdict")["blocks"] for line in b.get("lines", [])
                 for span in line["spans"] for c in span["chars"]
                 if abs(c["bbox"][1] - last[1]) < 3]
        stops = [c["bbox"][0] for c in chars if c["c"] == "."]
        letter_x = max(c["bbox"][0] for c in chars if c["c"] == "t" and c["bbox"][0] <= last[2])
    return min(stops, key=lambda x: abs(x - letter_x)), letter_x


@pytest.mark.skipif(not _soffice_available(), reason="needs LibreOffice and PyMuPDF")
def test_on_the_page_the_full_stop_follows_its_word(tmp_path, monkeypatch):
    stop_x, letter_x = _stop_and_last_letter_x(tmp_path)
    assert stop_x > letter_x, (stop_x, letter_x)

    # Negative control: the run stopping at the last letter.
    monkeypatch.setattr(arabic, "_LTR_TRAILING_PUNCT", frozenset())
    old = tmp_path / "old"
    old.mkdir()
    stop_x, letter_x = _stop_and_last_letter_x(old)
    assert stop_x < letter_x, (stop_x, letter_x)
