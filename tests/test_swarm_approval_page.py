"""A swarm approval request on the Web dashboard (Swarm audit SW-S03).

Every chat card now links to the Swarm page's approval panel. The panel shows
the whole call the gate record keeps, with the hash the card shows, and answers
the same shared decision as the chat buttons (platform ``web``): the first
human Approve or Deny anywhere decides it (owner decision D7).

Before, the card's bounded view was all an operator had. The dashboard's
pending list admits a gate whose chat session the caller owns, and a swarm
task has none, so a swarm request never reached the web at all.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_LONG_CALL = json.dumps({"content": "Meeting notes: " + "a" * 3000, "path": "/home/op/.ssh/authorized_keys"})


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    from kazma_core.safety import hitl_gates

    monkeypatch.delenv("KAZMA_GATE_REGISTRY", raising=False)
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    yield
    hitl_gates.set_db_path_for_tests(None)


def _client(monkeypatch, *, admin: str = "ok", user: str = "layla") -> TestClient:
    import kazma_ui.auth as auth
    from kazma_ui.swarm_panel import create_swarm_router

    monkeypatch.setattr(auth, "admin_decision", lambda request: admin)
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"username": user})
    app = FastAPI()
    app.include_router(create_swarm_router(None))
    return TestClient(app, raise_server_exceptions=False)


def _request(tool: str = "file_write", args: str = _LONG_CALL, *, mechanism: str = "swarm_bus") -> str:
    """A request as SafetyMiddleware opens one: the gate record, then the row."""
    from kazma_core.safety.hitl_gates import GateRow, register_gate
    from kazma_core.swarm import shared_approvals

    gate_id = "swarm-gate-" + uuid.uuid4().hex[:12]
    register_gate(GateRow(gate_id=gate_id, thread_id="swarm", tool=tool, mechanism=mechanism,
                          args_json=args, message=args[:500]), ttl_seconds=60)
    if mechanism == "swarm_bus":
        shared_approvals.open_request(gate_id, tool=tool, args=args, deadline=time.time() + 60)
    return gate_id


def _view(client: TestClient, approval_id: str) -> dict[str, Any]:
    r = client.get(f"/api/swarm/approvals/{approval_id}")
    assert r.status_code == 200, r.text
    return r.json()["approval"]


def test_the_panel_shows_the_whole_call_with_the_cards_hash(monkeypatch) -> None:
    from kazma_core.swarm.approval_card import render_arguments

    client = _client(monkeypatch)
    rid = _request()
    view = _view(client, rid)
    card = render_arguments(_LONG_CALL)
    assert "chars hidden]" in card.text, "the card cuts the call"
    assert view["arguments"] == _LONG_CALL, "the panel shows all of it"
    assert view["arguments_digest"] == card.digest
    assert (view["tool"], view["status"]) == ("file_write", "pending")
    assert view["deadline_at"] > time.time()


def test_the_web_answers_the_same_request(monkeypatch) -> None:
    from kazma_core.swarm import shared_approvals

    client = _client(monkeypatch)
    rid = _request()
    r = client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    view = r.json()["approval"]
    assert (view["status"], view["decided_by"], view["decided_on"]) == ("approved", "layla", "web")
    # The request's owner, waiting on the shared row, reads the web's answer.
    assert asyncio.run(shared_approvals._wait_outcome(rid))["status"] == "approved"
    # And a chat button pressed later decides nothing.
    assert shared_approvals.resolve(rid, False, actor="telegram:9", platform="telegram") is False


def test_a_deny_on_the_web_is_final(monkeypatch) -> None:
    """Owner decision D7: a human Deny is final, like an Approve."""
    from kazma_core.swarm import shared_approvals

    client = _client(monkeypatch)
    rid = _request()
    assert client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "deny"}).status_code == 200
    assert shared_approvals.resolve(rid, True, actor="discord:7", platform="discord") is False
    assert _view(client, rid)["status"] == "denied"


def test_an_answered_request_is_not_answered_again(monkeypatch) -> None:
    from kazma_core.swarm import shared_approvals

    client = _client(monkeypatch)
    rid = _request()
    shared_approvals.resolve(rid, True, actor="slack:U1", platform="slack")
    r = client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "deny"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "not_pending"
    assert (body["approval"]["status"], body["approval"]["decided_on"]) == ("approved", "slack")


def test_reading_is_for_users_and_answering_for_the_operator(monkeypatch) -> None:
    client = _client(monkeypatch, admin="forbidden")
    rid = _request()
    assert client.get(f"/api/swarm/approvals/{rid}").status_code == 200
    r = client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["code"] == "admin_required"
    assert _view(client, rid)["status"] == "pending"


def test_an_answer_that_cannot_be_recorded_is_said(monkeypatch, caplog) -> None:
    """Like a chat button (SW-S15): a 500 naming it, a WARNING with the actor,
    the request still pending -- never a 409 "already answered"."""
    import logging
    import sqlite3

    from kazma_core.swarm import shared_approvals

    client = _client(monkeypatch)
    rid = _request()

    def down(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(shared_approvals, "resolve", down)
    caplog.set_level(logging.WARNING, logger="kazma_core.swarm.shared_approvals")
    r = client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "approve"})
    assert (r.status_code, r.json()["error"]) == (500, "not_recorded")
    assert any("layla" in rec.getMessage() and "web" in rec.getMessage() for rec in caplog.records)
    monkeypatch.undo()
    assert (shared_approvals.get(rid) or {}).get("status") == "pending"


def test_a_decision_must_be_approve_or_deny(monkeypatch) -> None:
    client = _client(monkeypatch)
    rid = _request()
    r = client.post(f"/api/swarm/approvals/{rid}/decide", json={"decision": "yolo"})
    assert r.status_code == 422
    assert _view(client, rid)["status"] == "pending"


def test_the_record_still_shows_an_ended_request(monkeypatch) -> None:
    """The owner closes the row once it has read it; the gate record keeps
    the call, the outcome and who decided."""
    from kazma_core.swarm import shared_approvals
    from kazma_core.swarm.safety import SafetyMiddleware

    client = _client(monkeypatch)
    # Two different calls: the registry takes the same call on the same
    # thread for the same pause.
    approved, timed_out = _request(), _request(args='{"path": "b.txt"}')
    client.post(f"/api/swarm/approvals/{approved}/decide", json={"decision": "approve"})
    for rid in (approved, timed_out):
        if rid == timed_out:
            shared_approvals.settle(rid, "timeout")
        SafetyMiddleware._record_gate(rid, shared_approvals.get(rid) or {})
        shared_approvals.close(rid)
    view = _view(client, approved)
    assert (view["status"], view["decided_by"], view["arguments"]) == ("approved", "layla", _LONG_CALL)
    assert _view(client, timed_out)["status"] == "timeout"


def test_two_identical_requests_keep_their_own_records(monkeypatch) -> None:
    """Two identical calls asking at once are two requests: each is shown,
    and recorded, under its own id."""
    from kazma_core.safety.hitl_gates import gate_for
    from kazma_core.swarm import shared_approvals
    from kazma_core.swarm.safety import SafetyMiddleware

    client = _client(monkeypatch)
    first, second = _request(), _request()
    assert gate_for(first).gate_id == first and gate_for(second).gate_id == second
    client.post(f"/api/swarm/approvals/{second}/decide", json={"decision": "deny"})
    SafetyMiddleware._record_gate(second, shared_approvals.get(second) or {})
    assert gate_for(first).state == "pending", "the first request's record is untouched"
    assert (gate_for(second).state, gate_for(second).decision) == ("settled", "deny")
    assert _view(client, first)["status"] == "pending"


def test_a_repeated_chat_pause_is_still_one_gate() -> None:
    """Negative control: the graph's twin rule is kept for chat pauses."""
    from kazma_core.safety.hitl_gates import GateRow, gate_for, register_gate

    one = register_gate(GateRow(gate_id="gate-aaaa", thread_id="chat-1", tool="shell_exec",
                                mechanism="graph", args_json='{"command": "ls"}'))
    two = register_gate(GateRow(gate_id="native-bbbb", thread_id="chat-1", tool="shell_exec",
                                mechanism="graph", args_json='{"command": "ls"}'))
    assert two.gate_id == one.gate_id and gate_for("native-bbbb").gate_id == one.gate_id


