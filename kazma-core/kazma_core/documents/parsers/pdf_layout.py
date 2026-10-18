"""Layout-aware PDF reading order (multi-column / legal pages).

Uses geometry from PyMuPDF ``get_text("dict")`` blocks to recover a stable
reading order when pages have two (or more) text columns. Full-width bands
(titles, headings that span the page) stay as section anchors; narrow columns
are read top→bottom, left→right (or right→left when the page is Arabic-dominant).

This module is pure geometry + text joining — no I/O, no OCR.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Sequence

__all__ = [
    "blocks_from_pymupdf_dict",
    "extract_pymupdf_page_text",
    "is_rtl_dominant",
    "reading_order_text",
]


TextBlock = MutableMapping[str, Any]


def is_rtl_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when enough letters are RTL to prefer RTL column order.

    Delegates to :func:`kazma_core.documents.arabic.is_rtl_dominant`, the one
    home for direction (AGENTS §19H). This copy kept the codepoint-block
    regex AND the "any Arabic character in the first 200" escape hatch §19H
    removed from the original — the clause that made the threshold dead code
    and flipped English documents to right-to-left column order.
    """
    from kazma_core.documents.arabic import is_rtl_dominant as _is_rtl_dominant

    return _is_rtl_dominant(text, threshold=threshold)


