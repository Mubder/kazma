"""Web command parity guards (command audit 2026-08-19).

The user asked: are the web slash commands REALLY injected/sent, not just
cosmetic? These source-level guards pin the load-bearing wiring so a refactor
cannot silently turn a real command back into a client-side-only mock.
"""

from __future__ import annotations

from tests._module_source import module_source

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WS = _ROOT / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
_SSE = _ROOT / "kazma-ui" / "kazma_ui" / "sse_chat.py"
_CHAT_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"


def _ws_refuses_prompts() -> None:
    """The socket runs no turn since 2026-09-30 (AUD-026): a prompt sent there
    is refused, so a slash command can neither ride to the model nor be a
    cosmetic client-side clear on it. tests/test_ws_chat_is_telemetry_only.py
    drives the refusal; this pins the table it comes from."""
    ws = _WS.read_text(encoding="utf-8")
    assert '"send_prompt": "POST /api/chat/stream"' in ws
    assert "adelete_thread" not in ws and "needs_compaction" not in ws


def test_reset_is_real_where_turns_run():
    """`/reset` must delete checkpoints server-side.

    Before the 2026-08-19 fix, WS had no intercept: the client's local UI
    clear was the only effect and "/reset" rode to the LLM as a prompt —
    a cosmetic-only command on the (then default) WS transport. Turns run
    on SSE alone now, and the socket refuses prompts.
    """
    sse = module_source(_SSE)
    assert '"/reset"' in sse and "adelete_thread" in sse
    _ws_refuses_prompts()


def test_compact_is_real_where_turns_run():
    """`/compact` must run the compaction cycle (it was once SSE-only while
    WS ran turns too; turns are SSE-only now)."""
    sse = module_source(_SSE)
    assert '"/compact"' in sse and "needs_compaction" in sse
    _ws_refuses_prompts()


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


def test_capacity_and_yolo_intercepts_exist_where_turns_run():
    """The genuinely-wired commands stay wired: /yolo + capacity commands
    are intercepted server-side on SSE, the one transport that runs turns."""
    sse = module_source(_SSE)
    assert '"/yolo"' in sse and "is_capacity_command" in sse
    _ws_refuses_prompts()


def test_stale_socket_watchdog_exists():
    """Half-dead WS sockets must self-heal (2026-08-21 YOLO-silent incident):
    during an active turn, prolonged frame silence forces a reconnect so
    heartbeats/turn_complete reach the tab without a manual refresh.

    Turn Delivery V2: detection moved into a WORKER-backed liveness ticker —
    page timers get intensive-throttled in hidden tabs (>5 min => <=1/min),
    exactly when detection is needed most; worker timers are never throttled.
    Reconnect resumes via ?last_seq= journal replay."""
    js = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js").read_text(
        encoding="utf-8"
    )
    assert "_livenessCheck" in js and "_lastFrameAt" in js
    assert "new Worker(" in js
    # The server side journals heartbeats for a long turn, and the broker fans
    # them out to every watching socket (the WS graph client's own heartbeat
    # log left with it, 2026-09-30).
    streaming = (_ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py").read_text(
        encoding="utf-8"
    )
    assert 'emit_j("turn_heartbeat"' in streaming
