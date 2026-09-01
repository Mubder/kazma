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


def test_hitl_card_is_not_torn_down_after_approve() -> None:
    chat = _src(_CHAT_JS)
    render = chat.split("function renderHitlCard(data)", 1)[1].split(
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
