"""Web composer: /steer queues for edit, then submits the live turn."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._js_source import js_function_body
from tests._module_source import module_source

_CHAT_JS = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "static"
    / "js"
    / "chat.js"
)


_MODULES = _CHAT_JS.parent / "modules"
_TURN_VIEW_JS = _MODULES / "turn_view.js"
_TURN_DOC_JS = _MODULES / "turn_document.js"


def _js() -> str:
    return _CHAT_JS.read_text(encoding="utf-8")


def _view_js() -> str:
    return _TURN_VIEW_JS.read_text(encoding="utf-8")


def _turndoc_js() -> str:
    return _TURN_DOC_JS.read_text(encoding="utf-8")


#: The DOM movers deleted by the keyed-render cutover. Each one answered a
#: question about the transcript's SHAPE ("is this bubble historical? is that
#: card trapped? where does this card go relative to the text?") because
#: nothing owned the answer. modules/turn_view.js owns it now. They must stay
#: deleted: while a mover exists, something starts calling it again, and the
#: bubble has two writers once more.
_DELETED_DOM_MOVERS = (
    "function _placeHitlCard(",
    "function _parkClaimedHitlCard(",
    "function _rescueTurnDom(",
    "function _hitlCardIsTrapped(",
    "function _hitlHostContent(",
    "function _syncCotPanel(",
    "function _paintHitlFromDoc(",
)


def test_dom_movers_stay_deleted() -> None:
    """The render half of Turn Delivery V2 (KD-4) replaced all of these."""
    js = _js()
    for gone in _DELETED_DOM_MOVERS:
        assert gone not in js, f"{gone} is back — the bubble has two writers again"
    # And the authority that replaced them is actually wired in.
    assert "function _turnView()" in js
    assert "TV.render(el, doc, _turnRenderers, meta)" in js
    assert _TURN_VIEW_JS.is_file()


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
    """A later interrupt must not paint above the card already approved.

    Card order is no longer produced by moving nodes. ``slotPlan`` declares
    it — ``[workbench] [settled gates] [answer] [pending gates]`` — and the
    ordering pass in ``render`` makes the DOM match. The behaviour is
    exercised for real in tests/js/test_turn_view.js ("the settled gate
    stays above the pending one", "gates keep ask order"), which replays
    the frame sequences instead of grepping for a mover.
    """
    view = _view_js()
    plan = js_function_body(view, "function slotPlan(doc, has, TD)")
    # Ask order in, ask order out.
    assert "order.push(key)" in plan
    assert "settled" in plan and "pending" in plan
    # The declared sequence: workbench, settled gates, answer, pending gates.
    tail = plan.split("var plan = [];", 1)[1]
    i_work = tail.index("has.workbench")
    i_settled = tail.index("settled[i]")
    i_text = tail.index("has.text")
    i_pending = tail.index("pending[i]")
    assert i_work < i_settled < i_text < i_pending, (
        "the declared slot order changed — settled decisions must precede "
        "the answer they unblocked, and a live question must follow the "
        "text that provoked it"
    )


def test_a_gate_is_identified_by_its_interrupt_id_everywhere() -> None:
    """The document and the renderer must agree on what "a gate" is.

    Root cause of the whole class. ``_part_key`` returned a bare ``hitl``,
    so a turn could hold only ONE gate: a second approval overwrote the
    first, the transcript kept both cards, and every reconciliation between
    the two was a guess. Identity now comes from one function, used by both
    the document (to dedupe parts) and the view (to key DOM slots).
    """
    turndoc = _turndoc_js()
    assert "if (kind === 'hitl') return 'hitl:' + interruptIdOf(part);" in turndoc
    assert "partKey: partKey," in turndoc, "the view needs the document's identity fn"
    view = _view_js()
    gate_key = js_function_body(view, "function gateSlotKey(part, TD)")
    assert "TD.partKey(part)" in gate_key, (
        "the view is computing its own slot identity — it will drift"
    )
    py = module_source(
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "turn_document.py"
    )
    assert 'return ("hitl", _interrupt_id_of(part))' in py, (
        "the server-side projector still collapses every gate into one slot"
    )


def test_open_turn_pin_skips_bubbles_nested_in_cot() -> None:
    """A .message swallowed by a CoT panel is not the open turn.

    Pinning it put the approval card inside overflow:hidden / a collapsed
    body, so the dashboard listed the gate and chat looked empty
    (2026-09-02). Still relevant: _pinLiveAssistantBubble remains the
    anchor for the progress panel and the card builder.
    """
    js = _js()
    pin = js_function_body(js, "function _assistantBubbleForOpenTurn(create)")
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
    # Slice to the end of the method, not a byte budget — a comment added
    # inside it used to push the assertion out of a fixed 1200-char window.
    pause = store.split("_pauseForApproval(approval)", 1)[1].split(
        "_resetTurnState()", 1
    )[0]
    assert "this.pendingApproval = approval;" in pause
    assert "hasInlineApprovalCard()" in pause


def test_chat_client_boots_under_node() -> None:
    """The modules must RUN together, not just parse.

    `node --check` proves syntax. It does not catch a refactor that renames
    a function and misses one call site, or drops a helper something still
    references: the file parses, then throws ReferenceError in the browser
    on load, and the chat page is blank while every suite here stays green —
    because the rest of these tests assert on source TEXT.
    """
    import shutil
    import subprocess

    if shutil.which("node") is None:
        import pytest

        pytest.skip("node not available")
    harness = Path(__file__).resolve().parent / "js" / "test_boot.js"
    proc = subprocess.run(
        ["node", str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout


def test_turn_view_dom_harness_under_node() -> None:
    """Rendering must actually run, not just grep.

    Replaces ``test_place_hitl_card.js``: ``_placeHitlCard`` no longer
    exists. Card ordering is not a function that moves nodes any more — it
    is the slot order ``modules/turn_view.js`` declares, and the harness
    below replays real frame sequences (including the 2026-09-19 sequential
    approve) and asserts the resulting DOM.
    """
    import shutil
    import subprocess

    if shutil.which("node") is None:
        import pytest

        pytest.skip("node not available")
    harness = (
        Path(__file__).resolve().parent / "js" / "test_turn_view.js"
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
    """After restart/abort, `_awaitingApproval` alone must not prefix /steer.

    The corroborating check reads the DOCUMENT now (hasLiveGate): a fossil
    card with live-looking buttons is not a reason to rewrite the operator's
    next prompt as a steer of a turn that already ended.
    """
    js = _js()
    auto = js.split("if (_awaitingApproval && text && text.charAt(0) !== '/')", 1)
    assert len(auto) == 2
    body = auto[1][:1800]
    assert "hasLiveGate()" in body
    assert "no_active_task" in body
    assert "sending as a new message" in body
    assert "function _releaseHitlComposer" in js
    abort = js.split("appendMessage('user', '/abort')", 1)[1][:500]
    assert "_releaseHitlComposer('abort')" in abort


def test_hydrate_pending_without_gate_does_not_lock_composer() -> None:
    """Only a registry-confirmed live gate may lock the composer.

    A historical part carries ``pending`` long after its gate settled, so
    hydrating a session must not produce live Approve buttons and must not
    steal the next prompt as a /steer.
    """
    js = _js()
    state = js_function_body(js, "function _hitlDisplayState(part)")
    assert "_hydratingSession" in state and "'awaiting'" in state, (
        "hydration must show the gate without claiming it is live"
    )
    # Never invent a claim from leftover status.
    assert "_hitlAlreadyClaimed(part)" in state
    lock = js_function_body(js, "function _hitlShouldLock(part)")
    assert "_hitlGateRow" in lock and "'pending'" in lock
    build = js_function_body(js, "function _buildHitlSlotCard(part, ctx)")
    assert "_hitlShouldLock(part)" in build, (
        "the builder locks the composer without consulting the registry"
    )
    assert "if (lockComposer) pauseForApproval(data);" in js


def test_hitl_display_state_never_invents_approved() -> None:
    """§30: a card may never claim "Approved" without evidence.

    A stale click is recoverable — the server re-verifies and answers "no
    longer pending". A fabricated Approved stamp is the incident.
    """
    js = _js()
    state = js_function_body(js, "function _hitlDisplayState(part)")
    # Registry truth wins in BOTH directions: a pending row re-opens a card
    # a stale part called settled, and a claimed row settles a stale pending.
    assert "if (gateRow && String(gateRow.state || '') === 'pending') return 'pending';" in state
    assert "'claimed'" in state and "'resuming'" in state and "'inflight'" in state


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
    # The suppression guard is GONE, not narrowed. It was a heuristic answer
    # to "does a card for this gate already exist?", asked of the transcript.
    # TurnView calls renderHitlCard only when the gate's slot is empty, and
    # the slot key IS the interrupt id — so one gate cannot suppress another
    # and the same gate cannot be painted twice.
    for banned in (
        "_findHitlCard(iid",
        "_hitlCardIsClaimed(liveSameCard)",
        "if (_hitlAlreadyClaimed(data)) return;",
        "old.remove()",          # it also deleted other gates' cards on the way in
    ):
        assert banned not in rhc, (
            f"{banned} is back in the card builder — it will eat the next "
            "gate's card again (2026-09-02)"
        )
    # The only thing a live-card DOM scan may still decide here is whether
    # the Alpine store fallback is needed. It must never cause a RETURN —
    # that is the shape that made one gate's card suppress the next one's.
    assert "hasInlineApprovalCard()) return;" not in rhc
    assert "hasLiveGate()) return;" not in rhc
    store_block = rhc.split("if (hasInlineApprovalCard()) {", 1)[1][:300]
    assert "_clearStoreApproval();" in store_block
    assert "return;" not in store_block.split("}", 1)[0]
    assert "return card;" in js_function_body(js, "function renderHitlCard(data, opts)"), (
        "the builder must hand its node back so TurnView can place it"
    )


def test_a_silent_turn_reports_itself() -> None:
    """The detector this bug class never had.

    Every incident was found by the OPERATOR: the server had the answer, the
    bubble showed a placeholder, and nothing in the client knew the
    difference. TurnView re-derives what should be on screen after every
    pass and compares it to what is, so a regression surfaces as a
    diagnostic plus one authoritative resync rather than as a person waiting
    at a blank bubble.
    """
    view = _view_js()
    verify = js_function_body(
        view, "function verify(el, content, doc, slots, report, ctx)"
    )
    # The three ways a turn can be silent.
    assert "'text-missing'" in verify   # the answer has no host at all
    assert "'text-blank'" in verify     # the host is there and empty
    assert "'gate-missing:'" in verify  # a gate that could have shown, did not
    assert "onInvariant(" in verify
    # A reporter must never break the render it is reporting on.
    assert "catch (e) { /* a reporter must never break a render */ }" in verify

    js = _js()
    handler = js_function_body(js, "function _onRenderInvariant(info)")
    assert "console.error(" in handler
    assert "diag('render-invariant'" in handler
    assert "_resyncDelivery('invariant-' + info.code)" in handler
    # Report always, recover sparingly: hydration paints the whole
    # transcript at once and ends with its own resync, and a systematically
    # broken render must not become a fetch storm.
    assert "if (_hydratingSession) return;" in handler
    assert "if (_invariantSeen[key]) return;" in handler
    assert "_invariantResyncs >= _INVARIANT_RESYNC_MAX" in handler
    reset = js_function_body(js, "function _resetSessionTurnState()")
    assert "_invariantSeen = {};" in reset
    assert "_invariantResyncs = 0;" in reset


def test_recovery_paths_never_disarm_on_a_dom_scan() -> None:
    """A recovery path that can decline is not a recovery path.

    ``hasInlineApprovalCard()`` scanned the transcript for an enabled
    button, so it answered TRUE for a fossil card whose gate had already
    settled — and eight recovery paths early-returned on it. That is how
    the only unconditional route back to server truth switched itself off
    exactly when a turn had gone quiet, and why "approved twice then
    silence" survived every individual fix (2026-09-19).
    """
    js = _js()
    resync = js_function_body(js, "function _resyncDelivery(reason)")
    assert "hasInlineApprovalCard()" not in resync, (
        "the authoritative resync can decline again"
    )
    # The document-derived predicate is what logic may consult.
    live = js_function_body(js, "function hasLiveGate()")
    assert "hitlPartsOf" in live and "_hitlDisplayState" in live
    # The DOM predicate survives for exactly one job: deciding whether the
    # Alpine store fallback is needed.
    dom = js_function_body(js, "function hasInlineApprovalCard()")
    assert "querySelectorAll('.hitl-approval-card')" in dom


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
    """The merged Live Task Card is the ONE turn-state surface.

    Single-writer (_taskCardEvent), heartbeat-fed, stalled-honest, and the
    retired strip delegates to it instead of fighting it. These are
    STRUCTURAL assertions - that the wiring exists. What the card actually
    DOES on each event sequence is tested for real in
    tests/js/test_live_task_card.js (driven below).
    """
    js = _js()
    assert "function _taskCardEvent(ev)" in js
    assert 'id="live-task-card"' in (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")
    # Legacy strip delegates to the card - one surface, one writer.
    strip = js.split("function _setStatusStrip(msg)", 1)[1].split(
        "function _clearStatusStrip()", 1
    )[0]
    assert "_taskCardEvent({ t: 'text', msg: msg })" in strip
    # Heartbeats feed the card from BOTH SSE callback builders and the
    # WS store; pause shows awaiting + the watchdog countdown.
    assert js.count("t: 'hb'") >= 2
    assert "taskCard: _taskCardEvent" in js
    assert "_taskCardEvent({ t: 'approval', deadline: _hitlDeadlineOf(data) })" in js
    # Stalled honesty: a signal gap warns, then resyncs with BACKOFF. The
    # first version fired one resync and latched, so a dead stream sat amber
    # forever with nothing else attempted and no way to say so.
    tc = js.split("function _tcTick()", 1)[1].split("function _tcIsTerminal()", 1)[0]
    assert "_TC_STALL_MS" in tc
    assert "_resyncDelivery('heartbeat-gap')" in tc
    assert "_TC_STALL_RETRY_MS" in tc
    assert "_TC_STALL_MAX_TRIES" in tc
    assert "_tc.dead = true" in tc
    # Compact body: doc-fed steps, capped, tail-pinned, 2-line clamp is CSS.
    steps = js.split("function _tcStepsFromDoc()", 1)[1].split(
        "The single writer", 1
    )[0]
    assert "rows.slice(-_TC_STEP_CAP)" in steps
    assert "_TC_STEP_CAP = 50" in js
    assert "if (html === _tc.stepsHtml) return;" in steps, (
        "identical markup re-assigned - tears the subtree down and throws "
        "away the reader's scroll position"
    )
    assert "el.scrollTop = el.scrollHeight;" in steps
    # Live turns no longer build an in-bubble workbench: the bubble's
    # workbench is ONE slot, painted from the document's activity rows, and
    # it feeds the card rather than competing with it.
    cot = js_function_body(js, "function _paintWorkbenchSlot(panel, doc)")
    assert "_taskCardEvent({ t: 'doc' })" in cot
    assert "if (list._kzCotHTML === html) return;" in cot, (
        "identical markup re-assigned — tears the subtree down every frame"
    )


def test_live_task_card_behaviors_under_node() -> None:
    """Drive the real state machine on a fake clock; see the JS file.

    Substring assertions pass happily while the branch they name leaks a
    hide timer - which is exactly how "approve -> the card vanished and no
    response" shipped. These are the tests with teeth.
    """
    script = Path(__file__).resolve().parent / "js" / "test_live_task_card.js"
    assert script.is_file()
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    proc = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_live_task_card_never_hides_a_live_turn() -> None:
    """Structural guard for the vanishing card.

    Every liveness event - approval and resuming included - must go through
    _tcWake, which cancels a hide armed by the previous terminal frame.
    'approval' and 'resuming' used to skip it: a done frame less than 1.6s
    earlier blanked the card mid-approve, and a resume never restarted the
    tick timer (frozen elapsed, dead stall detection).
    """
    js = _js()
    wake = js.split("function _tcWake(now)", 1)[1].split(
        "/** Phase changes restart", 1
    )[0]
    assert "clearTimeout(_tc.doneTimer)" in wake
    assert "_tc.visible = true" in wake
    assert "setInterval(_tcTick, 1000)" in wake
    dispatch = js.split("function _taskCardEvent(ev)", 1)[1].split(
        "function _tcWake(now)", 1
    )[0]
    # The liveness arm: every event on it wakes the card. Read the `else if`
    # condition itself, not the `begin` branch that precedes it.
    live = dispatch.split("} else if (", 1)[1].split(") {", 1)[0]
    for ev in ("'token'", "'tool'", "'tool_end'", "'status'", "'hb'",
               "'approval'", "'resuming'"):
        assert ev in live, ev + " does not restore the card through _tcWake"
    assert "_tcWake(now);" in dispatch.split("} else if (", 1)[1]


def test_approval_freeze_is_scoped_to_the_card_decided() -> None:
    """Two concurrent approval cards is a supported state.

    The document holds one part per gate, so the renderer stacks a second
    card after the first — but _freezeHitlButtons used to disable every card
    in the transcript. Approving the first killed the second's buttons;
    nothing re-enables them (_reconcileHitlCardsWithGates only ever
    disables), and with no enabled button left the "is anything waiting?"
    check went false — so onDone took the endTurn branch while the graph was
    still parked on the untouched interrupt. Card gone, no reply
    (2026-09-03).
    """
    js = _js()
    assert "function _freezeHitlButtons(scope)" in js
    assert "_freezeHitlButtons(card);" in js
    assert "_freezeHitlButtons();" not in js, (
        "unscoped freeze reintroduced - it kills a sibling gate's buttons"
    )
    # Deciding gate A does not mean the turn stopped waiting on gate B.
    submit = js.split("function submitApproval(action, scope)", 1)[1]
    assert "_awaitingApproval = hasLiveGate();" in submit
    assert "_liveHitlDeadline()" in submit
    # The deadline has to be readable off the node for that to work.
    assert "card.setAttribute('data-approval-deadline'" in js


def test_store_approval_fallback_never_outlives_the_inline_card() -> None:
    """The ghost approval card on every hard refresh.

    The Alpine store keeps ``pendingApproval`` as a fallback for "the inline
    card never rendered", and cleared it only while
    ``hasInlineApprovalCard()`` was true — i.e. only while some card still
    had ENABLED buttons. On a hard refresh of a finished session the inline
    card paints and is immediately stamped "No longer pending" by the gate
    reconcile, so that check goes false and the fallback strip stays on
    screen: a dead card offering four live buttons for a gate the server had
    already settled (server said ``gates: [], gates_authoritative: true``).

    Two independent guards, because the strip lives outside ``messagesEl``
    and carries no interrupt id, so the reconcile sweep cannot reach it.
    """
    js = _js()
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"
    ).read_text(encoding="utf-8")

    # 1. A card that EXISTS is proof the paint landed — live buttons are not
    #    the test.
    assert "function hitlCardExistsFor(data)" in js
    assert "hitlCardExistsFor: hitlCardExistsFor," in js
    pause = store.split("_pauseForApproval(approval) {", 1)[1].split(
        "_resetTurnState()", 1
    )[0]
    assert "chat.hitlCardExistsFor(approval)" in pause
    assert "if (landed) this.pendingApproval = null;" in pause

    # 2. An authoritative gate list with nothing pending retires the strip.
    rec = js.split("function _reconcileHitlCardsWithGates()", 1)[1].split(
        "/** Server-truth recovery", 1
    )[0]
    assert "anyPending" in rec
    assert "_clearStoreApproval()" in rec


def test_session_change_unmounts_the_task_card_without_a_done_flash() -> None:
    """A brand-new empty session flashed a "Done" card for 1-2 seconds.

    ``newSession`` calls ``forceEndTurn``, whose terminal frame leaves the
    card on screen for its 1.6s retire animation. But a session change is
    the ABSENCE of a turn, not the end of one — there is nothing to
    animate away. ``_resetSessionTurnState`` now unmounts it outright.
    """
    js = _js()
    reset = js.split("function _resetSessionTurnState()", 1)[1].split(
        "/** Progress-idle failsafe", 1
    )[0]
    assert "_taskCardEvent({ t: 'reset' })" in reset
    # The reset branch must cancel BOTH timers, or a leftover one reveals or
    # re-hides the card after the session has already changed.
    branch = js.split("if (ev.t === 'reset') {", 1)[1].split("if (ev.t === 'begin')", 1)[0]
    assert "clearTimeout(_tc.doneTimer)" in branch
    assert "clearInterval(_tc.tickTimer)" in branch
    assert "_tc.visible = false;" in branch
    assert "_tc.el.hidden = true;" in branch
    assert "_tc.stepsEl.innerHTML = '';" in branch


def test_an_empty_read_never_wipes_the_steps_you_are_reading() -> None:
    """Expanding the CoT and letting the turn finish emptied it.

    ``_liveTurnId`` is retired and ``_docs`` is dropped around the end of a
    turn, so ``_tcStepsFromDoc`` reads back nothing — and it treated that as
    "this turn has no steps" and blanked the body under the reader.
    Clearing belongs to the events that KNOW a turn started or a session
    changed; both do it explicitly.
    """
    js = _js()
    steps = js.split("function _tcStepsFromDoc()", 1)[1].split(
        "The single writer", 1
    )[0]
    assert "if (!rows.length) return;" in steps, (
        "an empty read blanks the body again"
    )
    for owner in ("if (ev.t === 'reset') {", "if (ev.t === 'begin') {"):
        branch = js.split(owner, 1)[1][:2000]
        assert "_tc.stepsEl.innerHTML = '';" in branch, owner


def test_live_placeholder_is_never_a_bubble_identity() -> None:
    """The reply that appeared above the user's message AND below it.

    ``applyTurnEvent`` falls back to the turn id ``'live'`` for every frame
    the server has not stamped yet — which is most of them at the start of a
    turn. ``renderTurn`` both LOOKED UP and STAMPED bubbles by that id, so a
    bubble left carrying ``data-turn-id="live"`` became a permanent magnet:
    the next turn's untagged frames painted into that old bubble (above the
    new user row), and when a frame finally arrived carrying the real id the
    stale bubble was "historical" — a user row now follows it — so a second
    bubble was minted at the end. Same reply, twice, in the wrong order
    (observed live 2026-09-03: a bubble with data-turn-id="live" and one with
    the real id, both 331 chars).

    The open turn is anchored by ``currentMsgEl``; the placeholder must never
    reach the DOM.
    """
    js = _js()
    # The bubble for a turn is found in a REGISTRY, not by querySelector on
    # the turn id — so 'live' is a map key that gets renamed on promotion,
    # and can never be a selector that matches a leftover bubble.
    resolve = js_function_body(js, "function _bubbleForTurn(turnId, paintable)")
    assert "TV.elFor(id)" in resolve
    assert "TV.promote('live', id)" in resolve
    assert "querySelector" not in resolve, (
        "the paint target is being looked up in the DOM again"
    )
    render = js_function_body(js, "function renderTurn(doc, meta)")
    assert 'data-turn-id="' not in render, (
        "renderTurn is matching bubbles by attribute again — the magnet is back"
    )
    # Promotion must not clobber a different bubble already holding the id.
    promote = js_function_body(_view_js(), "function promote(fromId, toId)")
    assert "delete byTurn[from]" in promote
    assert "if (byTurn[to] && byTurn[to] !== el) return byTurn[to];" in promote
    # A turn that never got a real id can still leave one behind (older
    # builds, restored transcripts). beginTurn releases it via
    # _resetTurnState. Assert the invariant where it lives AND that the call
    # chain still reaches it: pinning it inline in beginTurn failed the build
    # over a refactor while the behaviour was intact (2026-09-12).
    reset = js_function_body(js, "function _resetTurnState()")
    assert '.message-assistant[data-turn-id="live"]' in reset
    assert "removeAttribute('data-turn-id')" in reset
    begin = js_function_body(js, "function beginTurn(opts)")
    assert "_resetTurnState()" in begin, (
        "beginTurn no longer reaches the stale-live-bubble release"
    )


def test_progress_only_frames_never_mint_an_empty_bubble() -> None:
    """The empty bubble that opened every turn.

    beginTurn seeds a "Thinking..." progress row; renderTurn used to answer
    it with createAssistantMessage(). Since the Live Task Card took the live
    view OUT of the bubble, that left a bare avatar + timestamp + reaction
    buttons with nothing inside until the first token.
    """
    js = _js()
    assert "function _assistantBubbleForOpenTurn(create)" in js
    assert "var mayCreate = create !== false;" in js
    assert "return mayCreate ? createAssistantMessage() : null;" in js
    assert "function _docHasBubbleContent(doc)" in js
    render = js_function_body(js, "function renderTurn(doc, meta)")
    assert "_docHasBubbleContent(doc)" in render
    assert "_bubbleForTurn(turnId, paintable)" in render
    assert "createAssistantMessage()" not in render, (
        "an unconditional mint is back in the paint path"
    )
    # The mint lives behind the paintable flag, in the resolver.
    resolve = js_function_body(js, "function _bubbleForTurn(turnId, paintable)")
    assert "if (!paintable) return null;" in resolve
    assert "el = createAssistantMessage();" in resolve
    # A finished turn still earns its bubble: the durable one-line workbench
    # summary and the approval card both need a host.
    host = js.split("function _docHasBubbleContent(doc)", 1)[1].split(
        "function _answerFromDoc", 1
    )[0]
    assert "'done'" in host and "'paused'" in host and "'hitl'" in host
    # A tool step has nothing to put in the bubble either.
    assert "_pinLiveAssistantBubble(false);" in js
    # ...and the "no response" diagnosis must not be gated on the bubble
    # being ABSENT, which any mint suppressed.
    done = js.split("onDone: function(data) {", 1)[1]
    assert "!tokenAccum && !interrupted && !_awaitingApproval && !_turnPainted" in done


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


def test_setplan_and_memory_explain_never_create_panels() -> None:
    """2026-09-03 live bug: setPlan/applyMemoryExplain called
    ensureProgressPanel() directly — on hydration a plan-only historical
    message minted a phantom in-bubble 'Working…' workbench over finished
    history. Both are attach-only now; plan progress rides the card."""
    js = _js()
    plan = js.split("function setPlan(items)", 1)[1].split(
        "function markPlanProgress(toolName)", 1
    )[0]
    assert "ensureProgressPanel()" not in plan
    assert "_taskCardEvent({" in plan
    mem = js.split("function applyMemoryExplain(data)", 1)[1].split(
        "\n  function ", 1
    )[0]
    assert "ensureProgressPanel()" not in mem
    # Card renders plan progress in the header meta.
    tc_render = js.split("function _tcRender()", 1)[1].split(
        "function _tcTick()", 1
    )[0]
    assert "_tc.planTotal" in tc_render


def test_claimed_card_parks_above_reply_and_collapses() -> None:
    """2026-09-03: with the Live Task Card there is no live in-bubble CoT
    panel, so a pending card docked at the BOTTOM of the bubble — and the
    post-approval reply painted ABOVE it ("response on top of the card").
    On claim the card must park between the CoT block and .message-text
    and collapse to the CoT-style one-line bar (click to re-expand)."""
    js = _js()
    assert "function _collapseClaimedHitlCard(card)" in js
    # Parking is no longer a node move: a settled gate sorts above the
    # answer because slotPlan says so (see
    # test_chained_hitl_card_appends_below_previous and the DOM harness).
    # What each claim path must still do is tell the DOCUMENT, so the model
    # and the screen agree without waiting for a server frame — and collapse
    # the card to its one-line bar.
    assert "function _noteGateDecided(data, state)" in js
    decide = js_function_body(js, "function _noteGateDecided(data, state)")
    assert "applyTurnEvent({" in decide and "type: 'hitl'" in decide
    # All four claim paths: security approve/deny, semantic option,
    # watchdog timeout, registry reconcile.
    set_state = js.split("function setCardState(state, label)", 1)[1].split(
        "function appendAssistantText", 1
    )[0]
    assert "_noteGateDecided(data," in set_state
    assert "_collapseClaimedHitlCard(card);" in set_state
    sem = js.split("_semCard.querySelectorAll('.hitl-sem-opt')", 1)[1][:900]
    assert "_noteGateDecided(data," in sem
    timeout = js_function_body(js, "function markApprovalTimedOut(msg)")
    assert "_noteGateDecided(" in timeout
    assert "_collapseClaimedHitlCard(card);" in timeout
    # Collapsed bar shows the decision chip in the header (actions hidden).
    collapse = js.split("function _collapseClaimedHitlCard(card)", 1)[1].split(
        "\n  function renderHitlCard", 1
    )[0]
    assert "hitl-collapse-chip" in collapse
    css = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    assert ".hitl-approval-card.hitl-collapsed .hitl-approval-body" in css


def test_claim_frame_does_not_unglue_the_collapsed_bar() -> None:
    """2026-09-03 live bug: the click path parked+collapsed the card, then
    the journal's claim frame arrived and _paintHitlFromDoc reassigned
    card.className wholesale — stripping hitl-collapsed so the card sprang
    back open mid-reply with a stranded decision chip in the header. Every
    claim site must re-assert park+collapse, and the chip/chevron must
    never render outside the collapsed bar."""
    js = _js()
    paint = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx)")
    assert paint.count("_collapseClaimedHitlCard(card);") >= 2, (
        "both claimed branches of the slot painter must re-assert the collapse"
    )
    # Better than re-asserting: the wholesale className assignment now
    # PRESERVES hitl-collapsed instead of stripping it and putting it back.
    assert paint.count("hitl-collapsed") >= 2
    assert "wasCollapsed ? ' hitl-collapsed' : ''" in paint
    # And the painter is idempotent, so a repeated claim frame is a no-op
    # rather than a re-stamp that flickers the bar.
    assert "data-hitl-shown" in paint
    assert "if (already === show) return;" in paint
    release = js_function_body(js, "function _releaseHitlComposer(reason)")
    assert "_collapseClaimedHitlCard(card);" in release
    css = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    assert ".hitl-approval-card:not(.hitl-collapsed) .hitl-collapse-chip" in css


def test_card_label_hysteresis_no_cot_autoexpand_card_reveal() -> None:
    """2026-09-03 trio: (1) the task-card header must not strobe on fast
    tool→think→tool flips; (2) NOTHING may auto-expand a CoT panel — the
    user's chevron click is the only opener (an auto-expanded panel pushed
    approval cards below the fold); (3) a PENDING approval card bounces the
    chat so it is visible; claimed cards never bounce."""
    js = _js()
    # (1) hysteresis — one accepted label+icon snapshot, escalations cut
    # through, reset on begin.
    render = js.split("function _tcRender()", 1)[1].split("\n  function ", 1)[0]
    assert "_TC_LABEL_MIN_MS" in render
    assert js.count("_TC_LABEL_MIN_MS") >= 2  # constant + use
    assert "_tc.labelShownAt = 0;" in js.split("case 'begin'", 1)[0] + js.split("if (ev.t === 'begin')", 1)[1][:800]
    # (2) auto-expand removed. There is no placement sweep left to do class
    # surgery on panels, and the renderer must not touch a workbench's
    # expansion state either — locked behaviourally in
    # tests/js/test_turn_view.js ("a collapsed workbench stays collapsed").
    assert js.count("classList.remove('is-collapsed', 'is-done')") == 0
    view = _view_js()
    assert "is-collapsed" not in view, (
        "the renderer is doing expansion surgery — the chevron is the only opener"
    )
    assert js.count("_revealHitlCard(") >= 3  # helper + both branches
    # (3) reveal bounces only live cards.
    reveal = js_function_body(js, "function _revealHitlCard(card)")
    assert "scrollIntoView" in reveal
    assert "document.hidden" in reveal
    assert "disabled" in reveal


def test_cot_stays_blue_and_collapsed_and_hydration_never_bounces() -> None:
    """2026-09-03 final polish round: (a) the CoT panel keeps its blue
    accent identity when done — the gray is-done override is gone (element
    colors inside keep their meaning); (b) the terminal frame must not
    touch expansion (the last auto-expand — finalizeProgress un-collapsed
    the panel in the gray style at turn end, then the next turn collapsed
    it back); (c) entering an old session with a stale pending-looking
    approval card must never scroll-bounce the reader to it."""
    js = _js()
    # (b) finalizeProgress no longer touches is-collapsed.
    fin = js.split("function finalizeProgress(ok)", 1)[1].split(
        "\n  function ", 1
    )[0]
    assert "is-collapsed" not in fin
    # (c) hydration guard: flag declared, set around the load paint, and
    # honored by the reveal.
    assert "var _hydratingSession = false;" in js
    assert "_hydratingSession = true;" in js
    reveal = js.split("function _revealHitlCard(card)", 1)[1].split(
        "\n  function renderHitlCard", 1
    )[0]
    assert "_hydratingSession" in reveal
    # (a) the gray overrides are gone from BOTH stylesheets.
    css = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    v5 = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.v5.css"
    ).read_text(encoding="utf-8")
    assert ".agent-progress.is-done {\n  opacity: 1;\n}" in css
    assert "is-done.is-collapsed {\n  background:" not in css
    assert "opacity: 0.92" not in v5


def test_replayed_frames_never_paint_pending_approval() -> None:
    """2026-09-03 ghost-card flash: refreshing any session that once had
    an approval painted a live card for <1s before reconciliation cleared
    it. The journal re-delivers the settled approval's retained frames on
    every attach — both transports now stamp ``replay: true`` and every
    pending-paint site refuses replayed frames; the gate registry stays
    the only authority for live questions (§30)."""
    js = _js()
    assert js.count("data && data.replay) return;") >= 3
    # The second ghost source: hydration itself painted stale pending
    # parts (frame guards alone weren't enough — the persisted part still
    # says 'pending'). A hydrated gate now resolves to 'awaiting': the card
    # is SHOWN, with its buttons disabled, so the transcript is honest and
    # the slot is present — the registry resync is still the only thing
    # that can make it clickable.
    #
    # Note the change of shape. The old rule was "paint NOTHING while
    # hydrating", which is exactly the move that leaves a gate with no node
    # on screen; the render invariant would now report that as a missing
    # slot. Showing it disabled says the same thing without lying and
    # without going silent.
    state = js_function_body(js, "function _hitlDisplayState(part)")
    assert "if (_hydratingSession) return 'awaiting';" in state
    painter = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx)")
    assert "if (show === 'awaiting')" in painter
    assert "b.disabled = true" in painter
    # Round 3: the FLASH itself was the Alpine fallback being armed by a
    # CLAIMED historical card (renderHitlCard lit pendingApproval whenever
    # the painted card had no enabled buttons). Only a live card arms the
    # fallback — the builder passes store:false for everything else.
    build = js_function_body(js, "function _buildHitlSlotCard(part, ctx)")
    assert "store: show === 'pending'," in build
    rhc = js_function_body(js, "function renderHitlCard(data, opts)")
    assert "opts && opts.store === false" in rhc
    hitl_guards = js.count("st === 'pending' && data && data.replay) return;")
    assert hitl_guards == 2  # attach + send onHitl handlers
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"
    ).read_text(encoding="utf-8")
    assert "frame.replay || data.replay" in store
    # Server: both replay loops stamp provenance.
    ws = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
    ).read_text(encoding="utf-8")
    assert 'data["replay"] = True' in ws
    sse = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")
    assert 'data["replay"] = True' in sse


def test_ops_alert_channel_routing_and_adapter_page() -> None:
    """2026-09-03: operators choose WHERE ops alerts go
    (notifications.ops.channels) and manage every adapter setting —
    including the swarm output channels — on the connectors settings tab
    (moved out of the Swarm page). 2026-09-04: lifecycle start/stop uses
    the same filter — the checkboxes must not be ops-only."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "observability" / "ops_alerts.py"
    ).read_text(encoding="utf-8")
    assert "def _ops_channels()" in src
    assert "notifications.ops.channels" in src
    # Channel selection filters the bus adapters by name.
    assert "FanOutBusAdapter" in src
    assert 'str(getattr(a, "name", "") or "").lower() in channels' in src
    # Telegram-direct fallback honors the choice.
    assert '"telegram" not in channels' in src

    settings_html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    assert "toggleRoutingList" in settings_html  # v2 selector helper
    assert "notifications.ops.channels" in (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js"
    ).read_text(encoding="utf-8")
    life = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "lifecycle_notifier.py"
    ).read_text(encoding="utf-8")
    assert "bus_send_targets" in life
    assert "_ops_channels" in life
    assert "Alerts go to" in settings_html
    assert "Adapters &amp; Routes" in settings_html
    assert "Delivery routing" in settings_html
    # The swarm output form is GONE from the Swarm page (moved, not
    # duplicated) — only the pointer card remains.
    swarm_html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "swarm.html"
    ).read_text(encoding="utf-8")
    assert "output-routing-bot-token" not in swarm_html
    assert "saveOutputTarget()" not in swarm_html
    assert "output-routing-card" in swarm_html  # pointer card remains


