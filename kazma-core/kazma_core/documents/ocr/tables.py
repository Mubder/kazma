"""Ruled tables on a scanned page, read cell by cell (Pillow only).

2026-10-13, a real 300 dpi scan of an Arabic study booklet: Tesseract reads a
whole page in its own segmentation, and on a ruled table that reading keeps
neither the rows nor most numbers -- 22 % of a contents table's page numbers
came back, 6 % of its rows kept both numbers on one line and 3 % came back
whole. Read one cell at a time inside the grid's own lines, the same page gave
91 % of the numbers, 91 % of the rows on one line and 56 % whole.

``_find_grids`` finds ruling lines from ink profiles: a horizontal rule is a
thin band of rows dark across a good share of the page; between two rules, a
column dark from top to bottom is a cell boundary. A band with at least two
cells is a table row, and two or more adjacent rows are a table. Nothing else
is inferred: a table without ruling lines, a narrow table and merged cells
that span rules stay with the page's own reading.

``read_tables`` reads each cell (single line, or block for a tall cell). A
cell whose reading holds no letter, or a doubtful one, is read once more as
digits only when ``eng`` is installed: Tesseract's text model often reads bold
digits in a narrow cell as Latin junk. The digit reading wins only when it is
confident. The work is bounded: at most ``max_cells`` cells and the page's own
deadline; past either, the tables not yet read keep the page's reading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from ..models import BlockType, BoundingBox, DocumentBlock

#: Share of the page width a horizontal rule must cross (a three-column
#: table of short values is about a quarter of the page).
_H_RULE_SHARE = 0.2
#: Share of a row band's height a vertical cell boundary must cover.
_V_RULE_SHARE = 0.85
#: A ruling line is thin: thicker dark bands are bars or pictures.
_MAX_RULE_FRACTION = 0.012
#: A cell is at least this share of the page wide. The strip between the two
#: lines of a page border is narrower: without this, every boxed section of a
#: framed page was a "table row" of border strips and one wide cell.
_MIN_CELL_FRACTION = 0.03
_DARK = 128
#: Grid lines are often printed light (a tinted table, a pale scan): lines are
#: found on a cut set from the page's own paper -- this far below its median
#: grey, never lighter than ``_LINE_CUT_MAX`` nor darker than ink's cut.
_LINE_BELOW_PAPER = 25
_LINE_CUT_MAX = 230
_INSET = 3


@dataclass(frozen=True)
class GridRow:
    top: int
    bottom: int
    cells: tuple[tuple[int, int], ...]  # (left, right) between boundaries, left to right


@dataclass(frozen=True)
class Grid:
    rows: tuple[GridRow, ...]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        left = min(row.cells[0][0] for row in self.rows)
        right = max(row.cells[-1][1] for row in self.rows)
        return left, self.rows[0].top, right, self.rows[-1].bottom


@dataclass(frozen=True)
class OcrTable:
    bbox: tuple[int, int, int, int]
    rows: tuple[tuple[str, ...], ...]  # in reading order (right to left for an RTL table)
    confidence: float | None
    rtl: bool


def _dark_mask(image: Any, cut: int = _DARK) -> Any:
    return image.convert("L").point(lambda value: 255 if value < cut else 0)


def _profile(mask: Any, *, rows: bool) -> list[float]:
    """Dark share of every row (``rows``) or column of ``mask``, 0..1."""
    from PIL import Image

    width, height = mask.size
    size = (1, height) if rows else (width, 1)
    reduced = mask.resize(size, Image.Resampling.BOX)
    return [value / 255.0 for value in reduced.tobytes()]


def _runs(profile: list[float], share: float, max_thickness: int) -> list[tuple[int, int]]:
    """Thin runs of consecutive entries at or above ``share``: (start, end)."""
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate([*profile, 0.0]):
        if value >= share and start is None:
            start = index
        elif value < share and start is not None:
            if index - start <= max_thickness:
                runs.append((start, index))
            start = None
    return runs


def _longest_stroke(data: bytes, width: int, y: int, gap: int) -> int:
    """Longest dark run along row ``y``, bridging gaps of up to ``gap`` pixels."""
    row = data[y * width:(y + 1) * width]
    best = start = 0
    last_dark = None
    for x, value in enumerate(row):
        if not value:
            continue
        if last_dark is None or x - last_dark > gap + 1:
            start = x
        last_dark = x
        best = max(best, x - start + 1)
    return best


def _horizontal_rules(mask: Any) -> list[tuple[int, int]]:
    """Thin bands of rows crossed by ONE continuous stroke.

    Average darkness is not enough: the joined baseline of a line of bold
    Arabic text is as dark across the page as a rule, and taking it for one
    cut lines of text in half. A baseline breaks at every space between
    words; a rule runs on (a few pixels of scanner noise bridged)."""
    width, height = mask.size
    candidates = _runs(_profile(mask, rows=True), _H_RULE_SHARE, max(2, int(height * _MAX_RULE_FRACTION)))
    data = mask.tobytes()
    gap = max(2, width // 400)
    needed = int(width * _H_RULE_SHARE * 0.85)
    return [
        (start, end) for start, end in candidates
        if max(_longest_stroke(data, width, y, gap) for y in range(start, end)) >= needed
    ]


def _find_grids(image: Any) -> list[Grid]:
    """Ruled tables on a page image, top to bottom."""
    gray = image.convert("L")
    histogram = gray.histogram()
    half, seen, paper = gray.width * gray.height / 2, 0, 255
    for level, count in enumerate(histogram):
        seen += count
        if seen >= half:
            paper = level
            break
    cut = max(_DARK + 12, min(_LINE_CUT_MAX, paper - _LINE_BELOW_PAPER))
    mask = _dark_mask(gray, cut)
    width, height = mask.size
    h_rules = _horizontal_rules(mask)
    min_band = max(8, height // 200)
    grids: list[Grid] = []
    current: list[GridRow] = []
    for (_, top), (bottom, _) in zip(h_rules, h_rules[1:]):
        row = None
        if min_band <= bottom - top <= height // 2:
            band = mask.crop((0, top + 1, width, bottom - 1))
            v_rules = _runs(_profile(band, rows=False), _V_RULE_SHARE, max(2, int(width * _MAX_RULE_FRACTION)))
            cells = tuple(
                (right_of, left_of)
                for (_, right_of), (left_of, _) in zip(v_rules, v_rules[1:])
                if left_of - right_of >= max(min_band, int(width * _MIN_CELL_FRACTION))
            )
            if len(cells) >= 2:
                row = GridRow(top=top, bottom=bottom, cells=cells)
        if row is not None and current and current[-1].bottom <= row.top + 2 * min_band:
            current.append(row)
            continue
        if len(current) >= 2:
            grids.append(Grid(rows=tuple(current)))
        current = [row] if row is not None else []
    if len(current) >= 2:
        grids.append(Grid(rows=tuple(current)))
    return grids


def _cell_image(image: Any, left: int, top: int, right: int, bottom: int) -> Any | None:
    """The cell's ink on white, without what is left of the grid at its edges."""
    from PIL import Image, ImageDraw

    crop = image.convert("L").crop((left + _INSET, top + _INSET, right - _INSET, bottom - _INSET))
    if crop.width < 4 or crop.height < 4:
        return None
    mask = _dark_mask(crop)
    margin = max(3, min(crop.width, crop.height) // 6)
    draw = ImageDraw.Draw(crop)
    # Left-over grid lines are nearly solid; a stroke of a letter near the
    # edge is not, and stays.
    for start, end in _runs(_profile(mask, rows=True), 0.8, crop.height):
        if start < margin or end > crop.height - margin:
            draw.rectangle((0, start, crop.width, end), fill=255)
    for start, end in _runs(_profile(mask, rows=False), 0.8, crop.width):
        if start < margin or end > crop.width - margin:
            draw.rectangle((start, 0, end, crop.height), fill=255)
    ink = sum(_profile(_dark_mask(crop), rows=True)) / max(1, crop.height)
    if ink < 0.004:
        return None  # an empty cell
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    return padded


def _has_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)


