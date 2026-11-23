"""Qwen's explicit context cache holds Kazma's repeated system prompt (the
memory plan's W4 pilot, 2026-11-23).

Model Studio caches a prompt's prefix implicitly at 20 % of the input price,
when it chooses to; a ``cache_control`` marker on a message makes it cache
that prefix for five minutes, renewed on each hit, at 10 % (measured on
qwen3.8-flash with the deep pass's system prompt: the implicit cache served
1,024 of its tokens, the marked one all 1,377 of the system text from the
second call on). Every OpenAI-compatible call to Model Studio
(``dashscope*.aliyuncs.com``, ``*.qwencloudapi.com``) now sends a leading
system message of at least ``_QWEN_CACHE_MIN_CHARS`` characters as one
marked text part; a shorter one (under the 1,024-token minimum), any other
message and any other provider are sent as before. The control: a host that
only looks like Model Studio's gets no marker.
"""

from __future__ import annotations

import json

import httpx
import pytest
from kazma_core import llm_provider
from kazma_core.llm_provider import _QWEN_CACHE_MIN_CHARS, LLMConfig, LLMProvider

QWEN = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"
LONG = ("You extract durable facts. " * (_QWEN_CACHE_MIN_CHARS // 20)).strip()


def _provider(seen: list[dict], base_url: str) -> LLMProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "qwen3.8-flash", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="demo-qwen-key", model="qwen3.8-flash"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [QWEN, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"])
async def test_a_long_system_prompt_is_marked_for_the_cache(base_url) -> None:
    seen: list[dict] = []
    await _provider(seen, base_url).chat([{"role": "system", "content": LONG},
                                          {"role": "user", "content": "I went to Porto last week."}])
    system, user = seen[-1]["messages"]
    assert system == {"role": "system",
                      "content": [{"type": "text", "text": LONG, "cache_control": {"type": "ephemeral"}}]}
    assert user == {"role": "user", "content": "I went to Porto last week."}


@pytest.mark.asyncio
@pytest.mark.parametrize(("base_url", "messages"), [
    (QWEN, [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]),
    (QWEN, [{"role": "user", "content": LONG}]),
    ("https://api.deepseek.com/v1", [{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}]),
    ("https://api.openai.com/v1", [{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}]),
    # The control: a host that only looks like Model Studio's.
    ("https://dashscope.aliyuncs.com.example.net/v1", [{"role": "system", "content": LONG},
                                                       {"role": "user", "content": "hi"}]),
])
async def test_nothing_else_is_marked(base_url, messages) -> None:
    seen: list[dict] = []
    await _provider(seen, base_url).chat(messages)
    assert seen[-1]["messages"] == messages


def test_the_stream_builds_the_same_body() -> None:
    """The blocking and the streaming call build their body in ``_chat_payload``."""
    provider = LLMProvider(LLMConfig(base_url=QWEN, api_key="demo-qwen-key", model="qwen3.8-flash"))
    payload = provider._chat_payload([{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}],
                                     None, None, None, None)
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    import inspect

    for method in (LLMProvider._chat_inner, LLMProvider._chat_stream_once):
        assert "self._chat_payload" in inspect.getsource(method)
    assert llm_provider._with_qwen_cache_marker([]) == []
