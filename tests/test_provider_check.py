"""Test checks a provider through the client chat uses, before saving it
(providers stage 1, 2026-11-08; kazma_core.provider_check).

Settings' Test probed every provider with OpenAI's request --
``Authorization: Bearer`` to ``/models`` and ``/chat/completions`` -- so
Anthropic, Azure and Bedrock failed Test while chat worked. And the dialog
saved the provider, typed key and all, before testing it; Save opened after
any attempt; Cancel left a refused key saved. Now one check (Settings' Test,
the dialog's check, the setup guide's check) sends one short message through
the client chat would build for the provider, says what failed as a code and
an HTTP status -- never the provider's reply -- and the dialog saves only
after it answered (or the owner says "Save anyway").
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from kazma_core import provider_adapters
from kazma_core import provider_check as pc
from kazma_core.model_registry import DiscoveryResult

ANTHROPIC_ANSWER = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-20250514",
                    "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 9, "output_tokens": 2}}


def _intercept(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    original = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def make(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return original(*args, transport=httpx.MockTransport(record), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make)
    return seen


def test_anthropic_is_checked_in_its_own_format(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=ANTHROPIC_ANSWER)
        return httpx.Response(404, json={"error": "not found"})

    seen = _intercept(monkeypatch, handler)
    entry = {"name": "anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "sk-ant-check"}
    result = asyncio.run(pc.check_model(entry, "claude-sonnet-4"))
    assert result["ok"] is True, result
    sent = seen[-1]
    assert sent.url.path == "/v1/messages"
    assert sent.headers["x-api-key"] == "sk-ant-check" and "authorization" not in sent.headers
    # The control: the removed probe's request -- Bearer to /chat/completions --
    # gets this server's 404, a working Anthropic key reported as broken.
    assert handler(httpx.Request("POST", "https://api.anthropic.com/v1/chat/completions",
                                 headers={"Authorization": "Bearer sk-ant-check"})).status_code == 404


class _Answer:
    def __init__(self, content: str = "OK", raises: BaseException | None = None) -> None:
        self.content = content
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def can_turn_off_reasoning(self) -> bool:
        return False

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(content=self.content, tool_calls=[])

    async def close(self):
        return None


def _stub(monkeypatch, client: _Answer) -> None:
    monkeypatch.setattr(provider_adapters, "build_client", lambda name, config, entry=None: client)


@pytest.mark.parametrize("raises,code,reachable", [
    (httpx.ConnectError("refused"), "unreachable", False),
    (None, "empty", True),
])
def test_a_failed_check_says_what_failed(monkeypatch, raises, code, reachable):
    client = _Answer(content="", raises=raises)
    _stub(monkeypatch, client)
    out = asyncio.run(pc.check_model({"name": "x", "base_url": "https://x.example/v1", "api_key": "k"}, "m"))
    assert (out["ok"], out["error_code"], out["reachable"]) == (False, code, reachable)
    assert client.calls[0]["max_tokens"] == pc.CHECK_MAX_TOKENS


def test_a_refusal_says_its_status_and_never_the_reply(monkeypatch):
    from kazma_core.llm_provider import LLMError

    request = httpx.Request("POST", "https://x.example/v1/chat/completions")
    refused = LLMError("LLM call failed (HTTP 404): internal-page token=secret-value", transient=False)
    refused.__cause__ = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    _stub(monkeypatch, _Answer(raises=refused))
    out = asyncio.run(pc.check_model({"name": "x", "base_url": "https://x.example/v1", "api_key": "k"}, "m"))
    assert out["error_code"] == "not_found" and "404" in out["error"]
    assert "secret-value" not in json.dumps(out) and "internal-page" not in json.dumps(out)


class _Registry:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, models: list[str] | None = None) -> None:
        self.rows = {r["name"]: dict(r) for r in rows or []}
        self.models = models
        self.upserts: list[dict[str, Any]] = []
        self.health: list[tuple[str, str]] = []
        self.listed: list[dict[str, Any]] = []

    def get_provider(self, name):
        row = self.rows.get(name)
        return dict(row) if row else None

    def resolve_provider_credentials(self, name):
        row = self.rows.get(name) or {}
        return row.get("base_url", ""), row.get("api_key", "")

    def stored_key_is_undecryptable(self, name):
        return False

    def probe_model_for(self, name):
        return (self.rows.get(name) or {}).get("model", "")

    async def discover(self, name, candidate=None):
        self.listed.append(dict(candidate or {}))
        if self.models is None:
            return DiscoveryResult.failed("unreachable", "The provider could not be reached.")
        return DiscoveryResult(True, list(self.models))

    def set_provider_health(self, name, value):
        self.health.append((name, value))

    def upsert_provider(self, data):
        self.upserts.append(dict(data))
        return dict(data)


def test_a_candidate_uses_the_typed_key_else_the_stored_one():
    stored = _Registry([{"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-stored"}])
    assert pc.candidate_entry(stored, "deepseek", api_key="sk-typed")["api_key"] == "sk-typed"
    assert pc.candidate_entry(stored, "deepseek", api_key="****")["api_key"] == "sk-stored"
    assert pc.candidate_entry(stored, "deepseek")["api_key"] == "sk-stored"
    with pytest.raises(pc.CandidateRefused) as no_key:
        pc.candidate_entry(_Registry(), "deepseek")
    assert no_key.value.code == "api_key_required"
    # A server on this network needs none, whatever it is called.
    lan = pc.candidate_entry(_Registry(), "my-vllm", base_url="http://192.168.1.20:8000/v1")
    assert lan["api_key"] == "" and lan["base_url"].startswith("http://192.168.1.20")
    with pytest.raises(pc.CandidateRefused) as unknown:
        pc.candidate_entry(_Registry(), "nope")
    assert unknown.value.code == "unknown_provider"


def _app(monkeypatch, registry: _Registry):
    import kazma_ui.providers as ui
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    activated: list[str] = []
    monkeypatch.setattr(ui, "get_model_registry", lambda: registry)
    monkeypatch.setattr(ui, "_activate_tested_provider", lambda reg, name: activated.append(name))
    app = FastAPI()
    app.include_router(ui.create_providers_router(SimpleNamespace(get=lambda *a, **k: None)))
    return TestClient(app), activated


def test_the_dialog_check_saves_nothing_and_lists_a_model_to_try(monkeypatch):
    client = _Answer()
    _stub(monkeypatch, client)
    registry = _Registry(models=["acme-chat-1", "whisper-1"])
    http, activated = _app(monkeypatch, registry)
    out = http.post("/api/providers/check", json={
        "name": "acme", "base_url": "https://api.acme.example/v1", "api_key": "sk-typed"}).json()
    assert out["success"] is True and out["chat_model"] == "acme-chat-1" and out["models"] == ["acme-chat-1"]
    assert registry.listed[0]["api_key"] == "sk-typed"
    assert registry.upserts == [] and registry.health == [] and activated == []

    refused = http.post("/api/providers/check", json={"name": "acme", "base_url": "https://api.acme.example/v1"})
    assert refused.json()["error_code"] == "api_key_required"


@pytest.mark.parametrize("raises,health,saved", [
    (None, "healthy", True),
    (httpx.ConnectError("refused"), "down", False),
])
def test_test_records_health_and_keeps_a_typed_key_only_when_it_answered(monkeypatch, raises, health, saved):
    _stub(monkeypatch, _Answer(raises=raises))
    registry = _Registry([{"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-old",
                           "model": "deepseek-chat"}])
    http, activated = _app(monkeypatch, registry)
    out = http.post("/api/providers/deepseek/test", json={"api_key": "sk-new"}).json()
    assert out["success"] is (health == "healthy")
    assert registry.health == [("deepseek", health)]
    assert (registry.upserts == [{"name": "deepseek", "api_key": "sk-new"}]) is saved
    assert (activated == ["deepseek"]) is saved


def test_a_provider_that_answers_but_not_as_a_model_is_chat_failing(monkeypatch):
    _stub(monkeypatch, _Answer(content=""))
    registry = _Registry([{"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk",
                           "model": "deepseek-chat"}])
    http, _ = _app(monkeypatch, registry)
    out = http.post("/api/providers/deepseek/test").json()
    assert out["error_code"] == "empty" and out["reachable"] is True
    assert registry.health == [("deepseek", "chat_failing")]


def test_the_dialog_saves_only_after_an_answer():
    """The page's half: Test no longer writes the provider, and Save opens on
    a successful check only (a failure offers Save anyway, behind a confirm)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
    js = (root / "static" / "js" / "settings_hub.js").read_text(encoding="utf-8")
    start = js.index("async testHubProviderFromModal()")
    body = js[start:js.index("providerCheckError(result, resp) {", start)]
    assert "/api/providers/check" in body and "method: 'POST'" in body
    assert "fetch('/api/providers'," not in body, "Test writes the provider again"
    assert "this.hubProviderTested = success;" in body
    html = (root / "templates" / "settings.html").read_text(encoding="utf-8")
    assert 'id="provider-save-anyway"' in html and "saveHubProviderAnyway()" in html
    assert html.count('@input="hubProviderTested = false"') == 4
