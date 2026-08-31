"""Unified Kazma document visual theme (PDF + DOCX, EN + AR).

Print projection of the Web brand: royal ``#3b82f6``, sky ``#38bdf8``,
deep navy ink. No gold. Editorial headings (type + accent rule), not
inverted bars. Arabic body is 12pt vs Latin 11pt; leading is 1.65 both ways.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "THEME",
    "format_page_number",
    "format_document_date",
    "theme_colors_reportlab",
    "theme_fonts",
    "theme_cs_size",
    "localized_chrome",
]

# Shared tokens — keep in sync with HTML engine CSS.
THEME: dict[str, Any] = {
    # Brand (kazma.css): royal accent, sky secondary, deep navy ink.
    "accent": "#3b82f6",
    "secondary": "#38bdf8",
    "heading": "#16223a",
    "heading_fill": "#16223a",
    "heading_text": "#16223a",
    "heading_on_fill": "#ffffff",
    "body": "#1e293b",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "bg_alt": "#f0f4fa",
    "quote": "#475569",
    "code_bg": "#eff6ff",
    "table_header_bg": "#eff6ff",
    "table_header_fg": "#16223a",
    "table_row_bg": "#f8fafc",
    "table_grid": "#bfdbfe",
    "title_size": 24,
    "h1_size": 17,
    "h2_size": 15,
    "h3_size": 13,
    "body_size": 11,
    # IBM Plex Sans Arabic is the brand face (UI + generated docs). It does
    # not need Sakkal's +5pt optical compensation; keep a 1pt CS bump.
    "body_size_ar": 12,
    "line_height": 1.65,
    "line_height_ar": 1.65,
    "page_margin": 56,
    "font_latin": "IBM Plex Sans Arabic",
    "font_arabic": "IBM Plex Sans Arabic",
    "page_size_mm": (210.0, 297.0),
}


def theme_cs_size(latin_pt: float | None = None) -> float:
    """Point size for complex-script (Arabic) given a Latin size.

    IBM Plex Sans Arabic is close to the Latin optical size, so Arabic body
    is ``body_size_ar`` (12pt) while Latin stays ``body_size`` (11pt).
    Headings keep the same delta. Chrome (≤9.5pt headers/footers/captions)
    gets a modest +2pt so it does not jump to body size.
    """
    body = float(THEME.get("body_size") or 11)
    body_ar = float(THEME.get("body_size_ar") or 12)
    if latin_pt is None:
        return body_ar
    try:
        pt = float(latin_pt)
    except (TypeError, ValueError):
        return body_ar
    if pt <= 9.5:
        return pt + 2.0
    return pt + (body_ar - body)


def theme_fonts(*, rtl: bool) -> dict[str, str]:
    """Latin vs complex-script font names for the active direction."""
    latin = str(THEME.get("font_latin") or "IBM Plex Sans Arabic")
    arabic = str(THEME.get("font_arabic") or "IBM Plex Sans Arabic")
    return {
        "latin": latin,
        "arabic": arabic,
        "cs": arabic if rtl else latin,
        "html": (
            "'IBM Plex Sans Arabic', 'IBM Plex Sans', sans-serif"
            if rtl
            else "'IBM Plex Sans', 'IBM Plex Sans Arabic', sans-serif"
        ),
    }


def theme_colors_reportlab() -> dict[str, Any]:
    """Resolve THEME hex strings to reportlab Color objects."""
    from reportlab.lib import colors

    out: dict[str, Any] = {}
    for key, val in THEME.items():
        if isinstance(val, str) and val.startswith("#"):
            out[key] = colors.HexColor(val)
        else:
            out[key] = val
    return out


def localized_chrome(*, rtl: bool, numerals: str = "latn") -> dict[str, str]:
    """Header/footer/page chrome strings for EN vs AR (same layout).

    Picked by :meth:`DocProfile.for_content` from the document language
    (``lang=en`` / ``lang=ar`` / auto-detect). Header uses ``brand_short``;
    footer uses ``brand``. Callers can still override ``header`` / ``footer``
    on the generate payload. ``page_fmt`` wraps the live page number
    (``Page {n}`` / ``صفحة {n}``).

    ``numerals`` selects the digit set for page numbers and any other generated
    figure: ``"latn"`` (ASCII 0-9) or ``"arab"`` (Arabic-Indic ٠-٩). The page
    format string is a template, so the substitution happens at render time —
    :func:`format_page_number` is the single place that applies the digit set.
    """
    if rtl:
        return {
            "brand": "منظومة كاظمة للذكاء الاصطناعي",
            "brand_short": "كاظمه",
            "toc": "المحتويات",
            "references": "المراجع",
            "page_fmt": "صفحة {n}",
            "lang": "ar",
            "dir": "rtl",
            "numerals": numerals if numerals in ("latn", "arab") else "latn",
        }
    return {
        "brand": "Kazma AI Platform",
        "brand_short": "Kazma",
        "toc": "Contents",
        "references": "References",
        "page_fmt": "Page {n}",
        "lang": "en",
        "dir": "ltr",
        "numerals": "latn",
    }


def format_page_number(value: int | str, *, numerals: str = "latn") -> str:
    """Render a page number in the document's digit set."""
    text = str(value)
    if numerals == "arab":
        from kazma_core.documents.arabic import to_arabic_numerals

        return to_arabic_numerals(text)
    return text


def format_document_date(when: date | None = None, *, rtl: bool,
                         calendar: str = "gregory",
                         numerals: str = "latn") -> str:
    """Format a document date in the profile's calendar and digit set.

    Kazma already ships a Hijri converter and Arabic month names in
    :mod:`kazma_core.cultural_context`; it was wired into chat and never into
    the document engines, so every Arabic document carried a Gregorian date in
    ASCII digits. This is the bridge. Falls back to the Gregorian rendering if
    the cultural module is unavailable.
    """
    from datetime import date as _date

    today = when or _date.today()
    if calendar == "islamic-umalqura":
        try:
            from kazma_core.cultural_context import (
                _gregorian_to_hijri_approx,
                _hijri_month_name,
            )

            year, month, day = _gregorian_to_hijri_approx(today)
            rendered = f"{day} {_hijri_month_name(month)} {year} هـ"
            return format_page_number(rendered, numerals=numerals) if numerals == "arab" else rendered
        except Exception:  # pragma: no cover - defensive
            logger.debug("[style_theme] Hijri conversion unavailable", exc_info=True)
    iso = today.isoformat()
    return format_page_number(iso, numerals=numerals) if numerals == "arab" else iso
