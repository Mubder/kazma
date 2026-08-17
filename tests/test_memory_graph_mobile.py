"""Memory graph inspect sheet stays compact on phones."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js"
_CSS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
_HTML = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "components" / "memory_console.html"


def test_inspect_uses_sheet_class_not_always_on_canvas() -> None:
    html = _HTML.read_text(encoding="utf-8")
    assert 'id="v2g-inspect"' in html
    assert 'class="v2g-inspect"' in html
    assert "hidden" in html.split('id="v2g-inspect"', 1)[1].split(">", 1)[0]
    # Old overlay filled 88vw × 45% of the canvas
    assert "88vw" not in html
    assert "max-height: 45%" not in html


def test_inspect_css_is_a_phone_bottom_sheet() -> None:
    css = _CSS.read_text(encoding="utf-8")
    assert ".v2g-inspect {" in css
    assert "max-height: min(36vh, 220px)" in css
    assert "backdrop-filter: none" in css
    assert "#v2g-tooltip { display: none !important; }" in css


def test_inspect_js_closes_and_caps_phone_edges() -> None:
    js = _JS.read_text(encoding="utf-8")
    assert "function _v2gInspectClose()" in js
    assert "function _v2gInspectSet(" in js
    assert "function _v2gIsPhone()" in js
    assert "_v2gInspectClose();" in js
    assert 'edgeLimit = (phone && !_v2gInspectAllEdges) ? 4' in js
    assert "sheetOpen" in js
    assert "v2g-inspect-more-btn" in js
