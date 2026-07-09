"""Unit tests for telegram_stt helpers."""

from __future__ import annotations

from kazma_gateway.adapters.telegram import TelegramAdapter
from kazma_gateway.adapters.telegram_stt import detect_voice_message


def test_detect_voice():
    assert detect_voice_message({"voice": {"file_id": "1"}}) is True
    assert detect_voice_message({"audio": {"file_id": "1"}}) is True
    assert detect_voice_message({"text": "hi"}) is False


def test_adapter_detect_delegates():
    assert TelegramAdapter.detect_voice_message({"voice": {}}) is True
