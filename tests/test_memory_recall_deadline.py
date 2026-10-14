"""Recall has a deadline, embeds the question at most once, and says when it
could not search by meaning; the embedder has a circuit breaker shown in
health (Memory audit M16, MEM-R07).

Found 2026-10-13: a failed question embedding was tried again by every
channel (four times a recall); a hanging remote embedder (two 60-second
attempts) could hold a turn for minutes before the first model call; a broken
local install retried the model load and logged it on every call; and
``memory_search`` answered "No relevant memories found." when recall had
failed. Each rule has its negative control.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "fake-model")
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES ('e_maya', 't', 's1', 1, 'Maya works at the Lisbon aquarium', "
        "'Noted.', 'episodic', ?, '{}')", (time.time() - 3600,))
    db.commit()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()


class _Failing:
    dim = 4

    def __init__(self):
        self.calls = 0

    def encode(self, text):
        self.calls += 1
        return []


class _Hanging:
    dim = 4

    def __init__(self, seconds: float):
        self.seconds, self.calls = seconds, 0

    def encode(self, text):
        self.calls += 1
        time.sleep(self.seconds)
        return [0.1, 0.2, 0.3, 0.4]


def _use(mem, emb) -> None:
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: emb)


QUESTION = "where does maya work these days"


def test_a_failed_question_embedding_is_tried_once_per_recall(mem):
    from kazma_core.memory.recall import recall

    emb = _Failing()
    _use(mem, emb)
    res = recall(QUESTION, tenant_id="t", local_only=True)
    assert emb.calls == 1
    assert [h.id for h in res.episodes] == ["e_maya"]  # keywords still answer
    assert res.degraded and "meaning search" in res.degraded


def test_without_the_memo_every_channel_tried_again(mem):
    """Negative control: the recall body without its memo."""
    from kazma_core.memory.recall import _recall

    emb = _Failing()
    _use(mem, emb)
    _recall(QUESTION, tenant_id="t", local_only=True)
    assert emb.calls >= 2, emb.calls


def test_a_hanging_embedder_is_bounded_by_the_deadline(mem):
    from kazma_core.memory import recall as recall_mod

    _use(mem, _Hanging(3.0))
    mem.mp.setattr(recall_mod, "_recall_deadline_s", lambda: 0.5)
    started = time.perf_counter()
    res = recall_mod.recall(QUESTION, tenant_id="t", local_only=True)
    took = time.perf_counter() - started
    assert took < 1.5, took
    assert [h.id for h in res.episodes] == ["e_maya"]
    assert res.degraded and "deadline" in res.degraded


def test_without_the_deadline_the_recall_waited_for_the_embedder(mem):
    """Negative control: the body without the deadline waits the whole call."""
    from kazma_core.memory.recall import _recall

    _use(mem, _Hanging(1.5))
    started = time.perf_counter()
    _recall(QUESTION, tenant_id="t", local_only=True)
    assert time.perf_counter() - started >= 1.5


def test_a_broken_local_install_logs_once_per_breaker_period(monkeypatch, caplog):
    from kazma_core.memory import embedder as E

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    e = E.LocalSentenceTransformerEmbedder(model_name="audit/not-a-model", allow_download=True)
    caplog.set_level(logging.WARNING, logger="kazma_core.memory.embedder")
    text = "a question long enough to skip the short-text cache " + "x" * 300

    def warnings() -> int:
        return sum(1 for r in caplog.records
                   if r.name == "kazma_core.memory.embedder" and "not installed" in r.getMessage())

    for _ in range(3):
        assert e.encode(text) == []
    assert warnings() == 1
    assert e.breaker.state()["state"] == "open"
    e.breaker.open_until = 0.0  # the period ends: one more try, one more line
    assert e.encode(text) == []
    assert warnings() == 2
    # Negative control: a breaker that never opens logs every call.
    monkeypatch.setattr(E._EmbedderBreaker, "allow", lambda self: True)
    for _ in range(3):
        e.encode(text)
    assert warnings() == 5


def test_a_failing_remote_embedder_is_not_called_while_its_breaker_is_open(monkeypatch):
    from kazma_core.memory import embedder as E

    posts = []

    class _Client:
        def post(self, *a, **k):
            posts.append(1)
            raise ConnectionError("endpoint down")

    e = E.OpenAICompatibleEmbedder("http://embed.invalid", "k", "m", 4)
    monkeypatch.setattr(e, "_ensure_client", lambda: _Client())
    assert e.encode("first question") == []
    assert len(posts) == 2  # its two attempts
    assert e.encode("second question") == [] and len(posts) == 2


def test_health_shows_the_open_breaker(mem):
    from kazma_core.memory import embedder as E
    from kazma_core.memory.v2_health import build_v2_health

    broken = E.OpenAICompatibleEmbedder("http://embed.invalid", "k", "m", 4)
    broken.breaker.failure("endpoint down")
    mem.mp.setattr(E, "_embedder", broken)
    health = build_v2_health()
    assert health["embedder"]["state"] == "open" and health["status"] == "DEGRADED"
    assert health["status_detail"] == "embedder_unavailable"
    broken.breaker.success()
    assert build_v2_health()["embedder"]["state"] == "closed"


def _search(query: str) -> str:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return asyncio.run(registry.get_tool("memory_search").func(query=query))


def test_the_search_tool_says_degraded_never_empty(mem):
    _use(mem, _Failing())
    out = _search("what was the zanzibar plan")
    assert "No relevant memories found." not in out and "incomplete" in out
    # Negative control: a recall that searched by meaning and found nothing.
    from kazma_core.memory.recall import RecallResult

    mem.mp.setattr("kazma_core.memory.recall.recall", lambda *a, **k: RecallResult([], []))
    assert "No relevant memories found." in _search("what was the zanzibar plan")
