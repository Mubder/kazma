"""X model bindings stay independent of global chat and never fall back."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def registry(monkeypatch):
    import kazma_core.model_registry as module

    calls = []
    local = SimpleNamespace(config=SimpleNamespace(model="local-model"))
    global_client = SimpleNamespace(config=SimpleNamespace(model="global-model"))

    class Registry:
        def list_providers(self):
            return [{"name": "local", "enabled": True, "model": "local-model"}]

        def get_client(self):
            calls.append("global")
            return global_client

        def get_client_by_provider(self, provider, model):
            calls.append((provider, model))
            return local

        def get_active_profile(self):
            return {"provider": "global", "model": "global-model"}

    monkeypatch.setattr(module, "get_model_registry", lambda: Registry())
    return calls, local, global_client


@pytest.mark.asyncio
async def test_pinned_model_is_reused_for_entire_decision(registry):
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    calls, local, _ = registry
    get_config_store().set("connectors.x.ai", {
        "selection": "specific", "provider": "local", "model": "local-model",
    })
    async with x_model_scope():
        assert await get_x_client("drafting") is local
        get_config_store().set("connectors.x.ai", {"selection": "global"})
        assert await get_x_client("verification") is local
    assert calls == [("local", "local-model")]


@pytest.mark.asyncio
async def test_global_inheritance_preserves_existing_behavior(registry):
    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    calls, _, global_client = registry
    async with x_model_scope():
        assert await get_x_client("drafting") is global_client
        assert await get_x_client("verification") is global_client
    assert calls == ["global"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [
    {"selection": "specific", "provider": "missing", "model": "local-model"},
    {"selection": "specific", "provider": "local", "model": ""},
    {"selection": "wrong"},
    "broken-json",
])
async def test_invalid_selection_cannot_call_global(selection, registry):
    from kazma_core.x_api.model_selection import XModelUnavailableError, get_x_client, x_model_scope

    calls, _, _ = registry
    async with x_model_scope(selection):
        with pytest.raises(XModelUnavailableError):
            await get_x_client("drafting")
    assert calls == []


@pytest.mark.asyncio
async def test_disabled_provider_cannot_be_used(registry, monkeypatch):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.model_selection import XModelUnavailableError, get_x_client, x_model_scope

    current = get_model_registry()
    monkeypatch.setattr(current, "list_providers", lambda: [{"name": "local", "enabled": False}])
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    async with x_model_scope({"selection": "specific", "provider": "local", "model": "m"}):
        with pytest.raises(XModelUnavailableError):
            await get_x_client("drafting")
    assert registry[0] == []


@pytest.mark.asyncio
async def test_concurrent_decisions_have_independent_bindings(registry):
    import asyncio

    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    async def decide(selection):
        async with x_model_scope(selection):
            await asyncio.sleep(0)
            return await get_x_client("drafting")

    local, inherited = await asyncio.gather(
        decide({"selection": "specific", "provider": "local", "model": "local-model"}),
        decide({"selection": "global"}),
    )
    assert local is registry[1]
    assert inherited is registry[2]


@pytest.mark.asyncio
async def test_preview_uses_selected_model_and_reports_actual_binding(registry, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.reply import preview_reply
    from kazma_core.x_api.stance import Subject

    calls, local, _ = registry

    async def chat(messages, **kwargs):
        return SimpleNamespace(content="A locally drafted reply.")

    local.chat = chat
    get_config_store().set("connectors.x.ai", {
        "selection": "specific", "provider": "local", "model": "local-model",
    })
    result = await preview_reply(
        parent_text="Coffee is good", subject_override=Subject(
            id="voice", match=("*",), view="Discuss coffee", mood="dry",
        ),
    )
    assert result.draft == "A locally drafted reply."
    assert result.to_dict()["models"] == [{
        "provider": "local", "model": "local-model", "roles": ["drafting", "context_verification", "verification", "factual_verification", "safety_verification"],
    }]
    assert calls == [("local", "local-model")]


@pytest.mark.asyncio
async def test_local_model_outage_does_not_use_global(registry):
    from kazma_core.x_api.model_selection import x_model_scope
    from kazma_core.x_api.reply import preview_reply
    from kazma_core.x_api.stance import Subject

    async def chat(*args, **kwargs):
        raise ConnectionError("Local server is stopped")

    registry[1].chat = chat
    async with x_model_scope({"selection": "specific", "provider": "local", "model": "local-model"}):
        result = await preview_reply(
            parent_text="Coffee", subject_override=Subject(id="voice", match=("*",), view="Talk about coffee"),
        )
    assert result.action == "failed"
    assert "Local server is stopped" in result.reason
    assert registry[0] == [("local", "local-model")]


@pytest.mark.asyncio
async def test_one_off_client_is_released_after_failed_draft(registry, monkeypatch):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    current = get_model_registry()
    released = []

    async def release(client):
        released.append(client)

    monkeypatch.setattr(current, "release_client", release, raising=False)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    with pytest.raises(RuntimeError):
        async with x_model_scope({"selection": "specific", "provider": "local", "model": "local-model"}):
            await get_x_client("drafting")
            raise RuntimeError("draft failed")
    assert released == [registry[1]]


def test_named_client_uses_environment_credentials_without_global_switch(monkeypatch):
    import kazma_core.model_registry as module

    current = module.get_model_registry()
    monkeypatch.setattr(current, "get_provider", lambda name: {
        "name": "openai", "enabled": True, "base_url": "https://api.openai.com/v1", "api_key": "",
    })
    monkeypatch.setenv("OPENAI_API_KEY", "test-env-key")
    captured = []

    class Client:
        pass

    def build(provider, config, entry):
        captured.append((provider, config.api_key, config.model))
        return Client()

    monkeypatch.setattr(module, "build_client", build)
    previous = (current._active_provider, current._active_model)
    client = current.get_client_by_provider("openai", "explicit-model")
    assert captured == [("openai", "test-env-key", "explicit-model")]
    assert current.provider_for_client(client) == "openai"
    assert (current._active_provider, current._active_model) == previous


def test_settings_model_round_trip_preserves_override_for_legacy_clients(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_core.model_registry import get_model_registry
    from kazma_ui.x_reply_api import protected_router, router

    async def no_loop():
        pass

    monkeypatch.setattr("kazma_core.x_api.mentions_fire.ensure_mentions_loop", no_loop)
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    selection = {"selection": "specific", "provider": "ollama", "model": "installed-model"}
    get_model_registry().upsert_provider({
        "name": "ollama", "enabled": True, "base_url": "http://127.0.0.1:11434/v1",
        "models": ["installed-model"],
    })
    with TestClient(app) as client:
        headers = {"X-Requested-With": "XMLHttpRequest"}
        response = client.put("/api/x/reply", json={"ai": selection}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["ai"] == selection
        response = client.put("/api/x/reply", json={}, headers=headers)
        assert response.status_code == 200, response.text
        assert client.get("/api/x/reply").json()["ai"] == selection
    assert get_config_store().get("connectors.x.ai") == selection


def test_invalid_model_save_is_atomic(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui.x_reply_api import protected_router

    store = get_config_store()
    store.set("connectors.x.reply.mode", "draft")
    app = FastAPI()
    app.include_router(protected_router)
    with TestClient(app) as client:
        response = client.put("/api/x/reply", json={
            "mode": "off", "ai": {"selection": "specific", "provider": "unknown", "model": "m"},
        }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 400
    assert store.get("connectors.x.reply.mode") == "draft"
    assert store.get("connectors.x.ai") is None


def test_model_settings_ui_uses_unsaved_binding():
    import subprocess
    from pathlib import Path

    script = Path(__file__).parent / "js" / "test_x_model_settings.js"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_standalone_draft_keeps_client_open_until_chat_finishes(registry, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.reply import draft_reply
    from kazma_core.x_api.stance import Subject

    current = get_model_registry()
    released = []

    async def release(client):
        released.append(client)

    async def chat(*args, **kwargs):
        assert released == []
        return SimpleNamespace(content="Draft from the local model.")

    registry[1].chat = chat
    monkeypatch.setattr(current, "release_client", release, raising=False)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    get_config_store().set("connectors.x.ai", {
        "selection": "specific", "provider": "local", "model": "local-model",
    })
    assert await draft_reply(
        subject=Subject(id="voice", match=("*",), view="Discuss coffee"), parent_text="Coffee",
    ) == "Draft from the local model."
    assert released == [registry[1]]


@pytest.mark.asyncio
@pytest.mark.parametrize("cached, close_fails", [(True, False), (False, False), (False, True)])
async def test_registry_release_preserves_cached_and_failed_clients(cached, close_fails):
    from kazma_core.model_registry import get_model_registry

    current = get_model_registry()
    closes = []

    class Client:
        async def close(self):
            closes.append(self)
            if close_fails:
                raise RuntimeError("close failed")

    client = current._track(Client())
    if cached:
        current._clients["test-local"] = client
    if close_fails:
        with pytest.raises(RuntimeError, match="close failed"):
            await current.release_client(client)
    else:
        await current.release_client(client)
    assert closes == ([] if cached else [client])
    assert (client in current._all_clients) == (cached or close_fails)


@pytest.mark.asyncio
async def test_preview_identity_uses_resolved_provider_instead_of_active_profile(registry, monkeypatch):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    current = get_model_registry()
    monkeypatch.setattr(current, "provider_for_client", lambda client: "fallback-provider", raising=False)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    async with x_model_scope({"selection": "global"}) as session:
        await get_x_client("drafting")
        assert session.identity == {"provider": "fallback-provider", "model": "global-model"}


async def test_role_override_is_independent_and_default_binding_is_reused(registry):
    from kazma_core.x_api.model_selection import get_x_client, x_model_scope

    settings = {"selection": "global", "roles": {
        "drafting": {"selection": "specific", "provider": "local", "model": "local-model"},
        "post_drafting": {"selection": "specific", "provider": "local", "model": "local-model"},
    }}
    async with x_model_scope(settings):
        assert await get_x_client("drafting") is registry[1]
        assert await get_x_client("post_drafting") is registry[1]
        assert await get_x_client("verification") is registry[2]
    assert registry[0] == [("local", "local-model"), "global"]


@pytest.mark.parametrize("host", ["https://api.openai.com/v1", "http://localhost.attacker.test/v1", "http://192.168.1.1/v1"])
async def test_local_only_rejects_non_loopback_before_client_creation(registry, monkeypatch, host):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.model_selection import XModelUnavailableError, get_x_client, x_model_scope

    current = get_model_registry()
    monkeypatch.setattr(current, "list_providers", lambda: [{"name": "local", "enabled": True, "base_url": host}])
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    async with x_model_scope({"selection": "specific", "provider": "local", "model": "local-model", "local_only": True}):
        with pytest.raises(XModelUnavailableError, match="loopback"):
            await get_x_client("drafting")
    assert registry[0] == []


async def test_local_only_checks_each_role_and_resolved_client(registry, monkeypatch):
    from kazma_core.model_registry import get_model_registry
    from kazma_core.x_api.model_selection import XModelUnavailableError, get_x_client, x_model_scope

    current = get_model_registry()
    monkeypatch.setattr(current, "list_providers", lambda: [
        {"name": "local", "enabled": True, "base_url": "http://127.0.0.1:11434/v1"},
        {"name": "cloud", "enabled": True, "base_url": "https://api.example.test/v1"},
    ])
    registry[1].config.base_url = "http://127.0.0.1:11434/v1"
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: current)
    settings = {"selection": "specific", "provider": "local", "model": "local-model", "local_only": True,
                "roles": {"verification": {"selection": "specific", "provider": "cloud", "model": "remote"}}}
    async with x_model_scope(settings):
        assert await get_x_client("drafting") is registry[1]
        with pytest.raises(XModelUnavailableError):
            await get_x_client("verification")
    assert registry[0] == [("local", "local-model")]


def test_unknown_role_or_nested_selection_is_rejected():
    from kazma_core.x_api.model_selection import XModelUnavailableError, validate_selection

    for settings in ({"roles": {"misspelled": {"selection": "global"}}},
                     {"roles": {"drafting": {"selection": "global", "roles": {}}}}):
        with pytest.raises(XModelUnavailableError):
            validate_selection(settings)


async def test_local_only_knowledge_retrieval_does_not_construct_semantic_index(monkeypatch):
    from kazma_core.x_api.model_selection import x_model_scope
    from kazma_core.x_api.reply import _knowledge_notes

    class Store:
        def get_library_for_tenant(self, ident, tenant):
            assert ident == "library" and tenant == "default"
            return {"id": ident, "chunk_count": 1}
        def fts_search(self, query, ident, *, limit):
            return [("chunk", -1)]
        def get_chunks_by_ids(self, ids):
            return {"chunk": {"library_id": "library", "content": "A local source.", "document_title": "Local"}}
    monkeypatch.setattr("kazma_core.stores.knowledge.get_knowledge_store", lambda: Store())
    def forbidden():
        pytest.fail("Local-only mode must not initialize a semantic/embedding path")
    monkeypatch.setattr("kazma_core.stores.knowledge_index.get_knowledge_index", forbidden)
    async with x_model_scope({"selection": "global", "local_only": True}):
        notes = await _knowledge_notes("query", library="library")
    assert notes.hit_count == 1 and "local source" in notes.notes
