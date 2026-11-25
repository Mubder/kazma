"""Cached input has its own price, for every provider's usage shape.

``cost_from_usage`` read two of them: DeepSeek's ``prompt_cache_hit_tokens``
were billed at the full input price, Anthropic's cached tokens (left out of
its ``input_tokens``) were taken out of the uncached ones, and Bedrock's were
not read at all. The owner's price takes a cached-input price.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from kazma_core.llm_provider import LLMConfig, LLMProvider, cost_from_usage
from kazma_core.settings_validation import SettingRejected, _model_prices
from kazma_core.token_usage import cache_write_tokens, token_counts

M = 1_000_000

#: (shape, usage, uncached, cached, written, completion)
SHAPES = [
    ("openai", {"prompt_tokens": 1000, "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 800}}, 200, 800, 0, 10),
    ("deepseek", {"prompt_tokens": 1000, "completion_tokens": 10,
                  "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200}, 200, 800, 0, 10),
    ("qwen explicit cache", {"prompt_tokens": 1000, "completion_tokens": 10,
                             "prompt_tokens_details": {"cached_tokens": 500, "cache_creation_input_tokens": 300}},
     200, 500, 300, 10),
    ("moonshot", {"prompt_tokens": 1000, "completion_tokens": 10, "cached_tokens": 800}, 200, 800, 0, 10),
    ("openai responses", {"input_tokens": 1000, "output_tokens": 10,
                          "input_tokens_details": {"cached_tokens": 800}}, 200, 800, 0, 10),
    ("anthropic", {"input_tokens": 200, "output_tokens": 10,
                   "cache_read_input_tokens": 800, "cache_creation_input_tokens": 300}, 200, 800, 300, 10),
    ("a proxy before anthropic", {"prompt_tokens": 1000, "completion_tokens": 10,
                                  "cache_read_input_tokens": 800}, 200, 800, 0, 10),
    ("no cache", {"prompt_tokens": 1000, "completion_tokens": 10}, 1000, 0, 0, 10),
]


def _old_cost(usage: dict, inp: float, outp: float) -> float:
    """The rule until 2026-11-25 (the control)."""
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or details.get("cache_read_input_tokens") or 0)
    cached = cached or int(usage.get("cache_read_input_tokens") or usage.get("cached_tokens") or 0)
    cached = min(cached, prompt)
    written = min(int(details.get("cache_creation_input_tokens") or 0), prompt - cached)
    return ((prompt - cached - written) * inp + written * inp * 1.25 + cached * inp * 0.1 + completion * outp) / M


@pytest.mark.parametrize(("shape", "usage", "fresh", "cached", "written", "completion"), SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_every_shape_bills_the_cached_tokens_at_the_cached_price(shape, usage, fresh, cached, written, completion):
    cost = cost_from_usage(usage, input_cost_per_1m=1.0, output_cost_per_1m=4.0, cached_input_cost_per_1m=0.3)
    expected = (fresh * 1.0 + cached * 0.3 + written * 1.25 + completion * 4.0) / M
    assert cost == pytest.approx(expected)
    # No cached price known: a tenth of the input price, as before.
    plain = cost_from_usage(usage, input_cost_per_1m=1.0, output_cost_per_1m=4.0)
    assert plain == pytest.approx((fresh + cached * 0.1 + written * 1.25 + completion * 4.0) / M)
    counts = token_counts(usage)
    assert (counts.prompt, counts.cached, cache_write_tokens(usage)) == (fresh + cached + written, cached, written)


def test_the_old_rule_was_wrong_for_deepseek_and_anthropic():
    usage = dict((s[0], s[1]) for s in SHAPES)
    new = {name: cost_from_usage(u, input_cost_per_1m=1.0, output_cost_per_1m=4.0) for name, u in usage.items()}
    wrong = {name for name, u in usage.items() if _old_cost(u, 1.0, 4.0) != pytest.approx(new[name])}
    assert wrong == {"deepseek", "anthropic"}
    assert _old_cost(usage["deepseek"], 1.0, 4.0) > new["deepseek"]
    assert _old_cost(usage["anthropic"], 1.0, 4.0) < new["anthropic"]


def test_the_owners_cached_price_reaches_the_call(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(db_path=str(tmp_path / "s.db"))
    try:
        store.set("models.prices", _model_prices(
            {"deepseek-chat": {"input": 0.28, "output": 0.42, "cached_input": 0.028}}), category="models")
        registry = ModelRegistry(store)
        registry.upsert_provider({"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                  "api_key": "demo-key", "models": ["deepseek-chat"], "enabled": True})
        client = registry.get_client("deepseek-chat")
        assert client.config.cached_input_cost_per_1m == 0.028
        cost, known = client._call_cost({"prompt_tokens": M, "completion_tokens": 0, "prompt_cache_hit_tokens": M})
        assert known and cost == pytest.approx(0.028)
    finally:
        store.close()


def test_a_cached_price_out_of_range_is_refused_and_an_empty_one_is_none():
    saved = _model_prices({"m": {"input": 1, "output": 2, "cached_input": ""}})
    assert saved == {"m": {"input": 1.0, "output": 2.0}}
    assert _model_prices({"m": {"input": 1, "output": 2, "cached_input": 0}})["m"]["cached_input"] == 0.0
    for bad in (-1, 5000, "x", True):
        with pytest.raises(SettingRejected):
            _model_prices({"m": {"input": 1, "output": 2, "cached_input": bad}})


def test_bedrock_bills_its_cache():
    from kazma_core.bedrock_llm import BedrockProvider

    client = BedrockProvider(LLMConfig(model="m", input_cost_per_1m=1.0, output_cost_per_1m=4.0,
                                       cached_input_cost_per_1m=0.1), region="us-east-1")
    answer = client._parse({"output": {"message": {"content": [{"text": "hi"}]}}, "stopReason": "end_turn",
                            "usage": {"inputTokens": 200, "outputTokens": 10, "cacheReadInputTokens": 800,
                                      "cacheWriteInputTokens": 300}}, "m", 0.0)
    assert answer.cost_usd == pytest.approx((200 + 800 * 0.1 + 300 * 1.25 + 40) / M)
    assert token_counts(answer.usage).cached == 800


def test_openrouters_list_states_the_cached_price():
    from kazma_core.model_details import from_listing

    listed = from_listing({"id": "x", "pricing": {"prompt": "0.000001", "completion": "0.000004",
                                                  "input_cache_read": "0.0000001"}})
    assert listed["cached_input_per_1m"] == pytest.approx(0.1)


# ── Every place that prices a call passes the cached price ──────────────


def _calls_without_the_cached_price(source: str) -> list[int]:
    """The calls of ``cost_from_usage``, by name or as an attribute, with no
    cached price named. Not read: a call through another name or a partial, a
    price computed with no call of it."""
    return [n.lineno for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "cost_from_usage"
            and not any(k.arg == "cached_input_cost_per_1m" for k in n.keywords)]


def test_every_call_of_the_cost_rule_passes_the_cached_price():
    root = Path(inspect.getfile(LLMProvider)).parent
    files = [p for p in sorted(root.rglob("*.py")) if "cost_from_usage(" in p.read_text(encoding="utf-8")]
    assert len(files) >= 3  # the OpenAI-shaped, Anthropic and Bedrock clients
    bad = {p.name: lines for p in files if (lines := _calls_without_the_cached_price(p.read_text(encoding="utf-8")))}
    assert bad == {}


def test_the_gate_sees_a_call_without_it():
    planted = "cost_from_usage(u, input_cost_per_1m=1, output_cost_per_1m=2)\n"
    assert _calls_without_the_cached_price(planted) == [1]