def test_mcp_npx_probe_covers_nvm_windows() -> None:
    """2026-09-03: this machine runs nvm-windows (NVM_SYMLINK=C:\nvm4w\nodejs)
    — the canonical Program Files/npm probes missed it, so a server started
    from a stale-PATH shell still lost the filesystem MCP server."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "mcp" / "manager.py"
    ).read_text(encoding="utf-8")
    probe = src.split("Probe the canonical Windows locations", 1)[1].split(
        "if resolved:", 1
    )[0]
    assert "NVM_SYMLINK" in probe
    assert "nvm4w" in probe


def test_mcp_resolved_shim_prepends_its_dir_to_child_path() -> None:
    """2026-09-03: npx.cmd was found via the probe, but its INTERNAL call to
    `node` still died — the nvm symlink dir (holding node.exe) was not on
    the stale-PATH server's environment, so the child couldn't resolve its
    own sibling. Resolving a shim must prepend its directory to the child
    PATH."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "mcp" / "manager.py"
    ).read_text(encoding="utf-8")
    probe = src.split("Probe the canonical Windows locations", 1)[1].split(
        "asyncio.create_subprocess_exec", 1
    )[0]
    assert 'env["PATH"] = (' in probe
    assert "_os.path.dirname(resolved)" in probe


def test_templates_parse_as_jinja() -> None:
    """2026-09-03: a JavaScript-style ``||`` inside a Jinja ``{{ }}``
    expression (my Delivery & Routing card) took the whole Settings page
    down with a 500. Compile every template with Jinja itself — this
    catches the whole class at test time, not at page-load time."""
    from jinja2 import Environment, FileSystemLoader

    root = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates"
    )
    env = Environment(loader=FileSystemLoader(str(root)))
    env.globals["t"] = lambda key, **kw: key
    env.globals["js_version"] = lambda: "1"
    env.globals["css_version"] = lambda: "1"
    for tpl in sorted(root.glob("*.html")):
        env.get_template(tpl.name)  # raises TemplateSyntaxError on bad syntax
    # And the specific regression: no JS pipes inside Jinja expressions.
    import re as _re

    settings = (root / "settings.html").read_text(encoding="utf-8")
    assert not _re.search(r"\{\{[^}]*\|\|[^}]*\}\}", settings)


