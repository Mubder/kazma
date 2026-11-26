"""The shared pool's space half, armed, on a real Postgres (Spaces S4-01 step 3).

Once the second lock is armed, a checkout of Kazma's own database asks whose
statements these are (``row_security.checkout_space``): a space's go to a
pool that signs in as ``kazma_space`` with the space set for the transaction;
the install's to the owner's pool as today; nothing said is refused before
anything is sent. Nothing arms it in the product yet (step 6); these tests arm
it, on a private database with the rules in place, and prove:

* a space's checkout reads, changes and writes its own rows only, through the
  pool's ``execute`` and ``connection`` alike; the install's sees every row;
  nothing bound is refused (design 6.1);
* a second transaction in one checkout is refused (review M4);
* every connection of the space half is the space role, and a pool whose role
  was dropped from its start options hands out nothing (M10); that failure is
  the space role's own problem while the owner signs in (M1);
* a dead backend at the checkout raises, never runs unset (M3);
* an install checkout inside a space one is refused unless declared (M13);
* not armed, the pool is today's: no space half is opened, nothing more sent;
* the step 3 review: a role or a space a checkout set for its session is gone
  before the next, and a connection that is not the space role is refused
  (M1); an old cursor is refused after the commit (L1); a held connection
  never commits what its store left open (L2); a failed checkout says what
  failed, judged once a minute (L3); a row of no space's shape is refused
  before a connection (L4); a space checkout inside another is refused
  unless declared (L5); what a workload sends is recorded (L7).

Needs a throwaway server and a superuser DSN (``KAZMA_DATABASE_URL`` +
``KAZMA_TEST_ALLOW_REAL_DB=1``); it makes ``kazma_app`` and ``kazma_space``.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.test_pg_app_role import SCRIPT, _every_store_set_up, _with

pytestmark = pytest.mark.postgres

psycopg = pytest.importorskip("psycopg")


def _sql(dsn: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(statement, params or None)
        return list(cur.fetchall()) if cur.description else []


@pytest.fixture(scope="module")
def db() -> Iterator[dict[str, str]]:
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with psycopg.connect(base, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("making roles needs a superuser DSN")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_app'").fetchone():
            pytest.skip("kazma_app already exists on this server: not a throwaway one")
        had_space_role = bool(admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space'").fetchone())
        name = f"kazma_pool_{uuid.uuid4().hex[:10]}"
        admin.execute(f'CREATE DATABASE "{name}"')
    su = _with(base, db=name)
    password = secrets.token_urlsafe(24)
    try:
        _every_store_set_up(su)
        _sql(su, SCRIPT.read_text(encoding="utf-8"))
        _sql(su, f"ALTER ROLE kazma_app PASSWORD '{password}'")
        app = _with(base, user="kazma_app", password=password, db=name)
        from kazma_core.db import row_security

        was = row_security.in_place()
        done = row_security.apply(app, vector_table="")
        row_security._mark_in_place(was)  # this module's apply is no process's
        assert done.complete, (done.refused, done.left)
        _sql(app, "DELETE FROM kazma_chat_sessions")
        _sql(app, "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('sa', 'c1'), ('sb', 'c2'), "
                  "('', 'c3')")
        yield {"base": base, "su": su, "app": app}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            admin.execute("DROP ROLE IF EXISTS kazma_app")
            if not had_space_role:
                admin.execute("DROP ROLE IF EXISTS kazma_space")


@pytest.fixture()
def pool(db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The shared pool on the private database, the lock armed."""
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool

    monkeypatch.setattr(row_security, "_armed", True)
    made = PostgresPool(db["app"], min_size=1, max_size=3)
    try:
        yield made
    finally:
        made.close()


def _sessions(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(str(r["session_id"]) for r in rows)


def test_a_spaces_checkout_reads_and_writes_its_own_rows_only(pool: Any) -> None:
    from kazma_core.spaces.row_work import install_statements, statements_of
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"):
        assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c1"]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user::text AS who")
            assert cur.fetchone()["who"] == "kazma_space"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pool.execute("INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('sb', 'planted')")
        with statements_of("sb"):  # a row's own space, said
            assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c2"]
    with bind_scope("default", "owner"):
        assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c3"], "'' is the owner's"
    # The install's door: every space's rows, as the owner.
    with install_statements("a test of the install's door"):
        assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c1", "c2", "c3"]


def test_nothing_bound_is_refused_before_anything_is_sent(pool: Any) -> None:
    from kazma_core.spaces.scope import NoSpaceBound

    with pytest.raises(NoSpaceBound):
        pool.execute("SELECT session_id FROM kazma_chat_sessions")
    assert pool._space_pool is None, "refused before any connection was asked for"


