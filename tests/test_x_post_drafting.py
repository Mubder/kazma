"""Studio generation honors X model bindings and creates reviewable drafts only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def generation(monkeypatch):
    from kazma_core.x_api.stance import ReplyConfig

    state = {"calls": [], "output": {"drafts": ["An opinion about coffee."]}}
    class Client:
        config = SimpleNamespace(model="studio-local", base_url="http://127.0.0.1:1234/v1")
        async def chat(self, messages, **kwargs):
            state["messages"] = messages
            return SimpleNamespace(content=json.dumps(state["output"]))
    class Registry:
        def list_providers(self):
            return [{"name": "local", "base_url": "http://127.0.0.1:1234/v1"}]
        def get_client_by_provider(self, provider, model):
            state["calls"].append((provider, model))
            return Client()
        def get_active_profile(self):
            return {"provider": "local", "model": "global-local"}
        def get_client(self):
            # The verifier inherits global and is unavailable in this fixture.
            # Post drafting still uses the explicit available local override.
            return None
        async def release_client(self, client):
            state["released"] = True
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())
    cfg = ReplyConfig(enabled=False, mode="draft", summoners=(), trigger="", max_replies_per_day=5,
                      max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                      poll_interval_s=60)
    monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: cfg)
    from kazma_core.config_store import get_config_store
    get_config_store().set("connectors.x.ai", {"selection": "global", "local_only": True,
                          "roles": {"post_drafting": {"selection": "specific", "provider": "local", "model": "studio-local"}}})
    return state


async def test_post_role_works_while_connector_is_off_and_closes_client(generation):
    from kazma_core.x_api.post_drafting import draft_posts
    result = await draft_posts("Coffee is a pleasant daily ritual.")
    assert result.drafts == ("An opinion about coffee.",) and result.review_required
    assert result.models[0]["model"] == "studio-local"
    assert result.models[0]["roles"] == ["post_drafting"]
    assert generation["calls"] == [("local", "studio-local")] and generation["released"]
    assert 'untrusted' in generation["messages"][1]["content"]


@pytest.mark.parametrize("output", [
    {"drafts": ["x" * 281]}, {"drafts": []}, {"drafts": [7]}, {"drafts": [""]},
    {"drafts": ["A draft"], "publish": True}, {"drafts": ["hi @target"]},
])
async def test_invalid_generated_text_is_not_saved(output, generation):
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.reply import DraftFailed
    generation["output"] = output
    with pytest.raises(DraftFailed):
        await draft_posts("A harmless idea.")
    assert generation["released"]


async def test_duplicate_alternatives_fail_as_a_whole(generation):
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.reply import DraftFailed
    generation["output"] = {"drafts": ["same", "same"]}
    with pytest.raises(DraftFailed, match="repeated"):
        await draft_posts("An idea.", count=2)


def test_generate_route_saves_exact_tenant_proposal_and_never_publishes(generation, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.agent.artifacts import ArtifactStore
    from kazma_core.config_store import get_config_store
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.ownership import x_config_key
    from kazma_ui.x_api import protected_router, router

    store = ArtifactStore(tmp_path / "artifacts.db")
    monkeypatch.setattr("kazma_core.agent.artifacts.get_artifact_store", lambda: store)
    def forbidden(*args, **kwargs):
        pytest.fail("Generating a Studio draft must never construct the X publishing client")
    monkeypatch.setattr("kazma_core.x_api.client.XClient", forbidden)
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    with tenant_scope("owner"), TestClient(app) as client:
        get_config_store().set(x_config_key("connectors.x.ai"), {"selection": "specific", "provider": "local", "model": "studio-local", "local_only": True})
        assert client.post("/api/x/generate", json={"brief": "Coffee"}).status_code == 403
        response = client.post("/api/x/generate", json={"brief": "Coffee"}, headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200, response.text
        data = response.json()
        item = data["proposal"]["items"][0]
        assert store.stored_text_for(item["id"], tenant_id="owner") == "An opinion about coffee."
        assert store.stored_text_for(item["id"], tenant_id="stranger") is None
        assert data["review_required"]
