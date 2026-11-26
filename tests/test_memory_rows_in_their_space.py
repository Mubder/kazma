"""Memory's copies in Postgres say whose rows they are (Spaces S4-01, step 0b).

The memory mirror and the pgvector index hold every space's memories in one
table each. Their writes ran with nothing bound, or in the space of whoever
started them; their schema was made on the caller's connection and committed
there; their deletes dropped the row's tenant and answered "deleted" whatever
the statement found (review rows 10, 12, 13, 19-23, 28, 40, 41, M9; H5, H6).

Each test fails on the code before 0b. No database: the connections are
stand-ins that record what each one was asked and the door it was checked out
through (``row_work.Door.now``, what the database door asks); the real pools
are in ``tests/test_space_doors_postgres.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.row_work import Door, install_statements, row_statements, statements_of
from kazma_core.spaces.scope import bind_scope, install_work

# ── the rule: a row Kazma keeps for itself ───────────────────────────────


def _inside(enter: Any) -> Door | None:
    with enter():
        with row_statements("family"):
            return Door.now()


def test_where_kazma_does_the_work_the_row_names_its_space() -> None:
    assert _inside(lambda: install_statements("a sweep over every space")) == Door(space="family")
    assert _inside(lambda: install_work("a sweep over every space")) == Door(space="family")
    with row_statements("family"):
        assert Door.now() == Door(space="family"), "nothing bound: Kazma's own work"


def test_where_a_request_does_it_the_bound_space_stays() -> None:
    """H6: a store never moves a request's statements into another space; a
    write into another space is said where it starts, and declared."""
    assert _inside(lambda: bind_scope("default", "owner")) == Door(space="default")
    with bind_scope("default", "owner"):
        with statements_of("family"):  # said where it starts
            with row_statements("family"):
                assert Door.now() == Door(space="family")
    # The control: statements_of moves it whatever is bound.
    with bind_scope("default", "owner"), statements_of("family"):
        assert Door.now() == Door(space="family")


# ── a stand-in Postgres ──────────────────────────────────────────────────


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self._rows: list[tuple] = []
        self.description: list[tuple] | None = None

    def execute(self, sql: str, params: Any = ()) -> None:
        text = " ".join(sql.split())
        self.conn.statements.append((text, tuple(params or ())))
        self._rows = self.conn.server.answer(text, tuple(params or ()))

    def executemany(self, sql: str, seq: Any) -> None:
        for params in seq:
            self.execute(sql, params)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        return None


class _Conn:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.door = Door.now()  # what the database door asks at the checkout
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0
        server.conns.append(self)

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Server:
    """Rows by id -> tenant, for one table."""

    def __init__(self, table: str) -> None:
        self.table = table
        self.rows: dict[str, str] = {}
        self.conns: list[_Conn] = []

    def connect(self) -> _Conn:
        return _Conn(self)

    def answer(self, sql: str, params: tuple) -> list[tuple]:
        if sql.startswith(f"DELETE FROM {self.table} WHERE id = %s AND tenant_id = %s"):
            row_id, tenant = params
            if self.rows.get(row_id) == tenant:
                del self.rows[row_id]
                return [(row_id,)]
            return []
        if sql.startswith(f"DELETE FROM {self.table} WHERE id = %s"):
            return [(params[0],)] if self.rows.pop(params[0], None) is not None else []
        if sql.startswith(f"SELECT 1 FROM {self.table} WHERE id = %s"):
            return [(1,)] if params[0] in self.rows else []
        if "_STATE_" in sql or "pg_extension" in sql:
            return [(True, True, True, None, True)]
        return []


@pytest.fixture
def mirror(monkeypatch: pytest.MonkeyPatch) -> Any:
    from kazma_core.memory.state_backend import PostgresStateBackend

    server = _Server("kazma_beliefs")
    backend = PostgresStateBackend(f"postgresql://kazma@db.example/{uuid.uuid4().hex}")
    monkeypatch.setattr(backend, "_connect", server.connect)
    monkeypatch.setattr(PostgresStateBackend, "_make_tables",
                        staticmethod(lambda ensure_schema, cur: cur.execute("CREATE TABLE kazma_beliefs (...)")),
                        raising=False)  # 0b's; the code before made them on the caller's connection
    backend.server = server
    return backend


def _writes(server: _Server) -> list[_Conn]:
    return [c for c in server.conns if not any(s.startswith("CREATE") for s, _p in c.statements)]


def _fact(row_id: str, tenant: str) -> dict[str, Any]:
    return {"id": row_id, "tenant_id": tenant, "subject": "user", "predicate": "lives_in", "object": "Kuwait"}


# ── the mirror ───────────────────────────────────────────────────────────


def test_the_mirrors_tables_are_made_on_a_connection_of_their_own(mirror: Any) -> None:
    """Review 22: made on the caller's connection and committed there, the
    caller's statement ran in a second transaction -- no space's, once the
    lock is armed."""
    assert mirror.mirror_belief(_fact("b1", "family")) is True
    made, write = mirror.server.conns
    assert made.statements[0][0].startswith("CREATE") and made.commits == 1
    assert made.door is not None and made.door.space == "" and "tables" in made.door.reason
    assert write.commits == 1 and not any(s.startswith("CREATE") for s, _p in write.statements)
    assert mirror.mirror_belief(_fact("b2", "family")) is True
    assert len(mirror.server.conns) == 3, "made once a process"


def test_a_sweeps_pushes_are_written_each_in_its_rows_space(mirror: Any) -> None:
    rows = [dict(_fact("e1", "family"), user_text="a"), dict(_fact("e2", "default"), user_text="b"),
            dict(_fact("e3", "family"), user_text="c")]
    assert sorted(mirror.mirror_episode_rows(rows)) == ["e1", "e2", "e3"]
    writes = _writes(mirror.server)
    assert sorted((c.door.space, c.door.how) for c in writes) == [("default", "row"), ("family", "row")]
    assert all(c.commits == 1 for c in writes), "one transaction a space"


def test_a_requests_own_pushes_stay_in_its_space(mirror: Any) -> None:
    with bind_scope("family", "layla"):
        mirror.mirror_belief(_fact("b1", "family"))
        mirror.mirror_belief(_fact("b2", "default"))  # not this request's to move: refused once armed
    assert [(c.door.space, c.door.how) for c in _writes(mirror.server)] == [("family", "scope"), ("family", "scope")]


def test_a_fact_kept_under_another_space_is_not_gone(mirror: Any, caplog: pytest.LogCaptureFixture) -> None:
    """H5: the delete answered True whatever it found, and the outbox took the
    fact off its queue -- a forgotten fact stayed in the mirror for good."""
    mirror.server.rows["b1"] = "default"
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.state_backend"):
        assert mirror.delete_belief("b1", tenant_id="family") is False
    assert mirror.server.rows == {"b1": "default"}, "another space's fact is not touched"
    assert any("still in the Postgres mirror" in r.getMessage() for r in caplog.records)
    [delete] = _writes(mirror.server)
    assert delete.statements[0] == ("DELETE FROM kazma_beliefs WHERE id = %s AND tenant_id = %s RETURNING id",
                                    ("b1", "family"))
    assert (delete.door.space, delete.door.how) == ("family", "row")


def test_a_fact_deleted_or_never_there_is_gone(mirror: Any) -> None:
    mirror.server.rows["b1"] = "family"
    assert mirror.delete_belief("b1", tenant_id="family") is True and mirror.server.rows == {}
    assert mirror.delete_belief("b2", tenant_id="family") is True, "never mirrored: nothing to keep"
    mirror.server.rows["b3"] = "default"
    assert mirror.delete_belief("b3") is True, "by id where the caller cannot name it (the sweep's outbox)"


def test_the_callers_that_know_the_facts_space_name_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.memory import state_backend

    asked: list[tuple[str, str | None]] = []

    class _Backend:
        def delete_belief(self, row_id: str, *, tenant_id: str | None = None) -> bool:
            asked.append((row_id, tenant_id))
            return True

    class _OldBackend:
        def delete_belief(self, row_id: str) -> bool:  # a stand-in of before
            asked.append((row_id, None))
            return True

    monkeypatch.setattr(state_backend, "get_state_backend", _Backend)
    assert state_backend.unmirror_belief_to_state("b1", "family") is True
    monkeypatch.setattr(state_backend, "get_state_backend", _OldBackend)
    assert state_backend.unmirror_belief_to_state("b2") is True
    assert asked == [("b1", "family"), ("b2", None)]
    import inspect

    from kazma_core.memory import macro_sleep
    from kazma_ui.routes_direct import memory as memory_routes

    assert "unmirror_belief_to_state(bid, tenant_id)" in inspect.getsource(macro_sleep)
    assert "unmirror_belief_to_state(str(bid), tid)" in inspect.getsource(memory_routes)


def test_the_probe_is_a_ping_of_the_install(mirror: Any) -> None:
    """Review 10: unbound, the probe turned the mirror off for every space."""
    with bind_scope("family", "layla"):
        assert mirror.available is True
    [ping] = mirror.server.conns
    assert ping.door.space == "" and "ping" in ping.door.reason


@pytest.mark.parametrize("method", ["fetch_episodes", "fetch_beliefs", "search_episodes", "search_beliefs"])
def test_a_refused_read_is_never_nothing_found(mirror: Any, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    """Review 40: a refusal answered [] even on recall's strict path."""
    from kazma_core.spaces.scope import NoSpaceBound

    def refused() -> Any:
        raise NoSpaceBound("no space is bound to this work")

    monkeypatch.setattr(mirror, "_connect", refused)
    monkeypatch.setattr(mirror, "_ensure", lambda: None)
    ask = getattr(mirror, method)
    args = (["e1"],) if method.startswith("fetch") else ("trip to kuwait",)
    assert ask(*args, tenant_id="family") == [], "Spaces off: as before"
    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(NoSpaceBound):
        ask(*args, tenant_id="family")


