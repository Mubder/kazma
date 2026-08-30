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
from collections.abc import Sequence
from typing import Any

# Direction detection is the single home of :mod:`.profile`; re-export so the
# PDF helpers below and historical `from ...rich_render import is_arabic_dominant`
# callers keep working.
from kazma_core.documents.arabic import (
    direction_of,
    has_rtl,
    iter_style_runs,
    shape_spans,
)
from kazma_core.documents.profile import (  # noqa: F401 (re-exported)
    arabic_ratio,
    is_arabic_dominant,
)

# Historic alias — a few tests and the quality heuristics still import it.
_AR_RE = re.compile(
    r"[؀-ۿݐ-ݿࡰ-࢟ࢠ-ࣿ"
    r"ﭐ-﷿ﹰ-﻿]"
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


# Safety margin (in points) subtracted from the column width when measuring a
# wrapped line.
#
# This used to be 20pt because the old code measured the *logical* string and
# then shaped it, so the prediction was systematically short and needed a large
# fudge. ``_visual_width`` now measures the actual shaped line, so the only
# residual error is the difference between the body font used for measurement
# and a bold or monospace run inside the same line. 4pt covers that.
#
# The 20pt version was visible in the output: Arabic body text was ragged 20pt
# short of the left margin on every line, so a right-aligned RTL page had a
# left margin of ~83pt against a right margin of ~56pt. The asymmetry read as a
# layout bug, because it was one.
_ARABIC_BODY_LINE_SAFETY_PT = 4.0


def shape_arabic_wrapped(
    text: str,
    col_width: float,
    font_name: str,
    font_size: float,
) -> str:
    """Shape an unstyled Arabic paragraph into column-wrapped visual lines.

    Kept for callers that hand in plain text with no inline markup. Styled
    paragraphs must go through :func:`inline_markdown_to_reportlab`, which
    shapes the paragraph as one bidi paragraph and keeps the style attached to
    the reordered segments.

    Line breaking happens on the **logical** text and each resulting line is
    reordered separately — which is exactly what the Unicode BiDi algorithm
    specifies (reordering is a per-line operation applied after line breaking).
    Lines are emitted in logical order so the paragraph reads top-to-bottom.
    """
    if not text:
        return ""
    if not has_rtl(text):
        return html.escape(text)
    rendered = _render_shaped_lines(
        [(text, None)],
        col_width=col_width,
        font_name=font_name,
        font_size=font_size,
        wrap=lambda body, style: body,
    )
    return rendered


def _measurer(font_name: str, font_size: float):
    """Return a width function, or ``None`` when ReportLab is unavailable."""
    try:
        from reportlab.pdfbase import pdfmetrics as _pm
    except Exception:  # pragma: no cover - reportlab is required for PDF output
        return None

    def _width(value: str) -> float:
        try:
            return _pm.stringWidth(value, font_name, font_size)
        except Exception:
            return 0.0

    return _width


def _split_span_words(spans: Sequence[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Split styled spans into whitespace-delimited word tokens.

    Whitespace is kept as its own token so a line break can consume it and the
    spacing around a style boundary survives (``**bold** text`` must not become
    ``**bold**text``, which is what per-span shaping used to produce).
    """
    tokens: list[tuple[str, Any]] = []
    for text, style in spans:
        for piece in re.split(r"(\s+)", text):
            if piece:
                tokens.append((piece, style))
    return tokens


def _render_shaped_lines(
    spans: Sequence[tuple[str, Any]],
    *,
    col_width: float | None,
    font_name: str | None,
    font_size: float | None,
    wrap,
) -> str:
    """Shape *spans* as one bidi paragraph and emit ReportLab mini-HTML.

    ``wrap(escaped_text, style)`` re-applies the caller's markup to one visual
    segment. When a column width and font are supplied the paragraph is broken
    into lines on the logical text first, then each line is reordered on its
    own; otherwise the whole paragraph is shaped as a single line and ReportLab
    does the wrapping (correct for short, single-line content such as titles,
    headings, bullets and citations).
    """
    logical = "".join(t for t, _ in spans)
    base_dir = direction_of(logical)

    def _emit(line_spans: Sequence[tuple[str, Any]]) -> str:
        # Trim edge whitespace tokens before shaping. A space left at the end of
        # a logical line lands at the *start* of the visual line once the RTL
        # run is reversed, which reads as a stray indent on every wrapped line.
        trimmed = list(line_spans)
        while trimmed and not trimmed[0][0].strip():
            trimmed.pop(0)
        while trimmed and not trimmed[-1][0].strip():
            trimmed.pop()
        if not trimmed:
            return ""
        segments = shape_spans(trimmed, base_dir=base_dir)
        return "".join(
            wrap(html.escape(text), style)
            for text, style in iter_style_runs(segments)
        )

    measure = (
        _measurer(font_name, font_size)
        if col_width and font_name and font_size
        else None
    )
    if measure is None:
        return _emit(spans)

    limit = max(0.0, float(col_width) - _ARABIC_BODY_LINE_SAFETY_PT)
    if limit <= 0:
        return _emit(spans)

    def _visual_width(line_spans: Sequence[tuple[str, Any]]) -> float:
        shaped = "".join(seg.text for seg in shape_spans(line_spans, base_dir=base_dir))
        return measure(shaped)

    tokens = _split_span_words(spans)
    if not tokens:
        return ""
    if _visual_width(tokens) <= float(col_width):
        return _emit(tokens)

    out_lines: list[str] = []
    current: list[tuple[str, Any]] = []
    for token, style in tokens:
        if not current and not token.strip():
            continue  # never open a line with whitespace
        candidate = current + [(token, style)]
        if current and _visual_width(candidate) > limit:
            rendered = _emit(current)
            if rendered:
                out_lines.append(rendered)
            current = [] if not token.strip() else [(token, style)]
        else:
            current = candidate
    if current:
        rendered = _emit(current)
        if rendered:
            out_lines.append(rendered)
    return "<br/>".join(out_lines)



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
        # Prefer space-joined for normal paragraphs (original behavior).
        # Pull $$display$$ out so it does not sit as raw TeX in the body.
        from kazma_core.documents.math_text import split_display_math

        for kind, chunk in split_display_math(space_joined):
            if kind == "math":
                blocks.append({"type": "math", "text": chunk, "display": True})
            elif chunk.strip():
                blocks.append({"type": "paragraph", "text": chunk.strip()})

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

        # Display math on its own line(s): $$ ... $$
        if stripped.startswith("$$"):
            flush_para()
            flush_list()
            rest = stripped[2:]
            if rest.endswith("$$") and len(rest) > 2:
                blocks.append({"type": "math", "text": rest[:-2].strip(), "display": True})
                i += 1
                continue
            math_lines = [rest] if rest else []
            i += 1
            while i < len(lines) and "$$" not in lines[i]:
                math_lines.append(lines[i])
                i += 1
            if i < len(lines):
                before, _, _ = lines[i].partition("$$")
                if before.strip():
                    math_lines.append(before)
                i += 1
            blocks.append({
                "type": "math",
                "text": "\n".join(math_lines).strip(),
                "display": True,
            })
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


_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*|__(.+?)__|"
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|"
    r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|"
    r"`([^`]+)`|"
    r"\[([^\]]+)\]\(([^)]+)\)"
)


def _inline_spans(text: str) -> list[tuple[str, Any]]:
    """Parse inline markdown into ``(text, style)`` spans in LOGICAL order.

    ``style`` is an opaque marker consumed by :func:`_wrap_span`. Parsing and
    styling are deliberately separated from shaping: the whole paragraph must
    reach the bidi algorithm as one string, or each styled fragment gets
    reordered on its own and the sentence comes out inside-out.
    """
    from kazma_core.documents.math_text import latex_to_unicode, split_inline_math

    spans: list[tuple[str, Any]] = []

    def _chunk(chunk: str) -> None:
        pos = 0
        for m in _INLINE_RE.finditer(chunk):
            if m.start() > pos:
                spans.append((chunk[pos:m.start()], None))
            if m.group(1) is not None or m.group(2) is not None:
                spans.append((m.group(1) or m.group(2), "b"))
            elif m.group(3) is not None or m.group(4) is not None:
                spans.append((m.group(3) or m.group(4), "i"))
            elif m.group(5) is not None:
                spans.append((m.group(5), "code"))
            elif m.group(6) is not None:
                spans.append((m.group(6), ("link", m.group(7))))
            pos = m.end()
        if pos < len(chunk):
            spans.append((chunk[pos:], None))

    for kind, chunk in split_inline_math(text):
        if kind == "math":
            spans.append((latex_to_unicode(chunk), "math"))
        elif kind == "money":
            spans.append(("$" + chunk.strip(), "money"))
        else:
            _chunk(chunk)
    return [(t, st) for t, st in spans if t]


def _wrap_span(body: str, style: Any) -> str:
    """Re-apply ReportLab mini-HTML markup to one already-escaped segment."""
    if style is None:
        return body
    if style == "b":
        return f"<b>{body}</b>"
    if style == "i":
        return f"<i>{body}</i>"
    if style == "code":
        return f'<font face="Courier" size="9" color="#0f172a">{body}</font>'
    if style == "math":
        return f'<font face="Courier" size="11">{body}</font>'
    if style == "money":
        return f'<font face="Courier">{body}</font>'
    if isinstance(style, tuple) and style and style[0] == "link":
        href = html.escape(str(style[1]), quote=True)
        return f'<link href="{href}" color="#2563eb"><u>{body}</u></link>'
    return body


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
    ``<br/>`` and ``<link>``. Everything else is escaped.

    The paragraph is shaped **once**, as a single bidi paragraph, and the
    markdown styles ride along on the reordered segments. Shaping each styled
    fragment separately — the previous behaviour — reordered every fragment
    internally and then emitted the fragments in logical order, so an Arabic
    sentence containing bold, italic, code or a link rendered inside-out with
    the spaces around each style boundary swallowed. Shaping is skipped
    entirely for text with no RTL content, and for engines (DOCX, HTML) that do
    their own shaping.
    """
    if not text:
        return ""

    spans = _inline_spans(text)
    if not spans:
        return ""

    if not shape_arabic or not has_rtl(text):
        return "".join(_wrap_span(html.escape(t), st) for t, st in spans)

    return _render_shaped_lines(
        spans,
        col_width=col_width,
        font_name=font_name,
        font_size=font_size,
        wrap=_wrap_span,
    )



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
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16223a")),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
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
            try:
                from reportlab.platypus import KeepTogether, Preformatted
                pre_style = styles.get("code", code_style)
                flow.append(KeepTogether([Preformatted(raw, pre_style)]))
            except Exception:
                flow.append(Paragraph(_highlight_code_pdf(raw, block.get("lang") or ""), code_style))
            flow.append(Spacer(1, 8))
        elif btype == "math":
            from kazma_core.documents.math_text import latex_to_unicode

            shown = html.escape(latex_to_unicode(block.get("text") or ""))
            math_style = styles.get("code", body_style)
            flow.append(Paragraph(shown, math_style))
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
