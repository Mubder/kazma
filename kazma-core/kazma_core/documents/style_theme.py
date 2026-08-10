"""Unified Kazma document visual theme (PDF + DOCX, EN + AR).

One design language for both directions. Only *direction* and *shaping*
change for Arabic; colors, spacing, heading bars, and tables stay identical.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "THEME",
    "theme_colors_reportlab",
    "localized_chrome",
]

# Shared tokens — keep in sync with HTML exporter CSS in skills/exporter.py
THEME: dict[str, Any] = {
    "accent": "#0f172a",
    "heading": "#1e3a5f",
    "heading_fill": "#1e3a5f",
    "heading_text": "#ffffff",
    "body": "#1e293b",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "bg_alt": "#f8fafc",
    "quote": "#475569",
    "code_bg": "#f1f5f9",
    "table_header_bg": "#1e3a5f",
    "table_header_fg": "#ffffff",
    "table_row_bg": "#f8fafc",
    "table_grid": "#94a3b8",
    "title_size": 20,
    "h1_size": 16,
    "h2_size": 14,
    "h3_size": 12.5,
    "body_size": 11,
    "line_height": 1.65,
    "page_margin": 54,  # points ≈ 19mm
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
