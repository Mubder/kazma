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
    """The render half of Turn Delivery V2 (KD-4) replaced all of these.

    HITL_VIEW_MODEL F: this grep keeps the movers deleted. It is not the
    proof of display-state correctness — that is ``resolve_gate_views``
    (``tests/test_gate_view.py``) plus Playwright incidents 2 and 3.
    """
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
    assert "seenTid" not in dash
    assert "item.gate_id || item.interrupt_id" in dash
    assert "payload.gate_id" in dash


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
    plan = js_function_body(view, "function slotPlan(doc, has, TD, gateState)")
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


def test_claimed_hitl_without_live_view_stays_settled() -> None:
    """Omit-from-plan dropped claimed cards to keptTail under the answer."""
    js = _js()
    lookup = js_function_body(js, "function _gateViewOf(part)")
    assert "ps !== 'pending'" in lookup
    assert "slot: 'settled'" in lookup
    build = js.split("if (entry.kind === 'workbench') {", 1)[1][:1200]
    # The fold moved to turn_preferences.js (UNIFIED_TURN_BLOCK.md §3), so
    # the build no longer names the class. What this line is really about
    # is that the workbench slot is BUILT for a claimed gate rather than
    # omitted from the plan — keep that, and check the fold now goes
    # through its one owner.
    assert "_applyActivityFold" in build
    assert "is-active" in build


def test_hitl_frames_ingest_gate_views() -> None:
    """Settle/done frames carry view + gate_views; the client must consume them."""
    js = _js()
    assert "function _ingestFrameGateViews(data)" in js
    default = js.split("function _defaultAttachCallbacks(epoch)", 1)[1]
    ar = default.split("onApprovalRequired: function(data)", 1)[1]
    assert "_ingestFrameGateViews(data)" in ar
    assert "view: (data && data.view) || undefined" in ar
    ingest = js_function_body(js, "function _ingestFrameGateViews(data)")
    assert "_serverGateViews = out" in ingest
    assert "v.interactive" in ingest
    click = js.split("function submitApproval(action, scope)", 1)[1]
    # Decision is approved/denied; inflight is the overlay, not part.state
    # (HITL_RANK would then reject the settle frame).
    assert "state: hitlState," in click


def test_sequential_hitl_does_not_mint_a_second_bubble() -> None:
    """Live 4-card run (2026-09-20): a resume/heal turn_id painted a second
    assistant row above the replay. One user row, one assistant bubble."""
    js = _js()
    body = js_function_body(js, "function _bubbleForTurn(turnId, paintable)")
    assert "_assistantBubbleForOpenTurn(false)" in body
    create_at = body.index("createAssistantMessage()")
    reuse_at = body.index("_assistantBubbleForOpenTurn(false)")
    assert reuse_at < create_at, "a new bubble is minted before reusing the open one"


def test_claimed_hitl_header_is_not_approval_required() -> None:
    """Collapsed claimed cards kept '⚠ Approval Required' next to an
    Approved chip, which read as two states / wrong order."""
    js = _js()
    assert "function _setHitlHeaderTitle(" in js
    assert 'class="hitl-header-title"' in js
    paint = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx, resolvedState)")
    assert "_setHitlHeaderTitle" in paint
    assert "Approval Required" in paint


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
    assert "hasInlineApprovalCard()" not in pause
    assert "chat._hitlApproval(approval)" in pause


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
    assert "_gateViewOf" in state
    assert "'awaiting'" not in state
    assert "_hydratingSession" not in state
    lock = js_function_body(js, "function _hitlShouldLock(part)")
    assert "v.interactive" in lock
    build = js_function_body(js, "function _buildHitlSlotCard(part, ctx, resolvedState)")
    assert "_hitlShouldLock(part)" in build, (
        "the builder locks the composer without consulting the view"
    )
    assert "if (lockComposer) pauseForApproval(data);" in js


def test_hitl_display_state_never_invents_approved() -> None:
    """§30: a card may never claim "Approved" without evidence.

    A stale click is recoverable — the server re-verifies and answers "no
    longer pending". A fabricated Approved stamp is the incident.

    ``_hitlDisplayState`` is a lookup of ``view``, not a second resolver.
    Display-state proof is the Python corpus and Playwright 2/3, not this
    grep (HITL_VIEW_MODEL F).
    """
    js = _js()
    state = js_function_body(js, "function _hitlDisplayState(part)")
    # A1: the server already resolved. The client looks up a view; it does
    # not re-derive Approved from a part stamp or a local click.
    assert "part.state" not in state
    assert "decided_locally" not in state
    assert "_gateViewOf" in state
    assert "return String(v.state || '')" in state


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
    assert "function _showStoreApproval" not in js
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


