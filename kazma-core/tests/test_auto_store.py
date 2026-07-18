"""Tests for automatic long-term memory writes."""

from __future__ import annotations

import pytest

from kazma_core.memory.auto_store import (
    extract_turn_texts,
    looks_durable,
    auto_store_from_messages,
)


def test_looks_durable_explicit_remember():
    assert looks_durable("Please remember that my favorite color is teal.")
    assert looks_durable("My name is Alex and I work at Acme.")
    assert looks_durable("I prefer dark mode in the IDE.")


def test_looks_durable_skips_noise():
    assert not looks_durable("hi")
    assert not looks_durable("ok")
    assert not looks_durable("/status")
    assert not looks_durable("thanks")


def test_extract_turn_texts():
    msgs = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "Remember my timezone is Asia/Kuwait."},
        {"role": "assistant", "content": "Got it — I'll remember Asia/Kuwait."},
    ]
    user, assistant = extract_turn_texts(msgs)
    assert "Asia/Kuwait" in user
    assert "remember" in assistant.lower() or "Asia" in assistant


@pytest.mark.asyncio
async def test_auto_store_writes_durable_fact(monkeypatch):
    stored: list[tuple[str, dict]] = []

    class _FakeStore:
        async def store(self, text: str, metadata=None):
            stored.append((text, metadata or {}))
            return "doc-1"

    async def _fake_get_store():
        return _FakeStore()

    monkeypatch.setattr("kazma_core.memory.auto_store._get_store", _fake_get_store)
    monkeypatch.setattr(
        "kazma_core.memory.auto_store._read_memory_cfg",
        lambda: {"enabled": True, "auto_store": True, "auto_store_mode": "durable"},
    )

    msgs = [
        {"role": "user", "content": "Remember that my favorite color is teal."},
        {"role": "assistant", "content": "Noted."},
    ]
    stats = await auto_store_from_messages(msgs)
    assert stats["enabled"] is True
    assert stats["durable"] == 1
    assert stored
    assert "teal" in stored[0][0].lower()


@pytest.mark.asyncio
async def test_auto_store_disabled(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.memory.auto_store._read_memory_cfg",
        lambda: {"enabled": True, "auto_store": False},
    )
    stats = await auto_store_from_messages(
        [{"role": "user", "content": "Remember my name is Sam."}]
    )
    assert stats["enabled"] is False
    assert stats["durable"] == 0
