"""Requests that newer models accept, and images for the models that see
(providers stage 1, 2026-11-08).

OpenAI's reasoning models (o-series, GPT-5) refuse ``max_tokens`` ("Use
'max_completion_tokens' instead") and any temperature but the default; Kazma
sent both on every call, so those models could not answer at all. Now
OpenAI's own API gets ``max_completion_tokens`` from the first request, and
any endpoint that refuses one of the two fields is asked once more without
it, remembered for that endpoint and model.

The vision list made ``qwen2.5-vl-72b-instruct`` and ``llama-3.2-11b-vision-
instruct`` text-only (the ``*-instruct`` guess beat the allow-list), every
DeepSeek vision model text-only (``deepseek*``; 113 live calls to
``deepseek-v4-flash-vision-exp`` had their images replaced by a note), and
matched no id with a vendor prefix (``openai/gpt-4o``). Each rule below has
the old behaviour as its control.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from kazma_core import llm_provider as lp
from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider

MAX_TOKENS_REFUSED = json.dumps({"error": {
    "message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
               "Use 'max_completion_tokens' instead.",
    "type": "invalid_request_error", "param": "max_tokens", "code": "unsupported_parameter"}})
TEMPERATURE_REFUSED = json.dumps({"error": {
    "message": "Unsupported value: 'temperature' does not support 0.7 with this model. "
               "Only the default (1) value is supported.",
    "type": "invalid_request_error", "param": "temperature", "code": "unsupported_value"}})
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 3, "completion_tokens": 1}}


@pytest.fixture(autouse=True)
def _fresh_fixes(monkeypatch):
    monkeypatch.setattr(lp, "_PARAM_FIXES", {})


class _Endpoint:
    """A chat endpoint that refuses the fields a reasoning model refuses."""

    def __init__(self, *, refuse_max_tokens: bool = True, refuse_temperature: bool = True,
                 then: tuple[int, str] | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.refuse_max_tokens = refuse_max_tokens
        self.refuse_temperature = refuse_temperature
        self.then = then

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.refuse_max_tokens and "max_tokens" in body:
            return httpx.Response(400, text=MAX_TOKENS_REFUSED)
        if self.refuse_temperature and "temperature" in body:
            return httpx.Response(400, text=TEMPERATURE_REFUSED)
        if self.then is not None:
            return httpx.Response(self.then[0], text=self.then[1])
        return httpx.Response(200, json=ANSWER)


def _client(endpoint: _Endpoint, base_url: str = "https://gateway.example.com/v1") -> LLMProvider:
    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="sk-request-fixes", model="o3"))
    http = httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(endpoint))

    async def _get_client() -> httpx.AsyncClient:
        return http

    provider._get_client = _get_client  # type: ignore[method-assign]
    return provider


def test_a_refused_field_is_dropped_and_remembered():
    endpoint = _Endpoint()
    provider = _client(endpoint)
    resp = asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert resp.content == "ok"
    # Three requests: as sent, without max_tokens, without temperature.
    assert [("max_tokens" in b, "temperature" in b) for b in endpoint.bodies] == [
        (True, True), (False, True), (False, False)]
    assert endpoint.bodies[-1]["max_completion_tokens"] == endpoint.bodies[0]["max_tokens"]
    # The next request to this model is right the first time.
    endpoint.bodies.clear()
    asyncio.run(provider.chat([{"role": "user", "content": "again"}]))
    assert len(endpoint.bodies) == 1 and "max_tokens" not in endpoint.bodies[0]
    assert "temperature" not in endpoint.bodies[0]


def test_control_without_the_fix_the_model_never_answers(monkeypatch):
    async def none(*args, **kwargs):
        return None

    endpoint = _Endpoint()
    provider = _client(endpoint)
    monkeypatch.setattr(provider, "_retry_refused_parameters", none)
    with pytest.raises(LLMError):
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))


def test_openai_gets_max_completion_tokens_from_the_first_request():
    endpoint = _Endpoint(refuse_temperature=False)
    provider = _client(endpoint, base_url="https://api.openai.com/v1")
    asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert len(endpoint.bodies) == 1 and "max_completion_tokens" in endpoint.bodies[0]
    # Another OpenAI-compatible endpoint keeps the field it was sent.
    other = provider._chat_payload([{"role": "user", "content": "x"}], None, None, None, "some-model")
    assert "max_tokens" in _client(_Endpoint())._chat_payload(
        [{"role": "user", "content": "x"}], None, None, None, "m")
    assert "max_completion_tokens" in other


def test_a_refusal_for_another_reason_after_a_fix_is_raised():
    endpoint = _Endpoint(refuse_temperature=False, then=(400, '{"error":{"message":"model not found"}}'))
    provider = _client(endpoint)
    with pytest.raises(LLMError, match="model not found"):
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert len(endpoint.bodies) == 2  # never a loop


@pytest.mark.parametrize("status,detail,fix", [
    (400, MAX_TOKENS_REFUSED, "max_completion_tokens"),
    (400, TEMPERATURE_REFUSED, "no_temperature"),
    (400, '{"error":{"message":"temperature must be between 0 and 2"}}', None),
    (500, MAX_TOKENS_REFUSED, None),
    (400, '{"error":{"message":"tools: maximum number of items is 128"}}', None),
])
def test_only_a_refused_field_is_fixed(status, detail, fix):
    assert lp._refused_parameter(status, detail) == fix


# ── vision ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model,kind", [
    ("qwen2.5-vl-72b-instruct", "vision"),
    ("meta-llama/llama-3.2-11b-vision-instruct", "vision"),
    ("deepseek-v4-flash-vision-exp", "vision"),
    ("deepseek-vl2", "vision"),
    ("openai/gpt-4o", "vision"),
    ("gpt-5", "vision"),
    ("o3", "vision"),
    ("o4-mini", "vision"),
    ("grok-4", "vision"),
    ("claude-opus-5-5", "vision"),
    ("deepseek-chat", "text"),
    ("o3-mini", "text"),
    ("mistral-7b-instruct", "text"),
    ("some-new-model", "unknown"),
])
def test_a_model_is_classed_in_one_order(model, kind):
    from kazma_core.vision_capability import classify

    assert classify(model) == kind


def test_control_the_old_order_made_vision_models_text_only():
    import fnmatch

    from kazma_core import vision_capability as vc

    old_deny = (*vc.TEXT_ONLY_PATTERNS, *vc.TEXT_ONLY_GUESSES)
    for model in ("qwen2.5-vl-72b-instruct", "deepseek-v4-flash-vision-exp"):
        assert any(fnmatch.fnmatch(model, p) for p in old_deny)  # the old rule: text-only
        assert vc.is_vision_capable(model)


def test_a_network_failure_on_the_retry_is_a_transient_llm_error():
    calls = {"n": 0}

    def endpoint(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, text=MAX_TOKENS_REFUSED)
        raise httpx.ConnectError("connection refused", request=request)

    provider = LLMProvider(LLMConfig(base_url="https://gateway.example.com/v1", api_key="sk-x", model="o3"))
    http = httpx.AsyncClient(base_url="https://gateway.example.com/v1", transport=httpx.MockTransport(endpoint))

    async def _get_client() -> httpx.AsyncClient:
        return http

    provider._get_client = _get_client  # type: ignore[method-assign]
    with pytest.raises(LLMError) as raised:
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert raised.value.transient is True
