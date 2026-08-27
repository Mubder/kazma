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


def test_capacity_row_visible_not_hidden_behind_popover() -> None:
    """Operator decision 2026-08-27: the FULL capacity row (budget status +
    mode pills + Reset + session usage) is a visible toolbar under the text
    field — the ⋯ composer-more popover that hid it is removed."""
    html = _CHAT.read_text(encoding="utf-8")
    assert 'class="composer-more"' not in html
    assert "composer-more-toggle" not in html
    assert 'id="capacity-bar"' in html
    chrome_at = html.find('class="composer-chrome"')
    cap_at = html.find('id="capacity-bar"')
    assert 0 <= chrome_at < cap_at, "capacity-bar must live inside .composer-chrome"


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


def test_transcript_wider_measure_small_side_margins() -> None:
    """Operator decision 2026-08-27: the inner chat column was too slim —
    message measure widened to 96ch and the 12vw side slab reduced to a
    small clamp() margin."""
    v5 = _V5.read_text(encoding="utf-8")
    css = _V5.parent.joinpath("kazma.css").read_text(encoding="utf-8")
    assert "max-width: min(100%, 96ch);" in v5
    assert "72ch" not in v5
    assert "clamp(16px, 3vw, 40px)" in css
    assert "clamp(64px, 12vw, 180px)" not in css


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
    assert "var(--tap)" in v5
    assert "min-height: var(--tap)" in v5 or "min-height:var(--tap)" in v5.replace(" ", "")


def test_chat_js_untouched_by_overhaul_contract() -> None:
    """G10: this overhaul must not rewrite chat.js (IDs stay the contract)."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "model-selector" in js
    assert "capacity-bar" in js
    assert "data-cap" in js


def test_approval_card_recovered_when_stream_dies() -> None:
    """Incident 2026-08-27 ("no response on schedule/permission tasks"): a
    client refresh/tab switch drops the SSE stream BEFORE the HITL interrupt
    arrives, so `interrupted` stays false and the pending approval is never
    rendered — it then silently hits the 60s auto-deny. Recovery of the missed
    approval card must therefore fire on a truncated stream AND on SSE final
    failure, not only on a clean `interrupted` terminal frame."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "(interrupted || truncated) && !hasInlineApprovalCard()" in js
    assert "setTimeout(recoverMissedApproval, 800)" in js
    assert "setTimeout(recoverMissedApproval, 1200)" in js


def test_voice_tts_latch_survives_reload() -> None:
    """The mid-turn flicker is a page refresh, which reset the in-memory TTS
    latch and re-fired the yellow 'TTS unavailable' toast on every response.
    The latch must persist across reloads via sessionStorage."""
    voice = _CHAT_JS.parent / "voice.js"
    src = voice.read_text(encoding="utf-8")
    assert "sessionStorage.getItem('kazma_tts_unavailable')" in src
    assert "sessionStorage.setItem('kazma_tts_unavailable', '1')" in src
    assert "sessionStorage.removeItem('kazma_tts_unavailable')" in src


def test_streaming_paint_throttled_not_per_token() -> None:
    """The 'double vision' flicker (2026-08-27): onToken replaced the FULL
    accumulated markdown innerHTML on EVERY token event — measured 5,311 DOM
    mutations (2,643 add / 2,624 del on .message-text) for one 150-word
    reply. Live paints must go through the coalescing scheduler and the
    terminal frame must flush the final text."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "_LIVE_RENDER_MIN_MS = 150" in js
    assert "function _scheduleLiveTextPaint(textEl)" in js
    assert js.count("_scheduleLiveTextPaint(textEl);") >= 2  # main + approve-resume
    assert "function _flushLiveTextPaint()" in js
    assert "_flushLiveTextPaint();" in js
