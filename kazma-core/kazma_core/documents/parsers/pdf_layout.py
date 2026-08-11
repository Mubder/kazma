"""Layout-aware PDF reading order (multi-column / legal pages).

Uses geometry from PyMuPDF ``get_text("dict")`` blocks to recover a stable
reading order when pages have two (or more) text columns. Full-width bands
(titles, headings that span the page) stay as section anchors; narrow columns
are read top→bottom, left→right (or right→left when the page is Arabic-dominant).

This module is pure geometry + text joining — no I/O, no OCR.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, MutableMapping, Sequence

__all__ = [
    "blocks_from_pymupdf_dict",
    "extract_pymupdf_page_text",
    "is_rtl_dominant",
    "reading_order_text",
]

_AR_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)

TextBlock = MutableMapping[str, Any]


def is_rtl_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when enough letters are Arabic script to prefer RTL column order."""

    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return bool(_AR_RE.search(text[:200]))
    arabic = sum(1 for ch in letters if _AR_RE.fullmatch(ch))
    return (arabic / len(letters)) >= threshold or bool(_AR_RE.search(text[:200]))


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


def extract_pymupdf_page_text(page: Any) -> tuple[str, dict[str, object]]:
    """Best-effort text for one PyMuPDF page: layout when multi-column, else plain.

    Single-column keeps plain ``get_text("text")`` (better continuous Arabic).
    Multi-column uses dict geometry for left/right (or RTL) reading order.
    """

    plain = (page.get_text("text") or "").strip()
    meta: dict[str, object] = {"method": "text", "column_count": 1}
    try:
        data = page.get_text("dict") or {}
        blocks = blocks_from_pymupdf_dict(data)
        width = float(getattr(getattr(page, "rect", None), "width", 0) or 0) or _page_width(
            data, blocks
        )
        layout_text, layout_meta = reading_order_text(blocks, page_width=width)
        meta.update(layout_meta)
        if int(layout_meta.get("column_count") or 1) >= 2 and layout_text.strip():
            meta["method"] = "dict_layout"
            return layout_text.strip(), meta
        if plain:
            meta["method"] = "text"
            return plain, meta
        if layout_text.strip():
            meta["method"] = "dict"
            return layout_text.strip(), meta
    except Exception:
        meta["method"] = "text_fallback"
    return plain, meta
