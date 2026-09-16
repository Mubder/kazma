"""Chat vs speech modality — Whisper is never the chat brain."""

from __future__ import annotations

from kazma_core.models.modality import chat_models, is_speech_model, speech_models
from kazma_core.runtime.model_switch import switch_active_model


def test_whisper_is_speech_gpt_is_chat() -> None:
    assert is_speech_model("whisper-large-v3")
    assert is_speech_model("whisper-1")
    assert is_speech_model("openai/whisper-large-v3")
    assert is_speech_model("distil-whisper-large-v3-en")
    assert is_speech_model("gpt-4o-mini-transcribe")
    assert is_speech_model("tts-1")
    assert not is_speech_model("gpt-4o")
    assert not is_speech_model("llama-3.3-70b")
    assert not is_speech_model("deepseek-chat")
    assert not is_speech_model("")


def test_chat_models_drops_whisper() -> None:
    assert chat_models(["gpt-4o", "whisper-1", "llama-3"]) == ["gpt-4o", "llama-3"]
    assert speech_models(["gpt-4o", "whisper-1"]) == ["whisper-1"]


def test_switch_refuses_whisper_as_active_chat() -> None:
    result = switch_active_model("whisper-large-v3")
    assert result.ok is False
    assert result.error_code == "invalid_model"
    assert "speech" in (result.error or "").lower()


def test_probe_model_skips_whisper(monkeypatch) -> None:
    from kazma_core.model_registry import ModelRegistry

    class _Store:
        def get(self, key, default=None):
            if key == "providers.list":
                return [{
                    "name": "groq",
                    "enabled": True,
                    "model": "whisper-large-v3",
                    "models": ["whisper-large-v3", "llama-3.3-70b"],
                }]
            if "selected_models" in key:
                return ["whisper-large-v3"]
            return default

        def set(self, *_a, **_k):
            return None

    reg = ModelRegistry(_Store())
    monkeypatch.setattr(reg, "get_discovered_models", lambda _n: ["whisper-large-v3", "llama-3.3-70b"])
    assert reg.probe_model_for("groq") == "llama-3.3-70b"
    assert "whisper" not in " ".join(reg.get_visible_models("groq")).lower()