def test_a_second_transaction_in_one_checkout_is_refused(pool: Any) -> None:
    from kazma_core.db.row_security import _SecondTransaction
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"), pool.connection() as conn:
        old = conn.cursor()
        old.execute("SELECT 1 FROM kazma_chat_sessions LIMIT 1")
        conn.commit()
        with pytest.raises(_SecondTransaction):
            conn.execute("SELECT 1 FROM kazma_chat_sessions WHERE session_id = 'nothing'")
        with pytest.raises(_SecondTransaction):  # a cursor of the first transaction too (step 3 review L1)
            old.execute("SELECT 1 FROM kazma_chat_sessions WHERE session_id = 'nothing'")
    # The next checkout is a fresh transaction in its space.
    with bind_scope("sa", "owner"):
        assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c1"]
    # The control: the install's checkout is the owner's, as today.
    with install_statements("a test of the install's door"), pool.connection() as conn:
        conn.execute("SELECT 1")
        conn.commit()
        conn.execute("SELECT 1")


def test_an_install_checkout_inside_a_space_one_is_refused_unless_declared(pool: Any) -> None:
    from kazma_core.db.row_security import _NestedInstallCheckout, install_inside_a_space
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"), pool.connection():
        with install_statements("a test"), pytest.raises(_NestedInstallCheckout):
            pool.execute("SELECT 1")
        with install_statements("a test"), install_inside_a_space("a test: no lock of the space's is waited for"):
            assert pool.execute("SELECT 1 AS one") == [{"one": 1}]


