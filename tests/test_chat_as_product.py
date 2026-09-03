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


def test_approval_card_never_eats_the_chat_pane() -> None:
    """2026-09-03: the approval card filled the whole chat.

    ``.hitl-approval-card { flex: 1 1 100% }`` was written for
    ``.message-content`` — a wrapping flex ROW, where the 100% basis is what
    puts the card on its own line instead of sharing it with the reasoning
    panel. The store-driven fallback card is a direct child of ``.chat-main``,
    a flex COLUMN: there ``flex-grow: 1`` means grow-to-fill, so the card took
    the entire pane and squeezed the transcript to a 24px sliver (measured in
    a real browser).
    """
    css = _V5.parent.joinpath("kazma.css").read_text(encoding="utf-8")
    # Anchor on the rule's own comment: ".hitl-approval-card {" also
    # matches earlier variant blocks.
    base = css.split("NEVER grow by default", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto;" in base, "the card grows by default again"
    assert "flex: 1 1 100%;" not in base
    # ...but inside a bubble it must still claim the whole line.
    line = css.split(".message-content > .hitl-approval-card,", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 100%;" in line
    # ...and the docked fallback strip is capped, so a long argument list
    # scrolls inside the card instead of pushing the transcript out.
    dock = css.split(".chat-main > .hitl-approval-card {", 1)[1].split("}", 1)[0]
    assert "max-height:" in dock
    assert "overflow-y: auto;" in dock


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
    mutations for one 150-word reply. Live paints stay coalesced (one render
    per 150 ms window) and now render FULL markdown (always-formatted
    streaming — safe once the status strip stopped flapping and scrolling
    became pin-to-bottom); plan fences are stripped live; the terminal flush
    renders the final formatted text exactly once."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "_LIVE_RENDER_MIN_MS = 150" in js
    assert "function _scheduleLiveTextPaint(textEl)" in js
    assert "_scheduleLiveTextPaint(textEl)" in js
    assert "function applyTurnEvent(ev)" in js
    assert "transformRenderedForPlan(KS.markdown(liveParts.prose))" in js
    assert "kz-planning" in js  # plan-only hop stays visible, not a blank nbsp
    assert "kazma-cot-restored" in js
    assert "function isPlanOnlyMessage(text)" in js
    assert "isPlanOnlyMessage(content)" in js
    assert "tryIngestPlanFromText(content)" in js
    assert "function _flushLiveTextPaint()" in js
    # The flush releases its target BEFORE painting (duplicate-terminal
    # guard, 2026-09-02) — the old "_paintLiveTextNow(_liveRenderEl, true)"
    # shape let a second transport's done repaint a closed turn.
    assert "_liveRenderEl = null" in js
    assert "_paintLiveTextNow(el, true)" in js


def test_user_bubble_renders_markdown() -> None:
    """Operator decision 2026-08-27: pasted formatted text in the USER bubble
    must render with the same rich pipeline as assistant replies (bold, code,
    tables, identical paragraph spacing). mdRender escapes all HTML
    internally, so pasted markup can never inject."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    body = js.split("function renderUserContentHtml(", 1)[1].split("\n  function ", 1)[0]
    assert "KS.markdown(s)" in body, "user bubbles must use the shared markdown renderer"


def test_scroll_is_pin_to_bottom_not_forced() -> None:
    """scrollToBottom is called from ~20 sites (every token batch). Snapping
    unconditionally while other turn elements change height made the view
    bounce up/down during streaming (measured: 13 direction reversals and 18
    >30px jumps in one 25s stream) — perceived as flicker — and it fought a
    reader who scrolled up. Auto-scroll must respect the pin state; only send
    and session load force the jump."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "function _installScrollPinTracker()" in js
    assert "var _userPinnedToBottom = true;" in js
    assert "if (!_userPinnedToBottom) return; // reader scrolled up — don't fight them" in js
    assert "function scrollToBottomForce()" in js
    assert js.count("scrollToBottomForce();") >= 2  # send + session load


def test_thinking_strip_is_retired_and_inert() -> None:
    """The strip is gone; the Live Task Card is the one turn-state surface.

    The original contract here was "toggle a class, never x-show" — x-show's
    display:none jumped the composer, so is-on animated opacity/height
    instead. The Live Task Card merge retired the strip entirely and left
    #thinking-indicator as an inert hidden node for the typingEl cache, so
    the is-on rule has nothing left to toggle. What still matters is that
    NOTHING re-attaches an Alpine display toggle to that in-flow element.
    """
    html = _CHAT.read_text(encoding="utf-8")
    assert "thinking-indicator" in html
    strip = html[html.index('id="thinking-indicator"') - 200:]
    strip = strip[: strip.index(">", strip.index('id="thinking-indicator"')) + 1]
    assert "hidden" in strip, "the retired strip must stay inert"
    assert "x-show" not in strip
    assert 'x-show="$store.agent && $store.agent.isThinking"' not in html
    # The card that replaced it is docked in the same place.
    assert 'id="live-task-card"' in html
    assert html.index('id="live-task-card"') < html.index('id="thinking-indicator"')


def test_status_strip_never_toggles_per_token() -> None:
    """#thinking-indicator sits IN-FLOW between transcript and composer: every
    hide/show shifts the composer ~33px and makes the streaming text bounce.
    onToken used to clear it on every token batch while heartbeats/plan
    events re-set it — constant flapping, perceived as the flicker. The strip
    must stay steady for the whole stream; only terminal paths may hide it."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    on_token_body = js.split("onToken: function(data) {", 1)[1].split("onToolCall:", 1)[0]
    assert "_clearStatusStrip" not in on_token_body


def test_live_assistant_bubble_is_pinned_not_minted() -> None:
    """CoT ladder (2026-09-02): plan/status hops called createAssistantMessage
    whenever currentMsgEl was null, stacking one bubble per hop.

    Law: the only mint is _assistantBubbleForOpenTurn (reuse last assistant
    after the last user). Progress/HITL/token/error pin that bubble.
    """
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "function _pinLiveAssistantBubble(create)" in js
    assert "function _assistantBubbleForOpenTurn(create)" in js
    assert "if (!currentMsgEl) currentMsgEl = createAssistantMessage()" not in js
    # `create: false` looks only. Since the Live Task Card took the live view
    # out of the bubble, a progress-only frame that minted one left a bare
    # avatar + timestamp + reaction buttons with nothing in it — the empty
    # bubble every turn opened with.
    assert "return mayCreate ? createAssistantMessage() : null;" in js
    # ensureProgressPanel is the live CoT mint site
    body = js.split("function ensureProgressPanel()", 1)[1].split("\n  function ", 1)[0]
    assert "_pinLiveAssistantBubble()" in body
    assert "createAssistantMessage()" not in body
    # Semantic HITL must send interrupt_id (registry claim) and not stick
    # on Resolving… after a 409.
    sem = js.split("Clarification Needed", 1)[1].split("function setCardState", 1)[0]
    assert "interrupt_id: data.interrupt_id" in sem
    assert "res.status === 409" in sem
