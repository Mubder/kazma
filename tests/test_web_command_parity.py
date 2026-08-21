"""Web command parity guards (command audit 2026-08-19).

The user asked: are the web slash commands REALLY injected/sent, not just
cosmetic? These source-level guards pin the load-bearing wiring so a refactor
cannot silently turn a real command back into a client-side-only mock.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WS = _ROOT / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
_SSE = _ROOT / "kazma-ui" / "kazma_ui" / "sse_chat.py"
_CHAT_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"


def test_reset_is_real_on_both_transports():
    """`/reset` must delete checkpoints server-side on SSE AND WS.

    Before the 2026-08-19 fix, WS had no intercept: the client's local UI
    clear was the only effect and "/reset" rode to the LLM as a prompt —
    a cosmetic-only command on the default (WS) transport.
    """
    sse = _SSE.read_text(encoding="utf-8")
    ws = _WS.read_text(encoding="utf-8")
    assert '"/reset"' in sse and "adelete_thread" in sse
    assert '"/reset"' in ws and "adelete_thread" in ws


def test_compact_is_real_on_both_transports():
    """`/compact` must run the compaction cycle on SSE AND WS (was SSE-only)."""
    sse = _SSE.read_text(encoding="utf-8")
    ws = _WS.read_text(encoding="utf-8")
    assert '"/compact"' in sse and "needs_compaction" in sse
    assert '"/compact"' in ws and "needs_compaction" in ws


def test_abort_is_visible_in_the_transcript():
    """`/abort` must append a user bubble — a toast-only command reads as
    'not really working' (the /about report: toast shown, nothing in chat)."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "appendMessage('user', '/abort')" in js


def test_unknown_slash_gets_a_hint():
    """Unknown commands (e.g. /about) must produce a non-blocking hint
    instead of silently riding to the LLM as a prompt."""
    js = _CHAT_JS.read_text(encoding="utf-8")
    assert "Unknown command" in js


def test_capacity_and_yolo_intercepts_exist_on_both_transports():
    """The genuinely-wired commands stay wired: /yolo + capacity commands
    are intercepted server-side on both SSE and WS."""
    sse = _SSE.read_text(encoding="utf-8")
    ws = _WS.read_text(encoding="utf-8")
    assert '"/yolo"' in sse and "is_capacity_command" in sse
    assert '"/yolo"' in ws and "is_capacity_command" in ws


def test_stale_socket_watchdog_exists():
    """Half-dead WS sockets must self-heal (2026-08-21 YOLO-silent incident):
    the server heartbeats active turns every ~4s; no frame for 30s during an
    active turn forces a reconnect so heartbeats/turn_complete reach the tab
    without a manual refresh."""
    js = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js").read_text(
        encoding="utf-8"
    )
    assert "_stalenessTimer" in js and "_lastFrameAt" in js
    assert "Stale live socket" in js
    # The server side must log heartbeat sends for diagnosability.
    ws = _WS.read_text(encoding="utf-8")
    assert "approve heartbeat n=" in ws
