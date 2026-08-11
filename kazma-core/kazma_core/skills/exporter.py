"""Industry-Grade PDF Export Architecture — Two-Stage HTML/PDF pipeline.

Uses the **same visual theme** as reportlab PDF generation
(``kazma_core.documents.style_theme``) so EN and AR exports match.
Arabic gets ``dir=rtl``; English gets ``dir=ltr``. Styling tokens are shared.
"""

from __future__ import annotations

import html as html_lib
import logging
import re

try:
    import markdown
except ImportError:
    markdown = None  # type: ignore

from kazma_core.documents.style_theme import THEME

logger = logging.getLogger(__name__)

__all__ = [
    "PDF_HTML_TEMPLATE",
    "generate_pdf_html_document",
    "prepare_markdown_for_pdf",
]

# Legacy name kept for importers; prefer generate_pdf_html_document().
PDF_HTML_TEMPLATE = ""


def prepare_markdown_for_pdf(raw_markdown: str) -> str:
    """Stage 1: clean markdown, isolate LTR tokens, compile to HTML5."""
    if not raw_markdown:
        return ""

    text = raw_markdown.replace(r"\$", "$")

    url_pattern = re.compile(r'(https?://[^\s<>"\'`]+[^\s<>"\'`\.,?!])')
    text = url_pattern.sub(r'<bdi dir="ltr">\1</bdi>', text)

    iso_pattern = re.compile(
        r"\b(ISO/IEC\s+[0-9:\-]+|NIST\s+[A-Z0-9\.\s\-]+|EU\s+AI\s+Act)\b"
    )
    text = iso_pattern.sub(r'<bdi dir="ltr">\1</bdi>', text)

    text = re.sub(
        r"\$\$(.*?)\$\$",
        r'<div class="math-block" dir="ltr">\1</div>',
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\$(.*?)\$", r'<span class="math-inline" dir="ltr">\1</span>', text)

    if markdown is not None:
        try:
            return markdown.markdown(
                text,
                extensions=[
                    "tables",
                    "fenced_code",
                    "codehilite",
                    "sane_lists",
                    "attr_list",
                ],
            )
        except Exception as exc:
            logger.debug("[exporter] markdown extension compile fallback: %s", exc)
            return markdown.markdown(text)

    return f"<pre>{html_lib.escape(text)}</pre>"


def _css(*, rtl: bool, brand: str) -> str:
    """Unified stylesheet — EN and AR differ only by direction / text-align-last."""
    t = THEME
    text_align_last = "right" if rtl else "left"
    direction = "rtl" if rtl else "ltr"
    return f"""
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    @page {{
      size: A4;
      margin: 20mm 15mm 20mm 15mm;
      @bottom-left {{
        content: counter(page);
        font-family: 'IBM Plex Sans Arabic', 'IBM Plex Sans', sans-serif;
        font-size: 8pt;
        color: {t["muted"]};
      }}
      @bottom-right {{
        content: "{brand}";
        font-family: 'IBM Plex Sans Arabic', 'IBM Plex Sans', sans-serif;
        font-size: 8pt;
        color: {t["muted"]};
      }}
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}

    body {{
      font-family: 'IBM Plex Sans Arabic', 'IBM Plex Sans', -apple-system, sans-serif;
      direction: {direction};
      text-align: justify;
      text-align-last: {text_align_last};
      line-height: 1.65;
      color: {t["body"]};
      font-size: 10.5pt;
    }}

    .header-card {{
      background: {t["accent"]};
      color: #fff;
      padding: 14px 16px;
      margin: 0 0 20px 0;
      border-radius: 4px;
    }}

    .header-card h1 {{
      font-size: 18pt;
      margin: 0 0 8px 0;
      color: #fff;
    }}

    .metadata-grid {{
      font-size: 8.5pt;
      color: #e2e8f0;
      display: table;
      width: 100%;
    }}

    .metadata-item {{
      display: table-cell;
      padding-inline-end: 15px;
    }}

    h1, h2 {{
      background: {t["heading_fill"]};
      color: #fff !important;
      padding: 8px 12px;
      margin: 18px 0 10px 0;
      border-radius: 3px;
      font-size: 14pt;
    }}

    h3 {{
      color: {t["heading"]};
      border-bottom: 2px solid {t["border"]};
      padding-bottom: 4px;
      margin: 14px 0 8px 0;
    }}

    p {{
      text-align: justify;
      margin: 0 0 0.75em 0;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 16px 0;
      direction: inherit;
      font-size: 9.5pt;
    }}

    th, td {{
      border: 1px solid {t["table_grid"]};
      padding: 8px 12px;
      text-align: start;
    }}

    th {{
      background-color: {t["table_header_bg"]};
      font-weight: 600;
      color: {t["table_header_fg"]};
    }}

    td {{
      background-color: {t["table_row_bg"]};
    }}

    pre, code {{
      font-family: 'IBM Plex Mono', Consolas, monospace !important;
      direction: ltr !important;
      text-align: left !important;
      unicode-bidi: isolate !important;
    }}

    pre {{
      background-color: {t["accent"]};
      color: #f8fafc;
      padding: 1rem 1.2rem;
      border-radius: 8px;
      font-size: 0.78rem !important;
      line-height: 1.55;
      white-space: pre-wrap !important;
      page-break-inside: avoid;
    }}

    p code, td code {{
      background-color: {t["code_bg"]};
      color: {t["accent"]};
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 8.5pt;
      border: 1px solid {t["border"]};
    }}

    .bidi-isolate, bdi, [dir="ltr"] {{
      direction: ltr !important;
      unicode-bidi: isolate !important;
      display: inline-block;
    }}

    ul, ol {{
      padding-inline-start: 1.4em;
      margin: 0.5em 0 1em 0;
    }}

    blockquote {{
      margin: 12px 0;
      padding: 8px 14px;
      background: {t["bg_alt"]};
      border-inline-start: 3px solid {t["heading"]};
      color: {t["quote"]};
    }}

    a {{ color: #2563eb; text-decoration: none; word-break: break-all; }}
"""


def generate_pdf_html_document(
    markdown_content: str,
    title: str = "Technical Report",
    model: str = "model",
    session_id: str = "session",
    timestamp: str = "",
    *,
    lang: str | None = None,
    rtl: bool | None = None,
) -> str:
    """Stage 2: full HTML document ready for WeasyPrint / Playwright.

    EN and AR share the same CSS theme; only ``dir`` / brand strings change.
    Direction comes from the unified :class:`DocProfile` (full Unicode Arabic
    detection + lang/rtl overrides) so this chat-export path agrees with the
    DOCX/PDF/HTML document engines — no parallel ad-hoc RTL heuristic.
    """
    from kazma_core.documents.profile import DocProfile

    profile = DocProfile.for_content(
        f"{title}\n{markdown_content}", language=lang, rtl=rtl,
    )
    chrome = profile.chrome
    compiled_body = prepare_markdown_for_pdf(markdown_content)
    safe_title = html_lib.escape(title)
    css = _css(rtl=profile.rtl, brand=chrome["brand"].replace('"', ""))

    return f"""<!DOCTYPE html>
<html lang="{chrome["lang"]}" dir="{chrome["dir"]}">
<head>
  <meta charset="UTF-8">
  <title>{safe_title}</title>
  <style>
{css}
  </style>
</head>
<body>
  <div class="header-card">
    <h1>{safe_title}</h1>
    <div class="metadata-grid">
      <div class="metadata-item"><strong>Model:</strong> <bdi dir="ltr">{html_lib.escape(model)}</bdi></div>
      <div class="metadata-item"><strong>Session:</strong> <bdi dir="ltr">{html_lib.escape(session_id)}</bdi></div>
      <div class="metadata-item"><strong>Date:</strong> <bdi dir="ltr">{html_lib.escape(timestamp)}</bdi></div>
    </div>
  </div>
  <div class="content-body">
    {compiled_body}
  </div>
</body>
</html>
"""
