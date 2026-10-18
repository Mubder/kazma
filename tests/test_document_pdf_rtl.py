"""PDF lines holding right-to-left script come out in logical order.

2026-10-13 (F09). PyMuPDF reverses a right-to-left line character by
character: a number drawn left to right inside Arabic came out backwards
("١٢٣٤٥٦٧" as "٧٦٥٤٣٢١"), ligature glyphs came out with their letters
swapped ("في" as "يف"), and Arabic table cells fully reversed. The repair
(``parsers/pdf_rtl.py``) reverses glyphs, not characters, and only the
right-to-left runs. Unit cases on synthetic glyph lines (the shapes read off
a real LibreOffice PDF), then the real parser on a real rendered PDF.
Negative control: PyMuPDF's character reversal of the same glyphs.

Same day, from the fidelity corpus: digits after Arabic letters are Arabic
numbers to the bidi algorithm, so a conforming renderer draws "في 2026-09-30"
as "30-09-2026 يف" and a table row's numbers right to left; read as one
left-to-right run they came back reversed. Each number cluster's reading is
now the one the bidi algorithm draws as the page shows (round trips below,
every line laid out by python-bidi itself). Table cells on one baseline came
back glued, and LibreOffice maps a tanween glyph to "/": a wide gap is a word
break and a zero-advance non-mark glyph is dropped. Negative control: the
same date without the bidi check reads back reversed.
"""

from __future__ import annotations

import pytest
from kazma_core.documents.parsers.pdf_rtl import Glyph, logical_line


def _visual(*texts: str) -> list[Glyph]:
    """Glyphs as drawn, left to right, 10 points apart."""
    return [Glyph(text, x * 10.0, x * 10.0 + 8.0, 100.0, 10.0) for x, text in enumerate(texts)]


# "بلغت ١٢٣٤٥٦٧ في" drawn left to right: "في" (one ligature glyph), the
# number left to right, then the word right to left.
LINE = _visual("في", " ", "١", "٢", "٣", "٤", "٥", "٦", "٧", " ", "ت", "غ", "ل", "ب")


def test_numbers_keep_their_digit_order():
    assert logical_line(LINE) == "بلغت ١٢٣٤٥٦٧ في"


def test_character_reversal_breaks_them():
    # Negative control: the old order reverses the number and the ligature.
    old = "".join(reversed("".join(g.text for g in LINE)))
    assert "٧٦٥٤٣٢١" in old and "يف" in old
    assert logical_line(LINE) != old


# What LibreOffice draws for "... في 2026-09-30." (read off the real PDF):
# the hyphens do not join Arabic-context numbers, so the parts run right to left.
DATE = _visual(".", "3", "0", "-", "0", "9", "-", "2", "0", "2", "6", " ", "ي", "ف")


def test_an_arabic_context_date_reads_back_in_logical_order():
    assert logical_line(DATE) == "في 2026-09-30."


def test_without_the_bidi_check_the_date_reads_back_reversed(monkeypatch):
    # Negative control: the left-to-right reading of every number (the
    # version before the check) returns the parts in drawn order.
    from kazma_core.documents import arabic

    monkeypatch.setattr(arabic, "visual_order", lambda text, base: None)
    assert logical_line(DATE) == "في 30-09-2026."


ROUND_TRIPS = [
    ("في 2026-09-30.", "R"),
    ("شحن المستودع ٤٢٥٠ وحدة في 2026-09-30.", "R"),
    ("الشمال ١٢٥٠ ١٨٤٠٠", "R"),
    ("انخفضت النسبة إلى ٣٫٧٥ بالمئة", "R"),
    ("رقم الهاتف 050-123-4567 للتواصل", "R"),
    ("نظام Kazma 2.0 الجديد", "R"),
    ("التقرير Report 2026-09-30 النهائي", "R"),
    ("بنسبة 99.95 بالمئة", "R"),
    ("Total: الشمال ١٢٥٠", "L"),
    ("Version 2.0 الإصدار ٣", "L"),
]


@pytest.mark.parametrize("logical, base", ROUND_TRIPS, ids=[str(i) for i in range(len(ROUND_TRIPS))])
def test_a_line_laid_out_by_the_bidi_algorithm_reads_back(logical, base):
    from kazma_core.documents.arabic import visual_order

    drawn = visual_order(logical, base)
    assert drawn is not None
    assert logical_line(_visual(*drawn), base=base) == logical


def test_an_ambiguous_number_keeps_its_drawn_place():
    # "Uptime التوفر 99.95" and "Uptime 99.95 التوفر" are drawn the same way
    # in a left-to-right line (the number reads as Arabic in the first): no
    # reader can tell them apart, and the reading as drawn is kept.
    from kazma_core.documents.arabic import visual_order

    assert visual_order("Uptime التوفر 99.95", "L") == visual_order("Uptime 99.95 التوفر", "L")
    drawn = visual_order("Uptime التوفر 99.95", "L")
    assert logical_line(_visual(*drawn), base="L") == "Uptime 99.95 التوفر"


