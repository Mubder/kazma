"""Kazma's "reasoning off" reaches Qwen, and the intent classifier asks it.

The memory plan's finding 12 (22 Nov 2026): on a gray-zone request
``agent/intent/classify._refine_acts_llm`` asks the chat model to classify
it within 4 s, with no reasoning switch. qwen3.7-plus thinks by default, and
in 0 of 16 dev turns it answered in time: every one waited the whole 4 s for
nothing. Kazma's switch reached DeepSeek's API only.

Now ``reasoning="off"`` reaches Qwen on Alibaba Cloud Model Studio (any
region's address: ``dashscope*.aliyuncs.com``, ``*.qwencloudapi.com``) as
``enable_thinking: false``, the top-level field Model Studio documents
(https://www.alibabacloud.com/help/en/model-studio/deep-thinking). No effort
level is documented for Qwen, so ``low`` / ``high`` / ``max`` are sent as
nothing (the chat's level on Qwen does what it did). A model that refuses
the field (a model that only thinks) is asked again without it. The
classifier asks for "off" wherever the client takes it, DeepSeek too.

The controls: today's classifier (no switch asked) sends nothing; a host
that only looks like Model Studio's takes no switch.
"""

from __future__ import annotations

import json

import httpx
import pytest
from kazma_core.agent.intent import classify
from kazma_core.agent.intent.types import IntentAct
from kazma_core.llm_provider import LLMConfig, LLMProvider

QWEN_HOSTS = ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
              "https://dashscope.aliyuncs.com/compatible-mode/v1",
              "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1")
DEEPSEEK = "https://api.deepseek.com/v1"
_REPLY = '{"acts": [{"kind": "document_generate", "confidence": 0.9, "slots": {}}]}'


def _provider(seen: list[dict], base_url: str, *, refuse_switch: bool = False,
              content: str = "Done.") -> LLMProvider:
    """An LLMProvider whose HTTP goes to a scripted endpoint at *base_url*."""

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if refuse_switch and "enable_thinking" in payload:
            return httpx.Response(400, json={"error": {
                "message": "The value of the enable_thinking parameter is restricted to True."}})
        return httpx.Response(200, json={
            "model": "qwen3.7-plus",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="demo-qwen-key", model="qwen3.7-plus"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


def _switches(payload: dict) -> dict:
    return {k: payload[k] for k in ("enable_thinking", "thinking", "reasoning_effort") if k in payload}


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", QWEN_HOSTS)
@pytest.mark.parametrize(("level", "expected"), [
    (None, {}), ("off", {"enable_thinking": False}), ("low", {}), ("high", {}), ("max", {}),
])
async def test_off_reaches_qwen_and_no_level_does(base_url, level, expected) -> None:
    seen: list[dict] = []
    provider = _provider(seen, base_url)
    assert provider.can_turn_off_reasoning() is True
    await provider.chat([{"role": "user", "content": "hi"}], reasoning=level)
    assert _switches(seen[-1]) == expected


@pytest.mark.parametrize("base_url", [
    "https://dashscope.aliyuncs.com.example.net/v1", "https://example.net/dashscope/v1",
    "https://aliyuncs.com/v1", "https://notqwencloudapi.com/v1", "https://api.openai.com/v1",
])
def test_control_a_host_that_only_looks_like_model_studio_takes_no_switch(base_url) -> None:
    assert _provider([], base_url).can_turn_off_reasoning() is False


@pytest.mark.asyncio
async def test_a_model_that_refuses_the_switch_is_asked_again_without_it() -> None:
    seen: list[dict] = []
    reply = await _provider(seen, QWEN_HOSTS[0], refuse_switch=True).chat(
        [{"role": "user", "content": "hi"}], reasoning="off")
    assert reply.content == "Done."
    assert [_switches(p) for p in seen] == [{"enable_thinking": False}, {}]


# ── the intent classifier, in both languages ─────────────────────────────

_GRAY = {
    "en": "I need the contract cleaned up and maybe a summary, not sure which first.",
    "ar": "أبي العقد مرتب ويمكن ملخص، مو متأكد أيهم أول.",
}
_HEURISTIC = (IntentAct(kind="general", confidence=0.4, slots={}, source="heuristic"),)


async def _refine(language: str, base_url: str) -> tuple[tuple[IntentAct, ...], list[dict]]:
    seen: list[dict] = []
    acts = await classify._refine_acts_llm(_GRAY[language], _provider(seen, base_url, content=_REPLY),
                                           _HEURISTIC)
    return acts, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "ar"])
@pytest.mark.parametrize(("base_url", "switch"), [
    (QWEN_HOSTS[0], {"enable_thinking": False}), (QWEN_HOSTS[2], {"enable_thinking": False}),
    (DEEPSEEK, {"thinking": {"type": "disabled"}}),
])
async def test_the_classifier_asks_for_no_reasoning(language, base_url, switch) -> None:
    acts, seen = await _refine(language, base_url)
    assert [_switches(p) for p in seen] == [switch]
    assert [(a.kind, a.source) for a in acts] == [("document_generate", "llm")]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "ar"])
async def test_control_todays_classifier_asks_nothing(language, monkeypatch) -> None:
    """The classifier as it was: no switch asked, so a thinking model thinks."""
    from kazma_core.agent import chat_reasoning

    monkeypatch.setattr(chat_reasoning, "_reasoning_kwargs_for", lambda client, level: {})
    _acts, seen = await _refine(language, QWEN_HOSTS[0])
    assert [_switches(p) for p in seen] == [{}]


@pytest.mark.asyncio
async def test_a_client_without_the_switch_is_asked_as_before() -> None:
    """A client with a chat of its own (no ``can_turn_off_reasoning``) gets
    no argument it does not take."""

    class _Own:
        def __init__(self) -> None:
            self.kwargs: list[dict] = []

        async def chat(self, messages, tools=None):  # takes no ``reasoning``
            self.kwargs.append({"tools": tools})
            return type("R", (), {"content": _REPLY})()

    own = _Own()
    acts = await classify._refine_acts_llm(_GRAY["en"], own, _HEURISTIC)
    assert own.kwargs == [{"tools": None}] and acts[0].source == "llm"