@pytest.mark.parametrize("method", ["fetch_episodes", "fetch_beliefs"])
def test_a_strict_fetch_raises_what_the_database_raised(mirror: Any, monkeypatch: pytest.MonkeyPatch,
                                                        method: str) -> None:
    def away() -> Any:
        raise ConnectionError("the server went away")

    monkeypatch.setattr(mirror, "_connect", away)
    monkeypatch.setattr(mirror, "_ensure", lambda: None)
    assert getattr(mirror, method)(["e1"], tenant_id="family") == []
    with pytest.raises(ConnectionError):
        getattr(mirror, method)(["e1"], tenant_id="family", strict=True)


# ── pgvector ─────────────────────────────────────────────────────────────


@pytest.fixture
def vectors(monkeypatch: pytest.MonkeyPatch) -> Any:
    from kazma_core.memory.backends import PgvectorBackend

    server = _Server("kazma_memory_vectors")
    backend = PgvectorBackend(dsn=f"postgresql://kazma@db.example/{uuid.uuid4().hex}")
    monkeypatch.setattr(backend, "_connect", server.connect)
    monkeypatch.setattr(PgvectorBackend, "_make_table",
                        lambda self, conn, key: conn.cursor().execute("CREATE TABLE kazma_memory_vectors (...)"),
                        raising=False)
    monkeypatch.setattr(backend, "probe", lambda *, force=False: "installed")
    backend.server = server
    return backend


