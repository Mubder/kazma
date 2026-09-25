"""Retiring saved drafts: reversible, never touches what went out, gates publishing.

2026-09-25: after the per-draft repair, "what posts did we not send" listed
35 unused drafts, 28 of them superseded copies, old versions and a test
draft — with no way to retire any of them, so every future answer and
X Studio's inbox would repeat them for up to 90 days.

``discard_proposal`` (tool) and X Studio's Dismiss/Restore share one store
operation. These tests drive the store, the registered tool, the chat
publish gate and the X Studio routes.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CSRF = {"X-Requested-With": "XMLHttpRequest"}
FOUR = ["draft one", "draft two", "draft three", "draft four"]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import kazma_core.agent.artifacts as artifacts_mod

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "kazma-data"))
    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "kazma-data" / "agent_artifacts.db"))
    artifacts_mod.reset_artifact_store()
    yield artifacts_mod.get_artifact_store()
    artifacts_mod.reset_artifact_store()


def _kind(store, pid):
    with store._connect() as conn:
        return conn.execute(
            "SELECT kind FROM agent_artifacts WHERE key = ?", (f"proposal:{pid}",)
        ).fetchone()[0]


def _unused_ids(store):
    return [r["id"] for r in store.list_proposals()]


# ── the store ─────────────────────────────────────────────────────────────


def test_discarding_one_draft_hides_only_that_draft(store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    assert store.discard_proposal(f"{pid}:2") == {"changed": 1, "skipped_used": 0}
    assert _unused_ids(store) == [f"{pid}:1", f"{pid}:3", f"{pid}:4"]
    assert _kind(store, pid) == "proposal"
    dismissed = store.list_proposals(only_discarded=True)
    assert [(r["id"], r["used_via"]) for r in dismissed] == [(f"{pid}:2", "discarded")]


def test_a_posted_draft_is_never_discarded(store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post", used_ref="99")
    report = store.discard_proposal(pid)  # the whole set
    assert report == {"changed": 3, "skipped_used": 1}
    item = store.resolve_proposal(f"{pid}:1")["items"][0]
    assert (item["used_via"], item["used_ref"]) == ("x_post", "99"), "what went out must not be rewritten"
    assert _kind(store, pid) == "proposal_posted", "every draft is now posted or discarded"


def test_restore_brings_back_only_discarded_drafts(store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    store.discard_proposal(pid)
    assert store.discard_proposal(pid, restore=True) == {"changed": 3, "skipped_used": 0}
    assert _unused_ids(store) == [f"{pid}:2", f"{pid}:3", f"{pid}:4"]
    assert _kind(store, pid) == "proposal"
    assert store.resolve_proposal(f"{pid}:1")["items"][0]["used_via"] == "x_post"


def test_discarding_twice_changes_nothing(store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    store.discard_proposal(f"{pid}:3")
    assert store.discard_proposal(f"{pid}:3") == {"changed": 0, "skipped_used": 0}
    assert store.discard_proposal("prop_missing") == {"changed": 0, "skipped_used": 0}


def test_a_discarded_draft_cannot_be_published_from_x_studio(store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    assert store.stored_text_for(f"{pid}:2") == "draft two"
    store.discard_proposal(f"{pid}:2")
    assert store.stored_text_for(f"{pid}:2") is None
    store.discard_proposal(f"{pid}:2", restore=True)
    assert store.stored_text_for(f"{pid}:2") == "draft two"


# ── the chat publish gate ─────────────────────────────────────────────────


def test_the_chat_gate_refuses_a_discarded_draft_until_it_is_restored(store):
    from kazma_core.agent.graph_tool_worker import _commitment_resolve_gate

    pid = store.save_proposal("default", "t-x", "tweets", FOUR)["proposal_id"]
    state = {"thread_id": "t-x", "tenant_id": "default", "messages": []}
    call = [{"id": "c1", "name": "x_post", "arguments": {"text": "x", "proposal_id": f"{pid}:2"}}]

    store.discard_proposal(f"{pid}:2")
    pending, blocked = _commitment_resolve_gate(state, call, allow_interrupt=False)
    assert pending == [] and len(blocked) == 1
    assert "discarded" in blocked[0]["content"] and "restore=True" in blocked[0]["content"]

    store.discard_proposal(f"{pid}:2", restore=True)
    pending, blocked = _commitment_resolve_gate(state, call, allow_interrupt=False)
    assert not blocked, blocked[0]["content"] if blocked else ""
    assert pending[0]["arguments"]["text"] == "draft two"


# ── the agent's tool ──────────────────────────────────────────────────────


def _tool(name):
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry._tools[name].func


def test_the_tool_is_registered_reversible_and_approval_free():
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.store_registry import TOOL_WRITES

    assert _tool("discard_proposal") is not None
    assert TOOL_TIERS["discard_proposal"] == "write"
    assert TOOL_WRITES["discard_proposal"].readers == ("list_proposals",)


def test_the_tool_discards_and_restores(store):
    pid = store.save_proposal("default", "", "tweets", FOUR)["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    discard = _tool("discard_proposal")

    out = asyncio.run(discard(proposal_id=pid))
    assert "Discarded 3 draft(s)" in out and "1 already posted/scheduled" in out
    assert "restore=True" in out

    listed = asyncio.run(_tool("list_proposals")())
    assert "No saved drafts left unused" in listed

    out = asyncio.run(discard(proposal_id=pid, restore=True))
    assert "Restored 3 draft(s)" in out
    assert asyncio.run(discard(proposal_id="")).startswith("Error:")


# ── X Studio ──────────────────────────────────────────────────────────────


@pytest.fixture()
def client(store):
    from kazma_ui.x_api import protected_router, router

    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    with TestClient(app) as c:
        yield c


def test_x_studio_dismiss_and_restore(client, store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]

    resp = client.post("/api/x/drafts/discard", json={"id": f"{pid}:2"}, headers=_CSRF)
    assert resp.status_code == 200 and resp.json()["changed"] == 1

    inbox = client.get("/api/x/drafts").json()["drafts"]
    assert f"{pid}:2" not in [d["id"] for d in inbox]
    dismissed = client.get("/api/x/drafts?dismissed=true").json()["drafts"]
    assert [d["id"] for d in dismissed] == [f"{pid}:2"]

    # A dismissed draft cannot be published until restored.
    blocked = client.post("/api/x/post", json={"text": "x", "proposal_id": f"{pid}:2"}, headers=_CSRF)
    assert blocked.status_code == 400

    resp = client.post("/api/x/drafts/discard", json={"id": f"{pid}:2", "restore": True}, headers=_CSRF)
    assert resp.json()["changed"] == 1
    assert f"{pid}:2" in [d["id"] for d in client.get("/api/x/drafts").json()["drafts"]]


def test_x_studio_dismiss_needs_the_same_origin_header(client, store):
    pid = store.save_proposal("default", "t", "tweets", FOUR)["proposal_id"]
    resp = client.post("/api/x/drafts/discard", json={"id": f"{pid}:2"})
    assert resp.status_code in (400, 403)
    assert len(_unused_ids(store)) == 4
