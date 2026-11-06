"""The resilience plan's Postgres code, on a real Postgres.

* ``backup.wal_archive.archiver_status`` reads ``pg_stat_archiver`` and
  ``archive_mode`` from a real server, and ``archive_problem`` reads them;
* ``db.availability`` tells a server that is not answering from a query the
  server refused: a real refused connection is an outage, a real division by
  zero or statement timeout is not, and its own sign-in gets in;
* ``backup.pg_move.move_from_container`` copies a container's Kazma tables
  into another database and compares every table's rows: the move is
  complete only when they match (the negative control loses a row after the
  restore, and the move must say so).

Needs a disposable Postgres (``@pytest.mark.postgres``: KAZMA_DATABASE_URL);
the move also needs the container that serves it (``KAZMA_DB_CONTAINER``):
``docker run --rm -d --name kazma-pgtest -p 127.0.0.1:55432:5432
-e POSTGRES_PASSWORD=test postgres:16``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from kazma_core.backup import pg_move, wal_archive
from kazma_core.db import availability

pytestmark = pytest.mark.postgres


def _dsn() -> str:
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn.startswith(("postgresql://", "postgres://")):
        pytest.skip("no Postgres configured")
    return dsn.replace("postgres://", "postgresql://", 1)


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path="/" + name))


def _with_port(dsn: str, port: int) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc.rsplit(":", 1)[0] if ":" in parts.netloc.rsplit("@", 1)[-1] else parts.netloc
    return urlunsplit(parts._replace(netloc=f"{netloc}:{port}"))


def test_the_archivers_numbers_come_from_the_server():
    status = wal_archive.archiver_status(_dsn())
    assert status["mode"] in ("off", "on", "always")
    assert isinstance(status["archived_count"], int) and isinstance(status["failed_count"], int)
    problem = wal_archive.archive_problem(status)
    if status["mode"] == "off":
        assert problem is not None and "archive_mode is off" in problem


def test_a_server_that_is_not_answering_is_an_outage_and_a_refused_query_is_not():
    psycopg = pytest.importorskip("psycopg")

    dsn = _dsn()
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as zero:
            conn.execute("SELECT 1 / 0")
        assert not availability.is_outage(zero.value), "a query the server refused"
        conn.execute("SET statement_timeout = 50")
        with pytest.raises(psycopg.Error) as cancelled:
            conn.execute("SELECT pg_sleep(2)")
        assert cancelled.value.sqlstate == "57014"
        assert not availability.is_outage(cancelled.value), "a query cancelled at its timeout"

    with pytest.raises(psycopg.OperationalError) as refused:
        psycopg.connect(_with_port(dsn, 1), connect_timeout=3)
    assert availability.is_outage(refused.value), "nothing listening: the database is away"


def test_its_own_sign_in_gets_in_and_names_a_refusal(monkeypatch):
    dsn = _dsn()
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: dsn)
    assert availability._sign_in() == (True, None)
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: _with_port(dsn, 1))
    ok, reason = availability._sign_in()
    assert ok is False and reason


def test_setup_sees_kazma_signed_in_on_the_new_server():
    """``kazma setup postgres --move-from-container`` starts Kazma on the new
    server and waits for its session there (``setup._kazma_signed_in``):
    another session of the role counts, the check's own never does. The
    negative control is the database with no other session."""
    psycopg = pytest.importorskip("psycopg")
    from kazma_cli import setup

    dsn = _dsn()
    name = f"kazma_signed_in_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = _with_database(dsn, name)
    try:
        assert setup._kazma_signed_in(url, wait_s=0) is False, "its own session is not Kazma's"
        with psycopg.connect(url, autocommit=True):
            assert setup._kazma_signed_in(url, wait_s=0) is True
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


# ── the move ──────────────────────────────────────────────────────────────


def _docker() -> str:
    from kazma_core.docker_cli import find_docker_cli

    docker = find_docker_cli()
    if not docker:
        pytest.skip("no docker command")
    return docker


@pytest.fixture()
def two_databases():
    psycopg = pytest.importorskip("psycopg")

    container = os.environ.get("KAZMA_DB_CONTAINER") or ""
    if not container:
        pytest.skip("KAZMA_DB_CONTAINER names no disposable container")
    dsn = _dsn()
    tag = uuid.uuid4().hex[:8]
    src, dst = f"kazma_move_src_{tag}", f"kazma_move_dst_{tag}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {src}")
        admin.execute(f"CREATE DATABASE {dst}")
    try:
        with psycopg.connect(_with_database(dsn, src), autocommit=True) as conn:
            conn.execute("CREATE TABLE kazma_settings (key text PRIMARY KEY, value text)")
            conn.execute("INSERT INTO kazma_settings VALUES ('a', '1'), ('b', '2'), ('c', '3')")
            conn.execute("CREATE TABLE kazma_chat_sessions (id text PRIMARY KEY)")
            conn.execute("INSERT INTO kazma_chat_sessions VALUES ('s1'), ('s2')")
            conn.execute("CREATE TABLE not_kazmas (id int)")  # never compared
        yield container, dsn, src, dst
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            for name in (src, dst):
                admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _run_restoring_in_the_container(container: str, dst: str, user: str, *, after_restore=None):
    """The move's runner, with ``pg_restore`` run where the server is (this
    machine may have no client tools): the dump is copied into the container
    and restored there, the same flags as the move's own call."""
    docker = _docker()

    def run(argv, **kwargs):
        if Path(str(argv[0])).stem == "pg_restore":
            dump = Path(argv[-1])
            inside = f"/tmp/{dump.name}-{uuid.uuid4().hex[:6]}"
            subprocess.run([docker, "cp", str(dump), f"{container}:{inside}"], check=True,
                           capture_output=True, timeout=120)
            done = subprocess.run([docker, "exec", container, "pg_restore", "-U", user, "-d", dst,
                                   "--no-owner", "--no-privileges", "--single-transaction",
                                   "--exit-on-error", inside],
                                  capture_output=True, text=True, timeout=600, check=False)
            if after_restore is not None and done.returncode == 0:
                after_restore()
            return done
        return subprocess.run(argv, **kwargs)

    return run