def test_guard_notifies_on_operator_reload() -> None:
    """2026-09-03: operator --reload was the ONLY silent restart path —
    every restart since Sep 2 was a reload, so the operator's usual
    Telegram restart alerts vanished. Reloads now send a quiet
    informational notice (not the crash-page tone)."""
    src = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "service" / "kazma_guard.py"
    ).read_text(encoding="utf-8")
    branch = src.split("consume_reload_request()", 1)[1].split(
        "self.restarts += 1", 1
    )[0]
    assert "self.notify.send(" in branch
    assert "operator reload" in branch


def test_four_delivery_routes_complete_fields() -> None:
    """2026-09-03 v4: the card carries EVERY field the old per-platform
    cards had — token, allowed users, guild/workspace, enabled toggle,
    Test — plus each route's destination. Connector state loads from
    /api/connectors and saves via POST /api/connectors, the exact flow the
    old dialogs used (token normalization, mask preservation, live
    allowlist apply, adapter refresh)."""
    settings_html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    # Route sections: credential + destination + old-dialog platform fields.
    for needle in (
        "Telegram — Main bot",
        "adapterRouting.tgToken",
        "tgMainChat",
        "adapterRouting.tgAllowed",           # telegram allowed user ids
        "Telegram — Group",
        "tgGroupToken",
        "tgGroupEnabled",
        "Dedicated Bot Token",
        "adapterRouting.discordToken",
        "adapterRouting.discordGuild",        # discord guild id (old dialog)
        "adapterRouting.discordAllowed",      # discord allowed user ids
        "discordChannel",
        "adapterRouting.slackToken",
        "adapterRouting.slackAppToken",
        "adapterRouting.slackWorkspace",      # slack workspace (old dialog)
        "adapterRouting.slackAllowed",        # slack allowed user ids
        "slackChannel",
        # Enabled toggles + Test buttons per platform (old card affordances)
        "adapterRouting.tgEnabled",
        "adapterRouting.discordEnabled",
        "adapterRouting.slackEnabled",
        "testRoute('telegram')",
        "testRoute('discord')",
        "testRoute('slack')",
        "alertRoutes",
        "swarmRoutes",
        "telegram-group",
    ):
        assert needle in settings_html, needle
    # The misleading badges are gone — every route owns its token now.
    assert "uses main bot token" not in settings_html

    hub = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js"
    ).read_text(encoding="utf-8")
    # Connector state rides the SAME endpoints as the old dialogs.
    assert "_fetch('/api/connectors')" in hub
    assert "fetch('/api/connectors'," in hub
    assert "gateway/refresh-adapters" in hub
    assert "_connectorPayloadFor" in hub
    # Destinations + selectors still write the canonical settings keys.
    for key in (
        "connectors.telegram.swarm_chat_id",
        "connectors.discord.swarm_channel_id",
        "connectors.slack.swarm_channel_id",
        "notifications.ops.channels",
        "notifications.swarm.routes",
    ):
        assert key in hub, key
    # The loader must read the GROUPED shape get_all() actually produces
    # (full dotted keys under the category) — the stripped read was the
    # "routing choices don't hold after save" bug (2026-09-03 v5).
    assert "grouped(notif, 'notifications', 'notifications.ops.channels')" in hub
    assert "grouped(conn, 'connectors', 'connectors.telegram.swarm_chat_id')" in hub