def test_every_space_connection_is_the_space_role(db: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    """M10, M1: a pool whose start option was dropped (a pooler, an address)
    would sign in as the owner and see every row. Its connections are refused
    as they are made, and the failure is the space role's own, not an outage."""
    from kazma_core.db import availability, row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.scope import bind_scope

    monkeypatch.setattr(row_security, "_armed", True)
    monkeypatch.setattr(row_security, "space_conninfo", lambda url, environ=None: url)  # the option lost
    monkeypatch.setattr(row_security, "_half_said", {})
    monkeypatch.setattr(row_security, "_role_refused", (0.0, ""))
    monkeypatch.setenv("KAZMA_PG_POOL_TIMEOUT", "1")
    noted: list[str] = []
    monkeypatch.setattr(availability, "note_failure", lambda kind, exc=None, **k: noted.append(kind) or True)
    made = PostgresPool(db["app"], min_size=1, max_size=2)
    try:
        with caplog.at_level(logging.WARNING, logger="kazma_core.db.row_security"):
            with bind_scope("sa", "owner"), pytest.raises(psycopg.OperationalError):
                made.execute("SELECT session_id FROM kazma_chat_sessions")
        assert any("connections sign in as kazma_app, not kazma_space" in r.getMessage() for r in caplog.records)
        assert noted == [], "the owner signs in: no outage of the database"
    finally:
        made.close()


def test_a_dead_backend_at_the_checkout_raises(db: dict[str, str]) -> None:
    """M3: libpq's exec_ never raises; its result is read."""
    from kazma_core.db import row_security

    conn = row_security.space_connection_class().connect(row_security.space_conninfo(db["app"]))
    try:
        pid = conn.info.backend_pid
        _sql(db["su"], "SELECT pg_terminate_backend(%s)", (pid,))
        with pytest.raises(psycopg.OperationalError, match="could not set its space"):
            row_security.begin_space(conn, "sa")
    finally:
        conn.close()


def test_not_armed_the_pool_is_todays(db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.scope import bind_scope

    monkeypatch.setattr(row_security, "_armed", False)
    made = PostgresPool(db["app"], min_size=1, max_size=2)
    try:
        with bind_scope("sa", "owner"):
            assert _sessions(made.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c1", "c2", "c3"]
        assert made.execute("SELECT current_user::text AS who") == [{"who": "kazma_app"}]
        assert made._space_pool is None, "no space half while not armed"
    finally:
        made.close()


# ── the step 3 review ────────────────────────────────────────────────────


def test_a_role_a_checkout_set_for_its_session_is_gone_in_the_next(db: dict[str, str],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: a session-level ``SET ROLE NONE`` (and a session ``set_config``) on
    a space connection outlived its checkout: with one connection, the next
    request, in another space, ran as ``kazma_app`` and saw every space's rows."""
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.scope import bind_scope

    monkeypatch.setattr(row_security, "_armed", True)
    made = PostgresPool(db["app"], min_size=1, max_size=1)
    try:
        with bind_scope("sa", "owner"), made.connection() as conn:
            conn.execute("SET ROLE NONE")  # the session's, not the transaction's
            conn.execute("SELECT set_config('kazma.space', 'sb', false)")
            conn.execute("CREATE TEMP TABLE kazma_left AS SELECT session_id FROM kazma_chat_sessions")
            conn.commit()
        with bind_scope("sb", "owner"), made.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user::text AS who, current_setting('kazma.space') AS space, "
                        "to_regclass('pg_temp.kazma_left') IS NULL AS gone")
            assert cur.fetchone() == {"who": "kazma_space", "space": "sb", "gone": True}
            cur.execute("SELECT session_id FROM kazma_chat_sessions")
            assert _sessions(cur.fetchall()) == ["c2"]
    finally:
        made.close()


def test_a_space_connection_that_is_not_the_space_role_is_closed_and_refused(db: dict[str, str],
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """M1, the second guard: a connection handed out as another role (a reset
    that did not run, a pooler) is refused and closed, never used. The
    control is the pool's reset left out."""
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.scope import bind_scope

    monkeypatch.setattr(row_security, "_armed", True)
    monkeypatch.setattr(row_security, "end_space", lambda conn: setattr(conn, "_kazma_out", False))
    made = PostgresPool(db["app"], min_size=1, max_size=1)
    try:
        with bind_scope("sa", "owner"), made.connection() as conn:
            conn.execute("SET ROLE NONE")
            conn.commit()
        with bind_scope("sb", "owner"), pytest.raises(row_security._NotTheSpaceRole):
            made.execute("SELECT session_id FROM kazma_chat_sessions")
        with bind_scope("sb", "owner"):  # a new connection, the space role's
            assert _sessions(made.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c2"]
    finally:
        made.close()


def test_a_held_connection_never_commits_what_its_store_left_open(pool: Any) -> None:
    """L2: an INSERT and then a Python error before the store's commit was
    committed when the held connection was given back."""
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import bind_scope

    marker = f"held-{uuid.uuid4().hex[:8]}"
    insert = "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('sa', %s)"
    try:
        with bind_scope("sa", "owner"):
            held = pool.held_connection()
            held.execute(insert, (marker,))
            held.close()  # the store's commit never came
        with install_statements("a test"):
            assert pool.execute("SELECT 1 FROM kazma_chat_sessions WHERE session_id = %s", (marker,)) == []
        # The control: what the store committed is kept.
        with bind_scope("sa", "owner"):
            held = pool.held_connection()
            held.execute(insert, (marker,))
            held.commit()
            held.close()
        with install_statements("a test"):
            assert len(pool.execute("SELECT 1 FROM kazma_chat_sessions WHERE session_id = %s", (marker,))) == 1
    finally:
        with install_statements("a test"):
            pool.execute("DELETE FROM kazma_chat_sessions WHERE session_id = %s", (marker,))


@pytest.mark.parametrize("why", ["full", "sign-in"])
def test_a_failed_space_checkout_says_what_failed_once_a_minute(db: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                                caplog: pytest.LogCaptureFixture, why: str) -> None:
    """L3: every failing space checkout signed in twice more, and a full pool
    was said as "the space role cannot sign in"."""
    import threading

    from kazma_core.db import availability, postgres_pool, row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.scope import bind_scope

    monkeypatch.setattr(row_security, "_armed", True)
    monkeypatch.setattr(row_security, "_half_said", {})
    monkeypatch.setattr(row_security, "_role_refused", (0.0, ""))
    monkeypatch.setenv("KAZMA_PG_POOL_TIMEOUT", "1")
    noted: list[str] = []
    monkeypatch.setattr(availability, "note_failure", lambda kind, exc=None, **k: noted.append(kind) or True)
    signins: list[str] = []
    real = postgres_pool.refusal_reason
    monkeypatch.setattr(postgres_pool, "refusal_reason", lambda dsn, **k: signins.append(dsn) or real(dsn, **k))
    if why == "sign-in":
        wrong = _with(db["app"], user="kazma_app", password="not-its-password")
        monkeypatch.setattr(row_security, "space_conninfo", lambda url, environ=None: wrong)
    made = PostgresPool(db["app"], min_size=1, max_size=1)
    taken, release = threading.Event(), threading.Event()

    def _hold() -> None:
        with bind_scope("sa", "owner"), made.connection():
            taken.set()
            release.wait(15)

    holder = threading.Thread(target=_hold, daemon=True)
    if why == "full":
        holder.start()
        assert taken.wait(10)
    try:
        with caplog.at_level(logging.WARNING, logger="kazma_core.db.row_security"):
            for _ in range(2):
                with bind_scope("sb", "owner"), pytest.raises(psycopg.OperationalError):
                    made.execute("SELECT 1")
        said = " | ".join(r.getMessage() for r in caplog.records)
        if why == "full":
            assert "pool was full" in said and "cannot sign in" not in said, said
        else:
            assert "cannot sign in" in said and "pool was full" not in said, said
        assert len(signins) == 2, "one sign-in each, judged once in the minute"
        assert noted == [], "the owner signs in: no outage of the database"
    finally:
        release.set()
        if holder.is_alive():
            holder.join(15)
        made.close()


def test_a_row_of_no_spaces_shape_is_refused_before_a_connection(db: dict[str, str],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """L4: ``begin_space`` raised ValueError for such an id, after taking a
    connection; it is a refusal before any is asked for."""
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.row_work import statements_of
    from kazma_core.spaces.scope import NoSpaceBound

    monkeypatch.setattr(row_security, "_armed", True)
    made = PostgresPool(db["app"], min_size=1, max_size=1)
    try:
        with statements_of("telegram:42"), pytest.raises(NoSpaceBound, match="not a space id"):
            made.execute("SELECT 1")
        assert made._space_pool is None
    finally:
        made.close()


def test_a_space_checkout_inside_another_is_refused_unless_declared(pool: Any) -> None:
    """L5: the second connection can wait for a lock the first holds."""
    from kazma_core.db.row_security import _NestedSpaceCheckout, space_inside_a_space
    from kazma_core.spaces.row_work import statements_of
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"), pool.connection():
        with pytest.raises(_NestedSpaceCheckout):
            pool.execute("SELECT 1")
        with statements_of("sb"), pytest.raises(_NestedSpaceCheckout):
            pool.execute("SELECT 1")
        with space_inside_a_space("a test: the second waits for no lock of the first"):
            assert pool.execute("SELECT 1 AS one") == [{"one": 1}]
    # The control: one at a time is as before.
    with bind_scope("sa", "owner"):
        assert pool.execute("SELECT 1 AS one") == [{"one": 1}]


# ── what is sent, recorded (L7) ──────────────────────────────────────────

_WORKLOAD = ["SELECT session_id FROM kazma_chat_sessions", "SELECT count(*) FROM kazma_chat_sessions", "SELECT 1"]


def _recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Every statement a cursor sends, every checkout's opening message and
    every address a connection is made to, as they happen."""
    from kazma_core.db import row_security

    seen: dict[str, list[str]] = {"sent": [], "began": [], "signed": []}
    execute = psycopg.Cursor.execute
    connect = psycopg.Connection.connect.__func__  # type: ignore[attr-defined]
    begin = row_security.begin_space

    def _execute(self: Any, query: Any, *args: Any, **kwargs: Any) -> Any:
        seen["sent"].append(str(query))
        return execute(self, query, *args, **kwargs)

    def _connect(cls: Any, conninfo: str = "", **kwargs: Any) -> Any:
        seen["signed"].append(str(conninfo))
        return connect(cls, conninfo, **kwargs)

    def _begin(conn: Any, space: str) -> None:
        seen["began"].append(space)
        begin(conn, space)

    monkeypatch.setattr(psycopg.Cursor, "execute", _execute)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(_connect))
    monkeypatch.setattr(row_security, "begin_space", _begin)
    return seen


def _workload(made: Any) -> None:
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"):
        made.execute(_WORKLOAD[0])
        held = made.held_connection()
        held.execute(_WORKLOAD[1])
        held.close()
    with install_statements("a test"):
        made.execute(_WORKLOAD[2])


@pytest.mark.parametrize("armed", [False, True])
def test_what_a_workload_sends_is_its_own_statements(db: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                     armed: bool) -> None:
    """Not armed, a workload sends its own statements and nothing more, on
    Kazma's login: no space setting, no role, no second pool (the plan's
    byte-for-byte check, step 3 review L7). The control: armed, the same
    workload's space checkouts are the space role's, set in their one message."""
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool

    monkeypatch.setattr(row_security, "_armed", armed)
    made = PostgresPool(db["app"], min_size=1, max_size=2)
    try:
        from kazma_core.spaces.row_work import install_statements

        with install_statements("a test: the pool's own first connection, before the record"):
            made.execute("SELECT 1")
        seen = _recorder(monkeypatch)
        _workload(made)
        sent = [s for s in seen["sent"] if s]  # "" is the pool's own liveness check
        space_signins = [c for c in seen["signed"] if row_security.SPACE_ROLE in c]
        if not armed:
            assert sent == _WORKLOAD, sent
            assert seen["began"] == [] and space_signins == [] and made._space_pool is None
        else:
            assert [s for s in sent if s in _WORKLOAD] == _WORKLOAD, sent
            assert seen["began"] == ["sa", "sa"] and space_signins, seen
    finally:
        made.close()
