"""HTML engine for the unified document layer.

Consumes a :class:`~kazma_core.documents.content_model.ContentModel` under a
:class:`~kazma_core.documents.profile.DocProfile` and renders themed, 
direction-aware HTML. Direction (``dir=rtl``/``ltr``), text-align, theme tokens
and localized chrome all come from the profile — the same single source the
DOCX and PDF engines use — so an Arabic HTML export matches the Arabic DOCX/PDF
in design and direction.

HTML/CSS handles bidi natively (``dir`` + ``unicode-bidi``), so (unlike the
reportlab PDF path) there is no shaping/isolation problem here; Latin tokens
embedded in Arabic are isolated with ``<bdi dir="ltr">`` for polish. Markdown
body is rendered via the ``markdown`` library (tables / fenced code / lists).
"""

from __future__ import annotations

import html as _html_lib
import logging
import re
from typing import Any

from kazma_core.documents.content_model import (
    BodyBlock,
    Block,
    CitationBlock,
    ContentModel,
    HeadingBlock,
    TableBlock,
    TitleBlock,
    TOCBlock,
)
from kazma_core.documents.profile import DocProfile

logger = logging.getLogger(__name__)

__all__ = ["HtmlEngine"]

# Latin tokens commonly embedded in Arabic docs — isolate them so the bidi
# algorithm keeps them LTR inside the RTL flow (cosmetic; CSS unicode-bidi
# would handle most of this anyway, but explicit <bdi> is robust everywhere).
_URL_RE = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
_STANDARDS_RE = re.compile(r"\b(ISO[\s/]*IEC[\s\-]*\d+(?:-\d+)?|NIST\s+\w+\s+\d+|ECMA-\d+)\b", re.IGNORECASE)


