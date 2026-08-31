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
from pathlib import Path
from typing import Any

from kazma_core.documents.content_model import (
    Block,
    BodyBlock,
    CitationBlock,
    ContentModel,
    HeadingBlock,
    ImageBlock,
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
        self._assets_dir: Any = None

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def render(self, model: ContentModel, *, assets_dir: Any = None) -> str:
        self._assets_dir = assets_dir
        self._last_heading_text = ""
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
            f'  <header class="doc-title"><h1>{title_esc}</h1></header>\n'
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
        """Themed stylesheet. EN/AR differ by direction + typeface."""
        from kazma_core.documents.fonts import embedded_font_face_css
        from kazma_core.documents.style_theme import theme_cs_size, theme_fonts

        t = self.theme
        direction = self.profile.html_dir
        text_align_last = "right" if self.profile.rtl else "left"
        fonts = theme_fonts(rtl=self.profile.rtl)
        # When a font is pinned, inline it so the export renders identically
        # wherever it is opened. Naming a family the recipient does not have
        # changes line breaks, page count and every Arabic metric the theme
        # tunes for.
        face_css, embedded_family = embedded_font_face_css(
            arabic=bool(self.profile.shape_arabic)
        )
        font_stack = (
            f"'{embedded_family}', {fonts['html']}" if embedded_family else fonts["html"]
        )
        if self.profile.rtl:
            body_pt = theme_cs_size()
            title_pt = theme_cs_size(t.get("title_size", 22))
            h1_pt = theme_cs_size(t.get("h1_size", 17))
            table_pt = theme_cs_size(10)
        else:
            body_pt = t.get("body_size", 11)
            title_pt = t.get("title_size", 22)
            h1_pt = t.get("h2_size", 15)
            table_pt = 10
        leading = t.get("line_height_ar", 1.65) if self.profile.rtl else t.get("line_height", 1.65)
        rule = t.get("accent", "#3b82f6")
        return f"""
    {face_css}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: {font_stack};
      direction: {direction};
      text-align: justify;
      text-align-last: {text_align_last};
      line-height: {leading};
      color: {t["body"]};
      font-size: {body_pt}pt;
      max-width: 820px;
      margin: 0 auto;
      padding: 28px 32px 40px;
      background: #fff;
    }}
    .doc-title {{
      margin: 0 0 28px 0;
      padding: 0 0 14px 0;
      border-bottom: 2px solid {rule};
    }}
    .doc-title h1 {{
      font-size: {title_pt}pt;
      font-weight: 650;
      margin: 0;
      color: {t["heading"]};
      letter-spacing: {('-0.01em' if self.profile.rtl else '-0.02em')};
      line-height: 1.25;
    }}
    .content-body h1, .content-body h2 {{
      background: none;
      color: {t["heading_fill"]} !important;
      padding: 0 0 6px 0;
      margin: 26px 0 12px 0;
      border: none;
      border-inline-start: 3px solid {rule};
      padding-inline-start: 12px;
      font-size: {h1_pt}pt;
      font-weight: 650;
    }}
    .content-body h3 {{
      color: {t["heading"]};
      border-bottom: 1px solid {rule};
      padding-bottom: 4px;
      margin: 18px 0 8px 0;
    }}
    .content-body h1, .content-body h2, .content-body h3 {{
      break-after: avoid;
      page-break-after: avoid;
    }}
    .content-body h1 + *, .content-body h2 + *, .content-body h3 + * {{
      break-before: avoid;
      page-break-before: avoid;
    }}
    p {{ text-align: justify; margin: 0 0 0.8em 0; }}
    table {{
      width: 100%; border-collapse: collapse; margin: 16px 0;
      direction: {direction}; font-size: {table_pt}pt;
    }}
    thead {{ display: table-header-group; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
    /* Never leave one line of a paragraph alone across a page break. */
    p, li {{ orphans: 3; widows: 3; }}
    .math-display {{
      direction: ltr !important; text-align: center; unicode-bidi: isolate;
      font-family: 'Cambria Math', 'Consolas', serif; font-size: 13pt;
      margin: 12px 0;
    }}
    .math-inline {{
      direction: ltr !important; unicode-bidi: isolate;
      font-family: 'Cambria Math', 'Consolas', serif;
    }}
    th, td {{ border: 1px solid {t["table_grid"]}; padding: 8px 12px; text-align: start; }}
    th {{ background-color: {t["table_header_bg"]}; font-weight: 600; color: {t["table_header_fg"]}; }}
    td {{ background-color: {t["table_row_bg"]}; }}
    pre, code {{
      font-family: 'Consolas', 'IBM Plex Mono', monospace !important;
      direction: ltr !important; text-align: left !important; unicode-bidi: isolate !important;
    }}
    pre {{
      background-color: {t["code_bg"]}; color: {t["body"]};
      padding: 14px 18px; border-radius: 8px; font-size: 9pt; line-height: 1.55;
      white-space: pre-wrap !important; overflow-x: auto;
      border: 1px solid {t["border"]};
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
    .toc a {{ color: {t["body"]}; }}
    .toc a:hover {{ text-decoration: underline; }}
    @page {{ size: A4; margin: 18mm 15mm; }}
    {self._pygments_css()}
"""

    @staticmethod
    def _pygments_css() -> str:
        """Pygments token-color CSS for codehilite (light 'friendly' style)."""
        try:
            from pygments.formatters import HtmlFormatter
            return HtmlFormatter(style="friendly").get_style_defs(".codehilite")
        except Exception:
            return ""

    @staticmethod
    def _slugify(text: str) -> str:
        """URL-safe slug from heading text (Unicode-aware, handles Arabic)."""
        slug = re.sub(r"[^\w\u0600-\u06FF -]", "", (text or "").lower()).strip().replace(" ", "-")
        return slug or "section"

    # ================================================================== #
    # block rendering
    # ================================================================== #
    def _first_title(self, model: ContentModel) -> str:
        for b in model.blocks:
            if isinstance(b, TitleBlock) and b.level == 0:
                return b.text
        return ""

    def _render_block(self, block: Block) -> str:
        """Render one block, tagging it when its direction is not the page's."""
        rendered = self._render_block_inner(block)
        text = getattr(block, "text", "") or getattr(block, "heading", "") or ""
        if not rendered.strip() or not text:
            return rendered
        direction = self.profile.block_direction(text)
        if direction == self.profile.direction:
            return rendered
        # A mixed document has blocks that read the other way. The browser
        # would inherit the page direction and lay them out backwards, so the
        # exception is marked explicitly. Uniform documents emit no wrapper at
        # all, which keeps their markup byte-identical to before.
        align = "right" if direction == "rtl" else "left"
        return (
            f'    <div dir="{direction}" style="text-align:{align}">\n'
            f"{rendered}\n    </div>"
        )

    def _render_block_inner(self, block: Block) -> str:
        from html import escape as esc

        if isinstance(block, TitleBlock):
            # level 0 is the document title → rendered as the header card in
            # _document(); subtitles (level 3) render as an h3 sub-heading.
            if block.level == 0:
                return ""
            return f"    <h3>{esc(block.text)}</h3>"
        if isinstance(block, HeadingBlock):
            from kazma_core.documents.heading_text import headings_equivalent

            prev = getattr(self, "_last_heading_text", "") or ""
            if headings_equivalent(block.text, prev):
                return ""
            self._last_heading_text = block.text or ""
            slug = self._slugify(block.text)
            return (
                f'    <h2 id="{slug}" style="color:{self.theme["heading_fill"]}">'
                f"{esc(block.text)}</h2>"
            )
        if isinstance(block, BodyBlock):
            return self._markdown_to_html(block.text, indent="    ")
        if isinstance(block, TOCBlock):
            items = "".join(
                f'<li><a href="#{self._slugify(e)}">{esc(e)}</a></li>'
                for e in block.entries if e
            )
            head = f'    <h2>{esc(self.profile.chrome["toc"])}</h2>\n'
            return head + f'    <ol class="toc">\n{items}    </ol>' if items else head
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
        if isinstance(block, ImageBlock):
            return self._image_html(block)
        return ""

    def _image_html(self, block: ImageBlock) -> str:
        """Embed an approved-asset image as a base64 data-URI (no external refs).

        Only files in the validated assets dir are reachable; missing images are
        skipped. A caption, if any, becomes a <figcaption>.
        """
        import base64

        path = self._resolve_asset(block.name)
        if path is None:
            logger.debug("[html] image not in approved assets, skipped: %s", block.name)
            return ""
        try:
            data = Path(path).read_bytes()
        except Exception:
            logger.debug("[html] image read failed: %s", block.name, exc_info=True)
            return ""
        ext = Path(path).suffix.lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp"}.get(ext, "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        from html import escape as esc
        cap = f"\n      <figcaption>{esc(block.caption)}</figcaption>" if block.caption else ""
        return (
            f'    <figure class="image"><img src="data:{mime};base64,{b64}" '
            f'alt="{esc(block.caption or block.name)}" style="width:{block.width_in}in;max-width:100%">{cap}\n'
            f"    </figure>"
        )

    def _resolve_asset(self, name: str) -> Any:
        if not name or not self._assets_dir:
            return None
        candidate = Path(str(self._assets_dir)) / Path(str(name)).name
        return candidate if candidate.is_file() else None

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
        prepared, held = self._hold_math(text or "")
        prepared = self._isolate(prepared) if self.profile.rtl else prepared
        try:
            import markdown
            html_body = markdown.markdown(
                prepared,
                extensions=["tables", "fenced_code", "sane_lists", "attr_list", "codehilite"],
            )
        except ImportError:
            html_body = "<pre>" + _html_lib.escape(prepared) + "</pre>"
        for i, frag in enumerate(held):
            html_body = html_body.replace(f"@@MATH{i}@@", frag)
        if indent:
            html_body = "\n".join(indent + ln if ln.strip() else ln for ln in html_body.splitlines())
        return html_body

    @staticmethod
    def _hold_math(text: str) -> tuple[str, list[str]]:
        """Replace ``$`` / ``$$`` math with placeholders the markdown pass won't eat."""
        from kazma_core.documents.math_text import (
            latex_to_unicode,
            split_display_math,
            split_inline_math,
        )

        held: list[str] = []

        def park(html_frag: str) -> str:
            held.append(html_frag)
            return f"@@MATH{len(held) - 1}@@"

        pieces: list[str] = []
        for kind, chunk in split_display_math(text):
            if kind == "math":
                pieces.append(
                    park(
                        f'<p class="math-display" dir="ltr">'
                        f"{_html_lib.escape(latex_to_unicode(chunk))}</p>"
                    )
                )
                continue
            inner: list[str] = []
            for k2, c2 in split_inline_math(chunk):
                if k2 == "math":
                    inner.append(
                        park(
                            f'<span class="math-inline" dir="ltr">'
                            f"{_html_lib.escape(latex_to_unicode(c2))}</span>"
                        )
                    )
                elif k2 == "money":
                    inner.append(
                        park(
                            f'<span class="math-inline" dir="ltr">'
                            f"${_html_lib.escape(c2.strip())}</span>"
                        )
                    )
                else:
                    inner.append(c2)
            pieces.append("".join(inner))
        return "".join(pieces), held

    def _isolate(self, text: str) -> str:
        """Wrap URLs and Latin standard tokens in ``<bdi dir="ltr">`` so they
        sit correctly inside an RTL flow (cosmetic robustness on top of CSS)."""
        text = _URL_RE.sub(r'<bdi dir="ltr">\1</bdi>', text)
        text = _STANDARDS_RE.sub(r'<bdi dir="ltr">\1</bdi>', text)
        return text
