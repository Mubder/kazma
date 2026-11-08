"""A model call's cost is its own price, or said to be unknown (providers
stage 1, 2026-11-08, kazma_core.model_prices).

Every OpenAI-compatible call was priced at gpt-4o-mini's $0.15 / $0.60 per
million tokens (``LLMConfig``'s default), and Anthropic's and Bedrock's
tables answered $3 / $15 for any model they did not name exactly. On the live
install 8,839 calls, all DeepSeek and Qwen, were priced at $50.05 that way,
and every ledger row named the client class ("LLMProvider"), never the
provider. Each rule below has the old behaviour as its control.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core import model_prices as mp
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.observability import llm_ledger

USAGE = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}


def _parse(provider: LLMProvider, usage: dict[str, Any] | None = None):
    data = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": dict(usage if usage is not None else USAGE)}
    return provider._parse_response(data, 1.0)


class _Store:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


# ── the generic client ──────────────────────────────────────────────────


def test_a_call_with_no_known_price_is_unknown_not_gpt_4o_mini():
    resp = _parse(LLMProvider(LLMConfig(base_url="https://api.deepseek.com/v1", api_key="k", model="deepseek-chat")))
    assert resp.cost_known is False and resp.cost_usd == 0.0
    # The control: the old default priced the same call as gpt-4o-mini.
    old = _parse(LLMProvider(LLMConfig(base_url="https://api.deepseek.com/v1", api_key="k", model="deepseek-chat",
                                       input_cost_per_1m=0.15, output_cost_per_1m=0.60)))
    assert old.cost_known is True and old.cost_usd == pytest.approx(0.75)


def test_a_configured_price_is_used():
    resp = _parse(LLMProvider(LLMConfig(base_url="https://api.deepseek.com/v1", api_key="k",
                                        input_cost_per_1m=0.27, output_cost_per_1m=1.10)))
    assert resp.cost_known is True and resp.cost_usd == pytest.approx(1.37)


def test_openrouter_reports_what_the_call_cost():
    client = LLMProvider(LLMConfig(base_url="https://openrouter.ai/api/v1", api_key="k"))
    resp = _parse(client, {**USAGE, "cost": 0.0123})
    assert resp.cost_known is True and resp.cost_usd == pytest.approx(0.0123)
    # Another provider's "cost" field is not read: its unit is not documented.
    other = _parse(LLMProvider(LLMConfig(base_url="https://api.example.com/v1", api_key="k")), {**USAGE, "cost": 9})
    assert other.cost_known is False and other.cost_usd == 0.0


# ── the price resolver ───────────────────────────────────────────────────


def test_the_owners_price_wins_by_provider_and_model_then_model():
    store = _Store({mp.OWNER_PRICES_KEY: {
        "deepseek-chat": {"input": 0.27, "output": 1.1},
        "openrouter:deepseek-chat": {"input": 0.3, "output": 1.2},
        "broken": {"input": "x", "output": 1},
    }})
    assert mp.price_for("deepseek", "https://api.deepseek.com/v1", "DeepSeek-Chat", store=store) == mp.Price(0.27, 1.1, "owner")
    assert mp.price_for("openrouter", "https://openrouter.ai/api/v1", "deepseek-chat", store=store).input_per_1m == 0.3
    assert mp.price_for("x", "https://api.example.com", "broken", store=store) is None
    assert mp.price_for("x", "https://api.example.com", "unknown-model", store=_Store()) is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://192.168.1.20:8000/v1",
    "http://10.0.0.5/v1", "http://gpu-box:8080/v1", "http://llm.local:8000/v1", "http://host.docker.internal:8000/v1",
    "http://100.101.102.103:8000/v1", "http://[fd00::1]:8000/v1",
])
def test_a_server_on_this_network_is_free(url):
    assert mp.price_for("vllm", url, "qwen2.5-32b", store=_Store()) == mp.Price(0.0, 0.0, "local")


@pytest.mark.parametrize("url", ["https://api.deepseek.com/v1", "https://8.8.8.8/v1", "https://openrouter.ai/api/v1"])
def test_a_cloud_server_is_not_free(url):
    assert mp.price_for("x", url, "m", store=_Store()) is None


def test_a_table_names_a_dated_id_and_never_a_newer_version():
    table = {"claude-opus-4": (15.0, 75.0), "claude-sonnet-4": (3.0, 15.0), "llama3-1-70b": (0.99, 0.99)}
    assert mp.table_price(table, "claude-sonnet-4-20250514") == (3.0, 15.0)
    assert mp.table_price(table, "claude-opus-4@20250514") == (15.0, 75.0)
    assert mp.table_price(table, "us.anthropic.claude-sonnet-4-20250514-v1:0") == (3.0, 15.0)
    assert mp.table_price(table, "meta.llama3-1-70b-instruct-v1:0") == (0.99, 0.99)
    # Opus 4.5 costs a third of Opus 4: a prefix match would charge it 3x.
    assert mp.table_price(table, "claude-opus-4-5") is None
    assert mp.table_price(table, "claude-haiku-4-5") is None
    # The control: a plain prefix match takes Opus 4.5 for Opus 4.
    assert next(n for n in table if "claude-opus-4-5".startswith(n)) == "claude-opus-4"


def test_anthropic_prices_an_unlisted_model_as_unknown_not_sonnet():
    from kazma_core.anthropic_llm import AnthropicProvider

    client = AnthropicProvider(LLMConfig(api_key="k", model="claude-opus-5-5"))
    cost, known = client._anthropic_cost("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert (cost, known) == (0.0, False)
    dated = client._anthropic_cost("claude-sonnet-4-20250514", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert dated == (pytest.approx(3.0), True)
    owner = AnthropicProvider(LLMConfig(api_key="k", input_cost_per_1m=5.0, output_cost_per_1m=25.0))
    assert owner._anthropic_cost("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 0}) == (
        pytest.approx(5.0), True)


# ── the registry ─────────────────────────────────────────────────────────


def test_the_registry_builds_clients_with_the_owners_price_and_rebuilds_on_a_change(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.set("providers.list", [{"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                      "api_key": "sk-prices-test", "models": ["deepseek-chat"], "enabled": True}],
                  category="providers")
        store.batch_set([("registry.active_provider", "deepseek", "registry"),
                         ("registry.active_model", "deepseek-chat", "registry")])
        registry = ModelRegistry(store)
        first = registry.get_client()
        assert first.config.input_cost_per_1m is None  # no price: unknown
        store.set(mp.OWNER_PRICES_KEY, {"deepseek-chat": {"input": 0.27, "output": 1.1}}, category="models")
        second = registry.get_client()
        assert second is not first, "a price change must rebuild the cached client"
        assert (second.config.input_cost_per_1m, second.config.output_cost_per_1m) == (0.27, 1.1)
    finally:
        store.close()


# ── the ledger ───────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    yield llm_ledger
    conn.close()


def test_every_sum_counts_the_calls_with_no_known_price(ledger):
    ledger.record_llm_call(thread_id="t", turn_id="u", provider="deepseek", prompt_tokens=10, cost_usd=0.0, priced=False)
    ledger.record_llm_call(thread_id="t", turn_id="u", provider="anthropic", prompt_tokens=10, cost_usd=0.5)
    assert ledger.turn_usage("u") == {"calls": 2, "tokens": 20, "cost": 0.5, "unpriced": 1}
    assert ledger.thread_usage("t")["unpriced"] == 1
    from datetime import UTC, datetime, timedelta

    assert ledger.usage_since(datetime.now(UTC) - timedelta(hours=1))["unpriced"] == 1
    assert ledger.unpriced_models(30) == []  # a call with no model name is not one to price


def test_the_models_with_no_known_price_are_listed(ledger):
    for _ in range(3):
        ledger.record_llm_call(provider="deepseek", model="deepseek-flash", priced=False)
    ledger.record_llm_call(provider="openrouter", model="google/gemini-2.5-flash", cost_usd=0.01)
    rows = ledger.unpriced_models(30)
    assert [(r["model"], r["calls"]) for r in rows] == [("deepseek-flash", 3)]


def test_old_rows_priced_by_the_borrowed_default_become_unknown(tmp_path, monkeypatch):
    path = tmp_path / "llm_calls.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, thread_id TEXT DEFAULT '',
            iteration INTEGER DEFAULT 0, provider TEXT DEFAULT '', model TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0, duration_ms REAL DEFAULT 0.0, status TEXT DEFAULT 'ok',
            error_kind TEXT DEFAULT '', failover_from TEXT DEFAULT '', turn_id TEXT DEFAULT '');
        INSERT INTO llm_calls (ts, provider, model, total_tokens, cost_usd) VALUES
            ('2026-11-01T00:00:00+00:00', 'LLMProvider', 'deepseek-flash', 100, 0.42),
            ('2026-11-01T00:00:00+00:00', 'AnthropicProvider', 'claude-sonnet-4', 100, 0.30),
            ('2026-11-01T00:00:00+00:00', 'ollama', 'llama3.2', 100, 0.11);
    """)
    old.commit()
    old.close()
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(path))
    try:
        rows = {m: (c, p) for m, c, p in conn.execute("SELECT model, cost_usd, priced FROM llm_calls")}
    finally:
        conn.close()
    assert rows["deepseek-flash"] == (0.0, 0)  # gpt-4o-mini's price was never its own
    assert rows["claude-sonnet-4"] == (0.30, 1)  # priced from its client's own table
    assert rows["llama3.2"] == (0.0, 1)  # a local server costs nothing


