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
        "function checkPendingApprovals", 1
    )[0]
    assert "_paintHitlFromDoc" in load


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
