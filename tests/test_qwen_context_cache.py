"""Qwen's explicit context cache holds Kazma's repeated system prompt (the
memory plan's W4 pilot, 2026-11-23).

Model Studio caches a prompt's prefix implicitly at 20 % of the input price,
when it chooses to; a ``cache_control`` marker on a message makes it cache
that prefix for five minutes, renewed on each hit, at 10 % (measured on
qwen3.8-flash with the deep pass's system prompt: the implicit cache served
1,024 of its tokens, the marked one all 1,377 of the system text from the
second call on). A call to Model Studio (``dashscope*.aliyuncs.com``,
``*.qwencloudapi.com``) of the deep pass's shape -- one system and one user
message, no tools -- on a measured model family sends a system message of at
least ``_QWEN_CACHE_MIN_CHARS`` characters as one marked text part.

Until the review of W4 (2026-11-24) every call to any Qwen model was marked:
a chat's leading system message changes within the five minutes, and each
change is a write, billed at 125 % of an input token -- which
``cost_from_usage`` did not count. ``KAZMA_PROMPT_CACHE=0`` turns the marker
off. A shorter message (under the 1,024-token minimum), any other message and
any other provider are sent as before; the control: a host that only looks
like Model Studio's gets no marker.
"""

from __future__ import annotations

import json

import httpx
import pytest
from kazma_core import llm_provider
from kazma_core.llm_provider import _QWEN_CACHE_MIN_CHARS, LLMConfig, LLMProvider, cost_from_usage

QWEN = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"
LONG = ("You extract durable facts. " * (_QWEN_CACHE_MIN_CHARS // 20)).strip()


def _provider(seen: list[dict], base_url: str, model: str = "qwen3.8-flash") -> LLMProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "qwen3.8-flash", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="demo-qwen-key", model=model))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize(("base_url", "model"), [
    (QWEN, "qwen3.8-flash"), (QWEN, "qwen3.7-plus"), (QWEN, "qwen3.8-flash-2026-10-01"),
    ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash"),
])
async def test_a_long_system_prompt_is_marked_for_the_cache(base_url, model) -> None:
    seen: list[dict] = []
    await _provider(seen, base_url, model).chat([{"role": "system", "content": LONG},
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
    assert llm_provider._with_qwen_cache_marker([], model="qwen3.8-flash") == []


@pytest.mark.asyncio
async def test_another_vendors_model_on_model_studio_is_not_marked() -> None:
    """Model Studio also serves other vendors' models, which may refuse a
    system message that is a list of parts; the cache was measured on Qwen."""
    seen: list[dict] = []
    messages = [{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}]
    await _provider(seen, QWEN, model="deepseek-v3.2").chat(messages)
    assert seen[-1]["messages"] == messages


CHAT = [{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}]
TOOLS = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.asyncio
@pytest.mark.parametrize(("messages", "model", "tools"), [
    # A chat turn: tools, an earlier answer, a second system note.
    (CHAT, "qwen3.8-flash", TOOLS),
    ([*CHAT, {"role": "assistant", "content": "Hello."}, {"role": "user", "content": "And now?"}], "qwen3.8-flash", None),
    ([CHAT[0], {"role": "system", "content": "Today is Sunday."}, CHAT[1]], "qwen3.8-flash", None),
    # A Qwen model the cache was not measured on.
    (CHAT, "qwen3.7-max", None),
    (CHAT, "qwen-plus", None),
    (CHAT, "deepseek-qwen-distill", None),
])
async def test_only_a_background_call_of_a_measured_model_is_marked(messages, model, tools) -> None:
    seen: list[dict] = []
    await _provider(seen, QWEN, model=model).chat(messages, tools=tools)
    # (Two system notes are merged into one before they are sent.)
    assert "cache_control" not in json.dumps(seen[-1]["messages"])
    assert all(isinstance(m["content"], str) for m in seen[-1]["messages"])


@pytest.mark.asyncio
async def test_the_prompt_caches_switch_turns_it_off(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PROMPT_CACHE", "0")
    seen: list[dict] = []
    await _provider(seen, QWEN).chat(CHAT)
    assert seen[-1]["messages"] == CHAT


def test_the_deep_pass_is_of_that_shape() -> None:
    """The call the cache was measured on is one the rule marks."""
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM

    marked = llm_provider._with_qwen_cache_marker(
        [{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": "This turn ..."}],
        model="qwen3.8-flash")
    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize(("usage", "usd"), [
    # 1,000 of the prompt's 3,000 tokens written to the cache, at 125 %.
    ({"prompt_tokens": 3000, "completion_tokens": 0,
      "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_input_tokens": 1000}}, 2000 + 1250),
    # ... and 1,000 read from it, at 10 %.
    ({"prompt_tokens": 3000, "completion_tokens": 0,
      "prompt_tokens_details": {"cached_tokens": 1000, "cache_creation_input_tokens": 1000}}, 1000 + 1250 + 100),
    ({"prompt_tokens": 3000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 1000}}, 2000 + 100),
    # Never more than the prompt.
    ({"prompt_tokens": 1000, "completion_tokens": 0,
      "prompt_tokens_details": {"cache_creation_input_tokens": 5000}}, 1250),
    ({"prompt_tokens": 1000, "completion_tokens": 0, "prompt_tokens_details": None}, 1000),
])
def test_a_cache_write_costs_a_quarter_more(usage, usd) -> None:
    assert cost_from_usage(usage, input_cost_per_1m=1.0, output_cost_per_1m=1.0) == pytest.approx(usd / 1_000_000)
