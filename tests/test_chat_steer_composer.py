"""Web composer: /steer queues for edit, then submits the live turn."""

from __future__ import annotations

from tests._module_source import module_source

from pathlib import Path

_CHAT_JS = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "static"
    / "js"
    / "chat.js"
)


def _js() -> str:
    return _CHAT_JS.read_text(encoding="utf-8")


def test_steer_menu_queues_draft_instead_of_autosend() -> None:
    """Catalog lives in chat_slash.js; chat.js queues on data-insert."""
    js = _js()
    slash = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat_slash.js"
    ).read_text(encoding="utf-8")
    assert "insert: '/steer '" in slash
    assert "insert: '/steer! '" in slash
    assert "data-insert=" in js
    assert "Steer queued — add your note, then Enter to apply." in js
    # Must not auto-send the placeholder template anymore.
    assert "{ cmd: '/steer <text>'" not in js
    assert "inputEl.value = btn.getAttribute('data-cmd')" not in js


def test_enter_and_send_submit_steer_during_generation() -> None:
    js = _js()
    assert "isSteerOrAbortCommand(draft)" in js
    # Generating + Enter used to swallow every keystroke. Steer must send.
    assert "_isGenerating && e.key === 'Enter'" in js
    enter = js.split("_isGenerating && e.key === 'Enter'")[1][:900]
    assert "sendMessage();" in enter
    assert "abortThenSend()" in enter
    # Send button: steer/abort draft wins over Stop; a typed follow-up
    # stop-and-sends instead of discarding the draft.
    click = js.split("sendBtn.addEventListener('click'")[1][:800]
    assert "isSteerOrAbortCommand(draft)" in click
    assert "sendMessage()" in click
    assert "abortThenSend()" in click


def test_followup_supersedes_instead_of_wait() -> None:
    """A new message must not be blocked behind Stop / 'still processing'."""
    js = _js()
    assert "function abortThenSend()" in js
    sse = module_source(Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "sse_chat.py")
    assert "Superseding in-flight turn" in sse
    assert "It will appear here shortly" not in sse
    assert "cancel_turn(thread_id)" in sse


def test_abort_generation_retires_live_turn_before_stop_wait() -> None:
    """Mid-turn send must not keep painting the first bubble.

    abortThenSend waits up to 1.5s for POST /stop. If `_sseEpoch` stays
    current and `_liveTurnId` follows the old turn, old tokens write into
    bubble 1 and old `done` without turn_id dumps into the new reply.
    """
    js = _js()
    abort = js.split("function abortGeneration(opts)", 1)[1].split(
        "function abortThenSend", 1
    )[0]
    assert "_sseEpoch++" in abort
    assert "_retireLiveTurn()" in abort
    assert abort.find("_sseEpoch++") < abort.find("fetch('/api/chat/stop'")
    assert "function _retireLiveTurn()" in js
    apply = js.split("function applyTurnEvent(ev)", 1)[1].split(
        "function destroyChatMouth", 1
    )[0]
    assert "_isRetiredTurn" in apply
    assert "_supersededLive" in apply
    assert "src === 'ws'" in apply
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "stores"
        / "agentStore.js"
    ).read_text(encoding="utf-8")
    assert "_isSupersededFrame" in store
    assert "must not resurrect" in store
    assert "paused_for_approval" in store
    apply = js.split("function applyTurnEvent(ev)", 1)[1].split(
        "function destroyChatMouth", 1
    )[0]
    assert "isHitl" in apply
    assert "recoverMissedApproval" in js
    dash = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "hitl_approval.js"
    ).read_text(encoding="utf-8")
    assert "if (card) card.remove();" in dash
    assert "seenTid" in dash


