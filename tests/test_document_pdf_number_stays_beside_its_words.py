"""A number Kazma reads back from a PDF stays beside the words it was typed
with (FA-230).

The 2026-11 functional audit: in a left-to-right paragraph ending with an
Arabic sentence that holds a year, "... في عام 2026." read back as "2026
ارتفعت ... في عام." -- the year moved to the front. ``pdf_rtl.logical_line``
tried the number clusters' readings fewest-Arabic first and kept the first
that reproduces the drawn line; here two do, and the first is wrong.

Among readings that draw the same line, the one whose numbers keep the words
they are drawn beside wins; a tie keeps the old order (the drawn place, as
``test_an_ambiguous_number_keeps_its_drawn_place`` requires). Lines laid out
by the bidi algorithm itself, EN and AR; the old tie-break is the control.
"""

from __future__ import annotations

import pytest

from kazma_core.documents.arabic import visual_order
from kazma_core.documents.parsers import pdf_rtl
from kazma_core.documents.parsers.pdf_rtl import Glyph, logical_line

YEAR = "ارتفعت المبيعات بنسبة كبيرة في عام 2026."


def _visual(drawn: str) -> list[Glyph]:
    return [Glyph(c, i * 10.0, i * 10.0 + 8.0, 100.0, 10.0) for i, c in enumerate(drawn)]


def _round_trip(logical: str, base: str) -> str:
    drawn = visual_order(logical, base)
    assert drawn is not None
    return logical_line(_visual(drawn), base=base)


@pytest.mark.parametrize(("logical", "base"), [
    (YEAR, "L"),                                   # the audit's sentence, in an LTR paragraph
    ("بلغ عدد الزوار 4500 زائر في عام 2025.", "L"),
    ("Sales grew. ارتفعت المبيعات في عام 2026.", "L"),
    (YEAR, "R"),
    ("Version 2.0 الإصدار ٣", "L"),                  # an English number stays English
])
def test_the_number_stays_with_its_words(logical, base):
    assert _round_trip(logical, base) == logical


def test_an_ambiguous_number_still_keeps_its_drawn_place():
    """A number drawn between an English and an Arabic word keeps both."""
    drawn = visual_order("Uptime التوفر 99.95", "L")
    assert logical_line(_visual(drawn), base="L") == "Uptime 99.95 التوفر"


def test_the_old_tie_break_moved_the_year_first(monkeypatch):
    """Negative control: every reproducing reading scoring the same, the
    first (fewest Arabic-context numbers) wins -- the reader before FA-230."""
    monkeypatch.setattr(pdf_rtl, "_kept_neighbours", lambda line, clusters, logical: 0)
    assert _round_trip(YEAR, "L") == "2026 ارتفعت المبيعات بنسبة كبيرة في عام."
