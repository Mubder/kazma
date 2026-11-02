"""Concurrent recalls all get meaning search (FA-276).

The 2026-11 functional audit: recall took an embedding slot with a
non-blocking acquire. With two recalls embedding, a third gave up at once --
well inside its own 5 s deadline -- logged "missed the deadline", and answered
from keywords only. Under ordinary load (a turn plus the chat's memory
search, or two chats at once) meaning search degraded for no reason.

Recall now waits for a slot until its own deadline, says "no embedding slot"
apart from "missed the deadline", and sizes the slots to the embedder (a local
model 2, a remote endpoint 8). Three concurrent questions through the real
question encoder on a 100 ms fake embedder; the old non-blocking acquire is
the negative control.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pytest

from kazma_core.memory import recall


class _SlowEmbedder:
    """100 ms an encoding, like a local model on a busy CPU."""

    concurrency = 2

    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def encode(self, text: str) -> list[float]:
        with self.lock:
            self.calls += 1
        time.sleep(0.1)
        return [1.0, float(len(text))]


def _three_at_once(monkeypatch, embedder: Any, deadline_s: float = 5.0) -> list[Any]:
    """Three recalls' question vectors, encoded at the same moment."""
    from kazma_core.memory import embedder as embedder_mod

    monkeypatch.setattr(embedder_mod, "get_embedder", lambda *a, **k: embedder)
    monkeypatch.setattr(recall, "_recall_deadline_s", lambda: deadline_s)
    recall._QUERY_VECTORS.clear()
    results: list[Any] = [None] * 3
    degraded: list[Any] = [None] * 3
    start = threading.Barrier(3)

    def one(i: int) -> None:
        memo = {"vectors": {}, "deadline": time.monotonic() + deadline_s, "degraded": None}
        token = recall._QUERY_MEMO.set(memo)
        try:
            start.wait()
            results[i] = recall._encode_query(f"question number {i}")
            degraded[i] = memo["degraded"]
        finally:
            recall._QUERY_MEMO.reset(token)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return [results, degraded]


def test_three_concurrent_recalls_all_get_vectors(monkeypatch):
    results, degraded = _three_at_once(monkeypatch, _SlowEmbedder())
    assert all(results), (results, degraded)
    assert degraded == [None, None, None]


def test_a_busy_embedder_is_said_apart_from_a_slow_one(monkeypatch, caplog):
    """Slots held past the deadline: "no embedding slot", never "missed the
    deadline" (that is the embedder itself being slow)."""
    gate = threading.Event()

    class _Held(_SlowEmbedder):
        def encode(self, text: str) -> list[float]:
            gate.wait(5)
            return [1.0]

    caplog.set_level(logging.WARNING, logger="kazma_core.memory.recall")
    try:
        _results, degraded = _three_at_once(monkeypatch, _Held(), deadline_s=0.3)
    finally:
        gate.set()
    assert any(d and "busy with other recalls" in d for d in degraded), degraded
    lines = [r.getMessage() for r in caplog.records if r.name == "kazma_core.memory.recall"]
    assert any("no embedding slot freed" in line for line in lines), lines


def test_slots_follow_the_embedder():
    from kazma_core.memory.embedder import LocalSentenceTransformerEmbedder, OpenAICompatibleEmbedder

    assert LocalSentenceTransformerEmbedder.concurrency == 2
    assert OpenAICompatibleEmbedder.concurrency == 8
    assert recall._slots_for(OpenAICompatibleEmbedder) is not recall._slots_for(LocalSentenceTransformerEmbedder)
    assert recall._slots_for(object())._value == recall._DEFAULT_EMBED_SLOTS


def test_the_old_non_blocking_acquire_lost_the_third(monkeypatch):
    """Negative control: the acquire before FA-276 gave up at once."""
    real = recall._slots_for

    class _NoWait:
        def __init__(self, slots: threading.BoundedSemaphore) -> None:
            self.slots = slots

        def acquire(self, timeout: float | None = None) -> bool:
            return self.slots.acquire(blocking=False)

        def release(self) -> None:
            self.slots.release()

    monkeypatch.setattr(recall, "_slots_for", lambda embedder: _NoWait(real(embedder)))
    results, degraded = _three_at_once(monkeypatch, _SlowEmbedder())
    assert sum(1 for r in results if not r) == 1, (results, degraded)