def test_chained_hitl_card_appends_below_previous() -> None:
    """A later interrupt must not paint above the card already approved."""
    js = _js()
    assert "function _placeHitlCard(content, card)" in js
    place = js.split("function _placeHitlCard(content, card)", 1)[1].split(
        "function renderHitlCard", 1
    )[0]
    assert "lastCard" in place
    assert "insertBefore(card, after.nextSibling)" in place
    assert "_hitlHostContent" in place
    # The old first-interrupt slot (right under CoT) is what stacked
    # schedule_task above cancel_scheduled.
    assert "insertBefore(card, progress.nextSibling)" not in js
    rescue = js.split("function _rescueTurnDom(el)", 1)[1].split(
        "function _answerFromDoc", 1
    )[0]
    assert "cursor.nextSibling" in rescue
    assert "hitl-approval-card" in rescue
    assert "function _hitlCardIsTrapped(card)" in js
    has_inline = js.split("function hasInlineApprovalCard()", 1)[1].split(
        "function _hitlInterruptIdOf", 1
    )[0]
    assert "_hitlCardIsTrapped" in has_inline
    pin = js.split("function _assistantBubbleForOpenTurn()", 1)[1].split(
        "function _pinLiveAssistantBubble", 1
    )[0]
    assert "closest('.agent-progress')" in pin


def test_hitl_is_not_epoch_gated_and_paints_from_status_gates() -> None:
    """A superseded SSE stream dropping approval_required left the card
    only on Dashboard. Pending gates from session status must paint too."""
    js = _js()
    attach = js.split("function _defaultAttachCallbacks(epoch)", 1)[1]
    ar = attach.split("onApprovalRequired: function(data)", 1)[1].split(
        "onHitl:", 1
    )[0]
    assert "if (!_mine()) return;" not in ar
    assert "_hitlAlreadyClaimed(data)" in ar
    assert "function _paintLiveGates()" in js
    assert "_paintLiveGates();" in js
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "stores"
        / "agentStore.js"
    ).read_text(encoding="utf-8")
    pause = store.split("_pauseForApproval(approval)", 1)[1][:1200]
    assert "this.pendingApproval = approval;" in pause
    assert "hasInlineApprovalCard()" in pause