def test_a_gate_has_exactly_one_state_for_ordering_and_labelling() -> None:
    """Reported from the live install, 2026-09-19, with screenshots.

    "Approved — running…" sat BELOW the finished reply; after a refresh
    "Waiting for approval" sat below a delete that had already run.

    ``slotPlan`` ordered by ``part.state``; ``_paintHitlSlotCard`` labelled
    from ``_hitlDisplayState(part)``. A part still stamped ``pending`` whose
    decision the registry had claimed was therefore SORTED as pending (after
    the answer) and PAINTED as approved — both screens exactly.

    That is the same defect turn_view.js was written to remove, one level
    in: two sources of truth for one fact. The resolver is threaded through
    ``slotPlan`` and the resolved value rides on the entry, so the painter is
    handed the answer rather than invited to compute its own.
    """
    view = _view_js()
    plan = js_function_body(view, "function slotPlan(doc, has, TD, gateState)")
    assert "typeof gateState === 'function'" in plan
    assert "state: shown" in plan, "the entry no longer carries the resolved state"
    assert "shown === 'pending' ? pending : settled" in plan, (
        "ordering is reading the raw part stamp again"
    )
    # The helper that answered this from the raw part stamp is deleted, not
    # merely unused — an exported second opinion is an invitation to ask it.
    # Assert on the DECLARATION and the export, never the bare name: the
    # comment explaining why it is gone contains the name, and a substring
    # check would match its own explanation.
    assert "function isPendingGate(" not in view, (
        "the raw-part-stamp helper is back; ordering can bypass the resolver "
        "again and a gate can be sorted one way and painted another"
    )
    assert "isPendingGate: isPendingGate" not in view, "it is exported again"

    js = _js()
    assert "gateState: _hitlDisplayState," in js, "chat.js no longer supplies the resolver"
    painter = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx, resolvedState)")
    assert "_hitlDisplayState(part)" in painter
    assert "_paintHitlSlotCard(el, entry.part, ctx, entry.state)" in js
    assert "_buildHitlSlotCard(entry.part, ctx, entry.state)" in js
    builder = js_function_body(js, "function _buildHitlSlotCard(part, ctx, resolvedState)")
    assert "_hitlDisplayState(part)" in builder
    assert "if (!show) return null" in builder


def test_load_session_joins_status_before_first_paint() -> None:
    """A1: messages + status land before TurnView hydrates history.

    Painting messages alone then guessing HITL state is the race that
    minted ghost buttons and frozen Waiting cards.
    """
    js = _js()
    load = js_function_body(js, "function loadSession(sessionId)")
    assert "Promise.all([" in load
    assert "/status" in load
    assert "/messages?stats=1" in load
    ingest_at = load.index("_ingestStatus(pair[1])")
    render_at = load.index("TVh.render(")
    assert ingest_at < render_at, "status ingested after the first hydrate paint"
    assert "status.gates" not in load
    assert "gate_views" in js_function_body(js, "function _ingestStatus(status)")


def test_awaiting_card_rebuilds_when_registry_says_pending() -> None:
    """Rebuild remains for a frozen node that later becomes pending."""
    js = _js()
    view = _view_js()
    assert "renderers.rebuild" in view, "TurnView no longer asks whether to rebuild"
    assert "function _rerenderHitlDocs()" in js
    resync = js_function_body(js, "function _resyncDelivery(reason)")
    assert "_rerenderHitlDocs()" in resync
    ingest_at = resync.index("_ingestStatus(status)")
    rerender_at = resync.index("_rerenderHitlDocs()")
    assert ingest_at < rerender_at, "re-render ran before views were ingested"

    rebuild = js_function_body(js, "rebuild: function(entry, el)")
    assert "entry.kind !== 'hitl'" in rebuild or "entry.kind !== \"hitl\"" in rebuild
    assert "!== 'pending'" in rebuild
    assert "data-hitl-shown" in rebuild

    rerender = js_function_body(js, "function _rerenderHitlDocs()")
    assert "pendingIds" in rerender
    assert "renderTurn(doc, { source: 'gates' })" in rerender
    assert "pendingIds[iid]" in rerender


