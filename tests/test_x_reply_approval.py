"""A reviewed draft cannot acquire authority for a changed revision or account."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kazma_core.x_api.approval import capture_basis
    from kazma_core.x_api.config import XCredentials, get_x_config
    from kazma_core.x_api.reply_store import reset_reply_store
    from kazma_core.x_api.stance import get_reply_config

    state = {"cfg": replace(get_x_config(), enabled=True, account_id="123", credentials=XCredentials("k", "s", "t", "ts")),
             "pipeline": "reviewed-pipeline", "calls": []}
    monkeypatch.setattr("kazma_core.x_api.config.get_x_config", lambda: state["cfg"])
    monkeypatch.setattr("kazma_core.x_api.qualification.pipeline_fingerprint", lambda cfg: state["pipeline"])
    store = reset_reply_store(tmp_path / "replies.db")
    store.claim(summon_id="456", parent_id="789", target_handle="target", summoner="owner")
    basis = capture_basis(get_reply_config())
    store.mark_awaiting("456", draft="Reviewed candidate", subject_id="card", decision={"approval_basis": basis})

    async def publish(**kwargs):
        state["calls"].append(kwargs)
        return True, {"tweet_id": "900"}

    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", publish)
    return store, state, basis


async def test_missing_or_stale_revision_never_claims_send(env):
    from kazma_core.x_api.reply import approve_summon

    store, state, basis = env
    old = store.get("456").approval_token
    assert not (await approve_summon("456")).ok
    store.mark_awaiting("456", draft="Changed candidate", subject_id="card", decision={"approval_basis": basis})
    assert not (await approve_summon("456", approval_token=old)).ok
    assert state["calls"] == [] and store.get("456").status == "awaiting_approval"
    current = store.get("456").approval_token
    assert (await approve_summon("456", approval_token=current)).ok
    assert state["calls"][0]["text"] == "Changed candidate"
    assert state["calls"][0]["reply_to_id"] == "456"


@pytest.mark.parametrize("change", ["account", "credentials", "policy", "expired", "unbound", "unverified"])
async def test_changed_authority_requires_fresh_review(env, change):
    from kazma_core.x_api.reply import approve_summon

    store, state, basis = env
    if change == "account":
        state["cfg"] = replace(state["cfg"], account_id="999")
    elif change == "credentials":
        state["cfg"] = replace(state["cfg"], credentials=replace(state["cfg"].credentials, access_token="rotated"))
    elif change == "policy":
        state["pipeline"] = "changed-subject-or-model"
    elif change == "unverified":
        state["cfg"] = replace(state["cfg"], account_id="")
    else:
        decision = {} if change == "unbound" else {"approval_basis": {**basis, "expires_at": time.time() - 1}}
        store.mark_awaiting("456", draft="Reviewed candidate", subject_id="card", decision=decision)
    token = store.get("456").approval_token
    result = await approve_summon("456", approval_token=token)
    assert not result.ok and result.reason
    assert store.get("456").status == "awaiting_approval"
    assert state["calls"] == []


async def test_stale_deny_does_not_discard_changed_candidate(env):
    from kazma_core.x_api.reply import deny_summon

    store, _, basis = env
    token = store.get("456").approval_token
    store.mark_awaiting("456", draft="Changed candidate", subject_id="card", decision={"approval_basis": basis})
    assert not (await deny_summon("456", approval_token=token)).ok
    assert store.get("456").status == "awaiting_approval"
    assert (await deny_summon("456", approval_token=store.get("456").approval_token)).ok


def test_api_record_omits_private_binding(env):
    store, _, _ = env
    assert "approval_basis" not in store.get("456").to_dict()["decision"]


async def test_claim_rechecks_monotonic_revision_when_clock_does_not_advance(env, monkeypatch):
    from kazma_core.x_api.reply import approve_summon

    store, state, basis = env
    original = store.get("456")
    monkeypatch.setattr("kazma_core.x_api.reply_store.time.time", lambda: original.updated_at)
    claim = store.claim_approval

    def changed_before_claim(sid, **kwargs):
        store.mark_awaiting(sid, draft="New draft at the same clock tick", subject_id="card", decision={"approval_basis": basis})
        return claim(sid, **kwargs)

    monkeypatch.setattr(store, "claim_approval", changed_before_claim)
    result = await approve_summon("456", approval_token=original.approval_token)
    assert not result.ok and state["calls"] == []
    assert store.get("456").updated_at == original.updated_at
    assert store.get("456").revision > original.revision


async def test_approval_retains_exact_review_and_actor(env):
    from kazma_core.x_api.reply import approve_summon

    store, state, _ = env
    assert (await approve_summon("456", approval_token=store.get("456").approval_token, actor="operator-alice")).ok
    entry = store.history("456")[0]
    assert entry["event"] == "approved" and entry["record"]["decision_actor"] == "operator-alice"
    assert entry["record"]["draft_text"] == "Reviewed candidate"
    assert state["calls"][0]["metadata"]["approval_actor"] == "operator-alice"


async def test_generation_scope_cannot_bind_changed_model_settings(env):
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.approval import capture_basis
    from kazma_core.x_api.model_selection import x_model_scope
    from kazma_core.x_api.ownership import x_config_key
    from kazma_core.x_api.stance import get_reply_config

    key = x_config_key("connectors.x.ai")
    selected = {"selection": "specific", "provider": "local", "model": "reviewed"}
    get_config_store().set(key, selected)
    async with x_model_scope():
        assert capture_basis(get_reply_config())
        get_config_store().set(key, {**selected, "model": "different"})
        assert capture_basis(get_reply_config()) == {}


@pytest.mark.parametrize("changed", ["content", "version", "permission", "stale"])
async def test_reviewed_factual_source_revalidated_before_approval(env, monkeypatch, changed):
    import hashlib
    from types import SimpleNamespace

    from kazma_core.x_api.reply import approve_summon

    store, state, basis = env
    source = {"source_id": "chunk", "library_id": "library", "document_id": "document", "version_id": "v1",
              "content": "A checked passage", "content_hash": hashlib.sha256(b"A checked passage").hexdigest(),
              "truncated": False, "published_at": time.time() - 60}
    row = dict(source)
    library = {"id": "library", "archived": False}
    if changed == "content":
        row["content"] = "Changed assertion"
    elif changed == "version":
        row["version_id"] = "v2"
    elif changed == "permission":
        library = None
    else:
        source["published_at"] = time.time() - 31 * 86400
    monkeypatch.setattr("kazma_core.stores.knowledge.get_knowledge_store", lambda: SimpleNamespace(
        get_chunks_by_ids=lambda ids: {"chunk": row}, get_library_for_tenant=lambda ident, tenant: library))
    decision = {"approval_basis": basis, "subject_id": "card", "evidence": {"sources": [source]},
                "checks": [{"check": "evidence", "claims": [{"kind": "fact", "source_ids": ["chunk"]}]}]}
    store.mark_awaiting("456", draft="Reviewed fact", subject_id="card", decision=decision)
    result = await approve_summon("456", approval_token=store.get("456").approval_token)
    assert not result.ok and state["calls"] == []
    assert store.get("456").status == "awaiting_approval"
