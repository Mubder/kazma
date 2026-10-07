"""A real approval endpoint cannot grant capability during decision-store outage."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("scope", ["once", "tool", "yolo"])
def test_storage_failure_neither_grants_nor_resumes(monkeypatch, scope):
    from kazma_ui.routes_direct import misc
    from kazma_ui import active_turns, auth, hitl_decision, hitl_status, reply_sink, session_manager, turn_runtime
    import kazma_ui.sse_chat._streaming as streaming

    payload = {"type": "hitl_approval", "tool": "file_write", "args": {"path": "fixture.txt"}}
    snapshot = SimpleNamespace(next=("tool_worker",), values={},
                               tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id="gate", value=payload)])])
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
    builder = SimpleNamespace(app=FastAPI(), _hitl_state={"graph": graph, "checkpointer": object()},
                              _graph_holder={"graph": graph}, session_store=None)
    owner = SimpleNamespace(session_id="owner", messages=[])
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: SimpleNamespace(
        get_by_thread_id=lambda _: owner, thread_is_exclusive=lambda _: True))
    monkeypatch.setattr(active_turns, "is_turn_running", lambda _: False)
    monkeypatch.setattr(misc, "_gate_not_pending", AsyncMock(return_value=""))
    monkeypatch.setattr(hitl_status, "persisted_hitl_for_thread", lambda _: None)
    monkeypatch.setattr(turn_runtime, "resolve_session_id", lambda _: "owner")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a: "turn")
    recorder = AsyncMock(side_effect=hitl_decision.GateDecisionUnavailable("store unavailable"))
    monkeypatch.setattr(hitl_decision, "record_gate_decision", recorder)
    grants, drive = Mock(), AsyncMock()
    monkeypatch.setattr(misc, "_apply_scope_grants", grants)
    monkeypatch.setattr(streaming, "_drive_graph_to_journal", drive)
    misc.register_misc_routes(builder)
    with TestClient(builder.app) as client:
        response = client.post("/api/approve/" + uuid4().hex,
                               json={"action": "approve", "interrupt_id": "gate", "scope": scope})
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "gate_decision_unavailable"
    assert recorder.call_args.kwargs["require_durable"] is True
    grants.assert_not_called()
    drive.assert_not_called()