def test_a_countdown_never_delivers_a_verdict_about_the_past() -> None:
    """Reported from the live install, 2026-09-19.

    The operator approved two gates (YOLO), refreshed, and both cards came
    back red: "Approval timed out — continuing without this tool."

    The slot painter re-attached the countdown on every render of anything
    that looked pending. On a stale part whose ``approval_deadline`` had long
    passed, the ticker's first tick stamped the card red AND wrote ``timeout``
    into the document — which outranks every other HITL state, so the verdict
    was permanent and survived the next refresh too.

    The server decides whether a gate timed out. This ticker is a courtesy
    display for a gate that is live right now, and it must not invent a denial
    for the same reason a card must not invent an approval.
    """
    js = _js()
    cd = js_function_body(js, "function _attachHitlCountdown(card, data)")
    assert "if (dl - Date.now() / 1000 <= 0) return;" in cd, (
        "an expired deadline can arm a ticker again — the first tick will "
        "stamp a gate the operator approved as timed out"
    )
    assert "_noteGateDecided" not in cd, (
        "the ticker still writes timeout into the document"
    )
    painter = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx, resolvedState)")
    assert "if (_hitlShouldLock(part))" in painter
    assert "_attachHitlCountdown" in painter


def test_sending_a_decision_is_not_making_one() -> None:
    """``setCardState('inflight')`` must not write a decision.

    It ran a ternary that mapped everything not 'approved' to 'denied', so
    the in-flight paint — made before the server has accepted anything —
    recorded ``denied`` for a gate the operator had just approved. Writing
    'inflight' instead would have been worse: it outranks 'approved' in
    HITL_RANK, so the confirmed approval arriving afterwards would have been
    rejected as a regression and the gate pinned mid-flight.
    """
    js = _js()
    set_state = js.split("function setCardState(state, label)", 1)[1].split(
        "function appendAssistantText", 1
    )[0]
    assert "state === 'approved' ? 'approved' : 'denied'" not in set_state
    assert "_noteGateDecided(data, state)" in set_state
    for terminal in ("'approved'", "'denied'", "'timeout'", "'error'"):
        assert terminal in set_state, f"{terminal} is no longer recorded"
    # The guard must exclude the in-flight paint.
    guard = set_state.split("if (state ===", 1)[1].split(")", 1)[0]
    assert "inflight" not in guard, "the in-flight paint records a decision again"


def test_a_confirmed_local_decision_outranks_a_stale_registry_row() -> None:
    """Why the approved card sat under the answer until a refresh.

    ``/status`` is a snapshot. Between the approve and the next resync it
    still lists the gate as pending, and the registry rule let that outrank
    the decision this tab had just watched the operator make — so the settled
    card kept sorting into the pending group, below the answer.

    Only a LOCAL decision earns this. A merely hydrated 'approved' still
    loses to a pending row, which is what keeps "never invent Approved"
    intact — and turn_document strips the flag on hydrate so it cannot be
    inherited across a refresh.
    """
    js = _js()
    state = js_function_body(js, "function _hitlDisplayState(part)")
    assert "decided_locally" not in state
    note = js_function_body(js, "function _noteGateDecided(data, state)")
    assert "decided_locally" not in note
    assert "_setHitlOverlay" in js
    assert "_applyApproveView" in js
    assert "_HITL_OVERLAY_MS" in js
    turndoc = _turndoc_js()
    assert "decided_locally" not in turndoc
    misc = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py"
    ).read_text(encoding="utf-8")
    assert "def _attach_hitl_view(" in misc
    assert "_attach_hitl_view(" in misc.split("return _JSONResponse(", 1)[1]


