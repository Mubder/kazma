"""A number read left to right off an Arabic line goes back to logical order.

2026-10-13 (F09 fidelity corpus). After Arabic letters the bidi algorithm
reads digits as Arabic numbers (W2) and does not join them across "-" (W4),
so a conforming renderer draws "في 2026-09-30" as "30-09-2026 يف". Tesseract
reads that word left to right and returned "30-09-2026": a date whose parts
are silently reversed in every Arabic OCR result. ``arabic.number_in_logical_order``
puts the parts back when -- and only when -- the bidi algorithm would have
split them; the TSV parser applies it per word with the text before it in its
paragraph. The rule is checked against python-bidi's own drawing, not a
hand-written expectation. Negative control: the parser's word as Tesseract
gave it.
"""

from __future__ import annotations

import pytest
from kazma_core.documents.arabic import number_in_logical_order, visual_order

CASES = [
    # (text before the number, the number as written)
    ("الموعد النهائي في", "2026-09-30"),
    ("رقم الهاتف", "050-123-4567"),
    ("التاريخ", "٢٠٢٦-٠٩-٣٠"),
    ("Report", "2026-09-30"),          # after Latin: joined, drawn as written
    ("النسبة", "3.75"),                 # "." joins either way
    ("التاريخ", "2026/09/30"),         # "/" joins either way
    ("", "2026-09-30"),                 # at the start: joined
]


@pytest.mark.parametrize("before, written", CASES, ids=[str(i) for i in range(len(CASES))])
def test_a_drawn_number_is_read_back_as_written(before, written):
    line = f"{before} {written}".strip()
    drawn_line = visual_order(line, "R")
    assert drawn_line is not None
    # The number's word as a left-to-right reader sees it on the drawn line.
    drawn = next(word for word in drawn_line.split() if any(c.isdigit() for c in word))
    assert number_in_logical_order(drawn, before) == written


def test_surrounding_punctuation_stays_in_place():
    assert number_in_logical_order("30-09-2026.", "في") == "2026-09-30."
    assert number_in_logical_order("(30-09-2026)", "في") == "(2026-09-30)"


def test_words_that_are_not_separated_numbers_are_untouched():
    for word in ("مرحبا", "Kazma", "4250", "٤٢٥٠", "-5", "30-", "a-b"):
        assert number_in_logical_order(word, "في") == word


def _tsv(*words: str) -> bytes:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = "".join(
        f"5\t1\t1\t1\t1\t{i}\t{500 - 40 * i}\t20\t30\t12\t90\t{word}\n" for i, word in enumerate(words, 1)
    )
    return (header + rows).encode()


def test_the_ocr_parser_returns_the_date_in_logical_order():
    from kazma_core.documents.ocr.tesseract import _parse_tsv

    payload = _tsv("الموعد", "النهائي", "في", "30-09-2026.")
    result = _parse_tsv(payload, page_number=1, language="ara", engine_version="5.4", dpi=300)
    assert result.blocks[0].text == "الموعد النهائي في 2026-09-30."


def test_without_the_repair_the_word_is_tesseracts(monkeypatch):
    # Negative control: the word as Tesseract gave it is the reversed date.
    from kazma_core.documents import arabic
    from kazma_core.documents.ocr.tesseract import _parse_tsv

    monkeypatch.setattr(arabic, "number_in_logical_order", lambda drawn, preceding: drawn)
    payload = _tsv("الموعد", "النهائي", "في", "30-09-2026.")
    result = _parse_tsv(payload, page_number=1, language="ara", engine_version="5.4", dpi=300)
    assert result.blocks[0].text == "الموعد النهائي في 30-09-2026."