class HtmlEngine:
    """Render a :class:`ContentModel` to themed HTML under a :class:`DocProfile`.

    ``HtmlEngine(profile).render(model)`` → full HTML document string.
    ``HtmlEngine(profile).render_markdown(text)`` → themed wrap of raw markdown
    (for the ``convert:markdown:html`` path that has no payload, only a source).
    """

    def __init__(self, profile: DocProfile) -> None:
        self.profile = profile
        self.theme = profile.theme

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def render(self, model: ContentModel) -> str:
        title = self._first_title(model)
        body_parts: list[str] = []
        for block in model.blocks:
            try:
                chunk = self._render_block(block)
                if chunk:
                    body_parts.append(chunk)
            except Exception:
                logger.debug("[html] block render failed: %r", block, exc_info=True)
        body_html = "\n".join(body_parts)
        return self._document(title, body_html, header_card=bool(title))

    def render_markdown(self, markdown_text: str, *, title: str | None = None) -> str:
        """Themed HTML wrap of a raw markdown string (convert path)."""
        body_html = self._markdown_to_html(markdown_text)
        safe_title = title or ""
        return self._document(safe_title, body_html, header_card=False)

    # ================================================================== #
    # document shell + theme CSS
    # ================================================================== #
    def _document(self, title: str, body_html: str, *, header_card: bool) -> str:
        from html import escape as esc

        chrome = self.profile.chrome
        title_esc = esc(title) if title else ""
        header = (
            f'  <div class="header-card"><h1>{title_esc}</h1></div>\n'
            if (header_card and title_esc) else ""
        )
        return (
            f"<!doctype html>\n"
            f'<html lang="{esc(chrome["lang"])}" dir="{self.profile.html_dir}">\n'
            f"<head>\n"
            f'  <meta charset="utf-8">\n'
            f"  <title>{title_esc}</title>\n"
            f"  <style>\n{self._css()}\n  </style>\n"
            f"</head>\n"
            f"<body>\n"
            f"{header}"
            f'  <div class="content-body">\n{body_html}\n  </div>\n'
            f"</body>\n</html>\n"
        )

    def _css(self) -> str:
        """Themed stylesheet. EN/AR differ only by direction (from the profile)."""
        t = self.theme
        direction = self.profile.html_dir
        text_align_last = "right" if self.profile.rtl else "left"
        return f"""
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: 'Segoe UI', 'IBM Plex Sans Arabic', 'IBM Plex Sans', -apple-system, sans-serif;
      direction: {direction};
      text-align: justify;
      text-align-last: {text_align_last};
      line-height: 1.65;
      color: {t["body"]};
      font-size: 11pt;
      max-width: 820px;
      margin: 0 auto;
      padding: 24px;
      background: #fff;
    }}
    .header-card {{
      background: {t["accent"]};
      color: #fff;
      padding: 16px 20px;
      margin: 0 0 22px 0;
      border-radius: 6px;
    }}
    .header-card h1 {{ font-size: 20pt; margin: 0; color: #fff; }}
    .content-body h1, .content-body h2 {{
      background: {t["heading_fill"]};
      color: #fff !important;
      padding: 8px 14px;
      margin: 22px 0 12px 0;
      border-radius: 4px;
      font-size: 15pt;
    }}
    .content-body h3 {{
      color: {t["heading"]};
      border-bottom: 2px solid {t["border"]};
      padding-bottom: 4px;
      margin: 16px 0 8px 0;
    }}
    p {{ text-align: justify; margin: 0 0 0.8em 0; }}
    table {{
      width: 100%; border-collapse: collapse; margin: 16px 0;
      direction: {direction}; font-size: 10pt;
    }}
    th, td {{ border: 1px solid {t["table_grid"]}; padding: 8px 12px; text-align: start; }}
    th {{ background-color: {t["table_header_bg"]}; font-weight: 600; color: {t["table_header_fg"]}; }}
    td {{ background-color: {t["table_row_bg"]}; }}
    pre, code {{
      font-family: 'Consolas', 'IBM Plex Mono', monospace !important;
      direction: ltr !important; text-align: left !important; unicode-bidi: isolate !important;
    }}
    pre {{
      background-color: {t["accent"]}; color: #f8fafc;
      padding: 14px 18px; border-radius: 8px; font-size: 9pt; line-height: 1.55;
      white-space: pre-wrap !important; overflow-x: auto;
    }}
    p code, td code {{
      background-color: {t["code_bg"]}; color: {t["accent"]};
      padding: 2px 6px; border-radius: 4px; font-size: 9.5pt; border: 1px solid {t["border"]};
    }}
    bdi, [dir="ltr"] {{ direction: ltr !important; unicode-bidi: isolate !important; }}
    ul, ol {{ padding-inline-start: 1.6em; margin: 0.5em 0 1em 0; }}
    blockquote {{
      margin: 12px 0; padding: 8px 16px; background: {t["bg_alt"]};
      border-inline-start: 3px solid {t["heading"]}; color: {t["quote"]};
    }}
    a {{ color: #2563eb; text-decoration: none; }}
    @page {{ size: A4; margin: 18mm 15mm; }}
"""

    # ================================================================== #
    # block rendering
    # ================================================================== #
    def _first_title(self, model: ContentModel) -> str:
        for b in model.blocks:
            if isinstance(b, TitleBlock) and b.level == 0:
                return b.text
        return ""

    def _render_block(self, block: Block) -> str:
        from html import escape as esc

        if isinstance(block, TitleBlock):
            # level 0 is the document title → rendered as the header card in
            # _document(); subtitles (level 3) render as an h3 sub-heading.
            if block.level == 0:
                return ""
            return f"    <h3>{esc(block.text)}</h3>"
        if isinstance(block, HeadingBlock):
            return f'    <h2 style="background:{self.theme["heading_fill"]}">{esc(block.text)}</h2>'
        if isinstance(block, BodyBlock):
            return self._markdown_to_html(block.text, indent="    ")
        if isinstance(block, TOCBlock):
            items = "".join(f"<li>{esc(e)}</li>" for e in block.entries if e)
            head = f'    <h2>{esc(self.profile.chrome["toc"])}</h2>\n'
            return head + f"    <ol>\n{items}    </ol>" if items else head
        if isinstance(block, TableBlock):
            out = []
            if block.heading:
                out.append(f'    <h2>{esc(block.heading)}</h2>')
            out.append("    " + self._table_html(block.headers, block.rows))
            return "\n".join(out)
        if isinstance(block, CitationBlock):
            # escape-then-isolate: esc() must run first so the <bdi> tags
            # _isolate inserts are NOT themselves escaped into literal text.
            items = "".join(f"<li>{self._isolate(esc(c))}</li>" for c in block.items)
            head = f'    <h2>{esc(self.profile.chrome["references"])}</h2>\n'
            return head + f"    <ol>\n{items}    </ol>"
        return ""

    def _table_html(self, headers: list[str], rows: list[list[str]]) -> str:
        from html import escape as esc

        # escape-then-isolate so the <bdi> tags survive as markup.
        head = "".join(f"<th>{self._isolate(esc(str(h)))}</th>" for h in headers)
        body = []
        for row in rows:
            cells = "".join(f"<td>{self._isolate(esc(str(c)))}</td>" for c in row)
            body.append(f"<tr>{cells}</tr>")
        return (
            "<table>\n"
            f"  <thead><tr>{head}</tr></thead>\n"
            f"  <tbody>{''.join(body)}</tbody>\n"
            "</table>"
        )

    # ================================================================== #
    # markdown + bidi isolation
    # ================================================================== #
    def _markdown_to_html(self, text: str, *, indent: str = "") -> str:
        """Render markdown to HTML via the ``markdown`` library, with bidi
        isolation of Latin tokens when RTL."""
        prepared = self._isolate(text) if self.profile.rtl else text
        try:
            import markdown
            html_body = markdown.markdown(
                prepared,
                extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
            )
        except ImportError:
            html_body = "<pre>" + _html_lib.escape(prepared) + "</pre>"
        if indent:
            html_body = "\n".join(indent + ln if ln.strip() else ln for ln in html_body.splitlines())
        return html_body

    def _isolate(self, text: str) -> str:
        """Wrap URLs and Latin standard tokens in ``<bdi dir="ltr">`` so they
        sit correctly inside an RTL flow (cosmetic robustness on top of CSS)."""
        text = _URL_RE.sub(r'<bdi dir="ltr">\1</bdi>', text)
        text = _STANDARDS_RE.sub(r'<bdi dir="ltr">\1</bdi>', text)
        return text
