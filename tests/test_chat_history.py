"""Kazma's conversation history, read from wherever it is stored.

``kazma_core.memory.chat_history`` is what the past-chats fallback and the
memory recovery read: the chat store the web UI writes (Postgres or SQLite)
with its save spool beside it, and LangGraph checkpoint history. Until
2026-09-26 the fallback opened only the SQLite file -- on a Postgres install
a leftover with 5 sessions from July, against 258 in Postgres -- and ranked
only the 400 most recent matches, with no tenant filter.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from kazma_core.memory import chat_history

_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    tenant_id TEXT, session_id TEXT, messages TEXT, created_at TEXT, total_cost REAL,
    total_tokens INTEGER, thread_id TEXT, updated_at TEXT DEFAULT '', title TEXT DEFAULT '',
    archived INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0, PRIMARY KEY (tenant_id, session_id)
);
"""


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    return tmp_path


def _store(data: Path):
    from kazma_ui.session_manager import SessionManager

    return SessionManager(db_path=str(data / "chat_sessions.db"))


def _save(data, session_id, messages, *, thread_id="", title="", tenant="default"):
    from kazma_ui.session_manager import ChatSession

    store = _store(data)
    try:
        store.put(ChatSession(session_id=session_id, tenant_id=tenant, thread_id=thread_id,
                              title=title, messages=list(messages)))
    finally:
        store.close()


def test_the_web_ui_and_memory_share_one_spool_rule(tmp_path):
    """The spool the web UI writes is the file memory reads: one rule."""
    from kazma_core.paths import chat_spool_db
    from kazma_ui.session_spool import spool_path_for

    for name in ("chat_sessions.db", "chat_sessions_test.db", "other.db"):
        path = tmp_path / name
        assert spool_path_for(str(path)) == str(chat_spool_db(path))
    assert chat_spool_db(tmp_path / "chat_sessions.db").name == "chat_sessions_spool.db"


def test_a_conversation_is_read_with_its_spooled_copy(data):
    """A save the database refused waits in the spool; both copies count."""
    from kazma_ui.session_spool import SessionSpool, spool_path_for

    _save(data, "s1", [{"role": "user", "content": "stored"}], thread_id="th-1")
    spool = SessionSpool(spool_path_for(str(data / "chat_sessions.db")))
    try:
        spool.save("default", "s1", {"session_id": "s1", "thread_id": "th-1",
                                     "messages": [{"role": "user", "content": "spooled"}]})
    finally:
        spool.close()
    for key in ("s1", "th-1"):
        copies = chat_history.conversations_for(key, strict=True)
        contents = sorted(m["content"] for c in copies for m in c["messages"])
        assert contents == ["spooled", "stored"]
        assert {c["thread_id"] for c in copies} == {"th-1"}
    assert chat_history.conversations_for("nobody", strict=True) == []


def _checkpoint(thread_id, messages):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from kazma_core.paths import checkpoints_db

    conn = sqlite3.connect(checkpoints_db(), check_same_thread=False)
    try:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        ckpt = empty_checkpoint()
        ckpt["channel_values"] = {"messages": list(messages)}
        ckpt["channel_versions"] = {"messages": 1}
        saver.put({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}, ckpt,
                  {"source": "loop", "step": 1}, {"messages": 1})
    finally:
        conn.close()


def test_checkpoint_history_is_read_from_the_saver(data):
    from langchain_core.messages import AIMessage, HumanMessage

    from kazma_core.memory.rehydrate import NOTE_CALL_MARK

    _checkpoint("plain", [HumanMessage("hello"), AIMessage("hi there")])
    _checkpoint("noted", [HumanMessage("remember x"), AIMessage(
        "", tool_calls=[{"name": "memory_store", "args": {"text": "x"}, "id": "c1"}])])
    versions = chat_history.checkpoint_message_versions("plain", strict=True)
    assert versions == [[{"role": "user", "content": "hello"},
                         {"role": "assistant", "content": "hi there"}]]
    assert chat_history.checkpoint_message_versions("plain", containing=NOTE_CALL_MARK) == []
    assert len(chat_history.checkpoint_message_versions("noted", containing=NOTE_CALL_MARK)) == 1
    assert chat_history.threads_containing(NOTE_CALL_MARK, strict=True) == ["noted"]
    assert chat_history.threads_containing(NOTE_CALL_MARK, after="noted", strict=True) == []


def test_a_store_never_created_is_empty_not_unreadable(data):
    """An install that never used checkpoints has no history: that is an
    answer, and a strict caller must not treat it as an outage (on Postgres
    the same case is a missing checkpoint_blobs table)."""
    from kazma_core.paths import checkpoints_db

    sqlite3.connect(checkpoints_db()).close()  # a database with no tables yet
    assert chat_history.checkpoint_message_versions("t", strict=True) == []
    assert chat_history.threads_containing(b"x", strict=True) == []
    sqlite3.connect(data / "chat_sessions.db").close()
    assert chat_history.conversations_for("s", strict=True) == []