def test_a_chat_approval_is_not_this_pages_to_show(monkeypatch) -> None:
    client = _client(monkeypatch)
    chat_gate = _request("shell_exec", '{"command": "ls"}', mechanism="graph")
    assert client.get(f"/api/swarm/approvals/{chat_gate}").status_code == 404
    assert client.post(f"/api/swarm/approvals/{chat_gate}/decide",
                       json={"decision": "approve"}).status_code == 404


def test_the_mechanism_is_what_refuses_a_chat_gate(monkeypatch) -> None:
    """Negative control: the same gate as a swarm request is shown."""
    client = _client(monkeypatch)
    swarm_gate = _request("shell_exec", '{"command": "ls"}', mechanism="swarm_bus")
    assert client.get(f"/api/swarm/approvals/{swarm_gate}").status_code == 200


@pytest.mark.parametrize("bad", ["nope", "..%2F..%2Fsettings", "a" * 300])
def test_an_unknown_or_malformed_id_is_not_found(monkeypatch, bad: str) -> None:
    client = _client(monkeypatch)
    assert client.get(f"/api/swarm/approvals/{bad}").status_code == 404


def test_a_swarm_request_is_not_on_the_pending_list() -> None:
    """Why the link is needed: the list admits a gate by its chat session."""
    from kazma_ui.hitl_gate_bridge import gate_row_to_pending_item
    from kazma_ui.routes_direct.misc import _is_own_bridge_gate
    from kazma_core.safety.hitl_gates import gate_for

    rid = _request()
    item = gate_row_to_pending_item(gate_for(rid))
    assert item["thread_id"] == "swarm" and not _is_own_bridge_gate(item)


