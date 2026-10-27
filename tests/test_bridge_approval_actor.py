"""AUD-100: a bus-bridge approval records the caller's session, not the body.

The approve route's graph path takes the deciding human from the server-side
session (audit M-W3). Its bus-bridge branch -- an approval for a ``kazma mcp``
process -- recorded ``body["actor"]`` verbatim, so any client could write who
approved into the gate registry's decision log.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def gate_db(tmp_path, monkeypatch):
    from kazma_core.safety import hitl_gates as hg

    monkeypatch.delenv("KAZMA_GATE_REGISTRY", raising=False)
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    hg.ensure_gate_schema()
    yield hg
    hg.set_db_path_for_tests(None)


def _approve(monkeypatch, body: dict, thread_id: str):
    from kazma_ui import auth
    from kazma_ui.routes_direct import misc

    builder = SimpleNamespace(
        app=FastAPI(), _hitl_state={}, _graph_holder={}, session_store=None
    )
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    import kazma_core.security.web_sessions as ws

    monkeypatch.setattr(
        ws, "get_session_payload",
        lambda sid: {"role": "admin", "session_id": "sess1234abcd"} if sid else {},
    )
    misc.register_misc_routes(builder)
    with TestClient(builder.app) as client:
        client.cookies.set(ws.SESSION_COOKIE, "cookie-value")
        return client.post("/api/approve/" + thread_id, json=body)


def test_the_bridge_records_the_session_not_the_body(monkeypatch, gate_db):
    hg = gate_db
    thread_id = "mcp-" + uuid4().hex[:8]
    gate = hg.register_gate(
        hg.GateRow(
            gate_id="g-" + uuid4().hex[:8],
            thread_id=thread_id,
            tool="shell_exec",
            mechanism="bus_bridge",
            tenant_id="default",
        )
    )
    r = _approve(
        monkeypatch,
        {"action": "approve", "actor": "the-owner-said-so"},
        thread_id,
    )
    assert r.status_code == 200, r.text
    assert r.json()["bridge"] is True
    row = hg.gate_for(gate.gate_id)
    assert row is not None
    assert row.decision == "approve"
    assert row.actor == "web:admin:sess1234", row.actor
    assert "the-owner-said-so" not in row.actor

