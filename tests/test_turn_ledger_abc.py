"""Turn Ledger leftovers A1/A2/B/C — structural fences + mint/persist.

A1: POST /api/approve mints a session when none exists.
A2: Gateway HITL pause persists open narration.
B:  Core ainvoke only in turn.py; persist aliases deleted.
C:  render(doc) projector; applyFinalAssistantText is gone.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kazma_ui import reply_sink
from kazma_ui.session_manager import SessionManager, set_session_manager
from kazma_ui.turn_runtime import ensure_session_for_thread, persist_reply

_ROOT = Path(__file__).resolve().parent.parent
_UI = _ROOT / "kazma-ui" / "kazma_ui"
_GW = _ROOT / "kazma-gateway" / "kazma_gateway"
_CORE = _ROOT / "kazma-core" / "kazma_core"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_STORE_JS = _UI / "static" / "js" / "stores" / "agentStore.js"
_MISC = _UI / "routes_direct" / "misc.py"
_GRAPH = _GW / "agent_handler" / "graph.py"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_approve_path_mints_a_session() -> None:
    src = _src(_MISC)
    assert "will stream but cannot be persisted" not in src
    assert "ensure_session_for_thread" in src
    assert "canonical_web_session" in _src(_UI / "turn_runtime.py")


def test_approve_skip_session_resolution_fails_ci() -> None:
    """Negative: a planted skip of session mint is visible to CI."""
    planted = (
        "_resume_session_id = ''\n"
        "logger.warning('will stream but cannot be persisted')\n"
    )
    assert "ensure_session_for_thread" not in planted
    assert "will stream but cannot be persisted" in planted
    assert "will stream but cannot be persisted" not in _src(_MISC)


def test_ensure_session_mints_unknown_thread() -> None:
    prev = None
    try:
        from kazma_ui.session_manager import get_session_manager

        prev = get_session_manager()
    except Exception:
        prev = None
    sm = SessionManager(db_path=":memory:")
    set_session_manager(sm)
    try:
        sid = ensure_session_for_thread("thread-unknown-a1")
        assert sid
        sess = sm.get_by_thread_id("thread-unknown-a1")
        assert sess is not None
        assert sess.session_id == sid
        assert sess.thread_id == "thread-unknown-a1"
        reply_sink.reset_reply_turns()
        turn = reply_sink.open_reply_turn("thread-unknown-a1")
        assert persist_reply(sid, turn, "the final answer", thread_id="thread-unknown-a1")
        rows = [m for m in sess.messages if m.get("role") == "assistant"]
        assert len(rows) == 1
        assert "final answer" in rows[0]["content"]
    finally:
        set_session_manager(prev)


def test_gateway_hitl_pause_persists_open_narration() -> None:
    src = _src(_GRAPH)
    assert "async def _persist_hitl_pause" in src
    assert "interrupted=True" in src
    assert src.count("await _persist_hitl_pause(") >= 2
    # Must not write platform ids into graph state (AGENTS.md §2).
    helper = src.split("async def _persist_hitl_pause", 1)[1].split("\nasync def ", 1)[0]
    body = helper.split('"""', 2)[-1] if '"""' in helper else helper
    assert "chat_id" not in body
    assert "close_turn" in helper
    assert "_sync_platform_session_to_web" in helper


def test_persist_alias_names_are_gone() -> None:
    ui_src = "\n".join(_src(p) for p in _UI.rglob("*.py") if "__pycache__" not in p.parts)
    assert "_persist_turn_reply" not in ui_src
    assert "_persist_final_assistant_message" not in ui_src


def test_apply_final_assistant_text_is_gone() -> None:
    chat = _src(_CHAT_JS)
    store = _src(_STORE_JS)
    assert "applyFinalAssistantText" not in chat
    assert "applyFinalAssistantText" not in store
    assert "kazma-working" not in chat
    assert "function renderTurn(doc, meta)" in chat
    assert "function applyTurnEvent(ev)" in chat
    assert "hasLiveSSE" not in chat
    assert "hasLiveSSE" not in store


def test_apply_final_fence_fails_on_synthetic_violation() -> None:
    planted = "window.KazmaChat.applyFinalAssistantText(content, model, {});"
    assert "applyFinalAssistantText" in planted
    assert "applyFinalAssistantText" not in _src(_CHAT_JS)


def test_duplicate_resume_attaches_instead_of_dead_ending() -> None:
    src = _src(_UI / "sse_chat" / "_streaming.py")
    assert "attaching to in-flight" in src
    assert "_sse_attach_stream" in src
    assert "Rejecting duplicate resume" not in src


def test_new_prompt_stream_subscribes_at_journal_head() -> None:
    """The journal is per-THREAD and retains the previous turn's frames. A
    new-prompt stream that replays from 0 re-delivers the old reply's token
    frames into the fresh turn — the two replies crossed bubbles in chat
    ("my answer appeared above the previous reply", 2026-09-02). The new
    prompt path must subscribe at the head captured BEFORE the drive task
    starts (its frames carry seq > head and arrive via resume ∪ live queue)."""
    src = _src(_UI / "sse_chat" / "__init__.py")
    assert "_journal_head = get_turn_broker().head_seq(thread_id)" in src
    assert "_sse_attach_stream(thread_id, session_id, _journal_head)" in src
    # Negative: the literal from-0 subscribe on the new-prompt path is the bug.
    assert "_sse_attach_stream(thread_id, session_id, 0)" not in src
    # Ordering: the head capture must precede the drive creation.
    assert src.index("_journal_head = get_turn_broker().head_seq(thread_id)") < src.index(
        "_drive = asyncio.create_task("
    )
    # The broker must expose the head publicly (no private journal reach).
    delivery = _src(_UI / "delivery.py")
    broker = delivery.split("class TurnBroker", 1)[1]
    assert "def head_seq(self, thread_id: str)" in broker


def test_assistant_snapshot_runs_are_coalesced_before_render() -> None:
    """A single turn must not render as a ladder of assistant snapshots.

    Regression shape (2026-09-02): consecutive assistant rows carried
    incremental prefixes ("Schedule", "Schedule the", ...) and rendered as many
    CoT bubbles. Both API payload and chat renderer now coalesce related runs.
    """
    chat = _src(_CHAT_JS)
    assert "function _coalesceAssistantRuns(rows)" in chat
    assert "messages = _coalesceAssistantRuns(messages);" in chat
    # Negative control: plain identical-only collapse misses prefix ladders.
    assert "prevAssistantContent === _cTrim" in chat
    sse = _src(_UI / "sse_chat" / "__init__.py")
    assert "def _coalesce_assistant_runs(" in sse
    assert "payload = _coalesce_assistant_runs(payload)" in sse


def test_error_frame_finishes_the_sse_stream() -> None:
    src = _src(_UI / "static" / "js" / "streaming.js")
    err = src.split("case 'error':", 1)[1].split("case ", 1)[0]
    assert "finishStream" in err
    chat = _src(_CHAT_JS)
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "state: 'error'" in approve
    css = _src(_UI / "static" / "css" / "kazma.css")
    assert ".hitl-approval-card.hitl-error" in css


def test_benign_sse_close_is_not_a_failed_turn() -> None:
    """Firefox/Edge report a normal SSE close as 'network error'.

    That must finish the stream (truncated), not call onError — otherwise
    the HITL card flips to Error while the server is still running and
    refresh shows a different transcript (turn f3525a25e05c, 2026-09-01).
    """
    src = _src(_UI / "static" / "js" / "streaming.js")
    pump = src.split("function pump()", 1)[1].split("function dispatch", 1)[0]
    assert "isBenignStreamClose" in pump
    assert "finishStream(undefined)" in pump
    assert "network error" in src
    chat = _src(_CHAT_JS)
    approve = chat.split("function submitApproval(action, scope)", 1)[1]
    approve = approve.split("var onceBtn", 1)[0]
    assert "sseFn" not in approve
    assert "fetch(approvalUrl" in approve
    assert "res.status === 409" in approve
    assert "text/event-stream" not in approve
    main_on_error = chat.split("buildSseCallbacks", 1)[1].split("onError: function(msg)", 1)[1].split("};", 1)[0]
    assert "_awaitingApproval" in main_on_error
    assert "_turnPainted" in main_on_error
    planted = "callbacks.onError(err.message);\n"  # catch with no benign guard
    assert "isBenignStreamClose" not in planted
    assert "isBenignStreamClose" in pump


def test_composer_clears_before_begin_turn() -> None:
    chat = _src(_CHAT_JS)
    send = chat.split("function sendMessage()", 1)[1].split("\n  function retry(", 1)[0]
    clear_at = send.find("_clearComposer()")
    begin_at = send.find("disableInput()")
    assert clear_at != -1 and begin_at != -1
    assert clear_at < begin_at, "composer must empty before beginTurn can throw"
    assert "e.isComposing" in chat
    assert "keyCode === 229" in chat


def test_collapsed_cot_cannot_eat_the_answer() -> None:
    chat = _src(_CHAT_JS)
    assert "function _rescueTurnDom(el)" in chat
    assert "function _answerFromDoc(TD, doc)" in chat
    stream = _src(_UI / "static" / "js" / "streaming.js")
    show = stream.split("function showTyping(el, text)", 1)[1].split("function hideTyping", 1)[0]
    assert "message-content" in show
    assert "kz-typing-row" in show
    hide = stream.split("function hideTyping(el)", 1)[1].split("function toast", 1)[0]
    assert "display = 'none'" in hide
    assert "message-content" in hide
    assert "message-user" in hide
    css = _src(_UI / "static" / "css" / "kazma.css")
    assert ".message-assistant > .message-content > .message-text" in css
    # Negative: the unscoped rule hid/restyled the You bubble after send.
    unscoped = [ln for ln in css.splitlines() if ln.strip() == ".message-content > .message-text {"]
    assert unscoped == []


def test_user_bubble_survives_cot_rescue() -> None:
    """CoT rescue/paint must never empty the sent You row (2026-09-01)."""
    chat = _src(_CHAT_JS)
    rescue = chat.split("function _rescueTurnDom(el)", 1)[1].split("\n  function ", 1)[0]
    assert "_isUserBubble(el)" in rescue
    assert "owner !== el" in rescue
    paint = chat.split("function _paintHTML(textEl, html)", 1)[1].split("\n  function ", 1)[0]
    assert "closest('.message-user')" in paint
    live = chat.split("function _paintLiveTextNow(textEl, final)", 1)[1].split("\n  function ", 1)[0]
    assert "closest('.message-user')" in live
    begin = chat.split("function beginTurn(opts)", 1)[1].split("\n  function ", 1)[0]
    assert "_docs.live" in begin
    render = chat.split("function renderTurn(doc, meta)", 1)[1].split("\n  function applyTurnEvent", 1)[0]
    assert "_isUserBubble(el)" in render
    # Negative: a rescue that lifts every .message-text under the panel
    # without an owner check is exactly the You-bubble-emptying bug.
    planted = (
        "var trapped = panel.querySelectorAll('.message-text, .hitl-approval-card');\n"
        "content.insertBefore(trapped[i], anchor);\n"
    )
    assert "owner !== el" not in planted
    assert "owner !== el" in rescue


def test_live_hitl_card_does_not_collapse_into_cot() -> None:
    chat = _src(_CHAT_JS)
    assert "holdOpen" in chat
    assert "markApprovalTimedOut" in chat
    assert "approval_timeout" in _src(_STORE_JS)


def test_duplicate_terminal_flush_never_wipes_the_reply() -> None:
    """SSE and WS both deliver a terminal frame; the first done's endTurn
    zeroes tokenAccum, so the SECOND terminal's flush must not paint "" over
    the finished reply (2026-09-02: reply vanished at end of stream until a
    refresh re-painted it)."""
    chat = _src(_CHAT_JS)
    live = chat.split("function _paintLiveTextNow(textEl, final)", 1)[1].split("\n  function ", 1)[0]
    # Guard: an empty accumulator at paint time is always a stale duplicate
    # terminal — never truth to paint over a finished bubble.
    assert "if (!String(tokenAccum || '').trim()) return;" in live
    flush = chat.split("function _flushLiveTextPaint()", 1)[1].split("\n  function ", 1)[0]
    assert "_liveRenderEl = null" in flush, "flush must release its target before painting"
    end = chat.split("function endTurn()", 1)[1].split("\n  function ", 1)[0]
    assert "_liveRenderEl = null" in end, "endTurn must release the live-paint target"
    # Negative control: the unguarded painter shape (flush straight into the
    # retained handle with no empty-accumulator check) is exactly the bug.
    planted = "if (_liveRenderEl) {\n      _paintLiveTextNow(_liveRenderEl, true);"
    assert planted not in flush


def test_terminal_cot_swap_preserves_expansion() -> None:
    """The turn being read stays open: the terminal restore-swap inherits the
    live panel's expansion instead of collapsing it (finalizeProgress never
    collapses; the swap must not smuggle the collapse back in)."""
    chat = _src(_CHAT_JS)
    sync = chat.split("function _syncCotPanel(el, activity, status, meta)", 1)[1].split("\n  function ", 1)[0]
    assert "existingCot && !existingCot.classList.contains('is-collapsed')" in sync
    assert "cot.classList.remove('is-collapsed')" in sync
    # Negative: an unconditional collapsed swap is the reported symptom.
    assert "existingCot.replaceWith(cot);" in sync  # swap still happens…
    restored = chat.split("function _buildRestoredWorkbench(activity)", 1)[1].split("\n  function ", 1)[0]
    assert "is-collapsed" in restored  # …but only history builds start collapsed


def test_cot_steps_do_not_trap_page_scroll() -> None:
    """Wheel over the CoT must chain to the page: overscroll-behavior:contain
    on the (often unscrollable) steps list swallowed every wheel gesture, so
    scrolling with the cursor over the CoT never moved the page (2026-09-02)."""
    css = _src(_UI / "static" / "css" / "kazma.css")
    block = css.split(".agent-progress-steps {", 1)[1].split("}", 1)[0]
    assert "max-height" in block and "overflow-y: auto" in block
    # Declaration-shaped check: the word in a comment must not trip the guard.
    assert "overscroll-behavior:" not in block


def test_historical_render_never_captures_open_turn_pointer() -> None:
    """A render targeting a bubble FOLLOWED by a user message is historical:
    paint it, but never re-pin currentMsgEl. A late hydrate/resync for turn N
    re-pinned its bubble, and turn N+1's first token painted into that old
    bubble and re-stamped its turn id — the two replies crossed bubbles
    ("my new message's answer appeared above the previous reply", 2026-09-02)."""
    chat = _src(_CHAT_JS)
    render = chat.split("function renderTurn(doc, meta)", 1)[1].split("\n  function applyTurnEvent", 1)[0]
    assert "_prevOpenEl" in render
    assert "message-user" in render
    assert "if (!_historical) currentMsgEl = el;" in render
    assert "if (_historical) currentMsgEl = _prevOpenEl;" in render
    # The open turn's wait/paint flags are off-limits to historical renders.
    assert "if (!_historical) _turnPainted = true;" in render
    assert "if (!_historical && (doc.status === 'done'" in render
    # Negative: unconditional adoption at the head is exactly the bug.
    assert "\n    currentMsgEl = el;" not in render


def test_live_cot_goes_through_the_document() -> None:
    chat = _src(_CHAT_JS)
    log = chat.split("function logProgress(step)", 1)[1].split("\n  function ", 1)[0]
    assert "applyTurnEvent" in log
    assert "function _syncCotPanel(el, activity, status, meta)" in chat


def test_messages_get_hydrates_legacy_rows() -> None:
    src = _src(_UI / "sse_chat" / "__init__.py")
    assert "hydrate_message" in src


def test_platform_sync_keeps_parts() -> None:
    from kazma_gateway.agent_handler.graph import _merge_transcript

    existing = [
        {
            "role": "user",
            "content": "check it",
        },
        {
            "role": "assistant",
            "content": "The timeout is 300 seconds.",
            "turn_id": "turn-1",
            "parts": [
                {"type": "reasoning", "text": "live API endpoint"},
                {"type": "text", "text": "The timeout is 300 seconds."},
            ],
        },
    ]
    converted = [
        {"role": "user", "content": "check it"},
        {"role": "assistant", "content": "The timeout is 300 seconds."},
    ]
    out = _merge_transcript(existing, converted)
    asst = [m for m in out if m.get("role") == "assistant"]
    assert len(asst) == 1
    assert asst[0]["turn_id"] == "turn-1"
    assert any(p.get("type") == "reasoning" for p in asst[0]["parts"])


def test_core_and_ui_ainvoke_allowlist() -> None:
    repo = _ROOT

    def calls(tree: ast.AST) -> list[int]:
        hits: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in ("ainvoke", "astream_events"):
                hits.append(int(node.lineno))
        return hits

    offenders: list[str] = []
    allowed = {
        "kazma-ui/kazma_ui/turn_runtime.py",
        "kazma-core/kazma_core/agent/turn.py",
    }
    for base in (_UI, _GW, _CORE):
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or "kazma_core_tests" in path.parts:
                continue
            rel = path.relative_to(repo).as_posix()
            if rel in allowed:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for lineno in calls(tree):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, "ainvoke leaked:\n  " + "\n  ".join(offenders)