def test_the_page_carries_the_request_from_the_server() -> None:
    """The id is in the page: a soft navigation runs the page's script
    before the address bar changes, so the script never reads the URL."""
    from tests.test_swarm_ui_panel import _build_client

    client = _build_client()
    html = client.get("/swarm?approval=swarm-gate-abc123").text
    assert 'id="swarm-approval" data-approval-id="swarm-gate-abc123"' in html
    assert 'id="swarm-approval"' not in client.get("/swarm").text
    hostile = client.get('/swarm?approval="><script>alert(1)</script>').text
    assert 'id="swarm-approval"' not in hostile and "<script>alert(1)" not in hostile


def test_a_cards_link_opens_the_request_the_web_answers(monkeypatch) -> None:
    """End to end: a swarm worker's danger call posts its card; the link on
    it names the request; the web's Approve lets the call run."""
    from tests.test_swarm_bus_approvals import _Card, _middleware, _until

    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example")
    client = _client(monkeypatch)
    card = _Card("telegram", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=10.0)

    async def go() -> bool:
        check = asyncio.create_task(mw.check("file_write", _LONG_CALL))
        await _until(lambda: card.posts)
        link = card.posts[0].split("(https://kazma.example", 1)[1].split(")", 1)[0]
        rid = link.split("approval=", 1)[1]
        r = await asyncio.to_thread(client.post, f"/api/swarm/approvals/{rid}/decide",
                                    json={"decision": "approve"})
        assert r.status_code == 200, r.text
        return await check

    assert asyncio.run(go()) is True
