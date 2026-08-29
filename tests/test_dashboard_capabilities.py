"""Dashboard Active Capabilities lists 0.10 product surfaces."""

from __future__ import annotations

from tests._module_source import module_source

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HTML = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "dashboard.html"
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "dashboard.js"
_I18N = _ROOT / "kazma-ui" / "kazma_ui" / "i18n.py"


def test_dash_caps_includes_new_surfaces() -> None:
    html = _HTML.read_text(encoding="utf-8")
    js = _JS.read_text(encoding="utf-8")
    i18n = module_source(_I18N)
    assert "window.DASH_CAPS" in html
    for cap_id in ("cua", "voice", "ide", "mcp", "hitl", "doc", "x"):
        assert f'id: "{cap_id}"' in html or f"id: '{cap_id}'" in html
        assert f"dashboard.cap.{cap_id}" in i18n
    assert "svgIcons[f.id]" in js
    assert "Computer use" in js
    assert "LiveKit" in js or "Voice duplex" in js


def test_stale_document_processor_label_gone() -> None:
    js = _JS.read_text(encoding="utf-8")
    assert "Document Processor" not in js
    assert "Web Crawler" not in js
