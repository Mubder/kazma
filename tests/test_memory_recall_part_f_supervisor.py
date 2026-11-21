"""Part F through the real supervisor node (framework audit F9).

* A follow-up ("And what does she teach?") reaches recall with the user's
  message before it: searched as typed, it found nothing about the person
  it is about. Control: the raw follow-up, as before.
* The chat's turns the model already reads are handed to recall
  (``in_context``), so they are not boosted into the block a second time.
* A recall that could not search by meaning, or at all, is said to the
  model in a registered turn note: an empty block read as "nothing
  remembered". Control: the turn without the note.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory.limits import turn_key


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


class _Response:
    def __init__(self, text: str) -> None:
        self.content = text
        self.tool_calls: list = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.0


class _LLM:
    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        self.seen.append(list(messages or []))
        return _Response("She teaches piano.")


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


CHAT = [
    {"role": "user", "content": "Tell me about Maya, my neighbour."},
    {"role": "assistant", "content": "Maya lives next door and has a sister."},
    {"role": "user", "content": "And what does she teach?"},
]


def _turn(mem, recall_fn, messages=CHAT) -> _LLM:
    from kazma_core.agent.graph_builder import supervisor_node

    mem.mp.setattr("kazma_core.memory.recall.recall", recall_fn)
    llm, fake = _LLM(), _Fake()
    asyncio.run(supervisor_node(
        {"messages": [dict(m) for m in messages], "iteration": 0, "max_iterations": 5,
         "tenant_id": "default", "thread_id": "th-part-f"},
        llm=llm, system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=fake, authority=fake, tracer=fake,
    ))
    return llm


def _spy():
    from kazma_core.memory.recall import RecallResult

    asked: list[tuple[str, dict]] = []

    def spy(query, **kw):
        asked.append((query, kw))
        return RecallResult([], [])

    return asked, spy


def test_a_follow_up_reaches_recall_with_the_message_before_it(mem):
    asked, spy = _spy()
    _turn(mem, spy)
    assert asked, "recall was not called"
    query = asked[0][0]
    assert "Maya" in query and query.endswith("And what does she teach?"), query


def test_without_the_rewrite_recall_gets_the_raw_follow_up(mem):
    """Negative control: the follow-up as typed, which names no one."""
    mem.mp.setattr("kazma_core.memory.question_shape.standalone_query", lambda q, _e: q)
    asked, spy = _spy()
    _turn(mem, spy)
    assert asked[0][0] == "And what does she teach?"


def test_recall_is_told_the_turns_the_model_already_reads(mem):
    asked, spy = _spy()
    _turn(mem, spy)
    seen = asked[0][1].get("in_context")
    assert seen and turn_key("Tell me about Maya, my neighbour.") in seen, seen
    assert turn_key("And what does she teach?") not in seen  # the latest is not a memory yet


INCOMPLETE = "## Memory search was incomplete"


def _degraded_recall(query, **kw):
    from kazma_core.memory.recall import RecallResult

    result = RecallResult([], [])
    result.degraded = "embedder_unavailable"
    return result


def _notes(llm: _LLM) -> list[str]:
    return [str(m.get("content") or "") for m in llm.seen[0] if m.get("role") == "system"]


def test_an_incomplete_recall_is_said_to_the_model(mem):
    llm = _turn(mem, _degraded_recall)
    assert llm.seen, "the model was not called"
    assert any(n.startswith(INCOMPLETE) for n in _notes(llm)), _notes(llm)


def test_a_failed_recall_is_said_to_the_model(mem):
    from kazma_core.memory.recall import _failed_recall

    llm = _turn(mem, lambda query, **kw: _failed_recall("primary_search_failed"))
    assert any(n.startswith(INCOMPLETE) and "could not search at all" in n for n in _notes(llm))


def test_without_the_note_an_incomplete_recall_reads_as_nothing_remembered(mem):
    """Negative control: the turn as it was, the model told nothing."""
    mem.mp.setattr("kazma_core.memory.recall.recall_incomplete_note", lambda _r: "")
    llm = _turn(mem, _degraded_recall)
    assert not any(n.startswith(INCOMPLETE) for n in _notes(llm))


def test_a_whole_recall_adds_no_note(mem):
    asked, spy = _spy()
    llm = _turn(mem, spy)
    assert not any(n.startswith(INCOMPLETE) for n in _notes(llm))
