"""Ruled tables on a scanned page are found and read cell by cell (ocr/tables.py).

2026-10-13, a real 300 dpi scan of an Arabic study booklet: the page's own
OCR reading kept 22 % of a contents table's page numbers and 3 % of its rows
on one line; read cell by cell, 91 % and 91 %. These tests hold the rules the
first real runs taught, each with the old rule as its negative control:

* a ruling line is ONE continuous stroke -- the joined baseline of a line of
  bold Arabic text is as dark across the page as a rule, and taken for one it
  cut lines of text in half;
* a cell is at least 3 % of the page wide -- the strip between the two lines
  of a page border is not, and every boxed section of a framed page became a
  "table row";
* a doubtful cell is read again as digits only, and that reading wins only
  when confident;
* a cell that cannot be read costs the cell pass, never the page.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

PIL = pytest.importorskip("PIL")
from kazma_core.documents.models import BlockType, BoundingBox, DocumentBlock  # noqa: E402
from kazma_core.documents.ocr import tables  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

W, H = 1654, 2339  # an A4 page at 200 dpi


def _page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (W, H), 255)
    return image, ImageDraw.Draw(image)


def _grid(draw, left, top, widths, heights, line=3, fill=0):
    xs = [left]
    for width in widths:
        xs.append(xs[-1] + width)
    ys = [top]
    for height in heights:
        ys.append(ys[-1] + height)
    for y in ys:
        draw.rectangle((xs[0], y, xs[-1], y + line - 1), fill=fill)
    for x in xs:
        draw.rectangle((x, ys[0], x + line - 1, ys[-1]), fill=fill)
    return xs, ys


def _ink(draw, box):
    """Stand-in text: a few short strokes in the middle of a box."""
    x0, y0, x1, y1 = box
    cy = (y0 + y1) // 2
    for x in range(x0 + 20, min(x1 - 20, x0 + 140), 30):
        draw.rectangle((x, cy - 8, x + 14, cy + 8), fill=0)


def test_a_ruled_table_is_found_with_its_rows_and_cells():
    image, draw = _page()
    xs, ys = _grid(draw, 200, 400, [400, 300, 300], [80, 80, 80, 80])
    for i in range(3):
        for j in range(4):
            _ink(draw, (xs[i], ys[j], xs[i + 1], ys[j + 1]))
    grids = tables._find_grids(image)
    assert len(grids) == 1
    assert [len(row.cells) for row in grids[0].rows] == [3, 3, 3, 3]
    left, top, right, bottom = grids[0].bbox
    assert abs(left - 200) < 8 and abs(right - xs[-1]) < 8 and abs(top - 400) < 8 and abs(bottom - ys[-1]) < 8


def test_a_light_tinted_grid_is_found():
    # Kazma's own PDF tables are drawn in a pale blue (grey ~215 on white).
    image, draw = _page()
    _grid(draw, 600, 900, [200, 150, 200], [70, 70, 70], line=2, fill=215)
    assert len(tables._find_grids(image)) == 1


def _framed_page():
    """A page border (two thin lines each side) with boxed text sections."""
    image, draw = _page()
    for x in (40, 70, W - 71, W - 41):
        draw.rectangle((x, 40, x + 2, H - 40), fill=0)
    for y in (40, 70, 500, 900, 1300, H - 71, H - 41):
        draw.rectangle((40, y, W - 40, y + 2), fill=0)
    for top in (500, 900):
        for line in range(4):
            y = top + 60 + line * 70
            for x in range(150, W - 200, 160):  # words
                draw.rectangle((x, y, x + 120, y + 20), fill=0)
    return image


def test_boxed_sections_of_a_framed_page_are_not_a_table():
    assert tables._find_grids(_framed_page()) == []


def test_without_the_minimum_cell_width_the_sections_become_a_table(monkeypatch):
    # Negative control: the border strips count as cells.
    monkeypatch.setattr(tables, "_MIN_CELL_FRACTION", 0.0)
    assert tables._find_grids(_framed_page())


def _baseline_page():
    """A table whose rows hold a bold Arabic-like line: words joined along a
    thick baseline, broken only between words."""
    image, draw = _page()
    xs, ys = _grid(draw, 150, 400, [700, 650], [200, 200])
    for j in range(2):
        y = (ys[j] + ys[j + 1]) // 2
        for x in range(xs[0] + 20, xs[-1] - 120, 140):
            draw.rectangle((x, y, x + 125, y + 4), fill=0)  # a word's joined baseline
            draw.rectangle((x + 30, y - 30, x + 40, y), fill=0)  # a letter's stem
    return image


def test_a_text_baseline_is_not_a_rule():
    grids = tables._find_grids(_baseline_page())
    assert len(grids) == 1 and len(grids[0].rows) == 2  # the two real rows, not four halves


def test_by_average_darkness_the_baseline_is_a_rule(monkeypatch):
    # Negative control: the old rule took every dark-enough row band.
    monkeypatch.setattr(
        tables, "_horizontal_rules",
        lambda mask: tables._runs(tables._profile(mask, rows=True), tables._H_RULE_SHARE,
                                  max(2, int(mask.size[1] * tables._MAX_RULE_FRACTION))),
    )
    grids = tables._find_grids(_baseline_page())
    assert sum(len(grid.rows) for grid in grids) > 2


# ── reading the cells ──────────────────────────────────────────────────────

class _Reader:
    """A provider double: answers per cell from the cell's position."""

    def __init__(self, answers):
        self.answers = answers  # x-order index -> (text, conf, digits, digit_conf)
        self.calls = []

    def recognize_region(self, path, *, language, dpi, psm, digits_only, **_):
        with Image.open(path) as cell:
            key = cell.size[0]
        text, confidence, digits, digit_conf = self.answers[key]
        self.calls.append((key, digits_only))
        value, conf = (digits, digit_conf) if digits_only else (text, confidence)
        blocks = (DocumentBlock(block_id="c", block_type=BlockType.TEXT, text=value),) if value else ()
        return SimpleNamespace(blocks=blocks, confidence=conf)