def test_cells_on_one_baseline_are_separate_words():
    # "الشمال | ١٢٥٠ | ١٨٤٠٠" drawn as a right-to-left table row, no space glyphs.
    row = [Glyph(c, 10.0 + 6 * i, 15.0 + 6 * i, 100.0, 10.0) for i, c in enumerate("١٨٤٠٠")]
    row += [Glyph(c, 200.0 + 6 * i, 205.0 + 6 * i, 100.0, 10.0) for i, c in enumerate("١٢٥٠")]
    row += [Glyph(c, 400.0 + 6 * i, 405.0 + 6 * i, 100.0, 10.0) for i, c in enumerate("لامشلا")]
    assert logical_line(row) == "الشمال ١٢٥٠ ١٨٤٠٠"


class _Page:
    def __init__(self, chars):
        self._chars = chars

    def get_texttrace(self):
        return [{"wmode": 0, "dir": (1.0, 0.0), "size": 10.0, "chars": self._chars}]


def _char(c: str, gid: int, x0: float, x1: float, y: float = 100.0):
    return (ord(c), gid, (x0, y), (x0, y - 8, x1, y + 2))


def test_a_mark_joins_its_letter_and_a_misread_mark_is_dropped():
    from kazma_core.documents.parsers.pdf_rtl import page_glyph_lines

    # "ريالاً" as LibreOffice draws it: the tanween before the lam-alef
    # ligature (its second letter a continuation, glyph -1) and mapped to "/".
    misread = _Page([_char("/", 66, 30.5, 30.5, 97.0), _char("ل", 12, 30.0, 35.0),
                     _char("ا", -1, 30.0, 30.0), _char("ا", 5, 35.1, 38.0),
                     _char("ي", 39, 38.0, 41.0), _char("ر", 67, 41.0, 44.0)])
    (line,) = page_glyph_lines(misread)
    assert logical_line(line) == "ريالا"
    # A mark mapped right is kept on the letter it sits on.
    marked = _Page([_char("\u064b", 66, 30.5, 30.5, 97.0), *misread._chars[1:]])
    (line,) = page_glyph_lines(marked)
    assert logical_line(line) == "ريالاً"


def test_a_decimal_with_an_arabic_separator():
    line = _visual("٣", "٫", "٧", "٥", " ", "لى", "إ")  # "لى" is one ligature glyph
    assert logical_line(line) == "إلى ٣٫٧٥"


def test_latin_inside_arabic_keeps_its_order():
    line = _visual("K", "a", "z", "m", "a", " ", "م", "ا", "ظ", "ن")
    assert logical_line(line) == "نظام Kazma"


def test_a_left_to_right_line_is_untouched():
    line = _visual("T", "o", "t", "a", "l", " ", "4", "2")
    assert logical_line(line) == "Total 42"


@pytest.mark.parametrize("case_id", ["ar-pdf", "bilingual-pdf"])
def test_a_rendered_arabic_pdf_reads_back_in_order(tmp_path, case_id):
    import shutil
    from pathlib import Path

    if not (shutil.which("soffice") or Path("C:/Program Files/LibreOffice/program/soffice.exe").exists()):
        pytest.skip("LibreOffice is not installed (Arabic PDF layout route)")
    from kazma_core.documents import fidelity
    from kazma_core.documents.service import DocumentService

    case = next(c for c in fidelity.corpus() if c.case_id == case_id)
    ir = DocumentService().parse_ingested_blob(fidelity.build_case(case, tmp_path))
    text = "\n".join(b.text for p in ir.pages for b in p.blocks)
    if case_id == "ar-pdf":
        assert "١٢٣٤٥٦٧" in text and "٧٦٥٤٣٢١" not in text
        assert "الاصطناعي" in text and "في" in text
        assert "2026-09-30" in text and "30-09-2026" not in text
        # The producer maps the tanween to "/": dropped, never read as text.
        assert "ريالا في" in text and "/" not in text
        table = next(b.text for p in ir.pages for b in p.blocks if str(b.block_type) == "table")
        assert table.splitlines()[0] == "المنطقة | الوحدات | الإيراد"  # right-to-left columns
        # The row in column order, words apart. Re-pointed 2026-10-18: a
        # table is read once, as the table, where it stands; its cells were
        # also read as page text, so every table came back twice.
        assert "الشمال | ١٢٥٠ | ١٨٤٠٠" in table
        assert "الشمال ١٢٥٠ ١٨٤٠٠" not in text, "not read a second time as page text"
    else:
        assert "99.95" in text and "اكتملت عملية الترحيل" in text
