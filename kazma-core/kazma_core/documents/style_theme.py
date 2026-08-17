"""Unified Kazma document visual theme (PDF + DOCX, EN + AR).

Print projection of the Web brand: royal ``#3b82f6``, sky ``#38bdf8``,
deep navy ink. No gold. Editorial headings (type + accent rule), not
inverted bars. Arabic uses a larger body size / leading.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "THEME",
    "theme_colors_reportlab",
    "theme_fonts",
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
    "body_size_ar": 14,
    "line_height": 1.65,
    "line_height_ar": 2.0,
    "page_margin": 56,
    "font_latin": "Calibri",
    "font_arabic": "Sakkal Majalla",
    "page_size_mm": (210.0, 297.0),
}


def theme_fonts(*, rtl: bool) -> dict[str, str]:
    """Latin vs complex-script font names for the active direction."""
    latin = str(THEME.get("font_latin") or "Calibri")
    arabic = str(THEME.get("font_arabic") or "Sakkal Majalla")
    return {
        "latin": latin,
        "arabic": arabic,
        "cs": arabic if rtl else latin,
        "html": (
            "'IBM Plex Sans Arabic', 'Sakkal Majalla', 'Traditional Arabic', "
            "'Segoe UI', 'Calibri', sans-serif"
            if rtl
            else "'Calibri', 'Segoe UI', 'IBM Plex Sans', sans-serif"
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


def localized_chrome(*, rtl: bool) -> dict[str, str]:
    """Header/footer/page chrome strings for EN vs AR (same layout)."""
    if rtl:
        return {
            "brand": "منظومة كاظمة للذكاء الاصطناعي",
            "toc": "المحتويات",
            "references": "المراجع",
            "page_fmt": "صفحة {n}",
            "lang": "ar",
            "dir": "rtl",
        }
    return {
        "brand": "Kazma AI Platform",
        "toc": "Contents",
        "references": "References",
        "page_fmt": "Page {n}",
        "lang": "en",
        "dir": "ltr",
    }
