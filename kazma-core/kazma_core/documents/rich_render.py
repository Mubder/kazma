"""Rich document rendering helpers — markdown structure + Arabic PDF shaping.

ReportLab draws LTR and does **not** shape Arabic (joining forms) or apply the
Unicode BiDi algorithm. Without ``arabic_reshaper`` + ``python-bidi``, Arabic
PDF text appears as disconnected, often reversed glyphs.

Word (DOCX) *does* OpenType shaping; for DOCX we set RTL/bidi paragraph flags
and semantic styles instead of pre-shaping.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "arabic_ratio",
    "is_arabic_dominant",
    "shape_for_pdf",
    "parse_rich_blocks",
    "inline_markdown_to_reportlab",
    "pdf_flowables_from_body",
    "docx_write_rich_body",
    "docx_set_rtl_paragraph",
]

_AR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_UL_RE = re.compile(r"^(\s*)([-*•])\s+(.+)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_INLINE_MD_RE = re.compile(
    r"(\*\*[^*]+\*\*|__[^_]+__|"
    r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)|"
    r"(?<!_)_(?!_)([^_]+)_(?!_)|"
    r"`([^`]+)`|"
    r"\[([^\]]+)\]\(([^)]+)\))"
)


def arabic_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha() or _AR_RE.match(c)]
    if not letters:
        return 0.0
    ar = sum(1 for c in letters if _AR_RE.match(c))
    return ar / len(letters)


def is_arabic_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when enough Arabic letters are present to drive RTL layout."""
    if not text or not _AR_RE.search(text):
        return False
    return arabic_ratio(text) >= threshold or bool(_AR_RE.search(text[:200]))