def test_a_row_names_the_provider_not_the_client_class(monkeypatch):
    from kazma_core import model_registry as mr

    client = object()
    registry = SimpleNamespace(provider_for_client=lambda c: "deepseek" if c is client else "")
    monkeypatch.setattr(mr, "_registry", registry)
    assert llm_ledger.provider_of(client) == "deepseek"
    assert llm_ledger.provider_of(object()) == "object"  # not built by the registry: its class


# ── the setting ──────────────────────────────────────────────────────────


def test_the_setting_takes_whole_entries_and_refuses_the_rest():
    from kazma_core.settings_validation import SettingRejected, validate_setting

    value, category = validate_setting(mp.OWNER_PRICES_KEY, {"DeepSeek-Chat": {"input": "0.27", "output": 1.1}})
    assert value == {"deepseek-chat": {"input": 0.27, "output": 1.1}} and category == "models"
    for bad in ({"m": {"input": -1, "output": 1}}, {"m": {"input": 1}}, {"": {"input": 1, "output": 1}},
                {"m": {"input": 1, "output": 5000}}, ["m"], "not json"):
        with pytest.raises(SettingRejected):
            validate_setting(mp.OWNER_PRICES_KEY, bad)


def test_cost_says_unknown_in_the_chat_apps():
    from kazma_gateway.slash_commands import _cmd_cost

    text = _cmd_cost({"total_tokens": 100, "total_cost": 0.0, "total_calls": 2, "unpriced_calls": 2})
    assert "unknown" in text and "$0.0000" not in text and "Model prices" in text
    partial = _cmd_cost({"total_tokens": 100, "total_cost": 0.5, "total_calls": 2, "unpriced_calls": 1})
    assert "at least `$0.5000`" in partial
    known = _cmd_cost({"total_tokens": 100, "total_cost": 0.5, "total_calls": 2, "unpriced_calls": 0})
    assert "`$0.5000`" in known and "unknown" not in known
