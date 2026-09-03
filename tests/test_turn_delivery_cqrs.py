"""Turn Delivery CQRS cutover — class locks, not incident patches.

Approve is a JSON command. The live tail is the journal attach. HITL pause
is not a terminal ``done``. A vanished in-memory cursor falls back to
SessionStore. Dual POST approve is 409.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_UI = _ROOT / "kazma-ui" / "kazma_ui"
_MISC = _UI / "routes_direct" / "misc.py"
_STREAMING = _UI / "sse_chat" / "_streaming.py"
_INIT = _UI / "sse_chat" / "__init__.py"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_HITL_JS = _UI / "static" / "js" / "hitl_approval.js"
_DOC_JS = _UI / "static" / "js" / "modules" / "turn_document.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_abort_releases_hitl_gate_and_composer() -> None:
    """ /abort must settle live gates so the next prompt is a new turn."""
    init = _src(_INIT)
    abort = init.split("async def abort_chat_turn", 1)[1]
    assert "abort_thread_hitl" in abort
    assert 'auto_deny=_hitl_now != "pending"' in init
    chat = _src(_CHAT_JS)
    assert "function _releaseHitlComposer" in chat
    assert "_releaseHitlComposer('abort')" in chat
    status = init.split("async def get_session_status", 1)[1].split(
        "async def delete_session", 1
    )[0]
    assert 'str(g.get("state") or "") == "pending"' in status
    assert 'hitl.get("gate")' in status


def test_command_resume_ignores_own_drive_task() -> None:
    src = _src(_STREAMING)
    assert "get_active_turn(thread_id)" in src
    assert "_running is not _me" in src
    chat = _src(_CHAT_JS)
    assert "function _attachJournal(" in chat
    assert "_reopenSseRef = _attachJournal" in chat
    status = _src(_INIT)
    assert '"paused"' in status.split("async def get_session_status", 1)[1].split(
        "async def delete_session", 1
    )[0]


def test_hard_steer_is_json_journal_resume() -> None:
    src = _src(_INIT)
    fn = src.split("async def steer_chat_turn", 1)[1].split(
        "async def abort_chat_turn", 1
    )[0]
    assert "StreamingResponse" not in fn
    assert "_drive_graph_to_journal" in fn
    assert "text/event-stream" not in fn
    stream = _src(_STREAMING)
    assert "is_hard_steer_interrupt" in stream
    chat = _src(_CHAT_JS)
    assert "text/event-stream" not in chat.split("fetch('/api/chat/steer'", 1)[1].split(
        "return;", 1
    )[0]
    assert "_attachJournal('steer-json')" in chat


def test_approve_route_returns_json_not_sse() -> None:
    src = _src(_MISC)
    fn = src.split("async def approve_tool", 1)[1].split("\n    @self.app.", 1)[0]
    assert "StreamingResponse" not in fn
    assert "text/event-stream" not in fn
    assert "_JSONResponse" in fn
    assert "_drive_graph_to_journal" in fn
    assert "create_task" in fn
    assert 'reason": "not_pending"' in fn or "reason\": \"not_pending\"" in fn
    assert "stamp_hitl_part_state" in fn
    planted = "return StreamingResponse(_stream_langgraph_events("
    assert planted not in fn


def test_chat_http_is_journal_attach_not_live_graph() -> None:
    src = _src(_INIT)
    gen = src.split("async def _event_generator", 1)[1].split(
        "async def _guarded_events", 1
    )[0]
    assert "_drive_graph_to_journal" in gen
    assert "_sse_attach_stream" in gen
    assert "wait_for_resume=True" not in gen


def test_hitl_pause_is_not_attach_terminal() -> None:
    src = _src(_STREAMING)
    assert "_SSE_ATTACH_TERMINAL" in src
    assert "approval_required" not in src.split("_SSE_ATTACH_TERMINAL", 1)[1].split(
        "\n", 1
    )[0]
    assert "mark_thread_paused" in src
    assert "is_thread_paused" in src


def test_approve_registers_resume_before_unpause() -> None:
    src = _src(_MISC)
    fn = src.split("async def approve_tool", 1)[1].split("\n    @self.app.", 1)[0]
    reg = fn.find("register_turn(thread_id, _resume_task)")
    unpause = fn.find("mark_thread_unpaused(thread_id)")
    assert reg != -1 and unpause != -1
    assert reg < unpause, "unpause-before-register closes the attach tail"


def test_approve_json_clears_hitl_wait_so_reattach_can_run() -> None:
    chat = _src(_CHAT_JS)
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "_awaitingApproval = false" in approve
    assert "_reopenSseRef('approve-json')" in approve
    attach = chat.split("function _attachJournal(reason)", 1)[1].split(
        "function _defaultAttachCallbacks", 1
    )[0]
    assert "last_event_id: cursor" in attach
    assert "_lastSeqSeen <= 0" not in attach


def test_chat_and_dashboard_approve_are_json_fetch() -> None:
    chat = _src(_CHAT_JS)
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "fetch(approvalUrl" in approve
    assert "sseFn" not in approve
    assert "text/event-stream" not in approve
    assert "res.status === 409" in approve
    assert "_resyncDelivery('approve-409')" in approve

    dash = _src(_HITL_JS)
    dash_fn = dash.split("async function submitApproval", 1)[1].split(
        "async function refreshPending", 1
    )[0]
    assert "sseFn" not in dash_fn
    assert "await fetch(url" in dash_fn
    assert "resp.status === 409" in dash_fn


def test_gap_status_resyncs_from_session_store() -> None:
    chat = _src(_CHAT_JS)
    assert "_resyncDelivery('sse-gap')" in chat
    assert "_lastSeqSeen = 0" in chat
    doc = _src(_DOC_JS)
    assert "type === 'hitl'" in doc or "type === \"hitl\"" in doc


def test_load_session_paints_hitl_from_parts() -> None:
    chat = _src(_CHAT_JS)
    assert "function _paintHitlFromDoc(el, doc)" in chat
    load = chat.split("function loadSession(sessionId)", 1)[1].split(
        "function newSession", 1
    )[0]
    assert "_paintHitlFromDoc" in load
    assert "checkPendingApprovals" not in chat
    assert "source: 'pending-list'" not in chat


def test_hitl_projector_is_monotonic() -> None:
    doc = _src(_DOC_JS)
    assert "function mergeHitlPart(" in doc
    assert "hitlRank(" in doc
    py = (
        _ROOT / "kazma-ui" / "kazma_ui" / "turn_document.py"
    ).read_text(encoding="utf-8")
    assert "def merge_hitl_part(" in py
    assert "HITL_RANK" in py


def test_attach_journal_has_inflight_guard() -> None:
    chat = _src(_CHAT_JS)
    attach = chat.split("function _attachJournal(reason)", 1)[1].split(
        "function _defaultAttachCallbacks", 1
    )[0]
    assert "_attachInFlight" in attach


def test_new_session_resets_hitl_client_state() -> None:
    """A new season must not inherit the previous season's generating/HITL flags.

    Leftover `_serverGenerating=true` after Approve painted the next session's
    pending card as already approved (2026-09-01). Server grants stay
    thread-scoped; this lock is the client half.
    """
    chat = _src(_CHAT_JS)
    assert "function _resetSessionTurnState()" in chat
    reset = chat.split("function _resetSessionTurnState()", 1)[1].split(
        "function _clearTurnTimers", 1
    )[0]
    assert "_serverGenerating = false" in reset
    assert "_docs = {}" in reset
    assert "_lastInterruptedThreadId = ''" in reset or '_lastInterruptedThreadId = ""' in reset
    new_fn = chat.split("function newSession()", 1)[1].split(
        "async function deleteSession", 1
    )[0]
    assert "_resetSessionTurnState()" in new_fn
    load = chat.split("function loadSession(sessionId)", 1)[1].split(
        "function bindCapacityBar", 1
    )[0]
    assert "_resetSessionTurnState()" in load


def test_pending_hitl_is_not_stamped_inflight_on_first_paint() -> None:
    """pauseForApproval sets _awaitingApproval before the card exists.

    Using that flag (or generating && !paused) as inflight painted
    "Approved — running…" with no click, while the dashboard still had
    live buttons (2026-09-01).
    """
    chat = _src(_CHAT_JS)
    paint = chat.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "function renderTurn(doc, meta)", 1
    )[0]
    assert "_awaitingApproval" not in paint
    assert "_serverGenerating && !_serverPaused" not in paint
    assert "_hitlAlreadyClaimed(hitl)" in paint
    assert "statusInflight" not in paint
    assert "renderHitlCard(hitl.payload, { lock: false })" in paint
    assert "renderHitlCard(hitl.payload, { lock: true })" in paint
    status = _src(_INIT)
    sess = status.split("async def get_session_status", 1)[1].split(
        "async def delete_session", 1
    )[0]
    assert "hitl_thread_status" in sess
    assert '"gate"' in sess or "'gate'" in sess


def test_hitl_claimed_match_is_interrupt_scoped() -> None:
    """Empty interrupt_id must not treat ANY claimed card as this gate."""
    chat = _src(_CHAT_JS)
    claimed = chat.split("function _hitlAlreadyClaimed(data)", 1)[1].split(
        "function _findHitlCard", 1
    )[0]
    assert "if (!iid) return true" not in claimed
    assert "iid === cid" in claimed
    assert "host.contains" not in claimed
    assert "tool === ctool" in claimed
    find = chat.split("function _findHitlCard", 1)[1].split(
        "function _notifyHitlResolved", 1
    )[0]
    assert "return null" in find
    assert "kazma:hitl-resolved" in chat
    dash = _src(_UI / "static" / "js" / "hitl_approval.js")
    assert "kazma:hitl-resolved" in dash
    paint = chat.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "function renderTurn(doc, meta)", 1
    )[0]
    assert "_findHitlCard" in paint
    render = chat.split("function renderHitlCard(data, opts)", 1)[1].split(
        "function submitApproval(action, scope)", 1
    )[0]
    assert "_findHitlCard" in render
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "_clearStoreApproval()" in approve


def test_hitl_card_is_not_torn_down_after_approve() -> None:
    chat = _src(_CHAT_JS)
    render = chat.split("function renderHitlCard(data, opts)", 1)[1].split(
        "function submitApproval(action, scope)", 1
    )[0]
    assert "_hitlAlreadyClaimed" in render
    assert "_hitlCardIsClaimed(old)" in render
    assert "messagesEl.querySelectorAll('.hitl-approval-card').forEach" not in render
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "_awaitingApproval = true" in approve
    assert "_freezeHitlButtons" in approve
    before, after = approve.split("Decision accepted", 1)
    assert "Allowed " not in before
    assert "Allowed " in after
    assert "_hitlAlreadyClaimed(data)" in chat.split("onApprovalRequired", 1)[1]


def test_ws_hitl_scan_fails_closed() -> None:
    src = _src(_UI / "routes" / "ws_chat.py")
    fn = src.split("async def _scan_and_emit_hitl_interrupt", 1)[1].split(
        "async def _emit_context_compacted", 1
    )[0]
    assert "is_truly_pending" in fn
    except_block = fn.split("except Exception:", 1)[1]
    assert "return False" in except_block.split("try:", 1)[0]
    assert "assign_interrupt_id" in fn
    assert "interrupt_id" in fn


def test_http_approve_once_does_not_grant() -> None:
    """Approve once must not write a tool grant (no cross-command immunity)."""
    src = _src(_MISC)
    fn = src.split("async def approve_tool", 1)[1].split(
        "async def list_pending_approvals", 1
    )[0]
    assert 'elif approved and scope == "tool":' in fn
    before_tool, after_tool = fn.split('elif approved and scope == "tool":', 1)
    assert "grant_tool(" not in before_tool
    assert "grant_tool(" in after_tool


def test_approve_409_running_is_not_error() -> None:
    chat = _src(_CHAT_JS)
    approve = chat.split("function submitApproval(action, scope)", 1)[1].split(
        "var onceBtn", 1
    )[0]
    assert "res.status === 409" in approve
    assert "state: 'inflight'" in approve or 'state: "inflight"' in approve
    assert "body.running" in approve


def test_journal_bounds_are_explicit() -> None:
    from kazma_ui.delivery import (
        DEFAULT_MAX_EVENTS_PER_THREAD,
        DEFAULT_MAX_THREADS,
        DEFAULT_TTL_SECONDS,
    )

    assert DEFAULT_MAX_EVENTS_PER_THREAD <= 2000
    assert DEFAULT_TTL_SECONDS <= 3600.0
    assert DEFAULT_MAX_THREADS <= 500


@pytest.mark.asyncio
async def test_concurrent_resume_claim_conflicts() -> None:
    from kazma_ui.routes_direct import misc as misc_mod

    tid = "cqrs-race-1"
    misc_mod._resume_inflight.discard(tid)
    results: list[str] = []

    async def claim() -> None:
        async with misc_mod._approve_lock_for(tid):
            if tid in misc_mod._resume_inflight:
                results.append("conflict")
                return
            misc_mod._resume_inflight.add(tid)
            results.append("claimed")
            await asyncio.sleep(0.05)

    try:
        await asyncio.gather(claim(), claim())
        assert results.count("claimed") == 1
        assert results.count("conflict") == 1
    finally:
        misc_mod._resume_inflight.discard(tid)


def test_stream_silence_journals_turn_heartbeats() -> None:
    """2026-09-03: during long tool/LLM calls the SSE path previously
    journaled NOTHING (only invisible keepalive comments) — the client's
    one indicator surface could not tell "working" from "hung", and the
    Command-resume path journaled nothing until close_turn. Silence must
    now emit JOURNALED turn_heartbeat frames carrying the live phase."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")

    # Streaming loop: 10s queue silence → keepalive AND a journaled
    # turn_heartbeat with phase/current/step/elapsed.
    loop = src.split('_hb: dict[str, Any] = {', 1)[1].split("finally:", 1)[0]
    assert 'yield ": keepalive' in loop  # raw source holds the literal \n\n
    assert 'emit_j("turn_heartbeat"' in loop
    # "detail" names WHAT the phase is acting on. A heartbeat that says only
    # "tool: file_search" for two minutes tells the reader far less than one
    # naming the query that is taking two minutes.
    for key in ('"phase"', '"current"', '"detail"', '"step"', '"elapsed_s"'):
        assert key in loop
    assert '_hb["detail"] = hb_arg_summary(inputs)' in src

    # Phase is tracked at every boundary the events already carry.
    assert src.count('_hb["phase"] = "tool"') >= 1
    assert src.count('_hb["phase"] = "llm"') >= 1
    assert src.count('_hb["phase"] = "supervisor"') >= 2

    # Command-resume (the post-approve path): ainvoke journals nothing
    # until close_turn — the resume loop must heartbeat too.
    resume = src.split("Heartbeat while the resumed graph runs", 1)[1][:900]
    assert 'emit_j("turn_heartbeat"' in resume
    assert '"phase": "resuming"' in resume

    # The client dispatches the frame to a dedicated handler on both the
    # send and attach callback builders, and the WS store feeds the same
    # card (checked in test_chat_steer_composer).
    js = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "streaming.js"
    ).read_text(encoding="utf-8")
    assert "case 'turn_heartbeat':" in js
    assert "callbacks.onHeartbeat" in js
