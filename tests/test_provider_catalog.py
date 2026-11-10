"""One provider list, kept as data, read by every screen (providers stage 2,
2026-11-08).

Before: the Settings page kept ten presets of its own (already different
from the server's twenty); a provider's wire format, sign-in and default
model lived in three other tables; a row named "Work Azure" spoke OpenAI's
format; Azure and Bedrock read their settings from the environment only;
no provider could send a header of its own or keep an address as typed;
speech services sat in the chat lists; and voice had two settings. Each rule
below has the old behaviour as its control.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    s = ConfigStore(db_path=str(tmp_path / "settings.db"))
    yield s
    s.close()


def _registry(store, rows: list[dict[str, Any]], active: tuple[str, str] = ("", "")):
    from kazma_core.model_registry import ModelRegistry

    store.set("providers.list", rows, category="providers")
    if active[0]:
        store.batch_set([("registry.active_provider", active[0], "registry"),
                         ("registry.active_model", active[1], "registry")])
    return ModelRegistry(store)


# ── the catalog ──────────────────────────────────────────────────────────


def test_every_preset_declares_its_format_sign_in_form_and_kind():
    from kazma_core.provider_adapters import ADAPTER_STYLES
    from kazma_core.providers import PROVIDER_PRESETS, catalog

    assert len(PROVIDER_PRESETS) >= 35  # 20 before, 15 more
    for pid, meta in PROVIDER_PRESETS.items():
        assert meta["api_style"] in ADAPTER_STYLES, pid
        assert meta["auth"] in ("bearer", "x-api-key", "api-key", "aws", "google", "none"), pid
        assert meta["form"] in ("key", "azure", "bedrock", "google", "local"), pid
        assert meta["kind"] in ("chat", "speech"), pid
        assert meta["local"] == (meta["form"] == "local"), pid
    for new in ("cerebras", "deepinfra", "huggingface", "cloudflare", "moonshot", "minimax", "siliconflow",
                "dashscope", "novita", "sambanova", "hyperbolic", "vllm", "llamacpp", "localai", "jan"):
        assert new in PROVIDER_PRESETS, new
    rows = catalog()
    assert rows[-1]["id"] == "custom" and {r["id"] for r in rows} == set(PROVIDER_PRESETS)


def test_the_page_keeps_no_preset_list_of_its_own():
    js = (ROOT / "kazma-ui/kazma_ui/static/js/providers.js").read_text(encoding="utf-8")
    assert "PRESETS:" not in js and "api.openai.com" not in js
    core = (ROOT / "kazma-ui/kazma_ui/static/js/settings_core.js").read_text(encoding="utf-8")
    assert "/api/providers/catalog" in core
    # Control: the page's old list is what this rule forbids.
    old = "PRESETS: {\n        openai: { name: 'OpenAI', base_url: 'https://api.openai.com/v1' },"
    assert "PRESETS:" in old


def test_the_catalog_route_is_the_catalog():
    from kazma_core.config_store import get_config_store
    from kazma_core.providers import catalog
    from kazma_ui.providers import create_providers_router

    router = create_providers_router(get_config_store())
    route = next(r for r in router.routes if r.path == "/api/providers/catalog")
    assert route.endpoint() == catalog()


def test_a_row_made_from_a_preset_under_another_name_speaks_its_format():
    from kazma_core.azure_llm import AzureProvider
    from kazma_core.llm_provider import LLMConfig, LLMProvider
    from kazma_core.provider_adapters import build_client

    config = LLMConfig(base_url="https://r.openai.azure.com", api_key="az", model="dep")
    assert isinstance(build_client("Work Azure", config, {"preset": "azure"}), AzureProvider)
    # Control: by name alone it was the OpenAI client.
    assert type(build_client("Work Azure", config, {})) is LLMProvider


def test_new_presets_are_offered_never_seeded():
    from kazma_core.model_registry_store import SEEDED_PRESETS, default_provider_entries, seed_missing_presets

    names = {r["name"] for r in default_provider_entries()}
    assert "cerebras" not in names and "vllm" not in names and "openai" in names
    rows, _changed = seed_missing_presets([])
    assert {r["name"] for r in rows} == SEEDED_PRESETS
    kept, _ = seed_missing_presets([{"name": "cerebras", "base_url": "https://api.cerebras.ai/v1"}])
    assert sum(r["name"] == "cerebras" for r in kept) == 1


# ── what a row carries ──────────────────────────────────────────────────


def test_a_row_keeps_the_new_fields_and_drops_empty_ones():
    from kazma_core.model_registry_store import normalize_provider_entry

    row = normalize_provider_entry({
        "name": "gw", "preset": "custom", "kind": "speech", "url_as_typed": True,
        "secret_headers": {"X-Org": "o1", "": "dropped"}, "api_version": "2024-10-21", "region": "",
        "api_keys": [{"id": "k2", "label": "second", "api_key": "sk-2"}, {"label": "no id"}],
    })
    assert row["kind"] == "speech" and row["url_as_typed"] is True and row["api_version"] == "2024-10-21"
    assert row["secret_headers"] == {"X-Org": "o1"} and "region" not in row
    assert row["api_keys"] == [{"id": "k2", "label": "second", "api_key": "sk-2"}]
    # Control: the old shape kept none of them.
    plain = normalize_provider_entry({"name": "gw"})
    assert "secret_headers" not in plain and "api_keys" not in plain


def test_every_new_secret_is_vaulted_by_its_name():
    from kazma_core.config_store import is_sensitive_config_key as secret

    assert secret("providers.list.gw.secret_headers.Authorization")
    assert secret("providers.list.gw.aws_secret_access_key")
    assert secret("providers.list.gw.aws_session_token")
    assert secret("providers.list.gw.api_keys.k2.api_key")
    # Control: a field named "headers" would not have been. (An
    # ``Authorization`` member is vaulted by its own name since 2026-11-10:
    # tests/test_masked_settings_go_to_the_vault.py; any other header is not.)
    assert not secret("providers.list.gw.headers.X-Org-Key")


def test_the_api_masks_every_new_secret_and_a_masked_save_keeps_it():
    from kazma_core.provider_check import merge_typed_secrets
    from kazma_ui.providers import _mask_provider_entry

    stored = {"name": "gw", "secret_headers": {"Authorization": "Bearer real-secret"},
              "aws_secret_access_key": "aws-real", "api_keys": [{"id": "k2", "label": "b", "api_key": "sk-second-real"}]}
    shown = _mask_provider_entry(stored)
    blob = json.dumps(shown)
    assert "real-secret" not in blob and "aws-real" not in blob and "sk-second-real" not in blob
    assert "secret_headers" not in shown and "Authorization" in shown["headers"]
    posted = {"secret_headers": shown["headers"], "aws_secret_access_key": shown["aws_secret_access_key"],
              "api_keys": shown["api_keys"]}
    kept = merge_typed_secrets(posted, stored)
    assert kept["secret_headers"] == {"Authorization": "Bearer real-secret"}
    assert kept["aws_secret_access_key"] == "aws-real"
    assert kept["api_keys"][0]["api_key"] == "sk-second-real"


# ── the clients ──────────────────────────────────────────────────────────


def test_headers_go_on_every_request():
    from kazma_core.llm_provider import LLMConfig, LLMProvider

    seen: list[dict[str, str]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    def run(headers: dict[str, str]) -> None:
        provider = LLMProvider(LLMConfig.from_dict({
            "base_url": "https://gw.example.com/v1", "api_key": "sk-x", "model": "m", "extra_headers": headers}))
        http = httpx.AsyncClient(base_url="https://gw.example.com/v1", transport=httpx.MockTransport(endpoint),
                                 headers={"Authorization": "Bearer sk-x", **provider.config.extra_headers})

        async def get_client():
            return http

        provider._get_client = get_client
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))

    run({"X-Org": "org-1"})
    assert seen[-1].get("x-org") == "org-1"
    run({})  # control: none set, none sent
    assert "x-org" not in seen[-1]


def test_a_header_that_could_split_a_request_is_dropped():
    from kazma_core.llm_provider import _header_dict

    assert _header_dict({"X-Ok": "v", "X-Bad": "v\r\nInjected: 1", "": "x"}) == {"X-Ok": "v"}


def test_an_address_kept_as_typed_gets_no_v1(store):
    from kazma_core.url_utils import normalize_provider_url, set_as_typed_urls

    url = "https://models.example-gw.ai/inference"
    set_as_typed_urls([])
    assert normalize_provider_url(url).endswith("/inference/v1")  # control: /v1 added
    registry = _registry(store, [{"name": "gw", "base_url": url, "api_key": "sk-gw", "url_as_typed": True,
                                  "models": ["m"]}])
    registry.list_providers()
    assert normalize_provider_url(url) == url
    set_as_typed_urls([])


def test_azure_takes_its_version_and_deployment_from_settings(monkeypatch):
    from kazma_core.azure_llm import AzureProvider
    from kazma_core.llm_provider import LLMConfig

    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2023-01-01")
    client = AzureProvider(LLMConfig.from_dict({"base_url": "https://res.openai.azure.com", "api_key": "az",
                                                "model": "my-deploy", "api_version": "2024-10-21"}))
    assert client._api_version == "2024-10-21" and client._deployment == "my-deploy"
    assert client._endpoint == "https://res.openai.azure.com"


def test_bedrock_takes_its_region_and_keys_from_settings(monkeypatch):
    import sys
    import types

    from kazma_core.llm_provider import LLMConfig
    from kazma_core.provider_adapters import build_client

    made: list[dict[str, Any]] = []
    fake = types.ModuleType("boto3")
    fake.client = lambda service, **kw: made.append({"service": service, **kw}) or object()
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.delenv("AWS_REGION", raising=False)
    client = build_client("bedrock", LLMConfig(model="anthropic.claude-3-5-sonnet"), {
        "region": "eu-west-1", "aws_access_key_id": "AKIAEXAMPLE", "aws_secret_access_key": "secret"})
    client._get_client()
    assert made[-1]["region_name"] == "eu-west-1"
    assert made[-1]["aws_access_key_id"] == "AKIAEXAMPLE" and made[-1]["aws_secret_access_key"] == "secret"


# ── one credentials rule ────────────────────────────────────────────────


def test_what_each_sign_in_needs(monkeypatch, tmp_path):
    from kazma_core.provider_check import credentials_problem

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert credentials_problem(None, {"name": "bedrock"}) == "aws_credentials_required"
    assert credentials_problem(None, {"name": "bedrock", "aws_access_key_id": "a", "aws_secret_access_key": "s"}) is None
    assert credentials_problem(None, {"name": "google", "google_mode": "vertex_ai"}) == "project_required"
    assert credentials_problem(None, {"name": "google", "google_mode": "vertex_ai", "project_id": "p"}) is None
    assert credentials_problem(None, {"name": "vllm", "base_url": "http://localhost:8000/v1"}) is None
    assert credentials_problem(None, {"name": "cerebras", "base_url": "https://api.cerebras.ai/v1"}) == "api_key_required"
    assert credentials_problem(None, {"name": "cerebras"}, "csk-real-key-123") is None


def test_the_setup_guide_offers_only_what_a_key_sets_up():
    from kazma_core.providers import PROVIDER_PRESETS
    from kazma_ui.setup_api import _guided

    guided = {pid for pid, meta in PROVIDER_PRESETS.items() if _guided(pid, meta)}
    assert {"openai", "anthropic", "cerebras", "moonshot", "google"} <= guided
    assert not guided & {"azure", "bedrock", "cloudflare", "custom", "ollama", "vllm"}


# ── speech ───────────────────────────────────────────────────────────────


def test_a_speech_service_is_never_a_chat_models_owner(store):
    registry = _registry(store, [
        {"name": "NVIDIA ASR", "base_url": "http://localhost:9000/v1", "api_key": "x",
         "models": ["nvidia/parakeet-ctc-1.1b", "gpt-oss"], "kind": "speech"},
        {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "api_key": "gsk-real-key",
         "models": ["gpt-oss"]},
    ])
    assert registry.find_provider_for_model("gpt-oss")["name"] == "groq"
    from kazma_core.models.modality import is_speech_model, row_is_speech

    assert is_speech_model("nvidia/parakeet-ctc-1.1b") and is_speech_model("nvidia/canary-1b")
    assert row_is_speech({"models": ["whisper-large-v3", "nvidia/canary-1b"]})
    assert not row_is_speech({"models": ["whisper-1", "gpt-4o"]})


# ── one voice setting ────────────────────────────────────────────────────


def test_a_stored_gateway_voice_value_moves_to_voice_before_kazma_yaml_seeds_it(tmp_path):
    from kazma_core.config_store import ConfigStore

    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text("voice:\n  enabled: false\n  stt_provider: openai\n", encoding="utf-8")
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=yaml_path)
    try:
        store.set("gateway.voice.enabled", True, category="gateway")
        store.reconcile_from_yaml()
        assert store.get("voice.enabled") is True, "the owner's voice switch was lost"
        assert store.get("gateway.voice.enabled") is None
        assert store.get("voice.stt_provider") == "openai"  # seeded where nothing moved
    finally:
        store.close()


def test_kazma_yaml_has_one_voice_section():
    import yaml

    shipped = yaml.safe_load((ROOT / "kazma.yaml").read_text(encoding="utf-8"))
    assert "voice" not in (shipped.get("gateway") or {}) and "enabled" in shipped["voice"]


def test_control_without_the_move_kazma_yaml_wins_over_the_owner(tmp_path, monkeypatch):
    from kazma_core import config_store as cs

    monkeypatch.setattr(cs, "_MOVED_KEYS", {})
    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text("voice:\n  enabled: false\n", encoding="utf-8")
    store = cs.ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=yaml_path)
    try:
        store.set("gateway.voice.enabled", True, category="gateway")
        store.reconcile_from_yaml()
        assert store.get("voice.enabled") is False  # the owner's switch, lost
    finally:
        store.close()