def test_an_unreadable_store_is_empty_unless_the_caller_asks(data):
    from kazma_core.paths import checkpoints_db

    Path(checkpoints_db()).write_bytes(b"not a database, " * 64)
    assert chat_history.checkpoint_message_versions("t") == []
    assert chat_history.threads_containing(b"x") == []
    with pytest.raises(sqlite3.DatabaseError):
        chat_history.checkpoint_message_versions("t", strict=True)
    with pytest.raises(sqlite3.DatabaseError):
        chat_history.threads_containing(b"x", strict=True)


# ── Search across every session ───────────────────────────────────────────


def _raw_sessions(path: Path, rows):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SESSIONS_DDL)
        conn.executemany(
            "INSERT INTO sessions (tenant_id, session_id, thread_id, title, messages, created_at) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


#: The fallback's query until 2026-09-26: the 400 most recent matches, any tenant.
OLD_WINDOW_SQL = (
    "SELECT session_id FROM sessions WHERE (title LIKE ? OR messages LIKE ?) "
    "ORDER BY created_at DESC LIMIT 400"
)


def test_the_best_match_is_found_however_old_it_is(data):
    path = data / "chat_sessions.db"
    rows = [("default", "oldest", "t0", "Saffron naming shortlist",
             '[{"role":"user","content":"names for the saffron kitten: Saffron, Zafran"}]',
             "2026-01-01T00:00:00")]
    for i in range(1, 1000):
        rows.append(("default", f"s{i:04d}", f"t{i}", "misc",
                     '[{"role":"user","content":"a saffron latte"}]' if i % 2 else "[]",
                     f"2026-09-{(i % 28) + 1:02d}T{i % 24:02d}:00:00"))
    _raw_sessions(path, rows)

    old = sqlite3.connect(path)
    try:
        window = [r[0] for r in old.execute(OLD_WINDOW_SQL, ("%saffron%", "%saffron%"))]
    finally:
        old.close()
    assert "oldest" not in window  # negative control: outside the old window

    hits = chat_history.search_sessions(["saffron"], limit=3, sqlite_path=path)
    assert hits[0]["session_id"] == "oldest"
    assert hits[0]["score"] == 5 + 2  # title, and twice in its messages


def test_search_stays_inside_the_tenant_and_skips_the_current_chat(data):
    path = data / "chat_sessions.db"
    _raw_sessions(path, [
        ("default", "mine", "t1", "boat trip", '[{"role":"user","content":"boat"}]', "2026-09-01"),
        ("default", "current", "t2", "boat now", '[{"role":"user","content":"boat"}]', "2026-09-02"),
        ("acme", "theirs", "t3", "boat plans", '[{"role":"user","content":"boat boat"}]', "2026-09-03"),
    ])
    ids = [h["session_id"] for h in chat_history.search_sessions(
        ["boat"], exclude_session_id="current", limit=5, sqlite_path=path)]
    assert ids == ["mine"]
    assert [h["session_id"] for h in chat_history.search_sessions(
        ["boat"], tenant_id="acme", limit=5, sqlite_path=path)] == ["theirs"]


def test_a_spooled_conversation_is_searched_too(data):
    from kazma_ui.session_spool import SessionSpool, spool_path_for

    path = data / "chat_sessions.db"
    _raw_sessions(path, [("default", "s1", "t1", "", "[]", "2026-09-01")])
    spool = SessionSpool(spool_path_for(str(path)))
    try:
        spool.save("default", "s1", {"session_id": "s1", "thread_id": "t1", "title": "",
                                     "created_at": "2026-09-01",
                                     "messages": [{"role": "user", "content": "the quokka photos"}]})
    finally:
        spool.close()
    hits = chat_history.search_sessions(["quokka"], limit=3, sqlite_path=path)
    assert [h["session_id"] for h in hits] == ["s1"]


@pytest.mark.postgres
def test_search_reads_the_store_the_web_ui_writes(data):
    """Written through SessionManager, found without naming a file: on the CI
    Postgres job both sides are Postgres, as on the live install."""
    token = f"zq{uuid.uuid4().hex[:10]}"
    sid = f"hist-{uuid.uuid4().hex[:8]}"
    _save(data, sid, [{"role": "user", "content": f"the {token} plan"},
                      {"role": "assistant", "content": "noted"}], title="plans")
    hits = chat_history.search_sessions([token], limit=3)
    assert [h["session_id"] for h in hits] == [sid]
    assert token in hits[0]["messages"]
    copies = chat_history.conversations_for(sid, strict=True)
    assert [m["content"] for m in copies[0]["messages"]][0] == f"the {token} plan"