def shape_for_pdf(text: str) -> str:
    """Reshape + BiDi-reorder for ReportLab (LTR drawing engine).

    Safe for mixed AR/EN: ``python-bidi`` handles base direction from content.
    Returns original text if reshape libraries are missing or text has no Arabic.
    """
    if not text or not _AR_RE.search(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        logger.warning(
            "[rich_render] arabic_reshaper/python-bidi missing — Arabic PDF may look broken"
        )
        return text
    try:
        # configuration: delete_harakat keeps diacritics when present
        reshaper = arabic_reshaper.ArabicReshaper(
            configuration={
                "delete_harakat": False,
                "support_ligatures": True,
            }
        )
        reshaped = reshaper.reshape(text)
        # base_dir auto from content; explicit 'R' when Arabic-dominant
        base = "R" if is_arabic_dominant(text) else None
        return get_display(reshaped, base_dir=base)
    except Exception:
        logger.debug("[rich_render] shape_for_pdf failed", exc_info=True)
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            return text


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
        text = " ".join(ln.strip() for ln in para_buf if ln.strip()).strip()
        para_buf = []
        if text:
            blocks.append({"type": "paragraph", "text": text})

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


def inline_markdown_to_reportlab(text: str, *, shape_arabic: bool = True) -> str:
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

    flow: list[Any] = []
    blocks = parse_rich_blocks(body)
    if not blocks and body.strip():
        # Fallback: plain paragraphs
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

    for block in blocks:
        btype = block["type"]
        if btype == "heading":
            level = int(block.get("level") or 2)
            key = f"h{min(level, 3)}"
            st = styles.get(key, styles.get("h2", body_style))
            text = inline_markdown_to_reportlab(block["text"], shape_arabic=shape_arabic)
            flow.append(Paragraph(text, st))
            flow.append(Spacer(1, 6))
        elif btype == "paragraph":
            text = inline_markdown_to_reportlab(block["text"], shape_arabic=shape_arabic)
            flow.append(Paragraph(text, body_style))
            flow.append(Spacer(1, 8))
        elif btype == "quote":
            text = inline_markdown_to_reportlab(block["text"], shape_arabic=shape_arabic)
            flow.append(Paragraph(f"<i>{text}</i>", quote_style))
            flow.append(Spacer(1, 8))
        elif btype == "code":
            raw = block.get("text") or ""
            # Code stays LTR — no Arabic reshape (would corrupt identifiers)
            escaped = html.escape(raw).replace("\n", "<br/>")
            flow.append(Paragraph(f'<font face="Courier" size="8">{escaped}</font>', code_style))
            flow.append(Spacer(1, 8))
        elif btype == "list":
            ordered = bool(block.get("ordered"))
            for idx, item in enumerate(block.get("items") or [], 1):
                level = int(item.get("level") or 0)
                prefix = f"{idx}." if ordered else "•"
                inner = inline_markdown_to_reportlab(
                    item.get("text") or "", shape_arabic=shape_arabic
                )
                # For shaped Arabic, put bullet after visual text is awkward;
                # use "prefix + space + text" and right-align style for AR.
                line = f"{html.escape(prefix)}&nbsp;&nbsp;{inner}"
                st = number_style if ordered else bullet_style
                # Indent via left/right padding on a clone would need style factory;
                # use non-breaking spaces for nesting.
                pad = "&nbsp;" * (level * 4)
                flow.append(Paragraph(pad + line, st))
            flow.append(Spacer(1, 6))
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


def docx_set_rtl_paragraph(paragraph: Any, *, justify: bool = True) -> None:
    """Mark a python-docx paragraph as RTL and optionally justify."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    if justify:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    else:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    p_pr = paragraph._p.get_or_add_pPr()
    # w:bidi enables RTL paragraph direction in Word
    existing = p_pr.find(qn("w:bidi"))
    if existing is None:
        bidi = OxmlElement("w:bidi")
        bidi.set(qn("w:val"), "1")
        p_pr.append(bidi)
    else:
        existing.set(qn("w:val"), "1")


def _docx_add_runs_with_inline_md(paragraph: Any, text: str) -> None:
    """Add runs for bold/italic/code/plain to a paragraph (no Arabic pre-shape)."""
    pattern = re.compile(
        r"\*\*(.+?)\*\*|__(.+?)__|"
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|"
        r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|"
        r"`([^`]+)`"
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos : m.start()])
        if m.group(1) is not None or m.group(2) is not None:
            run = paragraph.add_run(m.group(1) if m.group(1) is not None else m.group(2))
            run.bold = True
        elif m.group(3) is not None or m.group(4) is not None:
            run = paragraph.add_run(m.group(3) if m.group(3) is not None else m.group(4))
            run.italic = True
        elif m.group(5) is not None:
            run = paragraph.add_run(m.group(5))
            run.font.name = "Consolas"
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])
    if not text:
        paragraph.add_run("")


def docx_write_rich_body(
    document: Any,
    body: str,
    *,
    rtl: bool = False,
) -> None:
    """Write a rich markdown body into a python-docx Document."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    blocks = parse_rich_blocks(body)
    if not blocks and body.strip():
        blocks = [
            {"type": "paragraph", "text": p.strip()}
            for p in body.split("\n\n")
            if p.strip()
        ]

    for block in blocks:
        btype = block["type"]
        if btype == "heading":
            level = min(3, max(1, int(block.get("level") or 2)))
            p = document.add_heading(block.get("text") or "", level=level)
            if rtl:
                docx_set_rtl_paragraph(p, justify=False)
        elif btype == "paragraph":
            p = document.add_paragraph()
            _docx_add_runs_with_inline_md(p, block.get("text") or "")
            if rtl:
                docx_set_rtl_paragraph(p, justify=True)
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif btype == "quote":
            p = document.add_paragraph()
            run = p.add_run(block.get("text") or "")
            run.italic = True
            run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)
            if rtl:
                docx_set_rtl_paragraph(p, justify=True)
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.left_indent = Pt(18)
        elif btype == "code":
            p = document.add_paragraph()
            run = p.add_run(block.get("text") or "")
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT  # code always LTR
        elif btype == "list":
            style = "List Number" if block.get("ordered") else "List Bullet"
            for item in block.get("items") or []:
                p = document.add_paragraph(style=style)
                # Clear default empty run if any
                if p.runs:
                    p.runs[0].text = ""
                _docx_add_runs_with_inline_md(p, item.get("text") or "")
                if rtl:
                    docx_set_rtl_paragraph(p, justify=False)
        elif btype == "hr":
            p = document.add_paragraph("─" * 40)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