def test_a_vectors_table_is_made_on_a_connection_of_its_own(vectors: Any) -> None:
    """Review 23: up to three commits on the caller's connection before its
    own statement."""
    assert vectors.upsert("e1", [0.1, 0.2], tenant_id="family") is True
    made, write = vectors.server.conns
    assert made.statements[0][0].startswith("CREATE") and made.door.space == ""
    assert write.commits == 1 and not any(s.startswith("CREATE") for s, _p in write.statements)


def test_a_vector_is_written_in_its_rows_space(vectors: Any) -> None:
    """Review 20: the turns' vectors, the text fill and the repair wrote them
    with nothing bound."""
    vectors.upsert("e1", [0.1, 0.2], tenant_id="family")
    with bind_scope("default", "owner"):
        vectors.upsert("e2", [0.1, 0.2], tenant_id="default")
    assert [(c.door.space, c.door.how) for c in _writes(vectors.server)] == [("family", "row"), ("default", "scope")]


def test_a_vector_kept_under_another_space_is_not_erased(vectors: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Review 13, H5: the delete dropped its tenant and the forget journal
    recorded the vector as erased."""
    vectors.server.rows["e1"] = "default"
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.backends"):
        assert vectors.delete("e1", tenant_id="family") is False
    assert vectors.server.rows == {"e1": "default"}
    [delete] = _writes(vectors.server)
    assert delete.statements[0] == (
        "DELETE FROM kazma_memory_vectors WHERE id = %s AND tenant_id = %s RETURNING id", ("e1", "family"))
    assert (delete.door.space, delete.door.how) == ("family", "row")
    assert any("kept under another space" in r.getMessage() for r in caplog.records)
    vectors.server.rows["e2"] = "family"
    assert vectors.delete("e2", tenant_id="family") is True and "e2" not in vectors.server.rows


def test_the_forget_journal_keeps_a_vector_the_index_still_holds(vectors: Any, monkeypatch: pytest.MonkeyPatch,
                                                                 tmp_path: Any) -> None:
    from kazma_core.memory import forget_journal

    monkeypatch.setattr(forget_journal, "_connect", lambda: _journal(tmp_path))
    monkeypatch.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: vectors)
    vectors.server.rows["e1"] = "default"
    forget_journal.queue_remote_delete("e1", "family")
    assert forget_journal.retry_remote_deletes(sqlite3.connect(":memory:")) == {"deleted": 0, "pending": 1}
    vectors.server.rows["e1"] = "family"
    assert forget_journal.retry_remote_deletes(sqlite3.connect(":memory:")) == {"deleted": 1, "pending": 0}


def _journal(folder: Any) -> sqlite3.Connection:
    conn = sqlite3.connect(folder / "journal.db", isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS forget_remote_pending (item_id TEXT PRIMARY KEY, tenant_id TEXT, "
                 "added_at REAL, attempts INTEGER DEFAULT 0)")
    return conn


def test_the_vector_probe_is_a_ping_of_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 41: unbound, "unreachable" for every space."""
    from kazma_core.memory.backends import PgvectorBackend

    server = _Server("kazma_memory_vectors")
    backend = PgvectorBackend(dsn=f"postgresql://kazma@db.example/{uuid.uuid4().hex}")
    monkeypatch.setattr(backend, "_connect", server.connect)
    with bind_scope("family", "layla"):
        backend.probe(force=True)
    [ping] = server.conns
    assert ping.door.space == "" and "ping" in ping.door.reason


# ── the install's own passes ─────────────────────────────────────────────


@pytest.mark.parametrize(("sweep", "inner"), [
    ("_recover_erased_memories", "kazma_core.memory.rehydrate.run_rehydrate_pass"),
    ("_reconcile_memory_turns", "kazma_core.memory.turn_reconcile.run_turn_reconcile_pass"),
    ("_fill_cut_memory_text", "kazma_core.memory.text_fill.run_text_fill_pass"),
])
def test_memorys_sweeps_over_every_space_say_so(monkeypatch: pytest.MonkeyPatch, sweep: str, inner: str) -> None:
    """Review 19: with nothing bound, every read of the chats and checkpoints
    went through no door; an empty answer read as "every conversation was
    read" (H5)."""
    from kazma_core.memory import sweep_cursors, worker_bootstrap

    seen: list[Door | None] = []

    def records(*a: Any, **k: Any) -> dict[str, Any]:
        seen.append(Door.now())
        return {}

    monkeypatch.setattr(inner, records)

    class _Cursor:
        skip = False
        idle = False

    class _Skip:
        def __enter__(self) -> _Cursor:
            return _Cursor()

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(sweep_cursors, "idle_skip", lambda name: _Skip())
    getattr(worker_bootstrap, sweep)()
    assert len(seen) == 1 and seen[0] is not None and seen[0].space == "" and seen[0].reason


def test_the_forget_convergence_is_the_installs_and_each_row_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 21: run by the sweep, a chat move that stays and a memory
    restore, over every space's pending forgets."""
    from kazma_core.memory import forget, forget_journal

    seen: list[Door | None] = []

    def apply_pending(conn: Any) -> dict[str, Any]:
        seen.append(Door.now())
        with row_statements("family"):
            seen.append(Door.now())
        return {}

    monkeypatch.setattr(forget_journal, "apply_pending", apply_pending)
    monkeypatch.setattr(forget_journal, "retry_remote_deletes", lambda conn: {})
    for name in ("enforce_ledger", "finish_old_tombstones", "_forget_departed_entries"):
        monkeypatch.setattr(forget, name, lambda conn: {})
    with bind_scope("default", "owner"):  # a chat move's request
        forget.converge(sqlite3.connect(":memory:"))
    assert seen[0] is not None and seen[0].space == "" and "forget" in seen[0].reason
    assert seen[1] == Door(space="family"), "a row it removes, in that row's own space"


def test_the_mirrors_comparison_is_the_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 28: the sync read and pushed every space's rows with nothing bound."""
    from kazma_core.memory import state_backend

    seen: list[Door | None] = []

    def backend() -> Any:
        seen.append(Door.now())
        return state_backend._NullStateBackend()

    monkeypatch.setattr(state_backend, "get_state_backend", backend)
    monkeypatch.setattr(state_backend, "mirrors_to_postgres", lambda: False)
    state_backend.sync_state_mirror(sqlite3.connect(":memory:"))
    assert seen[0] is not None and seen[0].space == "" and "mirror" in seen[0].reason


# ── on a real Postgres ───────────────────────────────────────────────────


@pytest.fixture
def real_mirror(monkeypatch: pytest.MonkeyPatch) -> Any:
    import os

    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    pytest.importorskip("psycopg_pool")
    from kazma_core.db import space_doors
    from kazma_core.memory.state_backend import PostgresStateBackend

    monkeypatch.delenv("KAZMA_SPACE_DOOR_REPORT", raising=False)
    space_doors.reset()
    backend = PostgresStateBackend(dsn)
    try:
        yield backend
    finally:
        backend.close()
        space_doors.reset()


@pytest.mark.postgres
def test_a_mirrored_fact_is_gone_only_from_its_own_space_on_postgres(real_mirror: Any) -> None:
    from kazma_core.db import space_doors

    fact = f"s401-{uuid.uuid4().hex[:12]}"
    assert real_mirror.mirror_belief(_fact(fact, "default")) is True
    assert real_mirror.delete_belief(fact, tenant_id="family") is False, "another space's: kept, said"
    assert real_mirror.fetch_beliefs([fact], tenant_id="default"), "still there"
    assert real_mirror.delete_belief(fact, tenant_id="default") is True
    assert real_mirror.fetch_beliefs([fact], tenant_id="default") == []
    assert real_mirror.delete_belief(fact, tenant_id="default") is True, "gone stays gone"
    doors = {(e["door"], e["how"], e["reason"]) for e in space_doors.entries() if e["pool"] == "memory mirror"}
    assert ("install", "statements", "the memory mirror's tables, made once for every space") in doors, \
        "the tables on their own connection, as the install's"
    assert ("space", "row", "") in doors, "the writes in the fact's own space"
