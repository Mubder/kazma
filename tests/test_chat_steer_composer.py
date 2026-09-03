"""Web composer: /steer queues for edit, then submits the live turn."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._module_source import module_source

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
    pin = js.split("function _assistantBubbleForOpenTurn(", 1)[1].split(
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
    # Slice to the end of the method, not a byte budget — a comment added
    # inside it used to push the assertion out of a fixed 1200-char window.
    pause = store.split("_pauseForApproval(approval)", 1)[1].split(
        "_resetTurnState()", 1
    )[0]
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
    # Live turns no longer build an in-bubble workbench - the terminal
    # branch swaps in the durable summary when the turn ends.
    cot = js.split("function _syncCotPanel(el, activity, status, meta)", 1)[1].split(
        "function _paintHitlFromDoc(el, doc)", 1
    )[0]
    assert "_taskCardEvent({ t: 'doc' })" in cot


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

    _placeHitlCard deliberately stacks a second card after the first, but
    _freezeHitlButtons disabled every card in the transcript. Approving the
    first killed the second's buttons; nothing re-enables them
    (_reconcileHitlCardsWithGates only ever disables), and with no enabled
    button left hasInlineApprovalCard() went false - so onDone took the
    endTurn branch while the graph was still parked on the untouched
    interrupt. Card gone, no reply (2026-09-03).
    """
    js = _js()
    assert "function _freezeHitlButtons(scope)" in js
    assert "_freezeHitlButtons(card);" in js
    assert "_freezeHitlButtons();" not in js, (
        "unscoped freeze reintroduced - it kills a sibling gate's buttons"
    )
    # Deciding gate A does not mean the turn stopped waiting on gate B.
    submit = js.split("function submitApproval(action, scope)", 1)[1]
    assert "_awaitingApproval = hasInlineApprovalCard();" in submit
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
    render = js.split("function renderTurn(doc, meta)", 1)[1].split(
        "function applyTurnEvent(ev)", 1
    )[0]
    assert "if (turnId && turnId !== 'live') {" in render
    # Both halves guarded: the lookup AND the stamp.
    assert render.count("turnId !== 'live'") >= 2, (
        "one of the lookup/stamp pair is unguarded — the magnet is back"
    )
    lookup = render.split('.message-assistant[data-turn-id="', 1)[0]
    assert "turnId !== 'live'" in lookup
    stamp = render.split("el.setAttribute('data-turn-id', turnId)", 1)[0]
    assert stamp.rstrip().endswith("{")
    assert "turnId !== 'live'" in stamp.rsplit("if (", 1)[-1]
    # A turn that never got a real id can still leave one behind (older
    # builds, restored transcripts) — beginTurn releases it.
    begin = js.split("function beginTurn(opts)", 1)[1].split(
        "// \u2500\u2500 Turn lifecycle diagnostics", 1
    )[0]
    assert '.message-assistant[data-turn-id="live"]' in begin
    assert "removeAttribute('data-turn-id')" in begin


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
    render = js.split("function renderTurn(doc, meta)", 1)[1].split(
        "function applyTurnEvent(ev)", 1
    )[0]
    assert "_docHasBubbleContent(doc)" in render
    assert "_assistantBubbleForOpenTurn(_paintable)" in render
    assert "_assistantBubbleForOpenTurn()" not in render, (
        "an unconditional mint is back in the paint path"
    )
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
    assert "function _parkClaimedHitlCard(card)" in js
    assert "function _collapseClaimedHitlCard(card)" in js
    park = js.split("function _parkClaimedHitlCard(card)", 1)[1].split(
        "function _collapseClaimedHitlCard(card)", 1
    )[0]
    assert "message-text" in park
    # All three claim paths: security approve/deny, semantic option,
    # watchdog timeout.
    set_state = js.split("function setCardState(state, label)", 1)[1].split(
        "function appendAssistantText", 1
    )[0]
    assert "_parkClaimedHitlCard(card);" in set_state
    assert "_collapseClaimedHitlCard(card);" in set_state
    sem = js.split("_semCard.querySelectorAll('.hitl-sem-opt')", 1)[1][:900]
    assert "_parkClaimedHitlCard(_semCard);" in sem
    timeout = js.split("function markApprovalTimedOut(msg)", 1)[1].split(
        "\n  function ", 1
    )[0]
    assert "_parkClaimedHitlCard(card);" in timeout
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
    paint = js.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "\n  function renderTurn(doc, meta)", 1
    )[0]
    assert paint.count("_collapseClaimedHitlCard(card);") >= 2, (
        "both claimed branches of the projector must re-assert the collapse"
    )
    release = js.split("function _releaseHitlComposer", 1)[1].split(
        "\n  function ", 1
    )[0]
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
    # (2) auto-expand removed: the placement sweep and both force-expands
    # are gone; only un-done remains.
    place = js.split("function _placeHitlCard(content, card)", 1)[1].split(
        "\n  function ", 1
    )[0]
    assert "is-collapsed" not in place.replace("lastCard", "")  # no class surgery on panels
    assert js.count("classList.remove('is-collapsed', 'is-done')") == 0
    assert js.count("_revealHitlCard(") >= 3  # helper + both branches
    # (3) reveal bounces only live cards.
    reveal = js.split("function _revealHitlCard(card)", 1)[1].split(
        "\n  function renderHitlCard", 1
    )[0]
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
    # says 'pending'). _paintHitlFromDoc must refuse pending paints while
    # hydrating; the registry resync is the only pending painter on load.
    paint = js.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "\n  function renderTurn(doc, meta)", 1
    )[0]
    assert "if (_hydratingSession) return;" in paint
    # Round 3: the FLASH itself was the Alpine fallback being armed by a
    # CLAIMED historical card (renderHitlCard lit pendingApproval whenever
    # the painted card had no enabled buttons). Historical paints pass
    # store:false; only a live card arms the fallback.
    assert "renderHitlCard(hitl.payload, { lock: false, store: false });" in paint
    rhc = js.split("function renderHitlCard(data, opts)", 1)[1].split(
        "\n  function ", 1
    )[0]
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
    (moved out of the Swarm page)."""
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
    assert "toggleRoutingChannel" in settings_html
    assert "notifications.ops.channels" in (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js"
    ).read_text(encoding="utf-8")
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
