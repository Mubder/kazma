"""Plant one word through every memory writer, then read every table for it.

Shared by the forget-completeness gate (``test_memory_store_registry.py``,
Memory audit M01) and the forget-convergence matrix
(``test_memory_forget_converges.py``, M08/M10).
"""

from __future__ import annotations

import sqlite3
import struct
import time
from collections.abc import Iterator
from types import SimpleNamespace

WORD = "Zorblaxquint"
QUESTION = f"remember my project codename is {WORD} alpha"
ANSWER = f"Noted: your project codename is {WORD} alpha, and I will keep it in mind."
# A word FTS5 stores lower-cased and prefix-compressed: its tail is in any
# segment that holds it.
TAIL = b"blaxquint"


class _Vectors:
    """A remote vector index: what was put there, and what was taken out."""

    available = True

    def __init__(self) -> None:
        self.held: set[str] = set()

    def upsert(self, item_id, vec, *, tenant_id="default", meta=None):
        self.held.add(str(item_id))
        return True

    def delete(self, item_id, *, tenant_id="default"):
        self.held.discard(str(item_id))
        return True

    def search(self, *a, **k):
        return []

    def similarities(self, *a, **k):
        return {}


class _Encoder:
    def encode(self, text):
        return [1.0, 0.0, 0.0, 0.0]


def open_mem(tmp_path, monkeypatch) -> Iterator[SimpleNamespace]:
    """A private memory database pair with a recording remote index (a
    fixture body: ``yield from open_mem(tmp_path, monkeypatch)``)."""
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    vectors = _Vectors()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Encoder())
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob",
                        lambda text: struct.pack("4f", 1.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda *a, **k: vectors)
    monkeypatch.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: vectors)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db, ops=ops, vectors=vectors)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _chat(mem) -> None:
    """One web chat with two keys (session s1, thread th1)."""
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(
            session_id="s1", tenant_id="default", thread_id="th1", title="Notes",
            messages=[{"role": "user", "content": QUESTION}, {"role": "assistant", "content": ANSWER}],
        ))
    finally:
        store.close()


def _plant(mem) -> dict:
    """The word, through every writer that can put a turn's words somewhere."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.consolidator import _defer_turn_memory
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_ui import memory_api

    _chat(mem)
    eid = mirror_episode(session_id="th1", turn_number=1, user_text=QUESTION, assistant_text=ANSWER,
                         created_at=time.time() - 3600)
    # The agent's note from the same turn (the memory_store tool's shape).
    note = mirror_episode(session_id="memory_store", turn_number=0, user_text=f"codename {WORD} alpha",
                          source="memory_store_tool", metadata={"chat": "th1", "chat_turn": 1})
    # Two statements in the turn: the second supersedes the first, and the
    # audit log records both values.
    first = mutate_belief(mem.db, "user", "project_codename", f"{WORD} alpha", ops_conn=mem.ops,
                          predicate_type="functional", extraction_method="user_explicit",
                          source_session="th1", source_turn=1)
    second = mutate_belief(mem.db, "user", "project_codename", f"{WORD} beta", ops_conn=mem.ops,
                           predicate_type="functional", extraction_method="user_explicit",
                           source_session="th1", source_turn=1)
    mem.db.commit()
    assert first["action"] in ("append", "supersede", "transition"), first
    assert second["superseded_id"] == first["belief_id"], second
    # The operator corrects it: the old value moves to operator_edit.previous.
    assert memory_api._edit_belief_sync(second["belief_id"], {"object": f"{WORD} gamma"})["ok"]
    # Hygiene's "Archive invalidated": the superseded value moves to beliefs_archive.
    assert memory_api._archive_invalidated_sync()["archived"] >= 1
    # A turn the extraction pool deferred: the queue carries its words.
    _defer_turn_memory([{"role": "user", "content": QUESTION}, {"role": "assistant", "content": ANSWER}],
                       session_id="th1", turn=1, tenant_id="default", why="test")
    # The tables a one-off 2026-08 operation left on older installs.
    mem.db.execute(
        "CREATE TABLE episodes_archive (id TEXT, tenant_id TEXT, session_id TEXT, turn_number INTEGER, "
        "user_text TEXT, assistant_text TEXT, summary_text TEXT, tier TEXT, metadata_json TEXT, "
        "embedding BLOB, archived_at REAL)")
    mem.db.execute("INSERT INTO episodes_archive VALUES ('legacy-1', 'default', 'old', 1, ?, ?, '', "
                   "'episodic', '{}', x'00', 0)", (QUESTION, ANSWER))
    mem.db.execute("CREATE TABLE entities_archive (id TEXT, type TEXT, name TEXT, metadata_json TEXT, "
                   "aliases_json TEXT)")
    mem.db.execute("INSERT INTO entities_archive VALUES ('chunk-1', 'memory_chunk', ?, '{}', '[]')",
                   (ANSWER[:200],))
    mem.db.commit()
    # A tool call whose argument carried the word.
    _record_tool_call({"text": f"{WORD} alpha"})
    return {"eid": eid, "note": note, "facts": [first["belief_id"], second["belief_id"]]}


def _record_tool_call(arguments: dict) -> None:
    from kazma_core.agent import tool_registry as tr

    tr._PROCEDURAL_STOPPING.clear()
    tr._record_procedural_outcome("probe_tool", arguments, success=True)
    tr._PROCEDURAL_QUEUE.join()


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()]


def _holding(conn: sqlite3.Connection) -> set[str]:
    """``table.column`` of every value in the database that holds the word."""
    found: set[str] = set()
    for table in _tables(conn):
        cur = conn.execute(f'SELECT * FROM "{table}"')
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            for col, value in zip(cols, row, strict=True):
                if isinstance(value, str) and WORD.lower() in value.lower():
                    found.add(f"{table}.{col}")
                elif isinstance(value, bytes) and (TAIL in value.lower() or WORD.lower().encode() in value.lower()):
                    found.add(f"{table}.{col}")
    return found


def _everywhere(mem) -> set[str]:
    """Where the word is held: both memory databases, and the forget journal."""
    found: set[str] = set()
    for prefix, name in (("", "memory_state.db"), ("ops:", "memory_ops.db"),
                         ("journal:", "memory_forget_journal.db")):
        path = mem.dir / name
        if not path.exists():
            continue
        conn = sqlite3.connect(str(path))
        try:
            found |= {f"{prefix}{x}" for x in _holding(conn)}
        finally:
            conn.close()
    return found