def test_platform_adapters_is_the_single_token_ui() -> None:
    """2026-09-04 v5: ONE adapters interface. Platform Adapters owns every
    telegram/discord/slack field; the legacy dialog is Email/Webhook only
    (two token entry points for the same keys was operator-confusing), and
    the old all-platforms card list is filtered down to those integrations.
    The save is also diff-driven: only changed values are written and the
    slow adapter restart runs only when platform credentials changed — in
    the background, so the Save button never grays out for seconds."""
    settings_html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    assert "Platform Adapters" in settings_html
    assert "Delivery routing" in settings_html
    assert "Other integrations" in settings_html
    # The legacy add-connector dialog must NOT offer the chat platforms.
    assert '<option value="telegram">Telegram</option>' not in settings_html
    assert '<option value="discord">Discord</option>' not in settings_html
    assert '<option value="slack">Slack</option>' not in settings_html
    # The legacy card list shows only email/webhook.
    assert "hubConnectors.filter(x => x.name === 'email' || x.name === 'webhook')" in settings_html
    # Secret fields have show/hide toggles.
    assert "routingShow.tgToken" in settings_html
    assert "routingShow.slackAppToken" in settings_html

    hub = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js"
    ).read_text(encoding="utf-8")
    # Diff-driven save.
    assert "_routingDiffable" in hub
    assert "adapterRoutingSnapshot" in hub
    assert "if (puts.length === 0)" in hub
    # The slow adapter restart is gated on platform changes and runs in the
    # background (after the Saved toast), never blocking the button.
    assert "if (platformChanged)" in hub
    assert "adapterRoutingApplying = true" in hub
    # Group route saves through the output-target API; the mask is never
    # sent back over a stored secret.
    assert "tok !== '***'" in hub
    assert "swarm/output-target" in hub

    services = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "services.py"
    ).read_text(encoding="utf-8")
    assert 'if bot_token in ("***",):' in services

    providers = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "providers.py"
    ).read_text(encoding="utf-8")
    # Masked-extras guard: a masked app_token sent back unchanged must not
    # clobber the stored credential (the old dialog silently did).
    assert "_is_masked_placeholder(str(value))" in providers

    ops = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "observability" / "ops_alerts.py"
    ).read_text(encoding="utf-8")
    assert 'group_route: bool = False' in ops
    assert '"telegram-group" in channels' in ops
    # The Telegram-direct fallback honors the card's main-route chat id.
    assert "connectors.telegram.swarm_chat_id" in ops


