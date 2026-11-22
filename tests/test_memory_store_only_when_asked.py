"""memory_store writes only when the user's own words ask to remember.

The memory plan's finding 1 (22 Nov 2026). memory_store's description began
"Use when the user shares personal info" and ended "Only use this tool when
the user EXPLICITLY asks". On "My sister Noura works as a dentist." the
model followed the first sentence: the fact was kept twice, once as the turn
(Kazma keeps every turn and learns the facts the user states) and once as
the model's note, and the real-use suite forgot one copy while the other
still answered.

Now the tool stores a note only when the running turn's latest user message,
in the user's own words (``remember_request.is_remember_request`` through
``own_words``, English and Arabic), asks to remember; otherwise it refuses
before anything is written (``RefusedBeforeEffect``) and says the turn is
already remembered. Work with no user message -- a swarm worker -- is
refused the same way. The description says the same, and its contradictory
opening is gone.

Held through the real tool and the product's turn writer, in English and in
Arabic: a told fact is kept once; "remember this" is still stored; a quoted
"remember" is not the user's asking. The control: today's tool (the rule
taken out) keeps the told fact twice.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory.schema_v2 import ensure_primary_schema

from tests._tool_answer import answer_now

THREAD = "th-store-only-when-asked"

TOLD = {
    "en": ("My sister Noura works as a dentist.", "Sister Noura is a dentist"),
    "ar": ("أختي نورة تشتغل طبيبة أسنان.", "أخت المستخدم نورة طبيبة أسنان"),
}
ASKED = {
    "en": ("Remember that my sister Noura is a dentist.", "Sister Noura is a dentist"),
    "ar": ("تذكر أن أختي نورة طبيبة أسنان.", "أخت المستخدم نورة طبيبة أسنان"),
}
QUOTED = {
    "en": 'Translate to French: "Remember that my sister Noura is a dentist."',
    "ar": 'ترجم للإنجليزي: "تذكر أن أختي نورة طبيبة أسنان."',
}


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db)
    dual_write._reset_mirror()
    db.close()


def _store(text: str, *, user_said: str | None) -> str:
    """memory_store as the tool worker calls it, on a turn whose latest user
    message is *user_said* (None: no user message, as a swarm worker)."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
    from kazma_core.tools.export_session import reset_current_session_messages, set_current_session_messages

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    messages = [] if user_said is None else [{"role": "user", "content": user_said}]
    t_tok = set_current_thread_id(THREAD)
    m_tok = set_current_session_messages(messages)
    try:
        return answer_now(registry.get_tool("memory_store").func, text=text)
    finally:
        reset_current_session_messages(m_tok)
        reset_current_thread_id(t_tok)


def _remember_the_turn(said: str) -> None:
    """The turn itself, written as every turn is (the post-turn writer)."""
    from kazma_core.memory.dual_write import mirror_episode

    mirror_episode(session_id=THREAD, turn_number=1, user_text=said, assistant_text="Noted.",
                   source="chat", tenant_id="default")


def _copies(db, word: str) -> int:
    """How many memories hold *word*: the turn, and any note."""
    return int(db.execute("SELECT COUNT(*) FROM episodes WHERE user_text LIKE ? AND tier != 'forgotten'",
                          (f"%{word}%",)).fetchone()[0])


def _notes(db) -> int:
    return int(db.execute("SELECT COUNT(*) FROM episodes WHERE session_id = 'memory_store'").fetchone()[0])


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_told_fact_is_kept_once(mem, language) -> None:
    said, note = TOLD[language]
    _remember_the_turn(said)
    out = _store(note, user_said=said)
    assert out.startswith("Error: Not stored") and "already remembered" in out, out
    assert _notes(mem.db) == 0
    assert _copies(mem.db, "نورة" if language == "ar" else "Noura") == 1
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_todays_tool_keeps_a_told_fact_twice(mem, monkeypatch, language) -> None:
    from kazma_core.agent.tool_builtins import memory as memory_tools

    monkeypatch.setattr(memory_tools, "_asked_to_remember", lambda: True)
    said, note = TOLD[language]
    _remember_the_turn(said)
    assert _store(note, user_said=said).startswith("Stored memory")
    assert _notes(mem.db) == 1 and _copies(mem.db, "نورة" if language == "ar" else "Noura") == 2


@pytest.mark.parametrize("language", ["en", "ar"])
def test_remember_this_is_still_stored(mem, language) -> None:
    said, note = ASKED[language]
    out = _store(note, user_said=said)
    assert out.startswith("Stored memory"), out
    assert _notes(mem.db) == 1


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_quoted_remember_is_not_the_users_asking(mem, language) -> None:
    out = _store(ASKED[language][1], user_said=QUOTED[language])
    assert out.startswith("Error: Not stored"), out
    assert _notes(mem.db) == 0


def test_work_with_no_user_message_stores_nothing(mem) -> None:
    """A swarm worker's call: no user message, so no one asked."""
    out = _store("Sister Noura is a dentist", user_said=None)
    assert out.startswith("Error: Not stored") and _notes(mem.db) == 0


def test_the_description_no_longer_says_both() -> None:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    described = registry.get_tool("memory_store").description
    assert "ONLY when the user's own words ask you to remember" in described
    assert "Use when the user shares personal info" not in described
    invalidate = registry.get_tool("memory_invalidate").description
    assert "NEVER call it to record a change the user told you" in invalidate
    assert "Removes stale/duplicate facts" not in invalidate