def test_place_hitl_card_dom_harness_under_node() -> None:
    """Placement must actually run, not just grep: card after CoT, never in it."""
    import shutil
    import subprocess

    if shutil.which("node") is None:
        import pytest

        pytest.skip("node not available")
    harness = (
        Path(__file__).resolve().parent / "js" / "test_place_hitl_card.js"
    )
    proc = subprocess.run(
        ["node", str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout


def test_steer_post_sends_thread_id_and_does_not_require_local_turn_flag() -> None:
    js = _js()
    assert "thread_id: currentThreadId()" in js
    assert "/api/chat/steer" in js
    # Server is authoritative — do not hard-block on a local _turnActive flag.
    assert "var _turnActive = !!_isGenerating" not in js


def test_auto_steer_requires_live_card_not_fossil_flag() -> None:
    """After restart/abort, `_awaitingApproval` alone must not prefix /steer."""
    js = _js()
    auto = js.split("if (_awaitingApproval && text && text.charAt(0) !== '/')", 1)
    assert len(auto) == 2
    body = auto[1][:1800]
    assert "hasInlineApprovalCard()" in body
    assert "no_active_task" in body
    assert "sending as a new message" in body
    assert "function _releaseHitlComposer" in js
    abort = js.split("appendMessage('user', '/abort')", 1)[1][:500]
    assert "_releaseHitlComposer('abort')" in abort


def test_hydrate_pending_without_gate_does_not_lock_composer() -> None:
    js = _js()
    paint = js.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "function renderTurn(doc, meta)", 1
    )[0]
    assert "renderHitlCard(hitl.payload, { lock: false })" in paint
    assert "_awaitingApproval" not in paint
    assert "function renderHitlCard(data, opts)" in js
    assert "if (lockComposer) pauseForApproval(data);" in js


def test_hitl_card_suppression_is_interrupt_scoped_not_global() -> None:
    """The first live-button card on the page must not eat every later
    gate. 2026-09-02: a stale pending card from an earlier turn made
    ``hasInlineApprovalCard()`` true, so renderHitlCard returned before
    painting for EVERY later interrupt — cards appeared only on the
    Dashboard while the gates silently auto-denied (watchdog)."""
    js = _js()
    rhc = js.split("function renderHitlCard(data, opts)", 1)[1].split(
        "function setCardState(state, label)", 1
    )[0]
    guard = rhc.split("if (hasInlineApprovalCard()) {", 1)[1].split(
        "// Phase 3", 1
    )[0]
    # Same-interrupt cards skip (idempotent WS+SSE delivery) …
    assert "_findHitlCard(iid" in guard
    assert "_hitlCardIsClaimed(liveSameCard)" in guard
    # … a DIFFERENT interrupt's card must fall through and paint.
    assert "return;" in guard


def test_stale_hitl_cards_reconcile_to_registry_when_idle() -> None:
    """Once /status answers authoritatively and the thread is idle, a card
    with live buttons whose interrupt has no pending registry row is a
    fossil: stamp it resolved instead of offering Approve buttons that
    only ever 409 (§30 — the registry owns the decision)."""
    js = _js()
    assert "function _reconcileHitlCardsWithGates()" in js
    reconcile = js.split("function _reconcileHitlCardsWithGates()", 1)[1].split(
        "function recoverMissedApproval", 1
    )[0]
    assert "_serverGatesAuth" in reconcile
    # Never run while a pause may be in flight (row not registered yet).
    assert "_openHitlPart()" in reconcile
    assert "pending" in reconcile
    resync = js.split("function _resyncDelivery(reason)", 1)[1].split(
        "function _releaseHitlComposer", 1
    )[0]
    assert "if (!generating && !liveHitl) _reconcileHitlCardsWithGates();" in resync


def test_reconcile_is_positive_id_only_and_semantic_card_carries_iid() -> None:
    """2026-09-02 audit: the semantic clarify card was built without
    data-interrupt-id, so id-scoped consumers could not identify it and
    _reconcileHitlCardsWithGates could disable a LIVE semantic card in the
    window before the pending gate reached the status snapshot. Two rules:
    every card carries its interrupt id, and the reconcile only stamps
    cards whose id is KNOWN and confirmed absent — never a guess."""
    js = _js()
    rhc = js.split("function renderHitlCard(data, opts)", 1)[1].split(
        "function setCardState(state, label)", 1
    )[0]
    sem = rhc.split("data.kind.indexOf('semantic_') === 0", 1)[1].split(
        "_placeHitlCard(content, _semCard)", 1
    )[0]
    assert "_hitlInterruptIdOf(data)" in sem
    assert "setAttribute('data-interrupt-id'" in sem
    reconcile = js.split("function _reconcileHitlCardsWithGates()", 1)[1].split(
        "function recoverMissedApproval", 1
    )[0]
    assert "if (!cid) return;" in reconcile
    assert "if (pendingIids[cid]) return;" in reconcile


def test_approval_cards_count_down_to_watchdog_deadline() -> None:
    """2026-09-02: unattended cards died silently at 300s. The server stamps
    approval_deadline (SSE payload / status gates / pending items) and the
    card counts down, stamps itself timed-out at zero, and every claim path
    stops the ticker."""
    js = _js()
    assert "function _attachHitlCountdown(card, data)" in js
    assert "function _stopHitlCountdown(card)" in js
    rhc = js.split("function renderHitlCard(data, opts)", 1)[1].split(
        "function setCardState(state, label)", 1
    )[0]
    # Both card branches attach the countdown (semantic + security)…
    assert "_attachHitlCountdown(_semCard, data);" in rhc
    assert "_attachHitlCountdown(card, data);" in rhc
    # …the deadline flows through the gates payload projection…
    payload = js.split("function _payloadFromGate(g)", 1)[1].split(
        "function _paintLiveGates()", 1
    )[0]
    assert "approval_deadline" in payload
    # …zero stamps the card timed-out…
    cd = js.split("function _attachHitlCountdown(card, data)", 1)[1].split(
        "function renderHitlCard(data, opts)", 1
    )[0]
    assert "Approval timed out" in cd
    # …and every claim/timeout/reconcile path stops the ticker.
    assert js.count("_stopHitlCountdown(") >= 4


def test_steer_body_strips_placeholder() -> None:
    js = _js()
    assert "/^<[^>]+>$/.test(rest)" in js
    assert "function steerBody(text)" in js


def test_ws_steer_allows_paused_graph() -> None:
    ws = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "routes"
        / "ws_chat.py"
    ).read_text(encoding="utf-8")
    assert "allow steer (same as HTTP)" in ws
    assert 'getattr(_st_snap0, "next"' in ws