def test_swarm_routes_selector_has_a_real_consumer() -> None:
    """v3 shipped `notifications.swarm.routes` as a UI selector with NO
    consumer — a checkbox that does nothing. The gateway's
    `_maybe_send_to_output_target` must fan the final swarm report out over
    the selected routes, and every bus adapter needs a `name` so the
    ops-alert channel filter can select platforms at all (the filter read
    `getattr(a, "name", "")` and every adapter lacked the attribute —
    selecting any channel silently dropped the alert from the bus)."""
    dispatch = (
        Path(__file__).resolve().parent.parent
        / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "swarm_dispatch.py"
    ).read_text(encoding="utf-8")
    assert 'notifications.swarm.routes' in dispatch
    assert "_swarm_route_config" in dispatch
    # telegram-group resolves to the group route; the others to their
    # per-platform swarm chat/channel keys.
    assert "connectors.telegram.swarm_chat_id" in dispatch
    assert "connectors.discord.swarm_channel_id" in dispatch
    assert "connectors.slack.swarm_channel_id" in dispatch
    # Origin dedupe moved inside so a dispatch FROM the group still reaches
    # the other selected routes (the old outer guard skipped the whole send).
    assert "origin: IncomingMessage | None = None" in dispatch

    for fname, expected in (
        ("telegram_bus.py", '"telegram"'),
        ("discord_bus.py", '"discord"'),
        ("slack_bus.py", '"slack"'),
    ):
        src = (
            Path(__file__).resolve().parent.parent
            / "kazma-gateway" / "kazma_gateway" / "adapters" / fname
        ).read_text(encoding="utf-8")
        assert "def name(self)" in src, fname
        assert expected in src, fname