def table_regions(image_path: Path) -> list[tuple[int, int, int, int]]:
    """The boxes of the page's ruled tables, exactly as ``read_tables`` finds
    them: a line-by-line reader skips what the table pass will read."""
    from PIL import Image

    with Image.open(image_path) as source:
        image = source.convert("L")
    return [grid.bbox for grid in _find_grids(image)]


def read_tables(
    image_path: Path,
    *,
    provider: Any,
    language: str,
    digits: bool,
    dpi: int,
    timeout_seconds: float,
    output_limit_bytes: int,
    work_dir: Path,
    max_cells: int,
    deadline: float,
) -> tuple[list[OcrTable], str | None]:
    """Read every ruled table of the page; returns the tables and, when the
    work stopped early, why (the rest keep the page's own reading)."""
    from PIL import Image

    from ..arabic import is_rtl_dominant

    with Image.open(image_path) as source:
        image = source.convert("L")
    grids = _find_grids(image)
    tables: list[OcrTable] = []
    cells_read = 0
    line_px = max(12, int(dpi * 0.3))
    for grid_index, grid in enumerate(grids):
        if cells_read + sum(len(row.cells) for row in grid.rows) > max_cells:
            return tables, "table_cell_limit"
        rows: list[list[tuple[int, str]]] = []
        confidences: list[float] = []
        for row_index, row in enumerate(grid.rows):
            cells: list[tuple[int, str]] = []
            for cell_index, (left, right) in enumerate(row.cells):
                if time.monotonic() > deadline:
                    return tables, "table_deadline"
                cell = _cell_image(image, left, row.top, right, row.bottom)
                cells_read += 1
                if cell is None:
                    cells.append((left, ""))
                    continue
                path = work_dir / f"table-{grid_index}-{row_index}-{cell_index}.png"
                cell.save(path, dpi=(dpi, dpi))
                psm = 7 if cell.height - 40 <= 2 * line_px else 6
                common = dict(dpi=dpi, psm=psm, timeout_seconds=timeout_seconds,
                              output_limit_bytes=output_limit_bytes, work_dir=work_dir)
                try:
                    read = provider.recognize_region(path, language=language, digits_only=False, **common)
                    text = " ".join(block.text for block in read.blocks if block.text)
                    confidence = read.confidence
                    if digits and (not _has_letters(text) or (confidence or 0.0) < 0.5):
                        number = provider.recognize_region(path, language="eng", digits_only=True, **common)
                        number_text = " ".join(block.text for block in number.blocks if block.text)
                        if number_text and (number.confidence or 0.0) >= 0.75:
                            text, confidence = number_text, number.confidence
                finally:
                    path.unlink(missing_ok=True)
                if confidence is not None:
                    confidences.append(confidence)
                cells.append((left, text))
            rows.append(cells)
        rtl = is_rtl_dominant(" ".join(text for row in rows for _, text in row))
        ordered = tuple(
            tuple(text for _, text in sorted(row, key=lambda item: -item[0] if rtl else item[0]))
            for row in rows
        )
        tables.append(OcrTable(bbox=grid.bbox, rows=ordered,
                               confidence=round(mean(confidences), 6) if confidences else None, rtl=rtl))
    return tables, None


