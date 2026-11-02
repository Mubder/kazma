"""Forgetting a turn takes back the lessons it taught (roadmap theme 5, 2026-11).

A lesson learned from a chat turn joins every later prompt (the Soul,
``skills/self_improvement``). Since FA-212 a lesson comes only from a turn
memory would keep, and it records the chat and turn it came from -- but
nothing read that back: a turn the user forgot afterwards kept teaching.
``forget_lessons_from`` takes those lessons out of the Soul and its history;
forgetting a turn (``forget_episode``, a rewind's too) and a chat
(``forget_chat``) call it. A lesson another turn also taught stays.

The forget without the call (the code before theme 5) is the negative control.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.skills import self_improvement as si

KEY = "self_improvement.agent_evolution"
FIRST = "Answer travel questions with the local currency next to every price."
SECOND = "When asked for a schedule, give times in the user's own time zone."


@pytest.fixture()
def soul(tmp_path, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setattr(si, "_soul_requires_confirm", lambda: False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.close()
    dual_write._reset_mirror()
    store = get_config_store()
    store.delete(KEY)
    yield
    store.delete(KEY)
    dual_write._reset_mirror()


def _teach(lesson: str, chat: str, turn: int) -> None:
    assert si.apply_agent_mutation(si._DEFAULT_AGENT_ID, lesson, source={"chat": chat, "turn": turn})


def _lessons() -> list[str]:
    return si._lessons(si.get_agent_evolution_block())[1]


def _memory_of(chat: str, turn: int) -> str:
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id=chat, turn_number=turn, user_text=f"question {turn} of {chat}",
                         assistant_text="an answer", created_at=time.time() - 3600)
    assert eid
    return eid


def test_forgetting_a_turn_takes_back_its_lesson(soul):
    _teach(FIRST, "chat-a", 2)
    _teach(SECOND, "chat-b", 1)
    assert si.forget_lessons_from(["chat-a"], 2) == 1
    assert _lessons() == [SECOND]


def test_a_lesson_another_turn_also_taught_stays(soul):
    """Taught twice, it is in the Soul twice: one copy goes, the other turn's stays."""
    _teach(FIRST, "chat-a", 2)
    _teach(FIRST, "chat-b", 5)
    assert _lessons() == [FIRST, FIRST]
    assert si.forget_lessons_from(["chat-a"], 2) == 1
    assert _lessons() == [FIRST]
    assert si.forget_lessons_from(["chat-b"], 5) == 1
    assert _lessons() == []


def test_forgetting_a_chat_takes_back_every_lesson_it_taught(soul):
    _teach(FIRST, "chat-a", 1)
    _teach(SECOND, "chat-a", 4)
    assert si.forget_lessons_from(["chat-a", "thread-of-chat-a"], None) == 2
    assert _lessons() == []


def test_the_real_forget_takes_it_back(soul):
    from kazma_core.memory import forget

    eid = _memory_of("th-lesson", 1)
    _teach(FIRST, "th-lesson", 1)
    _teach(SECOND, "th-other", 1)
    out = forget.forget_episode(eid, tenant_id="default", by="user")
    assert out["ok"] and out["lessons_removed"] == 1
    assert _lessons() == [SECOND]


def test_forgetting_the_chat_through_memory_takes_them_back(soul):
    from kazma_core.memory import forget

    _memory_of("th-chat", 1)
    _teach(FIRST, "th-chat", 1)
    _teach(SECOND, "th-chat", 3)  # a turn with no memory of its own
    out = forget.forget_chat("th-chat", tenant_id="default")
    assert out["ok"] and out["lessons_removed"] == 2
    assert _lessons() == []


def test_negative_control_the_old_forget_kept_the_lesson(soul, monkeypatch):
    from kazma_core.memory import forget

    monkeypatch.setattr(si, "forget_lessons_from", lambda *a, **k: 0)
    eid = _memory_of("th-old", 1)
    _teach(FIRST, "th-old", 1)
    forget.forget_episode(eid, tenant_id="default", by="user")
    assert _lessons() == [FIRST]
