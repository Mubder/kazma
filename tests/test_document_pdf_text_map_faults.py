"""Kazma's PDF reader reads past the text-map faults LibreOffice writes.

Live 2026-10-18, a tweet archive Kazma rendered (LibreOffice, IBM Plex Sans
Arabic), read back by the agent to check it:

- the shadda drawn with no width, named "8" or "3": "يح3دد", stray "88";
- the kashida LibreOffice stretches a justified word with, named "G":
  "المعGGGرّف";
- every space of a bold face named U+2066 (an isolate drawn with the space
  glyph), and the page's reading failed on it: python-bidi refuses isolates;
- the scope table read twice, as page text and as a table, which the agent
  reported as a rendering defect and tried to "fix".
"""

from __future__ import annotations

from types import SimpleNamespace

from kazma_core.documents.parsers.pdf_layout import _segments_around_tables
from kazma_core.documents.parsers.pdf_rtl import logical_line, page_glyph_lines

SIZE = 11.0


def _page(chars: list[tuple[str, float]]) -> SimpleNamespace:
    """One line of glyphs drawn right to left, each (char, width)."""
    out, x = [], 400.0
    for char, width in chars:
        out.append((ord(char), 7, (x - width, 100.0), (x - width, 90.0, x, 102.0)))
        x -= width if width else 0.0
    span = {"wmode": 0, "dir": (1.0, 0.0), "size": SIZE, "chars": out}
    return SimpleNamespace(get_texttrace=lambda: [span])


def _read(chars: list[tuple[str, float]]) -> str:
    (line,) = page_glyph_lines(_page(chars))
    return logical_line(line, base="R")


W = 6.0


def test_a_mark_named_as_a_digit_or_a_kashida_named_g_is_not_text():
    # "يحدد" with a zero-width "8" (the shadda) and three 1.2 pt "G"s.
    word = [("ي", W), ("ح", W), ("8", 0.0), ("د", W), ("G", 1.2), ("G", 1.2), ("G", 1.2), ("د", W)]
    assert _read(word) == "يحدد"


def test_negative_control_real_digits_and_letters_stay():
    assert _read([("ي", W), (" ", 3.0), ("8", W)]) == "ي 8"
    assert _read([("ي", W), (" ", 3.0), ("G", W)]) == "ي G"


def test_a_mark_with_a_visible_advance_is_the_space_it_was_drawn_with():
    assert _read([("أ", W), ("\u2066", 3.0), ("ب", W)]) == "أ ب"


def test_an_isolate_does_not_fail_the_bidi_step():
    from kazma_core.documents.arabic import visual_order

    assert visual_order("أ \u2066X\u2069 ب", "R") is not None


def _block(text: str, y: float) -> dict:
    return {"x0": 50.0, "x1": 500.0, "y0": y, "y1": y + 10, "cx": 275.0, "cy": y + 5, "text": text}


def test_a_table_is_read_once_where_it_stands():
    blocks = [_block("before", 100), _block("cell a", 210), _block("cell b", 230), _block("after", 400)]
    segments = _segments_around_tables(blocks, [(40.0, 200.0, 510.0, 250.0)])
    assert segments == [("text", "before"), ("table", 0), ("text", "after")]


def test_a_table_no_block_reaches_comes_after_the_text():
    assert _segments_around_tables([_block("only", 100)], [(0, 600, 10, 700)]) == [
        ("text", "only"), ("table", 0)]
