"""Rich document rendering helpers — markdown structure + Arabic PDF shaping.

ReportLab draws LTR and does **not** shape Arabic (joining forms) or apply the
Unicode BiDi algorithm. Without ``arabic_reshaper`` + ``python-bidi``, Arabic
PDF text appears as disconnected, often reversed glyphs. The helpers here
(``shape_for_pdf``, ``shape_arabic_wrapped``, ``parse_rich_blocks``,
``inline_markdown_to_reportlab``, ``pdf_flowables_from_body``) serve the PDF
engine.

Word (DOCX) *does* OpenType shaping. DOCX rendering now lives in the unified
engine at :mod:`kazma_core.documents.engines.docx`, which consumes a
:class:`~kazma_core.documents.profile.DocProfile` so the bidi/alignment
semantics are correct by construction (no per-element flag patching here).

Direction logic (``_AR_RE`` / ``arabic_ratio`` / ``is_arabic_dominant``) is
owned by :mod:`kazma_core.documents.profile` and re-exported from here for
back-compat with the PDF path and existing tests.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

# Direction detection is the single home of :mod:`.profile`; re-export so the
# PDF helpers below and historical `from ...rich_render import is_arabic_dominant`
# callers keep working.
from kazma_core.documents.profile import (  # noqa: F401 (re-exported)
    _AR_RE,
    arabic_ratio,
    is_arabic_dominant,
)

logger = logging.getLogger(__name__)

__all__ = [
    "arabic_ratio",
    "is_arabic_dominant",
    "shape_for_pdf",
    "parse_rich_blocks",
    "try_parse_pipe_table_blob",
    "inline_markdown_to_reportlab",
    "pdf_flowables_from_body",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_UL_RE = re.compile(r"^(\s*)([-*•])\s+(.+)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
# GFM-style table row: | a | b |
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")


def shape_for_pdf(text: str) -> str:
    """Reshape + BiDi-reorder for ReportLab (LTR drawing engine)."""
    if not text or not _AR_RE.search(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return text
    try:
        reshaper = arabic_reshaper.ArabicReshaper(
            configuration={"delete_harakat": False, "support_ligatures": True}
        )
        base = "R" if is_arabic_dominant(text) else None
        return get_display(reshaper.reshape(text), base_dir=base)
    except Exception:
        return text


# Safety margin (in points) subtracted from the column width when wrapping
# Arabic body lines so the paragraph flows at the full page/column width
# (right-aligned lines ending at x1 ~ 535). A shaped visual line is slightly
# wider than its logical measurement (Arabic final-form glyphs widen, plus the
# body region has a ~6pt right gutter below the nominal col_width), so without
# this margin ReportLab wordWrap="CJK" would re-split an overflowing line
# mid-word. The margin absorbs both effects: every wrapped line is a
# whole-word, right-aligned (x1 ~ 535) column-wrapped line with NO chopped
# words. Only consulted when a paragraph actually overflows one line.
_ARABIC_BODY_LINE_SAFETY_PT = 20.0


def shape_arabic_wrapped(
    text: str,
    col_width: float,
    font_name: str,
    font_size: float,
) -> str:
    """Shape an Arabic paragraph into right-aligned, column-wrapped visual lines.

    Unlike :func:`shape_for_pdf` (which shapes the *whole* paragraph as one
    visual string and lets ReportLab re-wrap it left-to-right — the v9 bug that
    scrambled RTL line order and collapsed the body to narrow left-aligned
    lines), this packs the *logical* words greedily to the real column width
    (measured with ``pdfmetrics.stringWidth`` in the body font), shapes each
    resulting line independently via :func:`shape_for_pdf`, and joins them with
    ``<br/>``.

    Visual lines stay in logical (reading) order, so the paragraph start lands
    on the **top** line and every line is right-aligned, ending at the column's
    right edge (x1 ~ 535). A small safety margin keeps each shaped line
    from slightly overflowing the body region (which would make ReportLab
    re-split it mid-word). The result is HTML-escaped per line with raw
    ``<br/>`` separators (so ReportLab's ``<br/>`` is preserved and HTML
    entities never corrupt the bidi/joining stream).

    Non-Arabic input is returned escaped as-is (no shaping).
    """
    if not text:
        return ""
    if not _AR_RE.search(text):
        return html.escape(text)

    try:
        from reportlab.pdfbase import pdfmetrics as _pm

        def _width(s: str) -> float:
            return _pm.stringWidth(s, font_name, font_size)
    except Exception:  # pragma: no cover - reportlab is required for PDF output
        return html.escape(shape_for_pdf(text))

    v = shape_for_pdf(text)
    try:
        full = float(col_width)
    except (TypeError, ValueError):
        full = 0.0
    if full <= 0:
        return html.escape(v)
    # Fast path: the whole visual paragraph already fits on a single line.
    try:
        if _width(v) <= full:
            return html.escape(v)
    except Exception:
        return html.escape(v)

    # Greedy logical-word packing to the page-width column (minus a safety
    # margin). We never reverse the visual line list: lines are emitted in
    # logical order so the paragraph reads top-to-bottom beginning-first.
    wrap = max(0.0, full - _ARABIC_BODY_LINE_SAFETY_PT)
    words = text.split()
    if not words:
        return html.escape(v)
    lines: list[str] = []
    cur: list[str] = []
    for wd in words:
        if not cur:
            cur = [wd]
            continue
        candidate = " ".join(cur + [wd])
        try:
            if _width(shape_for_pdf(candidate)) > wrap:
                lines.append(shape_for_pdf(" ".join(cur)))
                cur = [wd]
            else:
                cur.append(wd)
        except Exception:
            # Fall back to appending the word if measurement fails.
            cur.append(wd)
    if cur:
        lines.append(shape_for_pdf(" ".join(cur)))
    return "<br/>".join(html.escape(ln) for ln in lines)


def _split_pipe_row(row: str) -> list[str]:
    inner = row.strip().strip("|")
    return [c.strip() for c in inner.split("|")]


def _is_sep_cell(cell: str) -> bool:
    c = (cell or "").strip().replace(" ", "")
    return bool(c) and bool(re.fullmatch(r":?-+:?", c))


def try_parse_pipe_table_blob(text: str) -> dict[str, Any] | None:
    """Parse a GFM table that may be multi-line OR collapsed onto one line.

    LLMs often emit: ``| h1 | h2 | |---|---| | r1 | r2 |`` as a single paragraph.
    Empty cells between header/sep/rows are row delimiters in collapsed form.
    """
    raw = (text or "").strip()
    if raw.count("|") < 4:
        return None

    # Multi-line GFM
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) >= 2 and _TABLE_ROW_RE.match(lines[0]) and _TABLE_SEP_RE.match(lines[1]):
        headers = _split_pipe_row(lines[0])
        rows = [_split_pipe_row(ln) for ln in lines[2:] if _TABLE_ROW_RE.match(ln)]
        if headers and rows:
            return {"type": "table", "headers": headers, "rows": rows}

    cells = [c.strip() for c in raw.strip().strip("|").split("|")]
    if len(cells) < 3:
        return None
    try:
        first_sep = next(i for i, c in enumerate(cells) if _is_sep_cell(c))
    except StopIteration:
        return None
    last_sep = first_sep
    while last_sep + 1 < len(cells) and _is_sep_cell(cells[last_sep + 1]):
        last_sep += 1

    headers = list(cells[:first_sep])
    while headers and headers[-1] == "":
        headers.pop()
    # drop leading empties
    while headers and headers[0] == "":
        headers.pop(0)
    ncols = len(headers)
    if ncols < 1:
        return None

    data = cells[last_sep + 1 :]
    # empty string acts as row break; also pack by ncols
    rows: list[list[str]] = []
    cur: list[str] = []
    for c in data:
        if c == "":
            if cur:
                # pad/truncate to ncols
                if len(cur) < ncols:
                    cur = cur + [""] * (ncols - len(cur))
                rows.append(cur[:ncols])
                cur = []
            continue
        cur.append(c)
        if len(cur) == ncols:
            rows.append(cur)
            cur = []
    if cur:
        if len(cur) < ncols:
            cur = cur + [""] * (ncols - len(cur))
        rows.append(cur[:ncols])
    rows = [r for r in rows if any(x.strip() for x in r)]
    if not rows:
        return None
    return {"type": "table", "headers": headers, "rows": rows}


def parse_rich_blocks(body: str) -> list[dict[str, Any]]:
    """Parse lightweight markdown into typed blocks for PDF/DOCX emitters.

    Supported:
      - AT1–H6 (``#`` … ``######``)
      - unordered / ordered lists (``-`` / ``*`` / ``1.``)
      - fenced code blocks (````)
      - horizontal rules
      - paragraphs (blank-line separated); inner lines join with space
      - blockquotes (``>``)
    """
    if not (body or "").strip():
        return []

    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    i = 0
    para_buf: list[str] = []
    list_buf: list[dict[str, Any]] | None = None
    list_kind: str | None = None

    def flush_para() -> None:
        nonlocal para_buf
        joined = "\n".join(ln.strip() for ln in para_buf if ln.strip()).strip()
        # Also try space-joined collapsed tables
        space_joined = " ".join(ln.strip() for ln in para_buf if ln.strip()).strip()
        para_buf = []
        if not joined:
            return
        parsed = try_parse_pipe_table_blob(joined) or try_parse_pipe_table_blob(space_joined)
        if parsed is not None:
            blocks.append(parsed)
            return
        # Prefer space-joined for normal paragraphs (original behavior)
        blocks.append({"type": "paragraph", "text": space_joined})

    def flush_list() -> None:
        nonlocal list_buf, list_kind
        if list_buf and list_kind:
            blocks.append({"type": "list", "ordered": list_kind == "ol", "items": list_buf})
        list_buf = None
        list_kind = None

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # Fenced code
        if stripped.startswith("```"):
            flush_para()
            flush_list()
            lang = stripped[3:].strip()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # closing fence
            blocks.append({"type": "code", "lang": lang, "text": "\n".join(code_lines)})
            continue

        if not stripped:
            flush_para()
            flush_list()
            i += 1
            continue

        # Markdown table (multi-line GFM or collapsed one-liner from LLMs)
        if "|" in stripped and (
            (i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()))
            or _is_sep_cell(stripped.strip("|").split("|")[0] if False else "")
            or re.search(r"\|\s*:?-+:?\s*\|", stripped)
        ):
            # Gather consecutive pipe-ish lines into one blob
            flush_para()
            flush_list()
            blob_lines = [stripped]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    break
                if "|" in nxt or _TABLE_SEP_RE.match(nxt):
                    blob_lines.append(nxt)
                    i += 1
                    continue
                break
            blob = "\n".join(blob_lines)
            parsed = try_parse_pipe_table_blob(blob)
            if parsed is not None:
                blocks.append(parsed)
                continue
            # Not a table — fall through as paragraph text
            para_buf.extend(blob_lines)
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            flush_para()
            flush_list()
            blocks.append(
                {
                    "type": "heading",
                    "level": min(6, len(hm.group(1))),
                    "text": hm.group(2).strip(),
                }
            )
            i += 1
            continue

        if _HR_RE.match(stripped):
            flush_para()
            flush_list()
            blocks.append({"type": "hr"})
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote_lines = [stripped.lstrip("> ").strip()]
            i += 1
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip("> ").strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(quote_lines)})
            continue

        um = _UL_RE.match(raw)
        om = _OL_RE.match(raw)
        if um or om:
            flush_para()
            kind = "ol" if om else "ul"
            m = om or um
            assert m is not None
            indent = len(m.group(1).replace("\t", "    ")) // 2
            item_text = m.group(3).strip()
            if list_kind != kind:
                flush_list()
                list_kind = kind
                list_buf = []
            assert list_buf is not None
            list_buf.append({"text": item_text, "level": indent})
            i += 1
            continue

        # Continuation of list item (indented line)
        if list_buf is not None and (raw.startswith("  ") or raw.startswith("\t")):
            list_buf[-1]["text"] = (list_buf[-1]["text"] + " " + stripped).strip()
            i += 1
            continue

        flush_list()
        para_buf.append(stripped)
        i += 1

    flush_para()
    flush_list()
    return blocks


def inline_markdown_to_reportlab(
    text: str,
    *,
    shape_arabic: bool = True,
    col_width: float | None = None,
    font_name: str | None = None,
    font_size: float | None = None,
) -> str:
    """Convert a subset of inline markdown to ReportLab ``<para>`` mini-HTML.

    ReportLab Paragraph supports ``<b>``, ``<i>``, ``<u>``, ``<font>``,
    ``<br/>``, ``<link>``. We escape everything else first, then re-inject tags.
    """
    if not text:
        return ""

    # Tokenize inline patterns on the *raw* string, escape segments
    parts: list[str] = []
    pos = 0
    # Simpler sequential pass
    pattern = re.compile(
        r"\*\*(.+?)\*\*|__(.+?)__|"
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|"
        r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|"
        r"`([^`]+)`|"
        r"\[([^\]]+)\]\(([^)]+)\)"
    )

    def _esc_shape(s: str) -> str:
        """Shape text for ReportLab's visual engine, then HTML-escape.

        For body paragraphs (``col_width`` + font + size supplied) Arabic
        segments are packed into right-aligned, column-wrapped visual lines via
        :func:`shape_arabic_wrapped` so RTL reading order is preserved
        top-to-bottom and lines end at the column right edge (v4 layout).
        Short / non-body text (titles, TOC, citations, bullets) keeps the
        whole-segment :func:`shape_for_pdf` path — those fit one line, so
        ReportLab wraps them itself.
        """
        if col_width is not None and font_name is not None and font_size is not None:
            # shape_arabic_wrapped already returns per-line HTML-escaped output
            # with raw <br/> separators, so we must NOT escape again here.
            if shape_arabic:
                return shape_arabic_wrapped(s, col_width, font_name, font_size)
            return html.escape(s)
        s2 = shape_for_pdf(s) if shape_arabic else s
        return html.escape(s2)

    for m in pattern.finditer(text):
        if m.start() > pos:
            parts.append(_esc_shape(text[pos : m.start()]))
        if m.group(1) is not None or m.group(2) is not None:
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            parts.append(f"<b>{_esc_shape(inner)}</b>")
        elif m.group(3) is not None or m.group(4) is not None:
            inner = m.group(3) if m.group(3) is not None else m.group(4)
            parts.append(f"<i>{_esc_shape(inner)}</i>")
        elif m.group(5) is not None:
            parts.append(
                f'<font face="Courier" size="9" color="#0f172a">'
                f"{_esc_shape(m.group(5))}</font>"
            )
        elif m.group(6) is not None:
            label = _esc_shape(m.group(6))
            href = html.escape(m.group(7), quote=True)
            parts.append(f'<link href="{href}" color="#2563eb"><u>{label}</u></link>')
        pos = m.end()
    if pos < len(text):
        parts.append(_esc_shape(text[pos:]))
    return "".join(parts) if parts else _esc_shape(text)


# ── Pygments code highlighting for reportlab PDF ──────────────────────
_PYGMENT_COLORS: dict[Any, str] = {}


def _init_pygment_colors() -> None:
    """Build the token-type → hex color map (once, lazily)."""
    global _PYGMENT_COLORS
    if _PYGMENT_COLORS:
        return
    try:
        from pygments.token import Token
    except ImportError:
        return
    _PYGMENT_COLORS = {
        Token.Keyword: "#0f172a",
        Token.Keyword.Constant: "#0e7490",
        Token.Keyword.Declaration: "#0e7490",
        Token.Keyword.Namespace: "#0e7490",
        Token.Name.Builtin: "#1e3a5f",
        Token.Name.Function: "#1d4ed8",
        Token.Name.Class: "#1d4ed8",
        Token.Name.Decorator: "#b45309",
        Token.Name.Exception: "#be123c",
        Token.String: "#15803d",
        Token.String.Doc: "#64748b",
        Token.String.Escape: "#b45309",
        Token.Number: "#b45309",
        Token.Comment: "#64748b",
        Token.Comment.Preproc: "#0e7490",
        Token.Operator: "#be123c",
        Token.Punctuation: "#475569",
        Token.Literal: "#15803d",
        Token.Error: "#be123c",
    }


def _color_for_token(ttype: Any) -> str | None:
    """Walk the Pygments token hierarchy to find the longest matching color."""
    _init_pygment_colors()
    while ttype:
        color = _PYGMENT_COLORS.get(ttype)
        if color:
            return color
        ttype = getattr(ttype, "parent", None)
    return None


def _highlight_code_pdf(raw: str, lang: str) -> str:
    """Tokenize code with Pygments → per-token colored reportlab ``<font>`` runs.

    Falls back to plain Courier (the original single-color rendering) if
    Pygments is unavailable or the language lexer is unknown.
    """
    fallback = f'<font face="Courier" size="8">{html.escape(raw).replace(chr(10), "<br/>")}</font>'
    try:
        from pygments.lexers import get_lexer_by_name
    except ImportError:
        return fallback
    try:
        lexer = get_lexer_by_name(lang or "text", stripnl=False)
    except Exception:
        return fallback
    parts: list[str] = []
    for ttype, text in lexer.get_tokens(raw):
        escaped = html.escape(text).replace("\n", "<br/>")
        color = _color_for_token(ttype)
        if color:
            parts.append(f'<font face="Courier" size="8" color="{color}">{escaped}</font>')
        else:
            parts.append(f'<font face="Courier" size="8">{escaped}</font>')
    return "".join(parts) if parts else fallback


def pdf_flowables_from_body(
    body: str,
    *,
    styles: dict[str, Any],
    shape_arabic: bool = True,
    Spacer: Any = None,
    Paragraph: Any = None,
    HRFlowable: Any = None,
    KeepTogether: Any = None,
    colors: Any = None,
    Table: Any = None,
    TableStyle: Any = None,
    font_name: str = "Helvetica",
    bold_font_name: str = "Helvetica-Bold",
    col_width: float | None = None,
    font_size: float | None = None,
) -> list[Any]:
    """Build ReportLab flowables from a rich body string."""
    if Paragraph is None or Spacer is None:
        from reportlab.platypus import Paragraph as _P
        from reportlab.platypus import Spacer as _S

        Paragraph = _P
        Spacer = _S
    if HRFlowable is None:
        try:
            from reportlab.platypus import HRFlowable as _HR
        except ImportError:
            _HR = None  # type: ignore[misc, assignment]
        HRFlowable = _HR
    if Table is None or TableStyle is None:
        try:
            from reportlab.platypus import Table as _T
            from reportlab.platypus import TableStyle as _TS

            Table, TableStyle = _T, _TS
        except ImportError:
            Table = TableStyle = None  # type: ignore[misc, assignment]
    if colors is None:
        try:
            from reportlab.lib import colors as _colors

            colors = _colors
        except ImportError:
            pass

    flow: list[Any] = []
    blocks = parse_rich_blocks(body)
    if not blocks and body.strip():
        blocks = [
            {"type": "paragraph", "text": p.strip()}
            for p in body.split("\n\n")
            if p.strip()
        ]

    body_style = styles["body"]
    quote_style = styles.get("quote", body_style)
    code_style = styles.get("code", body_style)
    bullet_style = styles.get("bullet", body_style)
    number_style = styles.get("number", body_style)
    heading_fill = styles.get("heading_fill")  # reportlab Color or None

    def _heading_bar(text_html: str, st: Any, level: int) -> None:
        """Heading with optional solid background bar (parity with styled PDF)."""
        para = Paragraph(text_html, st)
        if Table is None or colors is None or heading_fill is None:
            flow.append(para)
            flow.append(Spacer(1, 6))
            return
        fill = heading_fill if level <= 2 else colors.HexColor("#e2e8f0")
        tbl = Table([[para]], colWidths=["*"])
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), fill),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        flow.append(tbl)
        flow.append(Spacer(1, 8))

    def _md_table(headers: list[str], rows: list[list[str]]) -> None:
        if Table is None or TableStyle is None or colors is None:
            # Fallback: plain lines
            line = " | ".join(headers)
            flow.append(
                Paragraph(
                    inline_markdown_to_reportlab(line, shape_arabic=shape_arabic),
                    body_style,
                )
            )
            return

        def cell(val: str) -> str:
            return shape_for_pdf(val) if shape_arabic else val

        data = [[cell(h) for h in headers]]
        for row in rows:
            data.append([cell(c) for c in row])
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        flow.extend((tbl, Spacer(1, 10)))

    for block in blocks:
        btype = block["type"]
        if btype == "heading":
            level = int(block.get("level") or 2)
            key = f"h{min(level, 3)}"
            st = styles.get(key, styles.get("h2", body_style))
            text_html = inline_markdown_to_reportlab(
                block["text"], shape_arabic=shape_arabic
            )
            _heading_bar(text_html, st, level)
        elif btype == "paragraph":
            text_html = inline_markdown_to_reportlab(
                block["text"],
                shape_arabic=shape_arabic,
                col_width=col_width,
                font_name=font_name,
                font_size=font_size,
            )
            # body_para is TA_RIGHT for Arabic (ragged-right, v4 layout) and
            # TA_JUSTIFY for English (full-column justified). Falls back to
            # body_style for callers that did not register a body_para style.
            flow.append(Paragraph(text_html, styles.get("body_para", body_style)))
            flow.append(Spacer(1, 8))
        elif btype == "quote":
            text_html = inline_markdown_to_reportlab(
                block["text"], shape_arabic=shape_arabic
            )
            flow.append(Paragraph(f"<i>{text_html}</i>", quote_style))
            flow.append(Spacer(1, 8))
        elif btype == "code":
            raw = block.get("text") or ""
            flow.append(Paragraph(_highlight_code_pdf(raw, block.get("lang") or ""), code_style))
            flow.append(Spacer(1, 8))
        elif btype == "list":
            ordered = bool(block.get("ordered"))
            for idx, item in enumerate(block.get("items") or [], 1):
                level = int(item.get("level") or 0)
                prefix = f"{idx}." if ordered else "•"
                inner = inline_markdown_to_reportlab(
                    item.get("text") or "", shape_arabic=shape_arabic
                )
                line = f"{html.escape(prefix)}&nbsp;&nbsp;{inner}"
                st = number_style if ordered else bullet_style
                pad = "&nbsp;" * (level * 4)
                flow.append(Paragraph(pad + line, st))
            flow.append(Spacer(1, 6))
        elif btype == "table":
            _md_table(
                list(block.get("headers") or []),
                list(block.get("rows") or []),
            )
        elif btype == "hr":
            if HRFlowable is not None and colors is not None:
                flow.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.6,
                        color=colors.HexColor("#cbd5e1"),
                        spaceBefore=4,
                        spaceAfter=10,
                    )
                )
            else:
                flow.append(Spacer(1, 10))
    return flow
