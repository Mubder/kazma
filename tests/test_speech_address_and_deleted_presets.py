"""Two gaps the providers plan left (2026-11-08).

1. A speech service is known by its address. Live's "Cohere Transcriptions"
   row (``https://api.cohere.com/v2/audio/transcriptions``) lists no model and
   says no kind, so the stage-2 rule -- its kind, else every model a speech
   model -- called it chat, and it sat among the chat providers. An address
   ending at a speech endpoint cannot answer a chat message.
2. A deleted catalog provider stays deleted. The provider list merged every
   missing seeded preset back in, and every start seeded it again, so Delete
   did nothing for ``zai``, ``cohere`` and the rest -- and live's second Z.AI
   row ("Z.AI" beside the catalog's "zai") could only be tidied one way.

Each rule has the old behaviour as its control.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

LIVE_COHERE_SPEECH = {
    "name": "Cohere Transcriptions", "display_name": "Cohere Transcriptions",
    "base_url": "https://api.cohere.com/v2/audio/transcriptions", "enabled": False, "models": [],
}


def _old_row_is_speech(entry: dict, discovered=()) -> bool:
    """Stage 2's rule, kept as the control."""
    from kazma_core.models.modality import is_speech_model

    kind = str(entry.get("kind") or "").strip().lower()
    if kind:
        return kind == "speech"
    ids = [str(m).strip() for m in [*(entry.get("models") or []), *(discovered or ())] if str(m).strip()]
    return bool(ids) and all(is_speech_model(m) for m in ids)


# ── 1. speech by its address ─────────────────────────────────────────────


def test_lives_cohere_transcriptions_row_is_speech():
    from kazma_core.models.modality import row_is_speech
    from kazma_core.providers import is_speech_provider

    assert row_is_speech(LIVE_COHERE_SPEECH)
    assert is_speech_provider(LIVE_COHERE_SPEECH)
    assert not _old_row_is_speech(LIVE_COHERE_SPEECH), "control: the old rule saw a chat provider"


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1/audio/transcriptions",
    "https://api.openai.com/v1/audio/speech",
    "https://api.groq.com/openai/v1/audio/translations",
    "http://localhost:9000/v1/audio/transcriptions/",
    "https://api.deepgram.com/v1/listen",
    "https://api.elevenlabs.io/v1",
    "https://api.assemblyai.com/v2/transcript",
    "https://speech.example.com/v1/asr",
])
def test_a_speech_endpoint_is_speech_whatever_the_row_says(url):
    from kazma_core.models.modality import _is_speech_address, row_is_speech

    assert _is_speech_address(url)
    # "kind: chat" cannot make an audio endpoint answer a chat message.
    assert row_is_speech({"name": "x", "base_url": url, "kind": "chat", "models": ["gpt-4o"]})


def test_no_chat_provider_in_the_catalog_has_a_speech_address():
    from kazma_core.models.modality import _is_speech_address
    from kazma_core.providers import PROVIDER_PRESETS

    chat = {pid: meta for pid, meta in PROVIDER_PRESETS.items() if meta.get("kind") != "speech"}
    assert len(chat) >= 30
    for pid, meta in chat.items():
        assert not _is_speech_address(meta.get("base_url")), pid
    for url in ("", None, "https://api.openai.com/v1", "https://api.z.ai/api/paas/v4", "localhost:11434",
                "https://api.cohere.ai/compatibility/v1", "https://openrouter.ai/api/v1"):
        assert not _is_speech_address(url), url


def test_the_list_shows_it_under_speech_and_no_picker_offers_it(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.set("providers.list", [dict(LIVE_COHERE_SPEECH, enabled=True, api_key="co-real-key",
                                          models=["command-a-reasoning"])], category="providers")
        registry = ModelRegistry(store)
        assert registry.find_provider_for_model("command-a-reasoning") is None
        from kazma_core.models.options import model_options

        offered = {o["provider"] for o in model_options(registry, details=False)["options"]}
        assert "Cohere Transcriptions" not in offered
    finally:
        store.close()


class _Registry:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {r["name"]: dict(r) for r in rows}
        self.health: list[tuple[str, str]] = []

    def get_provider(self, name):
        row = self.rows.get(name)
        return dict(row) if row else None

    def resolve_provider_credentials(self, name):
        row = self.rows.get(name) or {}
        return row.get("base_url", ""), row.get("api_key", "")

    def stored_key_is_undecryptable(self, name):
        return False

    def probe_model_for(self, name):
        return ""

    async def discover(self, name, candidate=None):
        raise AssertionError("a speech service was asked for its chat models")

    def set_provider_health(self, name, value):
        self.health.append((name, value))


def _app(monkeypatch, registry):
    import kazma_ui.providers as ui
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ui, "get_model_registry", lambda: registry)
    monkeypatch.setattr(ui, "_activate_tested_provider", lambda reg, name: None)
    app = FastAPI()
    app.include_router(ui.create_providers_router(SimpleNamespace(get=lambda *a, **k: None)))
    return TestClient(app)