def _two_by_three(tmp_path, widths=(300, 360, 420)):
    image, draw = _page()
    xs, ys = _grid(draw, 200, 500, list(widths), [90, 90])
    for i in range(3):
        for j in range(2):
            _ink(draw, (xs[i], ys[j], xs[i + 1], ys[j + 1]))
    path = tmp_path / "page.png"
    image.save(path)
    return path


def _read(path, reader, tmp_path, **kwargs):
    import time

    options = dict(provider=reader, language="ara+eng", digits=True, dpi=200, timeout_seconds=30,
                   output_limit_bytes=1 << 20, work_dir=tmp_path, max_cells=600,
                   deadline=time.monotonic() + 60)
    options.update(kwargs)
    return tables.read_tables(path, **options)


def _cell_width(width):
    return width - 2 * tables._INSET - 3 + 40  # inside the lines, padded


def test_a_doubtful_cell_takes_a_confident_digit_reading(tmp_path):
    path = _two_by_three(tmp_path)
    answers = {
        _cell_width(300): ("ar ee", 0.31, "128", 0.91),  # junk Latin, doubtful -> digits
        _cell_width(360): ("الموجات", 0.88, "", 0.0),  # Arabic, confident -> kept, no digit call
        _cell_width(420): ("Mg2+", 0.82, "2", 0.95),  # letters, confident -> kept
    }
    reader = _Reader(answers)
    found, note = _read(path, reader, tmp_path)
    assert note is None and len(found) == 1
    # Arabic dominates: cells right to left.
    assert found[0].rtl and found[0].rows[0] == ("Mg2+", "الموجات", "128")
    assert (_cell_width(360), True) not in reader.calls and (_cell_width(420), True) not in reader.calls


def test_a_doubtful_digit_reading_does_not_replace_the_cell(tmp_path):
    path = _two_by_three(tmp_path)
    answers = {w: ("??", 0.2, "1500", 0.5) for w in map(_cell_width, (300, 360, 420))}
    found, _ = _read(path, _Reader(answers), tmp_path)
    assert set(found[0].rows[0]) == {"??"}


def test_past_the_cell_limit_the_tables_keep_the_page_reading(tmp_path):
    path = _two_by_three(tmp_path)
    reader = _Reader({w: ("x", 0.9, "", 0.0) for w in map(_cell_width, (300, 360, 420))})
    found, note = _read(path, reader, tmp_path, max_cells=5)
    assert found == [] and note == "table_cell_limit" and reader.calls == []


def test_past_the_deadline_the_tables_keep_the_page_reading(tmp_path):
    path = _two_by_three(tmp_path)
    found, note = _read(path, _Reader({}), tmp_path, deadline=0.0)
    assert found == [] and note == "table_deadline"


def test_the_table_replaces_the_lines_it_covers_in_reading_order():
    def line(text, y0, y1, x0=100, x1=1500):
        return DocumentBlock(block_id=text, block_type=BlockType.TEXT, text=text,
                             bounding_box=BoundingBox(x0, y0, x1, y1))

    above, inside, below = line("above", 100, 140), line("garbled cell text", 520, 560), line("below", 900, 940)
    table = tables.OcrTable(bbox=(200, 500, 1300, 680), rows=(("a", "b"), ("c", "d")), confidence=0.8, rtl=False)
    blocks = tables.with_tables((above, inside, below), [table], page_number=2, language="eng",
                                engine_version="5", dpi=200)
    assert [block.text for block in blocks] == ["above", "a | b\nc | d", "below"]
    assert blocks[1].block_type is BlockType.TABLE and blocks[1].metadata["extractor"] == "ocr-grid"


def test_a_cell_that_cannot_be_read_costs_the_cell_pass_not_the_page(tmp_path):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.errors import DocumentOcrError
    from kazma_core.documents.ocr import pipeline

    class _Failing:
        def recognize_region(self, *args, **kwargs):
            raise DocumentOcrError("Tesseract OCR exceeded its time limit", code="ocr_timeout")

    page_blocks = (DocumentBlock(block_id="l", block_type=BlockType.TEXT, text="page line",
                                 bounding_box=BoundingBox(250, 520, 600, 560)),)
    result = SimpleNamespace(blocks=page_blocks, page_number=1, language="eng", engine_version="5", dpi=200)
    blocks, note = pipeline._with_page_tables(
        _Failing(), _two_by_three(tmp_path), result, DocumentConfig(storage_root=tmp_path / "s"),
        language="eng", installed=("eng",), work_dir=tmp_path,
    )
    assert blocks == page_blocks and note.startswith("table_read_failed")


def test_the_setting_is_read_from_the_settings_store():
    from kazma_core.documents.config import DocumentConfig

    assert DocumentConfig.__dataclass_fields__["ocr_tables"].default is True
    source = (Path(tables.__file__).parents[1] / "config.py").read_text(encoding="utf-8")
    assert '"documents.ocr.tables"' in source
