"""Phase 7 — soul confirm→re-apply endpoint (the operator trigger)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.safety.commitment.store import get_commitment
from kazma_core.skills.self_improvement import (
    apply_agent_mutation, mint_soul_commitment,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "1")
    from kazma_ui.commitment_api import create_commitment_router

    app = FastAPI()
    app.include_router(create_commitment_router())
    return TestClient(app)


def test_pending_lists_minted_soul(client):
    cid = mint_soul_commitment("learn tool X", agent_id="a1")
    assert cid is not None
    r = client.get("/api/commitment/soul/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["pending"][0]["commitment_id"] == cid
    assert body["pending"][0]["agent_id"] == "a1"


def test_confirm_flips_and_reapplies(client):
    cid = mint_soul_commitment("learn tool X", agent_id="a1")
    # unconfirmed → held
    assert apply_agent_mutation("a1", "learn tool X", commitment_id=cid) is False
    # confirm via the endpoint → flips + re-applies
    r = client.post(f"/api/commitment/soul/{cid}/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["confirmed"] is True
    assert body["applied"] is True
    assert get_commitment(cid).status == "committed"
    # no longer pending
    assert client.get("/api/commitment/soul/pending").json()["count"] == 0


def test_reject_aborts(client):
    cid = mint_soul_commitment("bad delta", agent_id="a1")
    r = client.post(f"/api/commitment/soul/{cid}/reject")
    assert r.status_code == 200
    assert r.json()["rejected"] is True
    assert get_commitment(cid).status == "aborted"
    assert client.get("/api/commitment/soul/pending").json()["count"] == 0


def test_confirm_not_found(client):
    r = client.post("/api/commitment/soul/cmt_nonexistent/confirm")
    assert r.status_code == 200
    assert "error" in r.json()