def test_supervisor_steer_tid_falls_back_to_context() -> None:
    gb = (
        Path(__file__).resolve().parent.parent
        / "kazma-core"
        / "kazma_core"
        / "agent"
        / "graph_supervisor.py"
    ).read_text(encoding="utf-8")
    assert "_steer_tid = str(state.get(\"thread_id\") or \"\")" in gb
    assert "get_current_thread_id" in gb
    # Fallback sits in the steer gate, not some unrelated HITL import.
    idx = gb.index("_steer_tid = str(state.get(\"thread_id\") or \"\")")
    assert "get_current_thread_id" in gb[idx : idx + 400]


def test_live_task_card_single_writer_and_liveness() -> None:
    """2026-09-03: the merged Live Task Card is the ONE turn-state surface.
    Single-writer (_taskCardEvent), heartbeat-fed, stalled-honest, and the
    retired strip delegates to it instead of fighting it."""
    js = _js()
    assert "function _taskCardEvent(ev)" in js
    assert "id=\"live-task-card\"" in (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")
    # Legacy strip delegates to the card — one surface, one writer.
    strip = js.split("function _setStatusStrip(msg)", 1)[1].split(
        "function _clearStatusStrip()", 1
    )[0]
    assert "_taskCardEvent({ t: 'text', msg: msg })" in strip
    # Heartbeats feed the card from BOTH SSE callback builders and the
    # WS store; pause shows awaiting + the watchdog countdown.
    assert js.count("t: 'hb'") >= 2
    assert "taskCard: _taskCardEvent" in js
    assert "_taskCardEvent({ t: 'approval', deadline: _hitlDeadlineOf(data) })" in js
    # Stalled honesty: 20s signal gap → amber warning + one resync.
    tc = js.split("function _tcTick()", 1)[1].split("function _tcStepsFromDoc()", 1)[0]
    assert "> 20000" in tc
    assert "_resyncDelivery('heartbeat-gap')" in tc
    # Compact body: doc-fed steps, 50-row cap, 2-line clamp is CSS.
    steps = js.split("function _tcStepsFromDoc()", 1)[1].split(
        "The single writer", 1
    )[0]
    assert "rows.slice(-50)" in steps
    # Live turns no longer build an in-bubble workbench — the terminal
    # branch swaps in the durable summary when the turn ends.
    cot = js.split("function _syncCotPanel(el, activity, status, meta)", 1)[1].split(
        "function _paintHitlFromDoc(el, doc)", 1
    )[0]
    assert "_taskCardEvent({ t: 'doc' })" in cot


def test_ws_store_feeds_task_card() -> None:
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"
    ).read_text(encoding="utf-8")
    assert "_taskCard(ev)" in store
    assert "chat.taskCard(ev)" in store
    for needle in (
        "{ t: 'status', status: 'thinking'",
        "{ t: 'status', status: 'routing_node'",
        "{ t: 'status', status: 'synthesizing'",
        "{ t: 'token' }",
        "{ t: 'tool', name: tName }",
    ):
        assert needle in store, needle
