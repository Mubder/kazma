"""X model calls turn a thinking model's hidden reasoning off.

Live on 2026-10-26, every X Studio draft's evidence check failed as "Verifier
did not return valid JSON". The truncation warning, given its detail that day,
read: completion_tokens=1800, reasoning_tokens=1800, content_chars=0,
reasoning_chars=8762. deepseek-flash thinks by default at high effort, and the
reasoning counts against max_tokens, so the verifier's 1,800 tokens went to
hidden reasoning and no answer came back. The provider's retry at 3,600
sometimes ran out too.

Every X call is a budgeted structured judgment sized for its answer, so
``x_chat`` asks for ``reasoning="off"`` wherever the endpoint has a switch,
and ``LLMProvider`` sends DeepSeek's ``thinking: {"type": "disabled"}`` to
DeepSeek's own API only. A reply still cut off is said as cut off, not as
invalid JSON.

The fake DeepSeek below behaves as the live one did; the negative control
runs the same draft with the switch unavailable (the code before this change)
and the evidence check comes back unjudged.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import httpx
import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider

DEEPSEEK = "https://api.deepseek.com/v1"
DRAFT = "Kazma is a self-hosted AI agent that asks before it acts."


def _answer(payload: dict) -> str:
    system = str(payload["messages"][0]["content"])
    if "Write X post alternatives" in system:
        return json.dumps({"drafts": [DRAFT]})
    names = re.findall(r'"check": "(\w+)"', system)
    claim = {"text": DRAFT, "kind": "opinion", "status": "opinion", "source_ids": []}
    return json.dumps({"checks": [{"check": name, "verdict": "pass", "reason": "Judged.",
                                   "evidence": DRAFT, "source_ids": [],
                                   "claims": [claim] if name == "evidence" else []}
                                  for name in names]})


def fake_deepseek(seen: list[dict]):
    """Thinks unless told not to; its reasoning counts against max_tokens."""

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        verifying = "Independently verify" in str(payload["messages"][0]["content"])
        if verifying and payload.get("thinking") != {"type": "disabled"}:
            cap = payload["max_tokens"]
            return httpx.Response(200, json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "", "reasoning_content": "Let me think. " * 600},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 5000, "completion_tokens": cap,
                          "completion_tokens_details": {"reasoning_tokens": cap}}})
        return httpx.Response(200, json={
            "model": "deepseek-flash",
            "choices": [{"message": {"content": _answer(payload)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5000, "completion_tokens": 120}})

    return handle


def deepseek_provider(seen: list[dict]) -> LLMProvider:
    provider = LLMProvider(LLMConfig(base_url=DEEPSEEK, api_key="demo-deepseek-key", model="deepseek-flash"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_deepseek(seen)), base_url=DEEPSEEK)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


@pytest.fixture
def studio(monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.stance import ReplyConfig

    seen: list[dict] = []

    class Registry:
        def list_providers(self):
            return [{"name": "deepseek", "base_url": DEEPSEEK}]

        def get_client_by_provider(self, provider, model):
            return deepseek_provider(seen)

        def get_active_profile(self):
            return {"provider": "deepseek", "model": "deepseek-flash"}

        def get_client(self):
            return deepseek_provider(seen)

        async def release_client(self, client):
            pass

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())
    cfg = ReplyConfig(enabled=False, mode="draft", summoners=(), trigger="", max_replies_per_day=5,
                      max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                      poll_interval_s=60)
    monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: cfg)
    get_config_store().set("connectors.x.ai", {"selection": "specific", "provider": "deepseek",
                                               "model": "deepseek-flash"})
    return SimpleNamespace(seen=seen)


def evidence_reason(result) -> str:
    return next(check["reason"] for check in result.reviews[0]["checks"] if check["check"] == "evidence")


async def test_every_x_call_to_deepseek_turns_thinking_off_and_the_checks_judge(studio):
    from kazma_core.x_api.post_drafting import draft_posts

    result = await draft_posts("Introduce Kazma to developers in two short sentences.")
    assert result.drafts == (DRAFT,)
    assert studio.seen and all(p.get("thinking") == {"type": "disabled"} for p in studio.seen)
    verdicts = {c["check"]: c["verdict"] for c in result.reviews[0]["checks"]}
    assert verdicts["evidence"] == "pass" and verdicts["safety"] == "pass", result.reviews[0]["checks"]


async def test_negative_control_with_thinking_on_the_evidence_check_is_cut_off(studio, monkeypatch):
    from kazma_core.x_api.post_drafting import draft_posts

    # The code before this change: no switch, the model's own default.
    monkeypatch.setattr(LLMProvider, "can_turn_off_reasoning", lambda self: False)
    result = await draft_posts("Introduce Kazma to developers in two short sentences.")
    assert not any("thinking" in p for p in studio.seen)
    reason = evidence_reason(result)
    assert "cut off at its token limit" in reason
    assert "valid JSON" not in reason  # the misleading words the live check showed


def test_the_switch_reaches_deepseek_only():
    deepseek = LLMProvider(LLMConfig(base_url=DEEPSEEK, api_key="demo-deepseek-key", model="deepseek-flash"))
    openai = LLMProvider(LLMConfig(base_url="https://api.openai.com/v1", api_key="demo-openai-key", model="gpt-4o-mini"))
    assert deepseek.can_turn_off_reasoning() is True
    assert openai.can_turn_off_reasoning() is False
    messages = [{"role": "user", "content": "hi"}]
    assert deepseek._chat_payload(messages, None, 10, 0.0, None, None, "off")["thinking"] == {"type": "disabled"}
    assert "thinking" not in deepseek._chat_payload(messages, None, 10, 0.0, None, None, None)
    assert "thinking" not in openai._chat_payload(messages, None, 10, 0.0, None, None, "off")
    evil = LLMProvider(LLMConfig(base_url="https://api.deepseek.com.example.net/v1", api_key="demo-key", model="m"))
    assert evil.can_turn_off_reasoning() is False


def test_providers_with_their_own_chat_are_never_sent_the_switch():
    from kazma_core.anthropic_llm import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.config = LLMConfig(base_url=DEEPSEEK, api_key="demo-key", model="m")
    provider._via_gateway = False
    assert provider.can_turn_off_reasoning() is False


async def test_a_deepseek_model_that_refuses_the_switch_answers_without_it():
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if "thinking" in payload:
            return httpx.Response(400, json={"error": {"message": "Unknown parameter: thinking"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                         "usage": {}})

    provider = LLMProvider(LLMConfig(base_url=DEEPSEEK, api_key="demo-deepseek-key", model="deepseek-chat"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=DEEPSEEK)

    async def get_client():
        return client

    provider._get_client = get_client
    response = await provider.chat([{"role": "user", "content": "hi"}], max_tokens=10, reasoning="off")
    assert response.content == "ok"
    assert [("thinking" in p) for p in seen] == [True, False]


def test_a_truncation_warning_says_what_the_tokens_went_to():
    provider = LLMProvider(LLMConfig(base_url=DEEPSEEK, api_key="demo-deepseek-key", model="deepseek-flash"))
    response = provider._parse_response({
        "choices": [{"message": {"content": "", "reasoning_content": "x" * 8762}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 1800, "completion_tokens_details": {"reasoning_tokens": 1800}}}, 1.0)
    assert response.output_detail() == ("completion_tokens=1800, reasoning_tokens=1800, "
                                         "content_chars=0, reasoning_chars=8762")
