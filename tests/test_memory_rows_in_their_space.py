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
        self._rows = self.conn.server.answer(text, tuple(params or ()), self.conn.door)

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
    """Rows by id -> tenant, for one table. ``hide``: what the armed lock
    does -- a space's door sees its own space's rows only."""

    def __init__(self, table: str) -> None:
        self.table = table
        self.rows: dict[str, str] = {}
        self.conns: list[_Conn] = []
        self.hide = False

    def connect(self) -> _Conn:
        return _Conn(self)

    def _seen(self, row_id: str, door: Door | None) -> bool:
        if row_id not in self.rows:
            return False
        return not (self.hide and door is not None and door.space
                    and scope.space_of_record(self.rows[row_id]) != door.space)

    def answer(self, sql: str, params: tuple, door: Door | None = None) -> list[tuple]:
        if sql.startswith(f"DELETE FROM {self.table} WHERE id = %s AND COALESCE(NULLIF(tenant_id, ''), "
                          "'default') = %s"):
            row_id, tenant = params
            if self._seen(row_id, door) and scope.space_of_record(self.rows[row_id]) == tenant:
                del self.rows[row_id]
                return [(row_id,)]
            return []
        if sql.startswith(f"DELETE FROM {self.table} WHERE id = %s AND tenant_id = %s"):  # 0b before its review
            row_id, tenant = params
            if self._seen(row_id, door) and self.rows[row_id] == tenant:
                del self.rows[row_id]
                return [(row_id,)]
            return []
        if sql.startswith(f"DELETE FROM {self.table} WHERE id = %s"):
            if not self._seen(params[0], door):
                return []
            del self.rows[params[0]]
            return [(params[0],)]
        if sql.startswith(f"SELECT 1 FROM {self.table} WHERE id = %s"):
            return [(1,)] if self._seen(params[0], door) else []
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
    # The second keeps the request's door, said as another space's row's (0b review M1).
    assert [(c.door.space, c.door.how) for c in _writes(mirror.server)] == [("family", "scope"), ("family", "cross")]


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spaces enforced in this process (no lock armed: the stand-in hides
    nothing unless asked)."""
    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr("kazma_core.memory.state_backend._KEPT_SAID", set(), raising=False)
    monkeypatch.setattr("kazma_core.memory.backends._VECTOR_KEPT_SAID", set(), raising=False)


_NAMED = "DELETE FROM {} WHERE id = %s AND COALESCE(NULLIF(tenant_id, ''), 'default') = %s RETURNING id"


def test_a_fact_kept_under_another_space_is_not_gone(mirror: Any, enforced: None,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    """H5: the delete answered True whatever it found, and the outbox took the
    fact off its queue -- a forgotten fact stayed in the mirror for good."""
    mirror.server.rows["b1"] = "default"
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.state_backend"):
        assert mirror.delete_belief("b1", tenant_id="family") is False
    assert mirror.server.rows == {"b1": "default"}, "another space's fact is not touched"
    assert any("kept under another space" in r.getMessage() for r in caplog.records)
    [delete] = _writes(mirror.server)
    assert delete.statements[0] == (_NAMED.format("kazma_beliefs"), ("b1", "family"))
    assert (delete.door.space, delete.door.how) == ("family", "row")


def test_a_fact_deleted_or_never_there_is_gone(mirror: Any, enforced: None) -> None:
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


def test_a_vector_kept_under_another_space_is_not_erased(vectors: Any, enforced: None,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    """Review 13, H5: the delete dropped its tenant and the forget journal
    recorded the vector as erased."""
    vectors.server.rows["e1"] = "default"
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.backends"):
        assert vectors.delete("e1", tenant_id="family") is False
    assert vectors.server.rows == {"e1": "default"}
    [delete] = _writes(vectors.server)
    assert delete.statements[0] == (_NAMED.format("kazma_memory_vectors"), ("e1", "family"))
    assert (delete.door.space, delete.door.how) == ("family", "row")
    assert any("kept under another space" in r.getMessage() for r in caplog.records)
    vectors.server.rows["e2"] = "family"
    assert vectors.delete("e2", tenant_id="family") is True and "e2" not in vectors.server.rows


def test_the_forget_journal_keeps_a_vector_the_index_still_holds(vectors: Any, enforced: None,
                                                                 monkeypatch: pytest.MonkeyPatch,
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


#: Each pass over every space's chats, and what it reads first.
_PASSES = [
    ("kazma_core.memory.rehydrate", "run_rehydrate_pass", "kazma_core.paths.primary_memory_db"),
    ("kazma_core.memory.turn_reconcile", "run_turn_reconcile_pass", "kazma_core.memory.turn_reconcile._open_primary"),
    ("kazma_core.memory.text_fill", "run_text_fill_pass", "kazma_core.paths.primary_memory_db"),
]


def _first_read_records(monkeypatch: pytest.MonkeyPatch, target: str,
                        seen: list[Door | None] | None = None) -> list[Door | None]:
    seen = [] if seen is None else seen

    def records(*a: Any, **k: Any) -> None:
        seen.append(Door.now())
        return None

    monkeypatch.setattr(target, records)
    return seen


@pytest.mark.parametrize(("module", "name", "first_read"), _PASSES)
def test_memorys_passes_over_every_space_are_the_installs(monkeypatch: pytest.MonkeyPatch, module: str,
                                                          name: str, first_read: str) -> None:
    """Review 19: with nothing bound, every read of the chats and checkpoints
    went through no door; an empty answer read as "every conversation was
    read" (H5). 0b review M2: the pass itself says it, so a request that
    runs it (the memory restore) is the install's too."""
    import importlib

    run = getattr(importlib.import_module(module), name)
    seen = _first_read_records(monkeypatch, first_read)
    run()
    with bind_scope("family", "layla"):
        run()
    assert len(seen) == 2 and all(d is not None and d.space == "" and d.reason for d in seen), seen