def test_a_move_copies_the_kazma_tables_and_compares_every_row(two_databases, monkeypatch):
    psycopg = pytest.importorskip("psycopg")

    container, dsn, src, dst = two_databases
    user = urlsplit(dsn).username or "postgres"
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: _with_database(dsn, src))
    said: list[str] = []
    ok = pg_move.move_from_container(
        container, _with_database(dsn, dst), port=urlsplit(dsn).port or 5432, say=said.append,
        run=_run_restoring_in_the_container(container, dst, user), bin_dir="/no/client/tools")
    assert ok, said
    with psycopg.connect(_with_database(dsn, dst)) as conn:
        assert conn.execute("SELECT count(*) FROM kazma_settings").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM kazma_chat_sessions").fetchone()[0] == 2
    assert any("Every one of 2 Kazma tables" in line for line in said), said


def test_negative_control_a_row_lost_in_the_move_is_said(two_databases, monkeypatch):
    psycopg = pytest.importorskip("psycopg")

    container, dsn, src, dst = two_databases
    user = urlsplit(dsn).username or "postgres"
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: _with_database(dsn, src))

    def lose_a_row() -> None:
        with psycopg.connect(_with_database(dsn, dst), autocommit=True) as conn:
            conn.execute("DELETE FROM kazma_settings WHERE key = 'c'")

    said: list[str] = []
    ok = pg_move.move_from_container(
        container, _with_database(dsn, dst), port=urlsplit(dsn).port or 5432, say=said.append,
        run=_run_restoring_in_the_container(container, dst, user, after_restore=lose_a_row),
        bin_dir="/no/client/tools")
    assert ok is False
    assert any("DIFFERS kazma_settings: 3 -> 2" in line for line in said), said


def test_a_second_move_replaces_a_copy_kazma_never_started_on_and_never_one_it_did(two_databases, monkeypatch):
    """The live shape of 2026-11-06: a first copy made while Kazma still ran on
    the container. A second move replaces that copy (Kazma never started on
    it) with the container's newer rows; a database Kazma started on (a newer
    boot stamp there) or one something is connected to is never emptied."""
    psycopg = pytest.importorskip("psycopg")

    container, dsn, src, dst = two_databases
    user = urlsplit(dsn).username or "postgres"
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: _with_database(dsn, src))
    with psycopg.connect(_with_database(dsn, src), autocommit=True) as conn:
        conn.execute("INSERT INTO kazma_settings VALUES (%s, '100.5')", (pg_move.BOOT_KEY,))
    dst_url, port = _with_database(dsn, dst), urlsplit(dsn).port or 5432
    runner = _run_restoring_in_the_container(container, dst, user)
    emptied: list[bool] = []

    def empty() -> bool:
        emptied.append(True)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE {dst}")
            admin.execute(f"CREATE DATABASE {dst}")
        return True

    def move(empty_target=empty) -> tuple[bool, list[str]]:
        said: list[str] = []
        ok = pg_move.move_from_container(container, dst_url, port=port, say=said.append, run=runner,
                                         bin_dir="/no/client/tools", empty_target=empty_target)
        return ok, said

    ok, said = move()
    assert ok, said
    assert emptied == []  # an empty database: copied straight in

    # Kazma kept writing to the container after the first copy.
    with psycopg.connect(_with_database(dsn, src), autocommit=True) as conn:
        conn.execute("INSERT INTO kazma_chat_sessions VALUES ('s3')")
    ok, said = move(empty_target=None)
    assert not ok, said  # nothing given to empty it: the earlier copy stays as it is
    ok, said = move()
    assert ok, said
    assert emptied == [True]
    with psycopg.connect(dst_url) as conn:
        assert conn.execute("SELECT count(*) FROM kazma_chat_sessions").fetchone()[0] == 3

    # Kazma started on the new server and wrote there: never emptied again.
    with psycopg.connect(dst_url, autocommit=True) as conn:
        conn.execute("UPDATE kazma_settings SET value = '200.0' WHERE key = %s", (pg_move.BOOT_KEY,))
        conn.execute("INSERT INTO kazma_chat_sessions VALUES ('written-on-the-new-server')")
    ok, said = move()
    assert not ok and emptied == [True]
    assert any("Kazma has started on the new server" in line for line in said), said
    with psycopg.connect(dst_url) as conn:
        assert conn.execute("SELECT count(*) FROM kazma_chat_sessions").fetchone()[0] == 4

    # Something connected to it: refused whatever the stamps say.
    with psycopg.connect(dst_url, autocommit=True) as conn:
        conn.execute("UPDATE kazma_settings SET value = '100.5' WHERE key = %s", (pg_move.BOOT_KEY,))
    with psycopg.connect(dst_url):
        ok, said = move()
    assert not ok and emptied == [True]
    assert any("session(s) are connected" in line for line in said), said
