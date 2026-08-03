"""SaaS tenant binding + Postgres sparse-recall assist unit tests."""

from __future__ import annotations

from kazma_core.memory.config import resolve_tenant_id


def test_resolve_shared_ignores_auth(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "shared"
    )
    assert resolve_tenant_id("web", "u1", "s1", auth_user_id="alice") == "default"


def test_resolve_per_platform(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_platform"
    )
    assert resolve_tenant_id("telegram", "telegram:9") == "telegram"


def test_resolve_per_user_prefers_auth_user(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_user"
    )
    monkeypatch.setattr(
        "kazma_core.tenant_context.get_current_tenant_id", lambda: "default"
    )
    assert (
        resolve_tenant_id("web", "", "sess-1", auth_user_id="alice")
        == "web:alice"
    )


def test_resolve_per_user_prefers_context_over_session(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_user"
    )
    monkeypatch.setattr(
        "kazma_core.tenant_context.get_current_tenant_id",
        lambda: "tenant-from-jwt",
    )
    assert (
        resolve_tenant_id("web", "", "sess-1", auth_user_id="")
        == "tenant-from-jwt"
    )


def test_resolve_per_user_session_fallback(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_user"
    )
    monkeypatch.setattr(
        "kazma_core.tenant_context.get_current_tenant_id", lambda: "default"
    )
    assert resolve_tenant_id("web", "", "sess-xyz") == "web:sess-xyz"


def test_merge_remote_state_hits_dedupes(monkeypatch):
    from kazma_core.memory.recall import RecallHit, _merge_remote_state_hits

    class _BE:
        name = "postgres"
        available = True

    monkeypatch.setattr(
        "kazma_core.memory.state_backend.get_state_backend", lambda: _BE()
    )
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.search_state_episodes",
        lambda q, tenant_id="default", limit=10: [
            {"id": "ep-local", "user_text": "already have", "tier": "episodic"},
            {"id": "ep-remote", "user_text": "from postgres teal", "tier": "episodic"},
        ],
    )
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.search_state_beliefs",
        lambda q, tenant_id="default", limit=10: [
            {
                "id": "b-remote",
                "subject": "user",
                "predicate": "likes",
                "object": "teal",
                "confidence": 0.9,
                "structural_importance": 3,
            }
        ],
    )
    local_ep = [
        RecallHit(id="ep-local", content="already have", score=1.0, kind="episode")
    ]
    eps, bels = _merge_remote_state_hits(
        "teal",
        tenant_id="default",
        limit=5,
        episodes=list(local_ep),
        beliefs=[],
        explain=True,
    )
    ids = {h.id for h in eps}
    assert "ep-local" in ids
    assert "ep-remote" in ids
    assert any(h.id == "b-remote" for h in bels)
    assert any(
        (h.metadata or {}).get("remote_state") for h in eps if h.id == "ep-remote"
    )