@pytest.mark.parametrize("sweep", ["_recover_erased_memories", "_reconcile_memory_turns", "_fill_cut_memory_text"])
def test_memorys_sweeps_run_the_passes(monkeypatch: pytest.MonkeyPatch, sweep: str) -> None:
    from kazma_core.memory import sweep_cursors, worker_bootstrap

    class _Cursor:
        skip = False
        idle = False

    class _Skip:
        def __enter__(self) -> _Cursor:
            return _Cursor()

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(sweep_cursors, "idle_skip", lambda name: _Skip())
    seen = _first_read_records(monkeypatch, "kazma_core.paths.primary_memory_db")
    _first_read_records(monkeypatch, "kazma_core.memory.turn_reconcile._open_primary", seen)
    getattr(worker_bootstrap, sweep)()
    assert len(seen) == 1 and seen[0] is not None and seen[0].space == "" and seen[0].reason


def test_the_memory_restore_reads_every_spaces_chats_as_the_install(monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Any) -> None:
    """0b review M2: ``POST /api/system/memory/restore`` runs turn reconcile in
    the admin's request. In its space's door, once armed, the reconcile reads
    that space's chats only and moves the one cursor past every other
    space's: their turns never reach memory (§15 F)."""
    from kazma_core.memory import backup, forget

    monkeypatch.setattr(backup, "_is_memory_backup", lambda path: True)
    monkeypatch.setattr("kazma_core.db.sqlite_snapshot.snapshot_copy", lambda src, dest: None)
    monkeypatch.setattr(backup, "_bring_schema_up_to_date", lambda path: None)
    monkeypatch.setattr("kazma_core.memory.graph_sync.mark_full_due", lambda why: None)
    monkeypatch.setattr(forget, "converge", lambda: {})
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: str(tmp_path / "memory.db"))
    seen = _first_read_records(monkeypatch, "kazma_core.memory.turn_reconcile._open_primary")
    with bind_scope("family", "layla"):
        backup.restore_memory_backup(tmp_path / "backup.db")
    assert len(seen) == 1 and seen[0] is not None and seen[0].space == "", seen
    assert "every space's chats" in seen[0].reason


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


# ── the 0b review ────────────────────────────────────────────────────────


