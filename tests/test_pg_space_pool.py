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
* not armed, the pool is today's: no space half is opened, nothing more sent.

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

        done = row_security.apply(app, vector_table="")
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
        conn.execute("SELECT 1 FROM kazma_chat_sessions LIMIT 1")
        conn.commit()
        with pytest.raises(_SecondTransaction):
            conn.execute("SELECT 1 FROM kazma_chat_sessions WHERE session_id = 'nothing'")
    # The next checkout is a fresh transaction in its space.
    with bind_scope("sa", "owner"):
        assert _sessions(pool.execute("SELECT session_id FROM kazma_chat_sessions")) == ["c1"]
    # The control: the install's checkout is the owner's, as today.
    with install_statements("a test of the install's door"), pool.connection() as conn:
        conn.execute("SELECT 1")
        conn.commit()
        conn.execute("SELECT 1")


def test_an_install_checkout_inside_a_space_one_is_refused_unless_declared(pool: Any) -> None:
    from kazma_core.db.row_security import _install_inside_a_space, _NestedInstallCheckout
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import bind_scope

    with bind_scope("sa", "owner"), pool.connection():
        with install_statements("a test"), pytest.raises(_NestedInstallCheckout):
            pool.execute("SELECT 1")
        with install_statements("a test"), _install_inside_a_space("a test: no lock of the space's is waited for"):
            assert pool.execute("SELECT 1 AS one") == [{"one": 1}]
        # Another space's statements in a second checkout are no install's.
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
    monkeypatch.setattr(row_security, "_half_said_at", 0.0)
    monkeypatch.setenv("KAZMA_PG_POOL_TIMEOUT", "1")
    noted: list[str] = []
    monkeypatch.setattr(availability, "note_failure", lambda kind, exc=None, **k: noted.append(kind) or True)
    made = PostgresPool(db["app"], min_size=1, max_size=2)
    try:
        with caplog.at_level(logging.WARNING, logger="kazma_core.db.row_security"):
            with bind_scope("sa", "owner"), pytest.raises(psycopg.OperationalError):
                made.execute("SELECT session_id FROM kazma_chat_sessions")
        assert any("space role cannot sign in" in r.getMessage() for r in caplog.records)
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
