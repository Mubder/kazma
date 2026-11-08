"""Per-model details and model choice (providers stage 3, 2026-11-08).

Before: a model was a bare name (the first provider listing it got the
call); every model had a 1M window and a 16,384-token output limit; the
context sizes, prices and image support providers' model lists state were
thrown away; the chat's picker switched the model for the whole install
(Telegram included); the chat apps' /model was admin-only and install-wide,
with five models per provider on Discord and Slack; the terminal app merged
duplicate names. Each rule has its control.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore

    # This machine's gcloud credentials would switch the seeded Google row on.
    monkeypatch.setattr("kazma_core.model_registry_store.detect_gcp_project_id", lambda: "")

    s = ConfigStore(db_path=str(tmp_path / "settings.db"))
    yield s
    s.close()


TWO_PROVIDERS = [
    {"name": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-openai-real-key",
     "models": ["gpt-5-mini"]},
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-real-key",
     "models": ["gpt-5-mini", "google/gemini-2.5-flash"]},
    {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "models": ["llama3:8b"]},
    {"name": "keyless", "base_url": "https://api.example-cloud.ai/v1", "api_key": "", "models": ["k1"]},
]


def _registry(store, rows=TWO_PROVIDERS, active=("openai", "gpt-5-mini")):
    from kazma_core.model_registry import ModelRegistry

    store.set("providers.list", rows, category="providers")
    store.batch_set([("registry.active_provider", active[0], "registry"),
                     ("registry.active_model", active[1], "registry")])
    registry = ModelRegistry(store)
    registry._deserialize()  # what get_model_registry does
    return registry


# ── references ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("ref,expected", [
    ("openrouter:google/gemini-2.5-flash", ("openrouter", "google/gemini-2.5-flash")),
    ("OpenRouter:gpt-5-mini", ("openrouter", "gpt-5-mini")),
    ("llama3:8b", ("", "llama3:8b")),  # Ollama's own colon: llama3 is no provider
    ("ollama:llama3:8b", ("ollama", "llama3:8b")),
    ("poolside/laguna-s-2.1:free", ("", "poolside/laguna-s-2.1:free")),
    ("gpt-5-mini", ("", "gpt-5-mini")),
    ("openai:", ("", "openai:")),
])
def test_a_reference_names_its_provider_only_when_one_has_that_name(ref, expected):
    from kazma_core.model_refs import split_ref

    assert split_ref(ref, ["openai", "openrouter", "ollama"]) == expected


def test_a_reference_reaches_its_provider_and_a_bare_name_prefers_the_one_in_use(store):
    registry = _registry(store)
    assert registry.find_provider_for_model("openrouter:gpt-5-mini")["name"] == "openrouter"
    assert registry.find_provider_for_model("gpt-5-mini")["name"] == "openai"  # the one in use
    client = registry.get_client("openrouter:gpt-5-mini")
    assert "openrouter.ai" in client.config.base_url and client.config.model == "gpt-5-mini"
    registry.set_active_model("openrouter:google/gemini-2.5-flash")
    profile = registry.get_active_profile()
    assert (profile["provider"], profile["model"]) == ("openrouter", "google/gemini-2.5-flash")


def test_control_a_bare_name_two_providers_list_went_to_the_first(store):
    registry = _registry(store, active=("openrouter", "google/gemini-2.5-flash"))
    # The provider in use wins now; the first-listed (openai) used to.
    assert registry.find_provider_for_model("gpt-5-mini")["name"] == "openrouter"


# ── details ──────────────────────────────────────────────────────────────


def test_a_providers_listing_says_what_it_states():
    from kazma_core.model_details import from_listing

    openrouter = from_listing({"id": "x", "context_length": 1048576,
                               "top_provider": {"max_completion_tokens": 65536},
                               "architecture": {"input_modalities": ["text", "image"]},
                               "supported_parameters": ["tools"],
                               "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}})
    assert openrouter == {"context_window": 1048576, "max_output": 65536, "vision": True, "tools": True,
                          "input_per_1m": 0.3, "output_per_1m": 2.5}
    assert from_listing({"id": "g", "context_window": 131072, "max_completion_tokens": 32768}) == {
        "context_window": 131072, "max_output": 32768}
    assert from_listing({"id": "m", "max_context_length": 128000,
                         "capabilities": {"vision": False, "function_calling": True}}) == {
        "context_window": 128000, "vision": False, "tools": True}
    assert from_listing({"name": "models/gemini-2.5-flash", "inputTokenLimit": 1048576,
                         "outputTokenLimit": 65536}) == {"context_window": 1048576, "max_output": 65536}
    assert from_listing({"id": "bare"}) == {}
    assert from_listing({"id": "v", "pricing": {"prompt": "-1", "completion": "-1"}}) == {}


def test_the_catalog_holds_no_sizes():
    # The owner's 2026-10-18 rule: a window is the provider's or yours, never
    # a table's. Control: the stage-1 review proposed one.
    from kazma_core.model_details import CATALOG

    for preset, models in CATALOG.items():
        for model, facts in models.items():
            assert "context_window" not in facts and "max_output" not in facts, (preset, model)


def test_your_override_wins_then_the_provider_then_the_catalog(store):
    from kazma_core.model_details import details_for, reported_key

    store.set(reported_key("openai"), {"gpt-5-mini": {"context_window": 400000, "max_output": 128000,
                                                      "vision": False}}, category="registry")
    known = details_for("openai", "gpt-5-mini", store=store)
    assert (known.context_window, known.vision, known.input_per_1m) == (400000, False, 0.25)
    assert known.sources == {"context_window": "provider", "max_output": "provider", "vision": "provider",
                             "tools": "catalog", "input_per_1m": "catalog", "output_per_1m": "catalog"}
    store.set("models.details", {"openai:gpt-5-mini": {"context_window": 200000, "vision": True}}, category="models")
    known = details_for("openai", "gpt-5-mini", store=store)
    assert (known.context_window, known.vision) == (200000, True)
    assert known.sources["context_window"] == "owner"


def test_a_catalog_price_is_the_vendors_own_api_only(store):
    from kazma_core.model_details import details_for
    from kazma_core.model_prices import price_for

    direct = price_for("openai", "https://api.openai.com/v1", "gpt-5-mini", store=store,
                       details=details_for("openai", "gpt-5-mini", store=store))
    assert (direct.input_per_1m, direct.output_per_1m, direct.source) == (0.25, 2.0, "catalog")
    routed = price_for("openrouter", "https://openrouter.ai/api/v1", "gpt-5-mini", store=store,
                       details=details_for("openrouter", "gpt-5-mini", store=store))
    assert routed is None  # OpenRouter reports what a call cost


def test_the_known_window_caps_the_configured_one(store, monkeypatch):
    import kazma_core.model_registry as mr
    from kazma_core.model_details import reported_key
    from kazma_core.token_counter import resolve_context_window

    registry = _registry(store)
    monkeypatch.setattr(mr, "_registry", registry, raising=False)
    monkeypatch.setattr(mr, "get_model_registry", lambda: registry)
    store.set(reported_key("openai"), {"gpt-5-mini": {"context_window": 400000}}, category="registry")
    monkeypatch.setattr("kazma_core.token_counter._configured_window", lambda raw: 1_000_000)
    assert resolve_context_window({}, "gpt-5-mini") == 400000
    assert resolve_context_window({}, "unknown-model") == 1_000_000  # control: not known, not capped


def test_a_known_output_limit_caps_the_request(store):
    from kazma_core.model_details import reported_key

    registry = _registry(store)
    store.set(reported_key("openai"), {"gpt-5-mini": {"max_output": 4096}}, category="registry")
    assert registry.get_client("openai:gpt-5-mini").config.max_tokens == 4096
    store.set(reported_key("openai"), {}, category="registry")
    assert _registry(store).get_client("openai:gpt-5-mini").config.max_tokens == 16384  # control


def test_known_vision_wins_over_the_name(store, monkeypatch):
    import kazma_core.model_registry as mr
    from kazma_core.vision_capability import classify

    registry = _registry(store)
    monkeypatch.setattr(mr, "get_model_registry", lambda: registry)
    store.set("models.details", {"llama3:8b": {"vision": True}}, category="models")
    assert classify("llama3:8b") == "vision"
    store.set("models.details", {}, category="models")
    assert classify("llama3:8b") == "unknown"  # control: the name says nothing


def test_discovery_keeps_what_the_list_says(store, monkeypatch):
    registry = _registry(store)
    answer = {"data": [{"id": "google/gemini-2.5-flash", "context_length": 1048576,
                        "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}}]}

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("verify", None)
            super().__init__(*a, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=answer)), **kw)

    monkeypatch.setattr("kazma_core.model_registry.httpx.AsyncClient", _Client)
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", lambda *a, **k: None)
    monkeypatch.setattr("kazma_core.security.ssrf.resolves_to_own_network", lambda *a, **k: False)
    found = asyncio.run(registry.discover("openrouter"))
    assert found.ok
    kept = store.get("registry.model_details.openrouter")
    assert kept["google/gemini-2.5-flash"]["context_window"] == 1048576


# ── one list for every picker ───────────────────────────────────────────


def test_the_list_offers_what_can_answer_by_reference(store):
    from kazma_core.models.options import model_options

    listed = model_options(_registry(store))
    refs = [o["ref"] for o in listed["options"]]
    assert refs == ["openai:gpt-5-mini", "openrouter:google/gemini-2.5-flash", "openrouter:gpt-5-mini",
                    "ollama:llama3:8b"]
    assert listed["default"] == "openai:gpt-5-mini"
    assert not any(r.startswith("keyless:") for r in refs)  # no key, not offered
    local = next(o for o in listed["options"] if o["provider"] == "ollama")
    assert local["local"] and local["priced"]


def test_no_picker_builds_a_list_of_its_own():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    chat = (root / "kazma-ui/kazma_ui/static/js/chat.js").read_text(encoding="utf-8")
    side = (root / "kazma-ui/kazma_ui/static/js/modules/components.js").read_text(encoding="utf-8")
    tui = (root / "kazma-tui/kazma_tui/widgets/model_picker.py").read_text(encoding="utf-8")
    for src in (chat, side):
        assert "/api/models/options" in src
    assert "model_options" in tui and "get_universal_models" not in tui
    # The chat's picker sets this chat's model, never the install's.
    picker = chat[chat.index("function onModelChange()"):chat.index("// ── Send message via SSE")]
    assert "/api/settings/active_model" not in picker
    assert "kazma.chatModel." in chat


# ── the chat apps ────────────────────────────────────────────────────────


def test_a_chats_model_is_kept_and_cleared(store):
    from kazma_core.chat_model_choice import MAX_CHATS, chat_model_for, clear_chat_model, set_chat_model

    set_chat_model("tg:1", "groq:llama-3.3-70b", store=store)
    assert chat_model_for("tg:1", store=store) == "groq:llama-3.3-70b"
    assert chat_model_for("tg:2", store=store) == ""
    clear_chat_model("tg:1", store=store)
    assert chat_model_for("tg:1", store=store) == ""
    for i in range(MAX_CHATS + 5):
        set_chat_model(f"c{i}", "x:y", store=store)
    assert len(store.get("chat.models")) == MAX_CHATS


class _Store:
    async def get(self, _thread: str) -> dict[str, Any]:
        return {"chat_id": "1"}


class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound.text)
        return True


def _msg(text: str, platform: str = "discord") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(text=text, platform=platform, sender_id="u1", context_metadata={"chat_id": "1"})


def test_model_in_a_chat_app_sets_that_chats_model_only(store, monkeypatch):
    import kazma_core.model_registry as mr
    from kazma_gateway.agent_handler.commands import _try_model_command

    registry = _registry(store)
    monkeypatch.setattr(mr, "get_model_registry", lambda: registry)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    manager = _Manager()
    asyncio.run(_try_model_command(_msg("/model"), _Store(), manager, "discord:1"))
    assert "1. openai:gpt-5-mini (this chat)" in manager.sent[-1]
    assert "4. ollama:llama3:8b" in manager.sent[-1]  # every model, not five a provider
    asyncio.run(_try_model_command(_msg("/model 3"), _Store(), manager, "discord:1"))
    assert "openrouter:gpt-5-mini" in manager.sent[-1]
    assert store.get("chat.models")["discord:1"]["ref"] == "openrouter:gpt-5-mini"
    assert registry.get_active_profile()["provider"] == "openai", "the install's default moved"
    asyncio.run(_try_model_command(_msg("/model gpt-5-mini"), _Store(), manager, "discord:1"))
    assert "more than one provider" in manager.sent[-1]
    asyncio.run(_try_model_command(_msg("/model reset"), _Store(), manager, "discord:1"))
    assert "discord:1" not in (store.get("chat.models") or {})


def test_the_default_is_admin_only(store, monkeypatch):
    import kazma_core.model_registry as mr
    import kazma_gateway.agent_handler.graph as g
    from kazma_gateway.agent_handler.commands import _try_model_command
    from kazma_gateway.slash_commands import changes_global_config

    registry = _registry(store)
    monkeypatch.setattr(mr, "get_model_registry", lambda: registry)
    monkeypatch.setattr(g, "_sender_is_gateway_admin", lambda msg: False)
    manager = _Manager()
    asyncio.run(_try_model_command(_msg("/model default openrouter:gpt-5-mini"), _Store(), manager, "discord:1"))
    assert "admin-only" in manager.sent[-1]
    assert registry.get_active_profile()["provider"] == "openai"
    assert changes_global_config("/model default openrouter:gpt-5-mini")
    assert not changes_global_config("/model openrouter:gpt-5-mini")


def test_the_gateway_pins_the_chats_model_for_its_turn():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "kazma-gateway/kazma_gateway/agent_handler/graph.py").read_text(
        encoding="utf-8")
    turn = src[src.index("from kazma_core.agent.turn import run_agent_turn"):]
    turn = turn[:turn.index("finally:")]
    assert "chat_model_for, thread_id" in turn and "pin_turn_model(_chat_ref)" in turn


def test_model_details_are_validated_whole():
    from kazma_core.settings_validation import SettingRejected, validate_setting

    value, _category = validate_setting("models.details", {"openai:gpt-5": {"context_window": 400000, "tools": True}})
    assert value == {"openai:gpt-5": {"context_window": 400000, "tools": True}}
    for bad in ({"x": {"context_window": 100}}, {"x": {"vision": "yes"}}, {"x": {"colour": 1}}):
        with pytest.raises(SettingRejected):
            validate_setting("models.details", bad)
