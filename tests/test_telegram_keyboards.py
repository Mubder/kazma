"""Unit tests for telegram_keyboards builders."""

from __future__ import annotations

from kazma_gateway.adapters.telegram import TelegramAdapter
from kazma_gateway.adapters.telegram_keyboards import (
    build_approval_keyboard,
    build_model_keyboard,
    build_personality_keyboard,
    build_provider_keyboard,
)


def test_approval_keyboard():
    kb = build_approval_keyboard("tid-1")
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["callback_data"] == "hitl:approve:tid-1"
    assert rows[0][1]["callback_data"] == "hitl:deny:tid-1"
    # Adapter static methods stay compatible
    assert TelegramAdapter.build_approval_keyboard("x")["inline_keyboard"]


def test_personality_keyboard_caps_at_3():
    kb = build_personality_keyboard(["a", "b", "c", "d"])
    assert len(kb["inline_keyboard"]) == 3


def test_provider_keyboard_two_per_row():
    providers = [
        {"name": "openai", "display_name": "OpenAI"},
        {"name": "xai", "display_name": "xAI"},
        {"name": "ollama", "display_name": "Ollama"},
    ]
    kb = build_provider_keyboard(providers)
    assert len(kb["inline_keyboard"]) == 2
    assert kb["inline_keyboard"][0][0]["callback_data"] == "model_provider:openai"


def test_model_keyboard_shortens_display():
    kb = build_model_keyboard("openai", ["openai/gpt-4o", "mini"])
    assert kb["inline_keyboard"][0][0]["text"] == "gpt-4o"
    assert kb["inline_keyboard"][0][0]["callback_data"] == "model_select:openai/gpt-4o"
    assert kb["inline_keyboard"][1][0]["text"] == "mini"
