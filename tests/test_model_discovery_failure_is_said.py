"""A failed model discovery says it failed (FA-107).

The 2026-11 functional audit: a provider pointed at a closed port answered
discovery with the list an earlier discovery had found -- and the Gemini path
with its built-in list -- so Settings said "Found 2 models" over a provider
Kazma could not reach. ``discover_models`` returned the cache on any error and
the route had no error field.

The real registry and route, a provider on the closed port 127.0.0.1:9 (the
audit's reproduction). The page's half is ``tests/js/test_discovery_failure.js``.
The negative control is the old answer: the cached list as the result.
"""

from __future__ import annotations

import asyncio

import pytest

NAME = "fa107-closed"
OLD_LIST = ["old-model-a", "old-model-b"]


@pytest.fixture()
def registry():
    from kazma_core.model_registry import get_model_registry

    reg = get_model_registry()
    reg.upsert_provider({"name": NAME, "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-fa107",
                         "enabled": True})
    reg._discovered_models[NAME] = list(OLD_LIST)  # an earlier discovery's list
    yield reg
    reg._discovered_models.pop(NAME, None)
    reg.delete_provider(NAME)


def test_a_closed_port_is_a_failure_with_the_old_list_as_cached(registry):
    found = asyncio.run(registry.discover(NAME))
    assert found.ok is False and found.error == "unreachable", found
    assert found.models == [] and found.cached == OLD_LIST
    assert asyncio.run(registry.discover_models(NAME)) == []
    # Negative control: the old answer on this provider was the cached list,
    # which the page counted as found.
    assert found.cached == OLD_LIST != found.models


def test_the_route_answers_502_and_names_the_failure(registry):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_ui.providers import create_providers_router

    app = FastAPI()
    app.include_router(create_providers_router(get_config_store()))
    resp = TestClient(app).post(f"/api/providers/{NAME}/discover")
    assert resp.status_code == 502
    body = resp.json()
    assert body["ok"] is False and body["error"] == "unreachable" and body["count"] == 0
    assert body["cached"] == OLD_LIST
    # The earlier list is kept, labelled; nothing was persisted as found now.
    assert registry.get_discovered_models(NAME) == OLD_LIST


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, '{"error": "bad key"}', "http_401"),
    (200, "<html>proxy</html>", "bad_answer"),
    (200, '{"object": "list"}', "bad_answer"),
])
def test_an_answer_that_is_no_list_is_named(registry, monkeypatch, status, body, code):
    import httpx

    from kazma_core import model_registry

    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body.encode()))
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real(*args, transport=transport, **kwargs)

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", client)
    found = asyncio.run(registry.discover(NAME))
    assert found.ok is False and found.error == code and found.cached == OLD_LIST


def test_a_provider_that_answers_is_found(registry, monkeypatch):
    import httpx

    from kazma_core import model_registry

    answer = b'{"data": [{"id": "m-1"}, {"id": "m-2"}]}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=answer))
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real(*args, transport=transport, **kwargs)

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", client)
    found = asyncio.run(registry.discover(NAME))
    assert found.ok and found.models == ["m-1", "m-2"] and found.source == "provider"