def test_the_answer_is_painted_the_same_way_everywhere() -> None:
    """"The contents almost the same but how the text looks" — live install.

    ``_paintLiveTextNow`` and ``appendMessage`` both re-run the bidi pass and
    re-assert dir="auto" after writing innerHTML. The slot painter did not,
    so the terminal paint and the paint you get after a refresh (which goes
    through appendMessage) styled the same reply differently.
    """
    js = _js()
    paint = js_function_body(js, "function _paintTextSlot(textEl, doc, meta)")
    assert "KazmaBidi.apply(textEl, text)" in paint
    assert "setAttribute('dir', 'auto')" in paint
    # The other two paint paths still do it, so all three agree.
    live = js_function_body(js, "function _paintLiveTextNow(textEl, final)")
    assert "KazmaBidi.apply" in live
    append = js_function_body(js, "function appendMessage(role, content, attachmentName, ts, opts)")
    assert "KazmaBidi.apply" in append


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
    # HITL_VIEW_MODEL E: live = any view.interactive. A leftover pending
    # stamp on the document must not look like a live gate.
    live = js_function_body(js, "function hasLiveGate()")
    assert "_viewIsPending" in live
    assert "_serverGateViews" in live
    assert "hitlPartsOf" not in live
    assert "_hitlDisplayState" not in live
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


def test_one_status_surface_and_it_is_the_turn_header() -> None:
    """What the Live Task Card locked, relocated.

    The bar was "the ONE turn-state surface" — a page-level strip with a
    single writer (``_taskCardEvent``), heartbeat-fed and stall-honest.
    ``docs/plans/UNIFIED_TURN_BLOCK.md`` §3 moves that job INSIDE the turn,
    because one surface per PAGE is what forced it to own a phase machine,
    a clock and a retry budget of its own — and to guess when a turn it
    could not see had ended.

    These are structural assertions that the wiring exists. What the model
    actually decides on each state is tested in
    tests/js/test_turn_presentation.js; what the reader's fold does across
    repaints is tests/js/test_turn_preferences.js.
    """
    js = _js()
    html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")

    # Gone, with its controller and its markup.
    assert "_taskCardEvent" not in js
    assert "LIVE_TASK_CARD_BEGIN" not in js
    assert 'id="live-task-card"' not in html
    assert 'id="thinking-indicator"' not in html, (
        "the strip the bar itself replaced is still in the page"
    )

    # Replaced by a slot in the turn block, derived not told.
    assert "function _buildTurnHeader(" in js
    assert "function _paintTurnHeader(" in js
    assert "modules/turn_presentation.js" in html
    assert "modules/turn_preferences.js" in html


def test_the_header_cannot_vanish_from_a_live_turn() -> None:
    """The vanishing card, made structurally impossible.

    Every liveness event — approval and resuming included — had to pass
    through ``_tcWake`` to cancel a hide armed by the previous terminal
    frame. ``approval`` and ``resuming`` skipped it, so a done frame less
    than 1.6s earlier blanked the card mid-approve and a resume never
    restarted the tick timer: frozen elapsed, dead stall detection
    (2026-09-03).

    There is no hide timer now. The header is a keyed slot, the renderer's
    ``discard`` answers false for everything (turn_view.js contract 4:
    ambiguity never deletes), and the only timer is a repaint that stops
    itself. A frame cannot remove the header because nothing removes it.
    """
    js = _js()
    assert "discard: function() { return false; }" in js, (
        "the renderer can delete slots again; a header could vanish"
    )
    # The header's timer repaints and nothing else.
    tick = js_function_body(js, "function _tickLiveHeader()")
    for forbidden in ("hidden", "remove(", "innerHTML"):
        assert forbidden not in tick, (
            f"the header ticker does more than repaint ({forbidden})"
        )
    # Liveness is a FACT the model reads, not an event the header is told.
    facts = js_function_body(js, "function _headerFacts()")
    assert "lastSignalAgoMs" in facts
    assert "_lastTurnActivityTs" in facts
    # ...and silence is reported as silence rather than as an outcome.
    pres = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules"
        / "turn_presentation.js"
    ).read_text(encoding="utf-8")
    assert "'stalled'" in pres
    assert "STALL_MS" in pres


