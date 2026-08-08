"""Industry-Grade PDF Export Architecture for Kazma — Two-Stage HTML/PDF Compilation Pipeline.

Provides prepare_markdown_for_pdf() and generate_pdf_html_document() for generating
enterprise PDF reports with full Arabic RTL support, LTR code/math isolation,
running page footers, and IBM Plex fonts.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

try:
    import markdown
except ImportError:
    markdown = None  # type: ignore

logger = logging.getLogger(__name__)

__all__ = [
    "PDF_HTML_TEMPLATE",
    "generate_pdf_html_document",
    "prepare_markdown_for_pdf",
]

PDF_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    @page {{
      size: A4;
      margin: 20mm 15mm 20mm 15mm;
      @bottom-left {{
        content: "صفحة " counter(page) " من " counter(pages);
        font-family: 'IBM Plex Sans Arabic', sans-serif;
        font-size: 8pt;
        color: #6c757d;
      }}
      @bottom-right {{
        content: "منظومة كاظمة للذكاء الاصطناعي";
        font-family: 'IBM Plex Sans Arabic', sans-serif;
        font-size: 8pt;
        color: #6c757d;
      }}
    }}

    :root {{
      --font-arabic: 'IBM Plex Sans Arabic', -apple-system, sans-serif;
      --font-mono: 'IBM Plex Mono', monospace;
      --primary: #0f172a;
      --border: #e2e8f0;
      --bg-alt: #f8fafc;
    }}

    body {{
      font-family: var(--font-arabic);
      direction: rtl;
      text-align: right;
      line-height: 1.65;
      color: #1e293b;
      font-size: 10pt;
    }}

    .header-card {{
      border-bottom: 2px solid var(--primary);
      padding-bottom: 12px;
      margin-bottom: 20px;
    }}

    .header-card h1 {{
      font-size: 18pt;
      margin: 0 0 8px 0;
      color: var(--primary);
    }}

    .metadata-grid {{
      font-size: 8.5pt;
      color: #475569;
      display: table;
      width: 100%;
    }}

    .metadata-item {{
      display: table-cell;
      padding-inline-end: 15px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 16px 0;
      direction: rtl;
      font-size: 9.5pt;
    }}

    th, td {{
      border: 1px solid var(--border);
      padding: 8px 12px;
      text-align: right;
    }}

    th {{
      background-color: var(--bg-alt);
      font-weight: 600;
      color: var(--primary);
    }}

    pre, code {{
      font-family: var(--font-mono) !important;
      direction: ltr !important;
      text-align: left !important;
      unicode-bidi: isolate !important;
    }}

    pre {{
      background-color: #0f172a;
      color: #f8fafc;
      padding: 12px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 8.5pt;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-all;
      margin: 14px 0;
    }}

    p code, td code {{
      background-color: #f1f5f9;
      color: #0f172a;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 8.5pt;
      border: 1px solid #e2e8f0;
    }}

    .bidi-isolate, bdi, [dir="ltr"] {{
      direction: ltr !important;
      unicode-bidi: isolate !important;
      display: inline-block;
    }}

    .math-inline {{
      direction: ltr !important;
      unicode-bidi: isolate !important;
      display: inline-block;
      padding: 0 4px;
    }}

    .math-block {{
      direction: ltr !important;
      text-align: center !important;
      unicode-bidi: isolate !important;
      margin: 16px 0;
      padding: 10px;
      background-color: var(--bg-alt);
      border-radius: 4px;
    }}

    a {{
      color: #2563eb;
      text-decoration: none;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="header-card">
    <h1>{title}</h1>
    <div class="metadata-grid">
      <div class="metadata-item"><strong>النموذج:</strong> <bdi dir="ltr">{model}</bdi></div>
      <div class="metadata-item"><strong>الجلسة:</strong> <bdi dir="ltr">{session_id}</bdi></div>
      <div class="metadata-item"><strong>التاريخ:</strong> <bdi dir="ltr">{timestamp}</bdi></div>
    </div>
  </div>
  <div class="content-body">
    {content}
  </div>
</body>
</html>
"""


def prepare_markdown_for_pdf(raw_markdown: str) -> str:
    """Stage 1 Pre-processing: Clean markdown, isolate metadata/math, and compile to HTML5."""
    if not raw_markdown:
        return ""

    # 1. Fix escaped currency symbols (\$0.0035 -> $0.0035)
    text = raw_markdown.replace(r"\$", "$")

    # 2. Isolate URLs, model names, and standards
    url_pattern = re.compile(r'(https?://[^\s<>"\'`]+[^\s<>"\'`\.,?!])')
    text = url_pattern.sub(r'<bdi dir="ltr">\1</bdi>', text)

    iso_pattern = re.compile(
        r"\b(ISO/IEC\s+[0-9:\-]+|NIST\s+[A-Z0-9\.\s\-]+|EU\s+AI\s+Act)\b"
    )
    text = iso_pattern.sub(r'<bdi dir="ltr">\1</bdi>', text)

    # 3. Process Display Math ($$ ... $$) & Inline Math ($ ... $)
    text = re.sub(
        r"\$\$(.*?)\$\$",
        r'<div class="math-block" dir="ltr">\1</div>',
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\$(.*?)\$", r'<span class="math-inline" dir="ltr">\1</span>', text)

    # 4. Compile Markdown to semantic HTML5
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

    # Fallback if markdown library is missing
    return f"<pre>{text}</pre>"


def generate_pdf_html_document(
    markdown_content: str,
    title: str = "تقرير تقني",
    model: str = "gemini-2.5-flash",
    session_id: str = "sec-9901-b442-a11c",
    timestamp: str = "2026-08-08 16:45:00",
) -> str:
    """Stage 2 Compilation: Render full HTML document ready for PDF engines (WeasyPrint / ReportLab / Playwright)."""
    compiled_body = prepare_markdown_for_pdf(markdown_content)
    return PDF_HTML_TEMPLATE.format(
        title=title,
        model=model,
        session_id=session_id,
        timestamp=timestamp,
        content=compiled_body,
    )