def with_tables(
    blocks: tuple[DocumentBlock, ...],
    tables: list[OcrTable],
    *,
    page_number: int,
    language: str,
    engine_version: str | None,
    dpi: int,
    engine: str = "tesseract",
) -> tuple[DocumentBlock, ...]:
    """The page's lines outside every table, and one TABLE block per table, in
    top-to-bottom order."""
    if not tables:
        return blocks

    def inside(block: DocumentBlock, bbox: tuple[int, int, int, int]) -> bool:
        box = block.bounding_box
        if box is None:
            return False
        x = (box.x0 + box.x1) / 2
        y = (box.y0 + box.y1) / 2
        return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]

    kept = [block for block in blocks if not any(inside(block, table.bbox) for table in tables)]
    table_blocks = [
        DocumentBlock(
            block_id=f"p{page_number}-ocr-table-{index}",
            block_type=BlockType.TABLE,
            text="\n".join(" | ".join(row) for row in table.rows),
            bounding_box=BoundingBox(*table.bbox),
            confidence=table.confidence,
            metadata={
                "ocr": True,
                "extractor": "ocr-grid",
                "rows": len(table.rows),
                "columns": max(len(row) for row in table.rows),
                "language": language,
                "engine": engine,
                "engine_version": engine_version,
                "dpi": dpi,
                "direction": "rtl" if table.rtl else "ltr",
                "coordinate_space": "image_pixels",
            },
        )
        for index, table in enumerate(tables, start=1)
    ]
    ordered = sorted(
        [*kept, *table_blocks],
        key=lambda block: block.bounding_box.y0 if block.bounding_box is not None else 0.0,
    )
    return tuple(ordered)