def test_recovery_has_one_loop_not_two() -> None:
    """The bar ran a second recovery loop beside the reconciler.

    ``_tcTick`` watched for a 20s heartbeat gap and drove its own
    ``_resyncDelivery`` retries on a 30s backoff with a 3-try budget —
    while ``_reconcileTick`` was already resyncing every 6s for the whole
    time a turn might be undelivered. Two loops asking the same question;
    all the second one added was the words "not responding".

    Deleting the bar must not have deleted recovery, so this asserts the
    surviving loop is intact and that no second one came back with it.
    """
    js = _js()
    assert "function _reconcileTick()" in js
    tick = js_function_body(js, "function _reconcileTick()")
    assert "_resyncDelivery('reconcile')" in tick
    assert "_scheduleReconcile()" in tick
    assert "_turnMayBeUndelivered()" in tick
    # One scheduler, one budget.
    assert js.count("function _scheduleReconcile()") == 1
    assert "_TC_STALL_RETRY_MS" not in js
    assert "_TC_STALL_MAX_TRIES" not in js
    assert "nextResyncAt" not in js, "a second retry budget is back"


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
    # The sibling's countdown is PER CARD, read off the node. It used to be
    # re-read into a page-level clock for the Live Task Card as well; that
    # surface is gone (UNIFIED_TURN_BLOCK.md §3) and per card is the level
    # that was always right, because two gates can be waiting at once.
    assert "card.setAttribute('data-approval-deadline'" in js
    assert "function _attachHitlCountdown(card, data)" in js
    assert "_liveHitlDeadline" not in js, (
        "a page-level approval clock is back; deadlines belong to the card"
    )


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
    html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")
    assert "function _showStoreApproval" not in js
    assert 'class="hitl-approval-card"' not in html
    pause = store.split("_pauseForApproval(approval) {", 1)[1].split(
        "_resetTurnState()", 1
    )[0]
    assert "hasInlineApprovalCard" not in pause
    rec = js.split("function _reconcileHitlCardsWithGates()", 1)[1].split(
        "/** Server-truth recovery", 1
    )[0]
    assert "_clearStoreApproval()" in rec


def test_a_session_change_leaves_no_status_surface_behind() -> None:
    """A brand-new empty session flashed a "Done" card for 1-2 seconds.

    ``newSession`` calls ``forceEndTurn``, whose terminal frame left the
    Live Task Card on screen for its 1.6s retire animation. A session
    change is the ABSENCE of a turn, not the end of one, so there was
    nothing to animate away — but the bar outlived the transcript because
    it was a PAGE-level surface.

    The status line now lives inside the assistant bubble
    (UNIFIED_TURN_BLOCK.md §3), so changing session removes it with the
    transcript and the whole class is structural. What is left to check is
    that nothing page-level came back, and that the one timer the header
    owns is a display ticker that stops itself.
    """
    js = _js()
    # The element, not the word: the comments that explain why it is gone
    # mention it by name on purpose.
    assert "getElementById('live-task-card')" not in js, (
        "a page-level status surface is back"
    )
    assert "LIVE_TASK_CARD_BEGIN" not in js
    html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")
    assert 'id="live-task-card"' not in html
    # The header's only timer is a repaint. It reads the document, writes
    # nothing, and stops as soon as the turn is terminal.
    tick = js_function_body(js, "function _tickLiveHeader()")
    assert "_paintTurnHeader(" in tick
    assert "return !!(model && !model.terminal);" in tick
    stop = js_function_body(js, "function _stopHeaderTicker()")
    assert "clearInterval(_headerTicker)" in stop


def test_an_empty_read_never_wipes_the_steps_you_are_reading() -> None:
    """Expanding the CoT and letting the turn finish emptied it.

    ``_liveTurnId`` is retired and ``_docs`` is dropped around the end of a
    turn, so ``_tcStepsFromDoc`` reads back nothing — and it treated that as
    "this turn has no steps" and blanked the body under the reader.
    Clearing belongs to the events that KNOW a turn started or a session
    changed; both do it explicitly.
    """
    js = _js()
    # The Live Task Card kept its own step list, re-read it from _docs on
    # every tick, and blanked it when the read came back empty — which is
    # exactly what happens around the end of a turn. That second list is
    # gone; the workbench is the only one, and its painter refuses an empty
    # read outright rather than treating it as "no steps".
    assert "_tcStepsFromDoc" not in js, "a second step list is back"
    paint = js_function_body(js, "function _paintWorkbenchSlot(panel, doc)")
    assert "if (!activity.length) return;" in paint, (
        "the workbench painter no longer refuses an empty read — this is "
        "the line that stops a finished turn blanking the panel the reader "
        "has open"
    )
    assert "if (!html) return;" in paint


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
    answer = js_function_body(js, "function _answerFromDoc(TD, doc)")
    assert "type === 'reasoning'" not in answer, (
        "thoughts are being painted as the answer again"
    )
    # A tool step has nothing to put in the bubble either.
    assert "_pinLiveAssistantBubble(false);" in js
    # ...and the "no response" diagnosis must not be gated on the bubble
    # being ABSENT, which any mint suppressed.
    done = js.split("onDone: function(data) {", 1)[1]
    # The guard reads the DOCUMENT now, not a module-level accumulator
    # (UNIFIED_TURN_BLOCK.md, invariant U06). Same condition, one
    # authority.
    assert "!_liveAnswerText() && !interrupted && !_awaitingApproval && !_turnPainted" in done


