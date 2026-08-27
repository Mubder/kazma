"""Chat-as-product UI overhaul — ID contract + chrome (no chat.js rewrite)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHAT = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
_BASE = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "base.html"
_SIDEBAR = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "components" / "sidebar.html"
_V5 = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.v5.css"
_CHAT_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"

_IDS = (
    "chat-input",
    "send-btn",
    "chat-messages",
    "model-selector",
    "capacity-status",
    "session-cost",
    "session-tokens",
    "context-size",
    "capacity-bar",
    "voice-btn",
    "voice-live-btn",
    "attach-btn",
    "file-input",
    "thinking-indicator",
    "new-session-btn",
    "session-list",
    "session-search",
)


def test_load_bearing_ids_unique() -> None:
    html = _CHAT.read_text(encoding="utf-8")
    for eid in _IDS:
        needle = f'id="{eid}"'
        assert html.count(needle) == 1, f"{eid} must appear once (hidden OK)"
    assert 'data-cap="/yolo"' in html
    assert 'data-cap="/long on"' in html
    assert "chat-model-bar" in html
    assert 'class="composer-chrome"' in html
    assert 'class="capacity-group session-metrics"' in html


def test_composer_more_hides_capacity_not_deletes() -> None:
    html = _CHAT.read_text(encoding="utf-8")
    assert 'class="composer-more"' in html
    more_at = html.find('class="composer-more"')
    cap_at = html.find('id="capacity-bar"')
    assert 0 <= more_at < cap_at
    assert "composer-more-toggle" in html


def test_immersive_chat_class_on_layout() -> None:
    base = _BASE.read_text(encoding="utf-8")
    v5 = _V5.read_text(encoding="utf-8")
    assert "is-chat" in base
    assert ".app-layout.is-chat .chat-container" in v5


def test_chat_keeps_page_header() -> None:
    """Operator decision 2026-08-27: chat shows the page header — it carries
    the language toggle and unifies the look across pages. The immersive
    hide rule must NOT come back."""
    v5 = _V5.read_text(encoding="utf-8")
    assert ".app-layout.is-chat .page-header { display: none; }" not in v5


def test_session_search_input_contained() -> None:
    """RTL regression (2026-08-27): the sessions search input must be
    width-constrained to its wrapper — the intrinsic browser default
    overflowed the sidebar in Arabic."""
    v5 = _V5.read_text(encoding="utf-8")
    css = _V5.parent.joinpath("kazma.css").read_text(encoding="utf-8")
    assert "width: 100%;" in v5.split(".session-search-input {")[1].split("}")[0]
    assert "min-width: 0;" in css.split(".session-search-wrapper {")[1].split("}")[0]


def test_sidebar_is_grouped_not_more_disclosure() -> None:
    """Operator rejected always-on More that hid Work to 3 items."""
    html = _SIDEBAR.read_text(encoding="utf-8")
    assert "nav-more" not in html
    assert "<details" not in html
    assert "nav.primary" in html
    assert "nav.activity" in html
    assert "nav.configuration" in html
    nav_hrefs = [
        line for line in html.splitlines() if "nav-link" in line and "href=" in line
    ]
    joined = "\n".join(nav_hrefs)
    for href in (
        "/chat",
        "/workspace",
        "/ide",
        "/memory",
        "/dashboard",
        "/agents",
        "/research",
        "/documents",
        "/swarm",
        "/knowledge",
        "/replay",
        "/settings",
        "/skills",
        "/mcp",
    ):
        assert joined.count(f'href="{href}"') == 1, href


def test_reduced_motion_and_tap_target() -> None:
    v5 = _V5.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in v5
    assert "composer-more-toggle" in v5
    assert "min-width: var(--tap)" in v5 or "min-width:var(--tap)" in v5.replace(" ", "")


def test_chat_js_untouched_by_overhaul_contract() -> None:
    """G10: this overhaul must not rewrite chat.js (IDs stay the contract)."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "model-selector" in js
    assert "capacity-bar" in js
    assert "data-cap" in js