def test_a_space_door_for_another_spaces_row_is_said_cross() -> None:
    """M1, L3: a request bound to one space -- or a declared single-row
    door -- reaching another space's row keeps its door (nothing moves, H6)
    and says so as ``cross``, which step 1's report counts as a finding."""
    with bind_scope("family", "layla"), row_statements("default"):
        assert (Door.now(), Door.now().how) == (Door(space="family"), "cross")
    with bind_scope("default", "owner"), statements_of("family"), row_statements("default"):
        assert (Door.now(), Door.now().how) == (Door(space="family"), "cross"), "L3: not moved to default"
    # The controls: its own space's row, and Kazma's own work.
    with bind_scope("family", "layla"), row_statements("family"):
        assert Door.now().how == "scope"
    with install_statements("a sweep"), row_statements("default"):
        assert (Door.now(), Door.now().how) == (Door(space="default"), "row")


def test_the_report_counts_a_cross_door_as_a_finding(monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    import importlib.util
    from pathlib import Path as _Path

    from kazma_core.db import space_doors

    monkeypatch.setattr(space_doors, "_on", True)
    monkeypatch.setattr(space_doors, "_entries", {})
    monkeypatch.setattr(space_doors, "_said", set())
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="kazma_core.db.space_doors"):
        with bind_scope("family", "layla"), row_statements("default"):
            space_doors.checkout("memory mirror")
        with bind_scope("family", "layla"), row_statements("family"):
            space_doors.checkout("memory mirror")
    kinds = sorted(e["door"] for e in space_doors.entries())
    assert kinds == ["cross", "space"], kinds
    assert any("another space's door" in r.getMessage() for r in caplog.records)
    script = _Path(__file__).resolve().parent.parent / "scripts" / "space_doors_report.py"
    spec = importlib.util.spec_from_file_location("space_doors_report_0b", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = module.summarise({"entries": space_doors.entries()})
    assert [f["door"] for f in summary["findings"]] == ["cross"] and summary["new"]


def test_a_delete_from_another_spaces_door_is_never_gone(mirror: Any, vectors: Any, enforced: None,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    """M1: once armed, the door of a request bound to another space sees
    neither the row nor its absence; its "nothing there" was answered as
    gone, and pgvector's forget recorded the vector as erased."""
    for server in (mirror.server, vectors.server):
        server.hide = True
    mirror.server.rows["b1"] = "default"
    vectors.server.rows["e1"] = "default"
    with caplog.at_level(logging.WARNING), bind_scope("family", "layla"):
        assert mirror.delete_belief("b1", tenant_id="default") is False
        assert vectors.delete("e1", tenant_id="default") is False
    assert mirror.server.rows == {"b1": "default"} and vectors.server.rows == {"e1": "default"}
    assert sum("another space's door" in r.getMessage() for r in caplog.records) == 2
    # The control: the row's own space's door deletes it, and says gone.
    with bind_scope("default", "owner"):
        assert mirror.delete_belief("b1", tenant_id="default") is True
        assert vectors.delete("e1", tenant_id="default") is True
    assert mirror.server.rows == {} and vectors.server.rows == {}


def test_the_forget_queues_a_vector_a_door_of_another_space_cannot_see(vectors: Any, enforced: None,
                                                                        monkeypatch: pytest.MonkeyPatch,
                                                                        tmp_path: Any) -> None:
    """M1 through the forget path: the vector stays queued (§15 I)."""
    from kazma_core.memory import forget, forget_journal

    monkeypatch.setattr(forget_journal, "_connect", lambda: _journal(tmp_path))
    monkeypatch.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: vectors)
    vectors.server.hide = True
    vectors.server.rows["e1"] = "default"
    with bind_scope("family", "layla"):
        assert forget._delete_remote_vector(sqlite3.connect(":memory:"), "e1", "default") is False
    pending = _journal(tmp_path).execute("SELECT item_id, tenant_id FROM forget_remote_pending").fetchall()
    assert pending == [("e1", "default")]


def test_spaces_off_deletes_by_id_as_before(mirror: Any, vectors: Any) -> None:
    """L1: with Spaces off a copy held under another tenant string ('' from
    before S1-02, a vector moved by an older upsert) was no longer deleted
    and stayed pending for good; it is by id, as before 0b."""
    mirror.server.rows["b1"] = "family"
    vectors.server.rows.update({"e1": "", "e2": "telegram:42"})
    assert mirror.delete_belief("b1", tenant_id="default") is True and mirror.server.rows == {}
    assert vectors.delete("e1", tenant_id="default") is True
    assert vectors.delete("e2", tenant_id="default") is True and vectors.server.rows == {}
    [statement] = {s for c in _writes(vectors.server) for s, _p in c.statements if s.startswith("DELETE")}
    assert statement == "DELETE FROM kazma_memory_vectors WHERE id = %s RETURNING id"


def test_an_empty_tenant_is_the_owners_and_kept_is_said_once(vectors: Any, enforced: None,
                                                            caplog: pytest.LogCaptureFixture) -> None:
    """L1 once enforced: '' is the owner's (decision M6); a vector kept under
    another space is said once, not every pass."""
    vectors.server.rows.update({"e1": "", "e2": "family"})
    assert vectors.delete("e1", tenant_id="default") is True and "e1" not in vectors.server.rows
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.backends"):
        assert vectors.delete("e2", tenant_id="default") is False
        assert vectors.delete("e2", tenant_id="default") is False
    assert sum("e2" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING) == 1


def test_a_new_reason_for_the_same_id_is_said_again(vectors: Any, enforced: None,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    """Delta review L-d: keyed by id alone, a later reason was said at DEBUG only."""
    vectors.server.hide = True
    vectors.server.rows["e1"] = "default"
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.backends"):
        with bind_scope("family", "layla"):
            assert vectors.delete("e1", tenant_id="default") is False  # a door that cannot see it
        # Not armed here: the row's own door sees the copy kept under another
        # space (armed, it would not: delta review L-b, the arming step's proof).
        vectors.server.hide = False
        assert vectors.delete("e1", tenant_id="family") is False  # kept under another space
        assert vectors.delete("e1", tenant_id="family") is False  # the same again: DEBUG
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and "e1" in r.getMessage()]
    assert len(said) == 2 and "another space's door" in said[0] and "kept under another space" in said[1], said


@pytest.mark.parametrize("wrapper", ["search_state_episodes", "search_state_beliefs"])
def test_a_refused_search_is_never_no_matches_through_the_wrappers(monkeypatch: pytest.MonkeyPatch,
                                                                   wrapper: str) -> None:
    """L2: the module's wrappers answered [] for a refusal when not strict."""
    from kazma_core.memory import state_backend

    class _RefusedError(Exception):
        pass

    class _Backend:
        def search_episodes(self, *a: Any, **k: Any) -> list:
            raise _RefusedError("refused")

        search_beliefs = search_episodes

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: _Backend())
    monkeypatch.setattr(state_backend, "is_space_refusal", lambda exc: isinstance(exc, _RefusedError))
    with pytest.raises(_RefusedError):
        getattr(state_backend, wrapper)("x", tenant_id="default")
    # The control: another failure is still "no matches" when not strict.
    monkeypatch.setattr(state_backend, "is_space_refusal", lambda exc: False)
    assert getattr(state_backend, wrapper)("x", tenant_id="default") == []


def test_every_fetch_takes_strict() -> None:
    """L4: once recall passes strict=True, a backend without it raises TypeError."""
    from kazma_core.memory.state_backend import _NullStateBackend

    assert _NullStateBackend().fetch_episodes(["e1"], strict=True) == []
    assert _NullStateBackend().fetch_beliefs(["b1"], strict=True) == []


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
def test_a_mirrored_fact_is_gone_only_from_its_own_space_on_postgres(real_mirror: Any, enforced: None) -> None:
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


@pytest.mark.postgres
def test_spaces_off_a_mirrored_fact_is_deleted_by_id_on_postgres(real_mirror: Any) -> None:
    """0b review L1, on Postgres: with Spaces off the delete is by id, as
    before 0b, whatever tenant string the copy was written under."""
    fact = f"s401-{uuid.uuid4().hex[:12]}"
    assert real_mirror.mirror_belief(_fact(fact, "telegram:42")) is True
    assert real_mirror.delete_belief(fact, tenant_id="default") is True
    assert real_mirror.fetch_beliefs([fact], tenant_id="telegram:42") == []