def test_ws_store_owns_no_status_surface_of_its_own() -> None:
    """One surface, one owner — the point the task-card bridge was making.

    The WS store used to push every frame into the Live Task Card so the
    WS path and the SSE path drove the SAME indicator instead of two. With
    the bar gone (docs/plans/UNIFIED_TURN_BLOCK.md §3) the requirement is
    unchanged and simpler to state: the store feeds CONTENT to chat.js and
    paints no status of its own. It must not acquire a new bridge to a new
    surface, which is how the last one started.
    """
    store = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"
    ).read_text(encoding="utf-8")
    assert "taskCard" not in store
    assert "thinking-indicator" not in store
    # Content still goes to the one painter.
    assert "chat.logProgress(step)" in store
    assert "typeof chat.beginTurn === 'function'" in store
    # ...and the store touches no transcript DOM itself.
    for forbidden in (".message-content", ".message-text", "agent-progress",
                      "turn-header"):
        assert forbidden not in store, (
            f"the WS store is writing turn content directly ({forbidden})"
        )


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
    mem = js.split("function applyMemoryExplain(data)", 1)[1].split(
        "\n  function ", 1
    )[0]
    assert "ensureProgressPanel()" not in mem
    # Plan progress used to ride the Live Task Card's header meta. With the
    # bar gone the header derives its meta from the document instead, so
    # what matters is that neither of these functions mints a panel.


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
    assert "applyTurnEvent(ev)" in decide and "type: 'hitl'" in decide
    # All four claim paths: security approve/deny, semantic option,
    # watchdog timeout, registry reconcile.
    set_state = js.split("function setCardState(state, label)", 1)[1].split(
        "function appendAssistantText", 1
    )[0]
    assert "_noteGateDecided(data," in set_state
    assert "_collapseClaimedHitlCard(card);" in set_state
    assert "className" not in set_state
    sem = js.split("_semCard.querySelectorAll('.hitl-sem-opt')", 1)[1][:900]
    assert "_noteGateDecided(data," in sem
    timeout = js_function_body(js, "function markApprovalTimedOut(msg)")
    assert "_noteGateDecided(" not in timeout
    assert "applyTurnEvent({" in timeout
    assert "card.className" not in timeout
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
    paint = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx, resolvedState)")
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
    assert "_noteGateDecided" in release
    assert "className" not in release
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
    # (1) The header used to strobe because it was TOLD a phase on every
    # frame and needed hysteresis to damp the telling. It now DERIVES the
    # phase from the document (modules/turn_presentation.js), which is a
    # pure function of state and cannot flicker between two readings of
    # the same state. The damping constant went with the bar.
    assert "_TC_LABEL_MIN_MS" not in js
    assert "_HEADER_PHASE_LABELS" in js
    paint_header = js_function_body(js, "function _paintTurnHeader(el, doc)")
    assert "_headerModel(doc)" in paint_header, (
        "the header is being told a phase again instead of deriving one"
    )
    # ...and it writes only what changed, so a repaint per token costs no
    # DOM mutation and cannot strobe.
    assert "if (node && node.textContent !== text)" in paint_header
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
    # Ghost source two: a stale pending part. Join-before-paint + server
    # view means a part with no covering live row is error chrome (or
    # omitted), never live buttons. Hydration no longer returns 'awaiting'.
    state = js_function_body(js, "function _hitlDisplayState(part)")
    assert "if (_hydratingSession) return 'awaiting';" not in state
    assert "_gateViewOf" in state
    painter = js_function_body(js, "function _paintHitlSlotCard(card, part, ctx, resolvedState)")
    assert "b.disabled = true" in painter
    build = js_function_body(js, "function _buildHitlSlotCard(part, ctx, resolvedState)")
    assert "store: show === 'pending'," in build
    assert "function _showStoreApproval" not in js
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
    plan = js_function_body(view, "function slotPlan(doc, has, TD, gateState)")
    tail = plan.split("var plan = [];", 1)[1]
    assert tail.index("has.text") < tail.index("pending[i]")