def test_post_approve_attach_and_card_below_text() -> None:
    """2026-09-04 incident: after approving shell_exec the CoT sat frozen
    for 3+ minutes. The server journaled 8s heartbeats the whole time — the
    browser never re-attached because _attachJournal's anti-loop budget
    (_REOPEN_MAX=3) was exhausted by earlier stream hiccups and the decline
    was silent. An Approve click is a deliberate operator action and must
    never be rationed by that budget. Same incident: the second approval
    card rendered ABOVE the already-streamed text (insert-after-CoT) — a
    pending card must land below the text that provoked it."""
    chat = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    # Approve-driven attaches reset the reopen budget (never rationed).
    assert "reason === 'approve-json' || reason === 'approve-409'" in chat
    assert "_reopenCount = 0" in chat
    # A declined attach is no longer silent.
    assert "reopen budget exhausted" in chat
    # New pending card lands below the streamed text. This is no longer an
    # anchor computed with compareDocumentPosition — it is the declared slot
    # order (pending gates come after the answer slot), exercised on a real
    # DOM in tests/js/test_turn_view.js: "pending gate sits BELOW the interim
    # text".
    assert "compareDocumentPosition" not in chat, (
        "card placement is computing anchors again instead of declaring order"
    )
    view = _TURN_VIEW_JS.read_text(encoding="utf-8")
    plan = js_function_body(view, "function slotPlan(doc, has, TD)")
    tail = plan.split("var plan = [];", 1)[1]
    assert tail.index("has.text") < tail.index("pending[i]")
