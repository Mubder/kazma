"""Composer chrome: grouped capacity bar + quiet metrics (no gold)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHAT_HTML = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
_CHAT_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
_CSS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"


def test_capacity_bar_is_grouped_not_loose_pills() -> None:
    html = _CHAT_HTML.read_text(encoding="utf-8")
    assert 'class="composer-chrome"' in html
    assert 'class="capacity-group"' in html
    assert 'class="capacity-reset"' in html
    assert "capacity-pill-power" not in html
    assert "session-metrics" in html


def test_active_pills_use_muted_danger_not_gold() -> None:
    css = _CSS.read_text(encoding="utf-8")
    cap = css.split(".capacity-pill.is-on")[1].split(".capacity-reset")[0]
    assert "var(--danger)" in cap
    assert "#ca8a04" not in cap
    assert "#a16207" not in cap
    assert "#fff8e1" not in cap
    # Unrestricted must not look permanently on.
    assert "capacity-pill-power" not in html_power_block(css)


def html_power_block(css: str) -> str:
    if ".capacity-pill-power {" not in css:
        return ""
    return css.split(".capacity-pill-power")[1].split("}", 1)[0]


def test_metrics_share_button_row_and_shape() -> None:
    html = _CHAT_HTML.read_text(encoding="utf-8")
    css = _CSS.read_text(encoding="utf-8")
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert 'class="capacity-group session-metrics"' in html
    assert "capacity-stat" in html
    assert ".session-metrics" in css
    assert "margin-inline-start: auto" in css.split(".session-metrics")[1][:80]
    assert "flex-wrap: wrap" in css.split(".capacity-bar {")[1][:160]
    assert ".capacity-bar .session-metrics" in css
    assert "flex: 1 0 100%" in css
    assert ".char-badge.is-empty { display: none; }" in css.replace("\n", " ")
    assert "formatCompactCount" in js
    assert "'~' + formatCompactCount(totalTokens) + ' ctx'" in js
    assert "charBadge.hidden = n === 0" in js
    assert "YOLO' : 'HITL'" not in js
