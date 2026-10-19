"""Logical-order text for PDF lines that hold right-to-left script.

2026-10-13 (F09). PyMuPDF puts a right-to-left line in logical order by
reversing its CHARACTERS. A PDF draws glyphs left to right, and two kinds of
glyph do not survive a character reversal:

* a number inside Arabic text is drawn left to right, so "١٢٣٤٥٦٧" came out
  as "٧٦٥٤٣٢١" and "3.75" as "57.3" -- a silently wrong value;
* a ligature glyph maps to several characters in logical order ("في", "لا",
  "لى"), so reversing its characters gave "يف", "ال", "ىل" -- "الاصطناعي"
  read as "االصطناعي".

Table cells came out fully reversed ("داريلاإ" for "الإيراد"), and a
right-to-left table's columns in left-to-right order.

The fix reads glyphs in drawing order (``page.get_texttrace``: one entry per
character, a continuation character of a ligature carries glyph ``-1``),
groups each ligature back into one glyph, lays each visual line out left to
right, and reverses only the right-to-left runs, glyph by glyph.

Numbers need one more step (found on the fidelity corpus the same day). The
bidi algorithm reads digits after Arabic letters as ARABIC numbers (rule
W2): a hyphen between them does not join them (W4 joins only "/ , . :"), and
a space between two of them takes the right-to-left direction (N1). So a
conforming renderer draws "في 2026-09-30" as "30-09-2026 يف" and a table row
"الشمال ١٢٥٠ ١٨٤٠٠" as "١٨٤٠٠ ١٢٥٠ لامشلا"; read as one left-to-right run,
they came back as "30-09-2026" and with the columns swapped. Whether a drawn
number was read that way depends on text the line may not show, so each
number cluster's reading is not guessed: every combination (up to
``_MAX_SEARCHED`` clusters) is laid out again with the bidi algorithm
(``arabic.visual_order``) and the reading that reproduces the drawn glyphs is
kept. With none, or no bidi library, the left-to-right reading stands.

Two more repairs of what a producer draws: a gap wider than half a font size
between two glyphs is a word break (table cells on one baseline came out
glued: "المنطقةالوحداتالإيراد"), and a combining mark (zero advance) is
attached to the glyph it sits on. A zero-advance glyph that is NOT a mark is
a mark whose mapping the PDF got wrong -- LibreOffice maps the tanween of
"ريالاً" to "/" -- and is dropped rather than read as text.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = ["Glyph", "has_rtl_glyphs", "logical_line", "page_glyph_lines", "text_in_rect", "text_margins"]

#: Separators the bidi algorithm joins between two numbers of either kind (W4, "CS").
_COMMON_SEPARATORS = frozenset(",.:/٫٬")
#: Separators it joins only between two EUROPEAN numbers (W4, "ES").
_EUROPEAN_SEPARATORS = frozenset("-+")
_NUMBER_JOINERS = _COMMON_SEPARATORS | _EUROPEAN_SEPARATORS
#: A gap wider than this many font sizes between two glyphs is a word break.
_GAP_EM = 0.5
#: A Latin letter narrower than this many font sizes is a kashida misnamed
#: by the producer (Plex's narrowest letters are about a quarter em).
_KASHIDA_EM = 0.15
#: Number clusters per line whose reading is searched (2 ** this layouts).
_MAX_SEARCHED = 5
_BRACKETS = str.maketrans({c: "|" for c in "()[]{}<>"})


@dataclass
class Glyph:
    text: str
    x0: float
    x1: float
    y: float
    size: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class _Line:
    y: float
    size: float
    glyphs: list[Glyph] = field(default_factory=list)


def _bidi(text: str) -> str:
    for char in text:
        kind = unicodedata.bidirectional(char)
        if kind in {"R", "AL"}:
            return "R"
        if kind == "L":
            return "L"
        if kind in {"EN", "AN"}:
            return "D"
    return "N"


def has_rtl_glyphs(lines: Iterable[Sequence[Glyph]]) -> bool:
    return any(_bidi(g.text) == "R" for line in lines for g in line)


def page_glyph_lines(page: Any) -> list[list[Glyph]]:
    """Glyphs of the page grouped into visual lines, each sorted left to right."""
    glyphs: list[Glyph] = []
    marks: list[Glyph] = []
    for span in page.get_texttrace() or ():
        if int(span.get("wmode", 0) or 0) != 0:
            continue  # vertical writing is out of scope here
        direction = span.get("dir") or (1.0, 0.0)
        if abs(float(direction[1])) > 0.01 or float(direction[0]) <= 0:
            continue  # rotated text keeps PyMuPDF's own handling
        size = float(span.get("size") or 0) or 10.0
        for unicode, glyph_id, origin, bbox in (c[:4] for c in span.get("chars") or ()):
            char = chr(int(unicode))
            if unicodedata.category(char) == "Cf" and float(bbox[2]) - float(bbox[0]) >= 0.01:
                # A direction mark with a visible advance is the space glyph
                # it was drawn with: LibreOffice draws a mark its font lacks
                # with the space, and the PDF's text map then names every
                # space of that face by the mark (live 2026-10-18, U+2066
                # for each space of a bold heading).
                char = " "
            if int(glyph_id) == -1 and glyphs:
                glyphs[-1].text += char  # a ligature's next character, logical order
                continue
            glyph = Glyph(char, float(bbox[0]), float(bbox[2]), float(origin[1]), size)
            category = unicodedata.category(char)
            zero_advance = glyph.x1 - glyph.x0 < 0.01
            if ord(char) == 0xFFFD and glyph.x1 - glyph.x0 < _KASHIDA_EM * size:
                # The same kashida with no name at all: U+FFFD in the text map
                # (live 2026-10-20, IBM Plex Sans Arabic glyph 36, 1.18 pt:
                # "الح\ufffdرارة"). Narrower than any letter; never text.
                continue
            if (category[0] == "L" and unicodedata.bidirectional(char) == "L"
                    and glyph.x1 - glyph.x0 < _KASHIDA_EM * size):
                # Narrower than any letter: the kashida LibreOffice stretches
                # a justified Arabic word with, named "G" in its text map
                # (live 2026-10-18, "المعGGGرّف").
                continue
            if zero_advance and category[0] in "LN" and unicodedata.bidirectional(char) in {"L", "EN"}:
                # A digit or Latin letter that takes no room is a mark the
                # producer named wrong: LibreOffice names IBM Plex Sans
                # Arabic's shadda "8" or "3" (live 2026-10-18, "يح3دد").
                # Arabic letters of no width stay: a ligature's parts.
                continue
            if zero_advance and category[0] not in "LNZ":
                if category[0] == "M":
                    marks.append(glyph)
                # A zero-advance glyph that is no mark is a mark mapped wrong
                # by the producer ("/" for a tanween): dropped, never text.
                continue
            glyphs.append(glyph)
    lines: list[_Line] = []
    for glyph in sorted(glyphs, key=lambda g: (g.y, g.x0)):
        target = next((ln for ln in lines if abs(ln.y - glyph.y) <= 0.35 * max(ln.size, glyph.size)), None)
        if target is None:
            target = _Line(glyph.y, glyph.size)
            lines.append(target)
        target.glyphs.append(glyph)
    for mark in marks:
        _attach_mark(mark, lines)
    out = []
    for line in sorted(lines, key=lambda ln: ln.y):
        out.append(sorted(line.glyphs, key=lambda g: g.x0))
    return out


def _attach_mark(mark: Glyph, lines: list[_Line]) -> None:
    """Append a combining mark to the glyph it is drawn on (logical order:
    base, then mark); a mark over nothing is dropped."""
    best: tuple[float, Glyph] | None = None
    for line in lines:
        if abs(line.y - mark.y) > 0.8 * max(line.size, mark.size):
            continue
        for glyph in line.glyphs:
            if glyph.x0 - 0.5 <= mark.x0 <= glyph.x1 + 0.5:
                distance = abs(glyph.y - mark.y) + abs(glyph.cx - mark.x0) / 100.0
                if best is None or distance < best[0]:
                    best = (distance, glyph)
    if best is not None:
        best[1].text += mark.text


def _with_word_gaps(glyphs: list[Glyph]) -> list[Glyph]:
    out = [glyphs[0]]
    for prev, cur in zip(glyphs, glyphs[1:]):
        wide = cur.x0 - prev.x1 > _GAP_EM * max(prev.size, cur.size)
        if wide and not prev.text.isspace() and not cur.text.isspace():
            out.append(Glyph(" ", prev.x1, cur.x0, cur.y, cur.size))
        out.append(cur)
    return out


def _number_clusters(glyphs: Sequence[Glyph], kinds: Sequence[str]) -> list[tuple[int, int]]:
    """Maximal runs of digits joined by number separators, as (first, last)."""
    clusters: list[tuple[int, int]] = []
    i, n = 0, len(glyphs)
    while i < n:
        if kinds[i] != "D":
            i += 1
            continue
        end = i
        j = i + 1
        while j < n and (kinds[j] == "D" or (glyphs[j].text in _NUMBER_JOINERS and j + 1 < n and kinds[j + 1] == "D")):
            if kinds[j] == "D":
                end = j
            j += 1
        clusters.append((i, end))
        i = end + 1
    return clusters


def _assemble(
    glyphs: Sequence[Glyph], kinds: Sequence[str], base: str,
    clusters: Sequence[tuple[int, int]], arabic_numbers: Sequence[bool],
) -> str:
    """One logical reading of the line: each cluster read as a left-to-right
    run (``False``) or as Arabic-context numbers (``True``)."""
    n = len(glyphs)
    run_kind: list[str | None] = [k if k in {"R", "L"} else None for k in kinds]
    for (first, last), as_arabic in zip(clusters, arabic_numbers, strict=True):
        for i in range(first, last + 1):
            if not as_arabic:
                run_kind[i] = "L"
            elif kinds[i] != "D" and glyphs[i].text in _EUROPEAN_SEPARATORS:
                run_kind[i] = "R"  # not joined between Arabic numbers; resolves R (N1)
            else:
                run_kind[i] = "A"
    # A neutral between two runs of one direction takes it (numbers count as
    # right-to-left, N1); otherwise the base direction.
    strong = ["R" if k == "A" else k for k in run_kind]
    for i in range(n):
        if run_kind[i] is not None:
            continue
        before = next((strong[j] for j in range(i - 1, -1, -1) if strong[j] is not None), base)
        after = next((strong[j] for j in range(i + 1, n) if strong[j] is not None), base)
        run_kind[i] = before if before == after else base
    runs: list[tuple[str, list[Glyph]]] = []
    for glyph, kind in zip(glyphs, run_kind, strict=True):
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(glyph)
        else:
            runs.append((str(kind), [glyph]))

    def text(run: tuple[str, list[Glyph]]) -> str:
        kind, members = run
        return "".join(g.text for g in (reversed(members) if kind == "R" else members))

    if base == "R":
        return "".join(text(run) for run in reversed(runs))
    out: list[str] = []
    island: list[tuple[str, list[Glyph]]] = []
    for run in [*runs, ("L", [])]:
        if run[0] in {"R", "A"}:
            island.append(run)
            continue
        out.extend(text(member) for member in reversed(island))
        island = []
        out.append(text(run))
    return "".join(out)


def _canonical(text: str) -> str:
    kept = (c for c in text if unicodedata.category(c)[0] != "M" and unicodedata.category(c) != "Cf")
    return " ".join("".join(kept).translate(_BRACKETS).split())


def _drawn(glyphs: Sequence[Glyph]) -> str:
    """The line as a renderer's visual string: a right-to-left glyph's
    characters (a ligature, a mark) appear in reverse."""
    return _canonical("".join(g.text[::-1] if _bidi(g.text) == "R" else g.text for g in glyphs))


_NUMBER_RUN = re.compile(r"(\d[\d.,\u066b\u066c]*)")


_BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _brackets_outward(text: str) -> str:
    """A left-to-right line whose brackets read inside out, set right.

    One bracket glyph draws "(" in an English cell and, mirrored, ")" in an
    Arabic one; a PDF's text map names a glyph once, so LibreOffice named it
    ")" and the English header read "Avg high )°C(" (live 2026-10-20). A
    pair whose first bracket closes and whose last opens is swapped."""
    for opener, closer in _BRACKET_PAIRS:
        found = [ch for ch in text if ch in (opener, closer)]
        if found and found[0] == closer and found[-1] == opener and found.count(opener) == found.count(closer):
            text = text.translate(str.maketrans({opener: closer, closer: opener}))
    return text


def _numbers_right_to_left(drawn: str) -> str:
    """The logical order of a drawn line of numbers set right to left."""
    return "".join(reversed(_NUMBER_RUN.split(drawn)))


def logical_line(glyphs: Sequence[Glyph], *, base: str | None = None) -> str:
    """One visual line (glyphs left to right) in logical order.

    ``base`` ("R"/"L") is the paragraph direction when the layout shows it
    (see ``text_in_rect``); text alone cannot always tell: "Report تقرير"
    laid out left to right draws the same glyphs as "تقرير Report" laid out
    right to left.
    """
    if not glyphs:
        return ""
    if not any(_bidi(g.text) == "R" for g in glyphs):
        drawn = "".join(g.text for g in glyphs)
        if base == "R" and not any(_bidi(ch) == "L" for ch in drawn):
            # Numbers alone in a right-to-left paragraph (a table cell
            # "43 – 46"): each number reads left to right, the numbers run
            # right to left -- drawn "46 – 43" (live 2026-10-20).
            return _numbers_right_to_left(drawn)
        return _brackets_outward(drawn)
    line = _with_word_gaps(list(glyphs))
    kinds = [_bidi(g.text) for g in line]
    # The line's base direction is Kazma's one rule (arabic.is_rtl_dominant,
    # AGENTS §19H): letters decide, digits do not, and right-to-left wins
    # from 35 % of the letters ("نظام Kazma" is an Arabic line).
    from kazma_core.documents.arabic import is_rtl_dominant, visual_order

    if base not in {"R", "L"}:
        base = "R" if is_rtl_dominant("".join(g.text for g in line)) else "L"
    clusters = _number_clusters(line, kinds)
    first = _assemble(line, kinds, base, clusters, [False] * len(clusters))
    if not clusters or len(clusters) > _MAX_SEARCHED:
        return first
    drawn = _drawn(line)
    choices = sorted(itertools.product((False, True), repeat=len(clusters)), key=sum)
    for choice in choices:
        candidate = first if not any(choice) else _assemble(line, kinds, base, clusters, choice)
        shown = visual_order(candidate, base)
        if shown is None:
            return first  # no bidi algorithm here: the left-to-right reading
        if _canonical(shown) == drawn:
            return candidate
    return first


def text_margins(lines: Sequence[Sequence[Glyph]]) -> tuple[float, float] | None:
    """The left and right edge of the text on a page (or in a cell)."""
    edges = [(line[0].x0, line[-1].x1) for line in lines if line]
    if not edges:
        return None
    return min(e[0] for e in edges), max(e[1] for e in edges)


def _aligned_side(line: Sequence[Glyph], margins: tuple[float, float]) -> str | None:
    """"R" for a line set against the right margin, "L" for the left, None
    when it fills the width or is centred (it does not show its side)."""
    left, right = margins
    tolerance = max(6.0, 0.03 * (right - left))
    left_gap, right_gap = line[0].x0 - left, right - line[-1].x1
    if abs(left_gap - right_gap) <= tolerance:
        return None
    return "R" if right_gap < left_gap else "L"


def text_in_rect(
    lines: Sequence[Sequence[Glyph]],
    rect: Sequence[float],
    *,
    margins: tuple[float, float] | None = None,
    base: str | None = None,
) -> str:
    """Logical text of the glyphs whose centre lies in ``rect`` (x0, y0, x1, y1).

    The block's base direction comes from its layout when a line shows it
    (Kazma's engines set a right-to-left paragraph against the right margin):
    a line shorter than the measure, against one margin, decides for the whole
    block. Otherwise *base* when given (a cell of a right-to-left table),
    else each line falls back to ``arabic.is_rtl_dominant``.
    """
    x0, y0, x1, y1 = (float(v) for v in rect[:4])
    selected = []
    for line in lines:
        inside = [g for g in line if x0 <= g.cx <= x1 and y0 - 0.5 * g.size <= g.y <= y1 + 0.5 * g.size]
        if inside:
            selected.append(inside)
    if margins is not None:
        votes = [side for side in (_aligned_side(line, margins) for line in selected) if side]
        if votes:
            base = "R" if votes.count("R") >= votes.count("L") else "L"
    out = []
    for inside in selected:
        text = logical_line(inside, base=base).strip()
        if text:
            out.append(text)
    return "\n".join(out)
