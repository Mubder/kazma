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

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]


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
            # Delta review N1: a cursor held past the commit, a session lock, a channel.
            conn.execute("DECLARE kazma_held CURSOR WITH HOLD FOR SELECT session_id FROM kazma_chat_sessions")
            conn.execute("SELECT pg_advisory_lock(4401)")
            conn.execute("LISTEN kazma_left")
            conn.commit()
        # The idle connection as the pool's reset left it: the session's own
        # values, which a checkout's opening message would overwrite.
        half = made._space_half()
        idle = half.getconn()
        try:
            with idle.cursor() as cur:
                cur.execute("SELECT current_user::text AS who, coalesce(current_setting('kazma.space', true), '') AS space, "
                            "to_regclass('pg_temp.kazma_left') IS NULL AS gone, "
                            "(SELECT count(*) FROM pg_cursors WHERE name = 'kazma_held') AS held, "
                            "(SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                            "AND pid = pg_backend_pid()) AS locks, "
                            "(SELECT count(*) FROM pg_listening_channels()) AS channels")
                assert cur.fetchone() == {"who": "kazma_space", "space": "", "gone": True, "held": 0,
                                          "locks": 0, "channels": 0}
            idle.rollback()
        finally:
            half.putconn(idle)
        with bind_scope("sb", "owner"), made.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user::text AS who, current_setting('kazma.space') AS space")
            assert cur.fetchone() == {"who": "kazma_space", "space": "sb"}
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


# ── what reaches the server, recorded on the wire (L7, delta review N3) ──

_WORKLOAD = ["SELECT session_id FROM kazma_chat_sessions", "SELECT count(*) FROM kazma_chat_sessions", "SELECT 1"]
#: Transaction control psycopg sends around a workload's own statements.
_CONTROL = {"BEGIN", "COMMIT", "ROLLBACK"}


class _Wire:
    """A TCP proxy between the pools and the server that records what reaches
    it: each connection's start parameters and every statement it sends,
    simple (``Q``) or extended (``P``), whatever psycopg's build -- libpq's
    own sends (``pgconn.exec_``), commits and rollbacks included. A message
    is recorded before it is passed on, so the record is whole when the
    answer arrives."""

    _NOT_A_START = (80877103, 80877104)  # SSLRequest, GSSENCRequest

    def __init__(self, host: str, port: int) -> None:
        import socket
        import threading

        self._target = (host, port)
        self._listen = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._listen.getsockname()[1])
        self.startups: list[dict[str, str]] = []
        self.sent: list[str] = []
        self._open: list[Any] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        import socket
        import threading

        while True:
            try:
                client, _ = self._listen.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._target)
            self._open += [client, upstream]
            threading.Thread(target=self._from_client, args=(client, upstream), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client), daemon=True).start()

    @staticmethod
    def _close(*socks: Any) -> None:
        for sock in socks:
            try:
                sock.close()
            except OSError:
                continue

    def _pump(self, src: Any, dst: Any) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            return
        finally:
            self._close(src, dst)

    def _from_client(self, client: Any, upstream: Any) -> None:
        buf, started = b"", False
        try:
            while data := client.recv(65536):
                buf, started = self._read(buf + data, started)
                upstream.sendall(data)
        except OSError:
            return
        finally:
            self._close(client, upstream)

    def _read(self, buf: bytes, started: bool) -> tuple[bytes, bool]:
        while True:
            if not started:
                if len(buf) < 8 or len(buf) < int.from_bytes(buf[:4], "big"):
                    return buf, started
                size = int.from_bytes(buf[:4], "big")
                if int.from_bytes(buf[4:8], "big") not in self._NOT_A_START:
                    words = buf[8:size].split(b"\0")
                    self.startups.append({words[i].decode(): words[i + 1].decode()
                                          for i in range(0, len(words) - 1, 2) if words[i]})
                    started = True
                buf = buf[size:]
                continue
            if len(buf) < 5 or len(buf) < 1 + int.from_bytes(buf[1:5], "big"):
                return buf, started
            size = int.from_bytes(buf[1:5], "big")
            kind, body = buf[:1], buf[5:1 + size]
            if kind == b"Q":
                self.sent.append(body.rstrip(b"\0").decode(errors="replace"))
            elif kind == b"P":
                self.sent.append(body.split(b"\0")[1].decode(errors="replace"))
            buf = buf[1 + size:]

    def close(self) -> None:
        self._close(self._listen, *self._open)


def _via(url: str, port: int) -> str:
    """*url* through the wire on *port*, in plain text (the record reads it)."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    userinfo = parts.netloc.rpartition("@")[0]
    return urlunsplit(parts._replace(netloc=f"{userinfo}@127.0.0.1:{port}",
                                     query="sslmode=disable&gssencmode=disable"))


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


def _beyond_its_own(wire: _Wire, since: int, signed: int) -> list[str]:
    """What reached the server beyond the workload's own statements and
    psycopg's transaction control, what of the workload did not, and every
    sign-in not as Kazma's login alone."""
    from kazma_core.db import row_security

    sent = wire.sent[since:]
    out = [f"sent {s!r}" for s in sent if s.strip() and s.strip() not in _WORKLOAD and s.strip() not in _CONTROL]
    out += [f"missing {w!r}" for w in _WORKLOAD if w not in sent]
    out += [f"signed in {p}" for p in wire.startups[signed:]
            if row_security.SPACE_ROLE in p.get("options", "") or p.get("user") != "kazma_app"]
    return out


@pytest.mark.parametrize("armed", [False, True])
def test_what_reaches_the_server_is_the_workloads_own(db: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                      armed: bool) -> None:
    """Not armed, a workload's statements reach the server and nothing more,
    on Kazma's login, with no space half: the plan's byte-for-byte check,
    recorded on the wire (step 3 review L7, delta review N3). The control:
    armed, the same rule fails on what arming sends -- the checkout's opening
    message, the pool's reset, the space role's sign-in."""
    import time
    from urllib.parse import urlsplit

    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.spaces.row_work import install_statements

    address = urlsplit(db["app"])
    wire = _Wire(address.hostname or "127.0.0.1", address.port or 5432)
    monkeypatch.setattr(row_security, "_armed", armed)
    made = PostgresPool(_via(db["app"], wire.port), min_size=1, max_size=2)
    try:
        with install_statements("a test: the pool's own first connection, before the record"):
            made.execute("SELECT 1")
        since, signed = len(wire.sent), len(wire.startups)
        _workload(made)
        deadline = time.monotonic() + 5  # the pool's reset runs on its own thread
        while armed and not any("RESET ROLE" in s for s in wire.sent[since:]) and time.monotonic() < deadline:
            time.sleep(0.05)
        beyond = _beyond_its_own(wire, since, signed)
        if not armed:
            assert beyond == [], beyond
            assert made._space_pool is None
        else:
            assert beyond, "armed, the unarmed rule must fail"
            assert any("set_config('kazma.space'" in b and "current_user" in b for b in beyond), beyond
            assert any("RESET ROLE" in b for b in beyond), beyond
            assert any(b.startswith("signed in") and row_security.SPACE_ROLE in b for b in beyond), beyond
    finally:
        made.close()
        wire.close()
