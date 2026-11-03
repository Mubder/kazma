"""When Postgres refuses Kazma, the log and the startup error say why.

Live 2026-11-03: the owner moved Kazma to a new login (``kazma_app``) whose
password step had not taken. Postgres answered every sign-in with "password
authentication failed"; the pool reported only ``PoolTimeout``, Kazma's log
said "Postgres pool unreachable after 5 attempts" and "it may still be
starting", and the server waited 18 minutes before anyone saw the cause in
psycopg's stderr. ``postgres_pool.refusal_reason`` reads Postgres's own
answer with one direct sign-in once the pool has given up, ``_word_refusal``
says it in words, and ``ConfigStoreUnavailableError`` carries it.

Negative control: with the probe answering nothing (the old code) the
startup error is the old "may still be starting" sentence.
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest

from kazma_core.db.postgres_pool import _word_refusal

# libpq's own wordings, as psycopg raises them.
_PASSWORD = ('connection failed: connection to server at "127.0.0.1", port 5433 failed: '
             'FATAL:  password authentication failed for user "kazma_app"')
_ROLE = ('connection failed: connection to server at "127.0.0.1", port 5433 failed: '
         'FATAL:  role "kazma_app" does not exist')
_DATABASE = ('connection failed: connection to server at "127.0.0.1", port 5433 failed: '
             'FATAL:  database "kazmax" does not exist')
_REFUSED = ('connection failed: connection to server at "127.0.0.1", port 1 failed: '
            'Connection refused (0x0000274D/10061)\n\tIs the server running on that host '
            'and accepting TCP/IP connections?')


@pytest.mark.parametrize(("message", "expected"), [
    (_PASSWORD, "Postgres refused the password for kazma_app"),
    (_ROLE, "Postgres has no role named kazma_app"),
    (_DATABASE, "Postgres has no database named kazmax"),
    (_REFUSED, "nothing answered at 127.0.0.1:1"),
    ("FATAL:  the database system is starting up", "Postgres is still starting"),
    ("FATAL:  sorry, too many clients already", "Postgres has no free connection"),
    ('FATAL:  no pg_hba.conf entry for host "10.0.0.5"', "Postgres's pg_hba.conf does not let"),
])
def test_each_answer_is_said_in_words(message, expected):
    assert _word_refusal(message, host="127.0.0.1", port=1).startswith(expected)


def test_an_unknown_answer_is_passed_on_without_a_password():
    said = _word_refusal("odd failure at postgresql://kazma_app:s3cret@db/kazma")
    assert "s3cret" not in said and "odd failure" in said


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_closed_port_is_named_as_nothing_answering():
    pytest.importorskip("psycopg")
    from kazma_core.db.postgres_pool import refusal_reason

    port = _closed_port()
    reason = refusal_reason(f"postgresql://kazma_app:pw@127.0.0.1:{port}/kazma", timeout_s=2)
    assert reason == f"nothing answered at 127.0.0.1:{port}: is the database running?"


@pytest.fixture
def unreachable_store(monkeypatch):
    """The settings store pointed at an address nothing answers."""
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    import kazma_core.config_store as cs
    import kazma_core.db.backend as backend
    import kazma_core.db.postgres_pool as pp

    # The root conftest strips any database address from the environment
    # (no test may reach a real database), so the backend is pointed at the
    # closed port through its own functions.
    dsn = f"postgresql://kazma_app:pw@127.0.0.1:{_closed_port()}/kazma"
    import kazma_core.config_availability as availability

    monkeypatch.setattr(backend, "get_backend", lambda: backend.DatabaseBackend.POSTGRES)
    monkeypatch.setattr(availability, "get_backend", lambda: backend.DatabaseBackend.POSTGRES)
    monkeypatch.setattr(backend, "is_postgres", lambda: True)
    monkeypatch.setattr(pp, "get_database_url", lambda: dsn)
    monkeypatch.setattr(pp, "is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_PG_POOL_RETRIES", "1")
    monkeypatch.setenv("KAZMA_PG_POOL_TIMEOUT", "1")
    monkeypatch.setattr(pp, "_pool", None)
    monkeypatch.setattr(cs, "_config_store", None)
    return cs, pp


def test_the_startup_error_names_the_answer(unreachable_store):
    cs, pp = unreachable_store
    from kazma_core.config_availability import ConfigStoreUnavailableError

    with pytest.raises(ConfigStoreUnavailableError) as raised:
        cs.get_config_store()
    assert "nothing answered at 127.0.0.1" in str(raised.value)
    assert raised.value.reason and "nothing answered" in raised.value.reason
    assert "may still be starting" not in str(raised.value)


def test_without_the_probe_the_error_is_the_old_sentence(unreachable_store, monkeypatch):
    # Negative control: the probe answering nothing is the code before the fix.
    cs, pp = unreachable_store
    from kazma_core.config_availability import ConfigStoreUnavailableError

    monkeypatch.setattr(pp, "refusal_reason", lambda dsn, **kw: None)
    with pytest.raises(ConfigStoreUnavailableError) as raised:
        cs.get_config_store()
    assert "may still be starting" in str(raised.value)
    assert "nothing answered" not in str(raised.value)


# ── a real Postgres: the live incident's own answer ─────────────────────────


@pytest.mark.postgres
def test_a_role_with_no_password_is_named(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from urllib.parse import urlsplit, urlunsplit

    from kazma_core.db.postgres_pool import refusal_reason

    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    role = f"kazma_refusal_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    netloc = f"{role}:not-its-password@{parts.netloc.rpartition('@')[2]}"
    as_role = urlunsplit(parts._replace(netloc=netloc))
    with psycopg.connect(base, autocommit=True) as admin:
        if not admin.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles "
                             "WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("creating a role needs CREATEROLE")
        admin.execute(f"CREATE ROLE {role} LOGIN")  # the live shape: no password at all
    try:
        reason = refusal_reason(as_role)
        assert reason and reason.startswith(f"Postgres refused the password for {role}")
        missing = urlunsplit(parts._replace(netloc=f"no_such_{role}:pw@{parts.netloc.rpartition('@')[2]}"))
        assert refusal_reason(missing).startswith("Postgres ")  # refused or no such role
        assert refusal_reason(base) is None, "a sign-in that works has no refusal"
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {role}")


# ── scripts/check_database_login.py: the check before a reload ──────────────


def _check_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_database_login.py"
    spec = importlib.util.spec_from_file_location("check_database_login", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _point_at(monkeypatch, dsn):
    import kazma_core.db.backend as backend
    import kazma_core.env_files as env_files

    monkeypatch.setattr(env_files, "load_env_files", lambda **kw: [])  # never a real .env
    monkeypatch.setattr(backend, "get_database_url", lambda: dsn)


def test_the_check_says_why_a_sign_in_fails(monkeypatch, capsys):
    pytest.importorskip("psycopg")
    port = _closed_port()
    _point_at(monkeypatch, f"postgresql://kazma_app:pw@127.0.0.1:{port}/kazma")
    assert _check_script().main() == 1
    assert f"NOT OK: nothing answered at 127.0.0.1:{port}" in capsys.readouterr().out


def test_the_check_says_when_there_is_no_postgres(monkeypatch, capsys):
    _point_at(monkeypatch, None)
    assert _check_script().main() == 2
    assert "uses SQLite" in capsys.readouterr().out


@pytest.mark.postgres
def test_the_check_tells_a_wrong_password_from_the_right_one(monkeypatch, capsys):
    # The check the guide first gave -- psql inside the container over
    # 127.0.0.1 -- passed with a wrong password: the image trusts loopback.
    # This one signs in from outside, as Kazma does.
    psycopg = pytest.importorskip("psycopg")
    from urllib.parse import urlsplit, urlunsplit

    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    role = f"kazma_check_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    host = parts.netloc.rpartition("@")[2]
    with psycopg.connect(base, autocommit=True) as admin:
        if not admin.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles "
                             "WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("creating a role needs CREATEROLE")
        admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'right-one'")
    try:
        script = _check_script()
        _point_at(monkeypatch, urlunsplit(parts._replace(netloc=f"{role}:wrong-one@{host}")))
        assert script.main() == 1
        assert f"Postgres refused the password for {role}" in capsys.readouterr().out
        _point_at(monkeypatch, urlunsplit(parts._replace(netloc=f"{role}:right-one@{host}")))
        assert script.main() == 0
        assert f"as {role} (not a superuser)" in capsys.readouterr().out
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {role}")