def blocks_from_pymupdf_dict(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert a PyMuPDF ``page.get_text("dict")`` payload into text blocks."""

    blocks: list[dict[str, Any]] = []
    for raw in data.get("blocks") or ():
        if not isinstance(raw, Mapping):
            continue
        # type 0 = text, 1 = image
        if int(raw.get("type", 0) or 0) != 0:
            continue
        bbox = raw.get("bbox") or ()
        if len(bbox) < 4:
            continue
        x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        lines: list[str] = []
        for line in raw.get("lines") or ():
            if not isinstance(line, Mapping):
                continue
            spans = line.get("spans") or ()
            line_text = "".join(
                str(span.get("text", ""))
                for span in spans
                if isinstance(span, Mapping)
            )
            if line_text.strip():
                lines.append(line_text)
        if not lines:
            continue
        blocks.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "cx": (x0 + x1) / 2.0,
                "cy": (y0 + y1) / 2.0,
                "text": "\n".join(lines),
            }
        )
    return blocks


def _page_width(data: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> float:
    width = data.get("width")
    if width is not None:
        try:
            value = float(width)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    if blocks:
        return max(float(b["x1"]) for b in blocks) or 1.0
    return 1.0


def _split_columns(
    narrow: list[Mapping[str, Any]],
    page_width: float,
) -> list[list[Mapping[str, Any]]] | None:
    """Return left/right columns when a clear multi-column layout is present."""

    if len(narrow) < 2 or page_width <= 0:
        return None
    mid = page_width * 0.5
    left = [b for b in narrow if float(b["cx"]) < mid]
    right = [b for b in narrow if float(b["cx"]) >= mid]
    if not left or not right:
        return None
    # Require a gutter: left max x barely overlaps right min x.
    left_max = max(float(b["x1"]) for b in left)
    right_min = min(float(b["x0"]) for b in right)
    gutter_slop = page_width * 0.08
    if right_min + gutter_slop < left_max:
        return None
    # Both sides should look like columns (not one wide + one caption).
    max_col_width = page_width * 0.62
    if any(float(b["x1"]) - float(b["x0"]) > max_col_width for b in left + right):
        narrow_count = sum(
            1
            for b in left + right
            if float(b["x1"]) - float(b["x0"]) <= max_col_width
        )
        if narrow_count < max(2, int(0.6 * len(left + right))):
            return None
    return [left, right]


def reading_order_text(
    blocks: Sequence[Mapping[str, Any]],
    *,
    page_width: float,
    rtl: bool | None = None,
) -> tuple[str, dict[str, object]]:
    """Join blocks into reading-order text.

    Returns ``(text, meta)`` where ``meta`` includes ``column_count`` and
    ``rtl_columns``.
    """

    items = list(blocks)
    if not items:
        return "", {"column_count": 0, "rtl_columns": False}

    sample = "\n".join(str(b.get("text", "")) for b in items)
    use_rtl = is_rtl_dominant(sample) if rtl is None else bool(rtl)
    width = page_width if page_width > 0 else _page_width({}, items)
    full_threshold = width * 0.65

    full = [b for b in items if float(b["x1"]) - float(b["x0"]) >= full_threshold]
    narrow = [b for b in items if float(b["x1"]) - float(b["x0"]) < full_threshold]

    columns = _split_columns(narrow, width)
    if columns is None:
        ordered = sorted(
            items,
            key=lambda b: (float(b["y0"]), -float(b["x0"]) if use_rtl else float(b["x0"])),
        )
        return "\n".join(str(b["text"]) for b in ordered if str(b["text"]).strip()), {
            "column_count": 1,
            "rtl_columns": use_rtl,
            "layout": "single",
        }

    if use_rtl:
        columns = list(reversed(columns))

    multi_top = min(float(b["y0"]) for col in columns for b in col)
    multi_bot = max(float(b["y1"]) for col in columns for b in col)
    header = sorted(
        [b for b in full if float(b["y1"]) <= multi_top + 8.0],
        key=lambda b: float(b["y0"]),
    )
    footer = sorted(
        [b for b in full if float(b["y0"]) >= multi_bot - 8.0],
        key=lambda b: float(b["y0"]),
    )
    header_ids = {id(b) for b in header}
    footer_ids = {id(b) for b in footer}
    mid_full = sorted(
        [b for b in full if id(b) not in header_ids and id(b) not in footer_ids],
        key=lambda b: float(b["y0"]),
    )

    parts: list[str] = []
    for b in header:
        parts.append(str(b["text"]))
    for b in mid_full:
        parts.append(str(b["text"]))
    for col in columns:
        for b in sorted(col, key=lambda item: (float(item["y0"]), float(item["x0"]))):
            parts.append(str(b["text"]))
    for b in footer:
        parts.append(str(b["text"]))

    text = "\n".join(p for p in parts if p.strip())
    return text, {
        "column_count": len(columns),
        "rtl_columns": use_rtl,
        "layout": "multi_column",
        "header_blocks": len(header),
        "footer_blocks": len(footer),
    }


def _inside(block: Mapping[str, Any], rect: Sequence[float], slack: float = 1.0) -> bool:
    return (rect[0] - slack <= block["cx"] <= rect[2] + slack
            and rect[1] - slack <= block["cy"] <= rect[3] + slack)


def _segments_around_tables(
    blocks: Sequence[Mapping[str, Any]], tables: Sequence[Sequence[float]],
) -> list[tuple[str, Any]]:
    """The page in reading order as ``("text", str)`` and ``("table", index)``.

    A table's cells are text blocks too: read as page text AND as the table,
    every table came back twice (live 2026-10-18, an archive's scope table,
    which the agent then took for a rendering defect). A block whose centre
    lies in a table stands for that table, once, where it begins; a table no
    block reaches is placed after the text.
    """
    out: list[tuple[str, Any]] = []
    pending: list[str] = []
    placed: set[int] = set()
    for block in blocks:
        owner = next((i for i, rect in enumerate(tables) if _inside(block, rect)), None)
        if owner is None:
            pending.append(str(block["text"]))
            continue
        if owner in placed:
            continue
        if pending:
            out.append(("text", "\n".join(pending).strip()))
            pending = []
        out.append(("table", owner))
        placed.add(owner)
    if pending:
        out.append(("text", "\n".join(pending).strip()))
    out += [("table", i) for i in range(len(tables)) if i not in placed]
    return [seg for seg in out if seg[0] == "table" or seg[1]]


def extract_pymupdf_page_text(
    page: Any,
    glyph_lines: Sequence[Sequence[Any]] | None = None,
    *,
    tables: Sequence[Sequence[float]] = (),
) -> tuple[str, dict[str, object]]:
    """Best-effort text for one PyMuPDF page: layout when multi-column, else plain.

    Single-column keeps plain ``get_text("text")`` (better continuous Arabic).
    Multi-column uses dict geometry for left/right (or RTL) reading order.
    ``glyph_lines`` (``pdf_rtl.page_glyph_lines``, given when the page holds
    right-to-left script) re-reads every block in logical order: PyMuPDF's
    own reversal turned numbers and ligatures around (2026-10-13, F09).
    ``tables`` (the page's table rectangles): a single-column page then also
    returns ``meta["segments"]`` (``_segments_around_tables``), the text
    without the tables' cells and each table where it stands.
    """

    plain = (page.get_text("text") or "").strip()
    meta: dict[str, object] = {"method": "text", "column_count": 1}
    try:
        data = page.get_text("dict") or {}
        blocks = blocks_from_pymupdf_dict(data)
        if glyph_lines:
            from .pdf_rtl import text_in_rect, text_margins

            margins = text_margins(glyph_lines)
            for block in blocks:
                repaired = text_in_rect(
                    glyph_lines, (block["x0"], block["y0"], block["x1"], block["y1"]), margins=margins
                )
                if repaired:
                    block["text"] = repaired
            plain = "\n".join(block["text"] for block in blocks).strip() or plain
            meta["rtl_logical_order"] = "glyph_runs"
        width = float(getattr(getattr(page, "rect", None), "width", 0) or 0) or _page_width(
            data, blocks
        )
        layout_text, layout_meta = reading_order_text(blocks, page_width=width)
        meta.update(layout_meta)
        if int(layout_meta.get("column_count") or 1) >= 2 and layout_text.strip():
            meta["method"] = "dict_layout"
            return layout_text.strip(), meta
        if tables:
            meta["segments"] = _segments_around_tables(blocks, tables)
        if plain:
            meta["method"] = "text"
            return plain, meta
        if layout_text.strip():
            meta["method"] = "dict"
            return layout_text.strip(), meta
    except Exception:
        meta["method"] = "text_fallback"
    return plain, meta
