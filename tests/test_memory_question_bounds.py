"""Recall reads the question's text, bounded (Memory audits M17 and M25:
MEM-R04, MEM-R14).

Found 2026-10-13: on a photo turn the supervisor handed recall
``str(content)`` -- the Python repr of the message parts, base64 included:
recall embedded 200,125 characters and built a 6,029-term keyword query. And
nothing bounded a long question: a 2,000-term MATCH cost 668 ms a search, the
graph walk's seeding grew with words times names.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sqlite3
import statistics
import time
from types import SimpleNamespace

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    db.close()


# ── M17: the text parts only ──────────────────────────────────────────────

PAYLOAD = base64.b64encode(os.urandom(150_000)).decode()
PHOTO = [{"type": "text", "text": "what breed is this dog?"},
         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + PAYLOAD}}]


class _Response:
    def __init__(self, text: str) -> None:
        self.content = text
        self.tool_calls: list = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.0


class _LLM:
    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        return _Response("A corgi.")


class _Fake:
    compactor = type("C", (), {"retrieve_memories": staticmethod(lambda *a, **k: [])})()

    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def trace_llm_call(self, **kwargs) -> None:
        pass

    async def check_and_enforce(self, state):
        return state


def test_a_photo_turn_hands_recall_its_text_only(mem):
    from kazma_core.agent.graph_builder import supervisor_node
    from kazma_core.memory.recall import RecallResult

    asked: list[str] = []

    def spy(query, **kw):
        asked.append(query)
        return RecallResult([], [])

    mem.mp.setattr("kazma_core.memory.recall.recall", spy)
    fake = _Fake()
    asyncio.run(supervisor_node(
        {"messages": [{"role": "user", "content": PHOTO}], "iteration": 0, "max_iterations": 5,
         "tenant_id": "default", "thread_id": "th-photo"},
        llm=_LLM(), system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=fake, authority=fake, tracer=fake,
    ))
    assert asked and all("base64" not in q and len(q) < 200 for q in asked), [len(q) for q in asked]
    assert "what breed is this dog?" in asked[0]
    # Negative control: what the supervisor used to hand it.
    old = str(PHOTO)
    assert "base64," in old and len(old) > 200_000


def test_the_text_reader_leaves_an_image_out_when_asked():
    from kazma_core.agent.turn_input import extract_latest_user_text

    msgs = [{"role": "user", "content": PHOTO}]
    assert extract_latest_user_text(msgs, image_marker=False) == "what breed is this dog?"
    assert extract_latest_user_text(msgs) == "what breed is this dog?\n[image attachment]"


def test_recall_reads_at_most_the_bounded_question(mem):
    from kazma_core.memory import recall as recall_mod

    seen: list[str] = []
    real = recall_mod._recall

    def spy(query, **kw):
        seen.append(query)
        return real(query, **kw)

    mem.mp.setattr(recall_mod, "_recall", spy)
    long_q = "start " + "filler " * 5000 + "what did I decide about the zanzibar trip"
    recall_mod.recall(long_q, tenant_id="t", local_only=True)
    assert len(seen[0]) <= recall_mod._QUESTION_MAX_CHARS + 1
    assert seen[0].startswith("start") and seen[0].endswith("zanzibar trip")


# ── M25: cost bounded in question length ──────────────────────────────────


def _seed_names(mem, n: int = 1500) -> None:
    mem.db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, valid_from, ingested_at, extraction_method) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"b{i}", "t", f"node{i}", "related_to", "set", f"value{i}", 0.9, 2, 1.0, 1000.0, 1000.0,
          "llm_inferred") for i in range(n)],
    )
    mem.db.executemany(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES (?, 't', 's', ?, ?, 'ok', 'episodic', 1000, '{}')",
        [(f"e{i}", i, f"note about word{i} and node{i}") for i in range(2000)],
    )
    mem.db.commit()


def _median_recall_s(words: int, runs: int = 5) -> float:
    from kazma_core.memory.recall import recall

    query = " ".join(f"word{i}" for i in range(words))
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        recall(query, tenant_id="t", local_only=True)
        times.append(time.perf_counter() - started)
    return statistics.median(times)


def test_recall_at_2000_words_stays_within_twice_20_words(mem):
    _seed_names(mem)
    _median_recall_s(20, runs=1)  # warm caches
    short, long_ = _median_recall_s(20), _median_recall_s(2000)
    assert long_ <= 2 * short + 0.02, (short, long_)


def test_without_the_bounds_a_long_question_cost_more(mem):
    """Negative control: the bounds lifted."""
    from kazma_core.memory import recall as recall_mod

    _seed_names(mem)
    mem.mp.setattr(recall_mod, "_QUESTION_MAX_CHARS", 10**9)
    mem.mp.setattr(recall_mod, "_QUESTION_MAX_TERMS", 10**9)
    _median_recall_s(20, runs=1)
    short, long_ = _median_recall_s(20), _median_recall_s(2000)
    assert long_ > 2 * short + 0.02, (short, long_)
