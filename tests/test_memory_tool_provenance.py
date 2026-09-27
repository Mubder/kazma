"""What the agent stores with its memory tools belongs to the chat it was said
in (2026-09-27, plan U1).

The memory tools wrote a note under the session "memory_store" and facts with
no source, so "forget this chat", "forget this turn" and "don't remember this
chat" could not reach them: on the live install a test chat's token
("pebble-live") outlived the chat as a current fact. Every note and fact a
memory tool writes now names the chat and turn (the thread the tool worker
bound, the turn index the turn's memory carries), and a chat kept out of
memory stores nothing.

Held here with the real tool, each with its negative control, and a source
gate: every memory write in the agent's tool code names its chat.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import forget
from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]
THREAD = "th-tool-notes"


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


def _store(text: str, *, thread: str | None = THREAD, turns: int = 1) -> str:
    """The memory_store tool, called as the tool worker calls it: in a chat
    whose turn *turns* is running."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
    from kazma_core.tools.export_session import reset_current_session_messages, set_current_session_messages

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    tool = registry.get_tool("memory_store")
    messages = []
    for i in range(turns):
        messages += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    t_tok = set_current_thread_id(thread)
    m_tok = set_current_session_messages(messages)
    try:
        return asyncio.run(tool.func(text=text))
    finally:
        reset_current_session_messages(m_tok)
        reset_current_thread_id(t_tok)


def _note(db, text):
    return db.execute(
        "SELECT id, tier, user_text, metadata_json FROM episodes WHERE session_id = 'memory_store' "
        "AND (user_text = ? OR tier = 'forgotten') ORDER BY created_at DESC", (text,)
    ).fetchone()


def _fact(db, text):
    return db.execute(
        "SELECT id, object, source_session, source_turn, invalidated_at, metadata_json FROM beliefs "
        "WHERE predicate = 'noted' AND (object = ? OR metadata_json LIKE '%forgotten%')", (text,)
    ).fetchone()


def test_a_tool_note_names_its_chat_and_turn(mem):
    out = _store("Remember the token pebble-live.", turns=3)
    assert out.startswith("Stored memory"), out
    note = _note(mem.db, "Remember the token pebble-live.")
    assert json.loads(note["metadata_json"])["chat"] == THREAD
    assert json.loads(note["metadata_json"])["chat_turn"] == 3
    fact = _fact(mem.db, "Remember the token pebble-live.")
    assert (fact["source_session"], fact["source_turn"]) == (THREAD, 3)


def test_forgetting_the_chat_forgets_what_the_agent_stored_in_it(mem):
    _store("Remember the token pebble-live.")
    out = forget.forget_chat(THREAD, tenant_id="default", conn=mem.db)
    assert out["ok"] and out["forgotten"] >= 1 and out["facts_forgotten"] >= 1
    note = mem.db.execute("SELECT tier, user_text FROM episodes WHERE session_id = 'memory_store'").fetchone()
    assert (note["tier"], note["user_text"]) == ("forgotten", "")
    fact = mem.db.execute("SELECT object, invalidated_at FROM beliefs WHERE predicate = 'noted'").fetchone()
    assert fact["object"] == "" and fact["invalidated_at"] is not None


def test_a_note_stored_outside_any_chat_is_not_the_chats(mem):
    """Negative control: the note as it was written before 2026-09-27 -- no
    chat -- survives the chat's forget, which is the hole this closes."""
    _store("Remember the token pebble-live.", thread=None)
    forget.forget_chat(THREAD, tenant_id="default", conn=mem.db)
    note = mem.db.execute("SELECT tier, user_text FROM episodes WHERE session_id = 'memory_store'").fetchone()
    assert note["tier"] != "forgotten" and note["user_text"]
    fact = mem.db.execute("SELECT object, invalidated_at FROM beliefs WHERE predicate = 'noted'").fetchone()
    assert fact["object"] and fact["invalidated_at"] is None


def test_forgetting_a_turn_forgets_the_notes_of_that_turn_only(mem):
    from kazma_core.memory.dual_write import mirror_episode

    _store("note from turn one", turns=1)
    _store("note from turn two", turns=2)
    eid = mirror_episode(session_id=THREAD, turn_number=2, user_text="q1", assistant_text="a1")
    assert forget.forget_episode(eid, tenant_id="default", conn=mem.db)["ok"]
    rows = {r["user_text"] or r["id"]: r["tier"] for r in mem.db.execute(
        "SELECT id, user_text, tier FROM episodes WHERE session_id = 'memory_store'")}
    assert rows.get("note from turn one") not in (None, "forgotten")
    assert "note from turn two" not in rows  # emptied with its turn
    facts = {r["source_turn"]: r["object"] for r in mem.db.execute(
        "SELECT source_turn, object FROM beliefs WHERE predicate = 'noted'")}
    assert facts[1] == "note from turn one" and facts[2] == ""


def test_a_chat_kept_out_of_memory_stores_nothing_through_the_tools(mem):
    forget.set_chat_remembered(THREAD, False, tenant_id="default", conn=mem.db)
    out = _store("Remember the token pebble-live.")
    assert out.startswith("Not stored"), out
    assert mem.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


def test_the_same_tool_stores_in_a_remembered_chat(mem):
    """Negative control for the refusal: memory on, the tool stores."""
    forget.set_chat_remembered(THREAD, False, tenant_id="default", conn=mem.db)
    forget.set_chat_remembered(THREAD, True, tenant_id="default", conn=mem.db)
    assert _store("Remember the token pebble-live.").startswith("Stored memory")


# ── the source gate ───────────────────────────────────────────────────────

#: Memory writers and the keyword that names the chat a write belongs to.
_WRITERS = {"mutate_belief": "source_session", "mirror_episode": "metadata"}
_TOOL_CODE = ("kazma-core/kazma_core/agent", "kazma-skills/kazma_skills")


def unnamed_memory_writes(sources: dict[str, str]) -> list[str]:
    """Calls to a memory writer in agent tool code that do not name the chat."""
    found = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _WRITERS and _WRITERS[name] not in {k.arg for k in node.keywords}:
                found.append(f"{rel}:{node.lineno} {name}")
    return found


def _tool_sources() -> dict[str, str]:
    out = {}
    for root in _TOOL_CODE:
        for path in (REPO / root).rglob("*.py"):
            if "tests" not in path.parts:
                out[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    return out


def test_every_memory_write_in_tool_code_names_its_chat():
    assert unnamed_memory_writes(_tool_sources()) == []


def test_the_gate_sees_a_write_that_names_no_chat():
    """Negative control."""
    src = "def t(conn):\n    mutate_belief(conn, 'user', 'noted', 'x', tenant_id='default')\n"
    assert unnamed_memory_writes({"x.py": src}) == ["x.py:2 mutate_belief"]
