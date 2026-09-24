"""A reply the database refused must survive a restart.

2026-09-24: a NUL character in one tool result made Postgres refuse every
save of one chat. The reply lived only in the process's memory, and the
restart that picked up the fix discarded a finished 2,882-char answer. The
NUL is fixed separately; these tests are about the CLASS -- whatever makes
the primary store refuse a save, the reply is kept in the local spool,
served on every load, and written to the primary once it accepts it.

"Restart" here is a new SessionManager on the same files, which is exactly
what a new process does. The primary store is whatever the test run
selects (SQLite by default; Postgres in the CI Postgres job).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from kazma_ui.session_manager import SessionManager
from kazma_ui.session_spool import SessionSpool, merge_spooled, spool_path_for

ANSWER = "the finished answer the user must not lose"


def _refuse(monkeypatch):
    def boom(self, session):
        raise RuntimeError("database refused the write")

    monkeypatch.setattr(SessionManager, "_upsert_db", boom)


def _accept(monkeypatch):
    monkeypatch.undo()


@pytest.fixture
def alerts(monkeypatch):
    seen: list[str] = []
    import kazma_core.observability.ops_alerts as ops

    monkeypatch.setattr(ops, "alert", lambda key, *a, **k: seen.append(key))
    return seen


def _primary_text(sm: SessionManager, sid: str) -> str:
    """What the PRIMARY store holds, read raw (no spool overlay)."""
    if sm._pg:
        from kazma_core.db.pg_helpers import get_pool

        row = get_pool().execute_one(
            "SELECT messages::text AS m FROM kazma_chat_sessions "
            "WHERE tenant_id = %s AND session_id = %s",
            ("default", sid),
        )
        return row["m"] if row else ""
    conn = sqlite3.connect(sm.db_path)
    try:
        row = conn.execute(
            "SELECT messages FROM sessions WHERE tenant_id = ? AND session_id = ?",
            ("default", sid),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def _texts(sess) -> str:
    return " | ".join(str(m.get("content") or "") for m in (sess.messages if sess else []))


def _new(db) -> SessionManager:
    return SessionManager(db_path=str(db))


def test_a_refused_save_survives_a_restart_and_heals(tmp_path, monkeypatch, alerts):
    db = tmp_path / "chat_sessions.db"
    sid = f"spool-{uuid.uuid4().hex[:8]}"

    sm = _new(db)
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "draft the posts", "ts": "t0"})

    _refuse(monkeypatch)
    with sm.transact(sid) as sess:  # must not raise: the spool took it
        sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    assert "session.persist_spooled" in alerts
    assert ANSWER not in _primary_text(sm, sid)
    sm.close()

    # Restart while the database still refuses: the reply is still there.
    sm2 = _new(db)
    assert ANSWER in _texts(sm2.get(sid))
    sm2.close()

    # The database accepts writes again: the boot drain writes it through.
    _accept(monkeypatch)
    sm3 = _new(db)
    assert ANSWER in _primary_text(sm3, sid)
    assert sm3._spool is not None and sm3._spool.keys() == set()
    sm3.close()


class _Create:
    """``get_or_create`` + ``put`` as a context, so the first write is a save."""

    def __init__(self, sm: SessionManager, sid: str) -> None:
        self.sm, self.sid = sm, sid

    def __enter__(self):
        self.sess = self.sm.get_or_create(self.sid)
        return self.sess

    def __exit__(self, *exc):
        self.sm.put(self.sess)
        return False


def test_refresh_from_the_database_keeps_a_spooled_reply(tmp_path, monkeypatch, alerts):
    """The Web UI refreshes gateway sessions from the DB; that used to drop it."""
    sid = f"gw-telegram-{uuid.uuid4().hex[:8]}"
    sm = _new(tmp_path / "chat_sessions.db")
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})
    _refuse(monkeypatch)
    with sm.transact(sid) as sess:
        sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    sm._refresh_from_db(sid)
    assert ANSWER in _texts(sm.get(sid))
    sm.close()


def test_the_next_accepted_save_clears_the_spool(tmp_path, monkeypatch, alerts):
    sid = f"spool-{uuid.uuid4().hex[:8]}"
    sm = _new(tmp_path / "chat_sessions.db")
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})
    _refuse(monkeypatch)
    with sm.transact(sid) as sess:
        sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    assert sm._spool.keys()
    _accept(monkeypatch)
    with sm.transact(sid) as sess:
        sess.messages.append({"role": "user", "content": "next", "ts": "t2"})
    assert sm._spool.keys() == set()
    assert ANSWER in _primary_text(sm, sid)
    sm.close()


def test_a_chat_whose_every_save_was_refused_is_still_listed(tmp_path, monkeypatch, alerts):
    db = tmp_path / "chat_sessions.db"
    sid = f"spool-{uuid.uuid4().hex[:8]}"
    sm = _new(db)
    _refuse(monkeypatch)
    with _Create(sm, sid) as sess:
        sess.thread_id = f"thread-{sid}"
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})
        sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    sm.close()

    sm2 = _new(db)
    assert sid in {s.session_id for s in sm2.list_all(prune_empty=False)}
    sm2._sessions.clear()  # force the database path of the thread lookup
    assert ANSWER in _texts(sm2.get_by_thread_id(f"thread-{sid}"))
    sm2.close()


def test_deleting_a_chat_also_drops_its_spooled_copy(tmp_path, monkeypatch, alerts):
    db = tmp_path / "chat_sessions.db"
    sid = f"spool-{uuid.uuid4().hex[:8]}"
    sm = _new(db)
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})
    _refuse(monkeypatch)
    with sm.transact(sid) as sess:
        sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    _accept(monkeypatch)
    sm.delete(sid)
    sm.close()
    sm2 = _new(db)
    assert sm2.get(sid) is None, "a deleted chat came back from the spool"
    sm2.close()


def test_when_the_spool_also_refuses_the_save_still_fails_loudly(tmp_path, monkeypatch):
    """Both stores refusing must reach the caller, whose alert says NOT saved."""
    sid = f"spool-{uuid.uuid4().hex[:8]}"
    sm = _new(tmp_path / "chat_sessions.db")
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})
    _refuse(monkeypatch)

    def spool_boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(SessionSpool, "save", spool_boom)
    with pytest.raises(RuntimeError, match="database refused"):
        with sm.transact(sid) as sess:
            sess.messages.append({"role": "assistant", "content": ANSWER, "turn_id": "t1"})
    sm.close()


def test_a_genuine_postgres_refusal_is_spooled(tmp_path, monkeypatch, alerts):
    """Today's actual failure, not a patched one: Postgres rejecting a NUL.

    The NUL-stripping encoder is switched off so the database refuses for
    real (UntranslatableCharacter), exactly as it did live.
    """
    import json

    import kazma_core.db.pg_helpers as pg_helpers
    import kazma_core.db.postgres_pool as postgres_pool

    db = tmp_path / "chat_sessions.db"
    sid = f"spool-{uuid.uuid4().hex[:8]}"
    sm = _new(db)
    if not sm._pg:
        sm.close()
        pytest.skip("needs the Postgres backend (CI Postgres job)")
    with _Create(sm, sid) as sess:
        sess.messages.append({"role": "user", "content": "q", "ts": "t0"})

    monkeypatch.setattr(pg_helpers, "json_dumps", lambda v: json.dumps(v))
    monkeypatch.setattr(postgres_pool, "_without_nul", lambda p: p)
    with sm.transact(sid) as sess:
        sess.messages.append(
            {"role": "assistant", "content": ANSWER + chr(0), "turn_id": "t1"}
        )
    assert "session.persist_spooled" in alerts, "Postgres accepted the NUL?"
    assert ANSWER not in _primary_text(sm, sid)
    sm.close()

    monkeypatch.undo()  # the fix is back: the restart heals it
    sm2 = _new(db)
    assert ANSWER in _primary_text(sm2, sid)
    assert sm2._spool.keys() == set()
    sm2.close()


def test_the_spool_sits_next_to_its_database():
    assert spool_path_for(":memory:") == ":memory:"
    assert spool_path_for("/d/chat_sessions.db").replace("\\", "/") == "/d/chat_sessions_spool.db"


def test_a_row_another_writer_added_after_the_spool_is_kept():
    spooled = {
        "updated_at": "2026-09-24T10:01:00+00:00",
        "messages": [
            {"role": "user", "content": "q1", "ts": "2026-09-24T10:00:00+00:00"},
            {"role": "assistant", "content": ANSWER, "turn_id": "t1",
             "ts": "2026-09-24T10:01:00+00:00"},
        ],
    }
    primary = {
        "updated_at": "2026-09-24T10:30:00+00:00",
        "messages": [
            {"role": "user", "content": "q1", "ts": "2026-09-24T10:00:00+00:00"},
            {"role": "user", "content": "q2", "ts": "2026-09-24T10:30:00+00:00"},
        ],
    }
    merged = merge_spooled(primary, spooled)
    assert [m["content"] for m in merged["messages"]] == ["q1", ANSWER, "q2"]
    # ...and when the spool is the newer side, it is the answer as written.
    assert merge_spooled({**primary, "updated_at": "2026-09-24T09:00:00+00:00"}, spooled)[
        "messages"
    ] == spooled["messages"]