def test_test_sends_a_speech_service_no_chat_message(monkeypatch):
    import kazma_core.provider_check as pc

    async def never(*a, **k):
        raise AssertionError("a chat message was sent to a speech service")

    monkeypatch.setattr(pc, "check_model", never)
    registry = _Registry([dict(LIVE_COHERE_SPEECH, api_key="co-real-key")])
    http = _app(monkeypatch, registry)

    saved = http.post("/api/providers/Cohere Transcriptions/test").json()
    assert saved["success"] is False and saved["error_code"] == "speech_provider"
    assert registry.health == [], "a speech service's health was overwritten by a chat check"

    typed = http.post("/api/providers/check", json={
        "name": "my-stt", "base_url": "https://api.openai.com/v1/audio/transcriptions", "api_key": "sk-x"}).json()
    assert typed["error_code"] == "speech_provider"

    # Control: with the old rule the same Test goes after the row's chat models.
    import kazma_core.models.modality as modality

    monkeypatch.setattr(modality, "row_is_speech", _old_row_is_speech)
    with pytest.raises(AssertionError, match="chat models"):
        http.post("/api/providers/Cohere Transcriptions/test")

    # The page words the code in both languages.
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS

    entry = TRANSLATIONS["settings.provider_check.speech_provider"]
    assert entry["en"] and entry["ar"]


# ── 2. a deleted catalog provider stays deleted ──────────────────────────


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    s = ConfigStore(db_path=str(tmp_path / "settings.db"))
    yield s
    s.close()


def _live_rows() -> list[dict[str, Any]]:
    return [
        {"name": "Z.AI", "display_name": "Z.AI", "base_url": "https://api.z.ai/api/paas/v4/", "enabled": True,
         "health": "chat_failing", "models": []},
        {"name": "zai", "display_name": "Z.AI (GLM)", "base_url": "https://api.z.ai/api/paas/v4", "enabled": False,
         "health": "unknown", "models": []},
    ]


def _start(store):
    """A start: the registry built and its presets seeded, as at boot."""
    from kazma_core.model_registry import ModelRegistry

    registry = ModelRegistry(store)
    registry._seed_missing_presets()
    return registry


def _names(registry) -> set[str]:
    return {p["name"] for p in registry.list_providers()}


def test_a_deleted_catalog_provider_stays_deleted_across_a_start(store):
    from kazma_core.model_registry_store import REMOVED_PRESETS_KEY

    store.set("providers.list", _live_rows(), category="providers")
    registry = _start(store)
    registry.delete_provider("zai")
    assert "zai" not in _names(registry)
    assert store.get(REMOVED_PRESETS_KEY) == ["zai"]
    assert "zai" not in _names(_start(store)), "the next start seeded the deleted provider again"
    assert "zai" not in [p.get("name") for p in store.get("providers.list")]


def test_control_without_the_record_the_deleted_preset_comes_back(store, monkeypatch):
    from kazma_core.model_registry import ModelRegistry

    monkeypatch.setattr(ModelRegistry, "_removed_presets", lambda self: frozenset())
    store.set("providers.list", _live_rows(), category="providers")
    registry = _start(store)
    registry.delete_provider("zai")
    assert "zai" in _names(registry), "control: the list no longer merges presets in"


def test_adding_it_again_takes_it_off_the_list(store):
    from kazma_core.model_registry_store import REMOVED_PRESETS_KEY

    store.set("providers.list", _live_rows(), category="providers")
    registry = _start(store)
    registry.delete_provider("zai")
    registry.upsert_provider({"name": "zai", "display_name": "Z.AI (GLM)",
                              "base_url": "https://api.z.ai/api/paas/v4", "enabled": False})
    assert "zai" in _names(registry)
    assert store.get(REMOVED_PRESETS_KEY) == []
    assert "zai" in _names(_start(store))


def test_a_row_of_the_owners_own_is_deleted_without_a_record(store):
    from kazma_core.model_registry_store import REMOVED_PRESETS_KEY

    store.set("providers.list", _live_rows(), category="providers")
    registry = _start(store)
    registry.delete_provider("Z.AI")
    assert _names(registry) >= {"zai"} and "Z.AI" not in _names(registry)
    assert store.get(REMOVED_PRESETS_KEY) is None


def test_the_list_and_the_record_are_one_write(store, monkeypatch):
    store.set("providers.list", _live_rows(), category="providers")
    registry = _start(store)
    writes: list[list[str]] = []
    real = store.batch_set

    def spy(items, **kw):
        writes.append([key for key, _v, _c in items])
        return real(items, **kw)

    monkeypatch.setattr(store, "batch_set", spy)
    monkeypatch.setattr(store, "set", lambda *a, **k: pytest.fail("a provider delete wrote one key alone"))
    registry.delete_provider("cohere")
    assert writes == [["providers.list", "providers.removed_presets"]]
