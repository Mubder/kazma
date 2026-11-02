"""Kazma runs as a database role that is not a superuser.

Functional audit 2026-11, FA-218: the live install signed in to Postgres as
``kazma``, the server's bootstrap superuser, and every boot said so
(``security/service_exposure.py``). The way out is
``deploy/postgres/kazma_app_role.sql``: it makes ``kazma_app`` (no superuser,
no CREATEROLE, CREATEDB for the restore rehearsal) and hands it the database,
its schema and every object in it. Postgres refuses the obvious one-liner,
``REASSIGN OWNED BY <bootstrap superuser>``, because the system catalogs are
that role's too -- so the script names each object.

This file proves the procedure the guide gives the owner, on a private
database seeded the way a superuser install is (every store's own schema
setup, run as the superuser): after the script, Kazma's own setup of every
Postgres store, a settings write, the memory mirror, the checkpointer, a
dump and the restore rehearsal all work as ``kazma_app``, and the security
check no longer reports a superuser. Negative controls: the role without the
script cannot read Kazma's settings, and the boot check names the superuser.

Needs a throwaway server and a superuser DSN (``KAZMA_DATABASE_URL`` +
``KAZMA_TEST_ALLOW_REAL_DB=1``); it creates ``kazma_app`` cluster-wide, so a
server that already has that role is refused, never changed.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import pytest

pytestmark = pytest.mark.postgres

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "postgres" / "kazma_app_role.sql"
ROLE = "kazma_app"


def _with(dsn: str, *, user: str | None = None, password: str | None = None, db: str | None = None) -> str:
    parts = urlparse(dsn)
    netloc = parts.netloc.rpartition("@")[2]
    if user is not None:
        netloc = f"{quote(user, safe='')}:{quote(password or '', safe='')}@{netloc}"
    else:
        netloc = parts.netloc
    path = f"/{db}" if db is not None else parts.path
    return urlunparse(parts._replace(netloc=netloc, path=path))


def _run_script(psycopg, dsn: str) -> None:
    """The script as psql runs it: its own BEGIN/COMMIT, one statement batch."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SCRIPT.read_text(encoding="utf-8"))


def _seed_as_superuser(dsn: str) -> None:
    """Every Postgres store's own setup, run as the superuser (a live install's history)."""
    psycopg = pytest.importorskip("psycopg")

    _every_store_set_up(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO kazma_settings (key, value, category) VALUES (%s, %s, %s)",
            ("drill.owner", '"superuser"', "drill"),
        )


def _every_store_set_up(dsn: str) -> None:
    """Kazma's own schema setup of each Postgres store, then one write each."""
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer
    from kazma_core.db.postgres_pool import PostgresPool, _ensure_core_schema
    from kazma_core.documents.admission import IntakeAdmissionStore
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository
    from kazma_core.memory.state_backend import PostgresStateBackend

    pool = PostgresPool(dsn, min_size=1, max_size=2)
    try:
        _ensure_core_schema(pool)
        jobs = PostgresDocumentJobRepository(pool)
        repo = PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)
        IntakeAdmissionStore(jobs)
        DocumentAuditStore(repo)  # creates or replaces the audit guard function
    finally:
        pool.close()

    mirror = PostgresStateBackend(dsn)
    try:
        episode = f"e_drill_{uuid.uuid4().hex[:8]}"
        assert mirror.mirror_episode({"id": episode, "session_id": "drill", "turn_number": 1,
                                      "user_text": "q", "assistant_text": "a"}), "the memory mirror wrote"
    finally:
        mirror.close()

    async def _checkpointer() -> None:
        saver = await open_postgres_checkpointer(dsn, max_size=2)
        await close_postgres_checkpointer(saver)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_checkpointer())


@pytest.fixture(scope="module")
def drill():
    """A private database on the throwaway server, seeded as the superuser."""
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with psycopg.connect(base, autocommit=True) as admin:
        superuser, bootstrap = admin.execute(
            "SELECT rolsuper, oid = 10 FROM pg_roles WHERE rolname = current_user").fetchone()
        if not superuser:
            pytest.skip("creating a role needs a superuser DSN")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone():
            pytest.skip(f"{ROLE} already exists on this server: not a throwaway one")
        name = f"kazma_role_{uuid.uuid4().hex[:10]}"
        admin.execute(f'CREATE DATABASE "{name}"')
    su_dsn = _with(base, db=name)
    password = secrets.token_urlsafe(24)
    try:
        _seed_as_superuser(su_dsn)
        yield {
            "superuser_dsn": su_dsn,
            "app_dsn": _with(base, user=ROLE, password=password, db=name),
            "password": password,
            "bootstrap": bootstrap,
        }
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            for (scratch,) in admin.execute(
                    "SELECT datname FROM pg_database WHERE pg_get_userbyid(datdba) = %s", (ROLE,)).fetchall():
                admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
            admin.execute(f"DROP ROLE IF EXISTS {ROLE}")


def _role_check(psycopg, dsn: str) -> str | None:
    from kazma_core.security.service_exposure import _superuser_problem

    with psycopg.connect(dsn) as conn:
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT rolname, rolsuper FROM pg_roles WHERE rolname = current_user")
        return _superuser_problem(cur.fetchone())


def test_kazma_runs_as_the_role_the_script_makes(drill):
    psycopg = pytest.importorskip("psycopg")

    su, app = drill["superuser_dsn"], drill["app_dsn"]
    # Negative control: the boot check names a superuser login.
    assert "superuser" in (_role_check(psycopg, su) or "")
    if drill["bootstrap"]:
        # Why the script names each object: Postgres refuses to move the
        # bootstrap superuser's objects wholesale.
        with psycopg.connect(su, autocommit=True) as conn:
            conn.execute("CREATE ROLE kazma_reassign_probe")
            try:
                with pytest.raises(psycopg.errors.DependentObjectsStillExist):
                    conn.execute("REASSIGN OWNED BY CURRENT_USER TO kazma_reassign_probe")
            finally:
                conn.execute("DROP ROLE kazma_reassign_probe")

    # Negative control: a login role alone cannot read Kazma's settings.
    with psycopg.connect(su, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE {ROLE} LOGIN")
        conn.execute(f"ALTER ROLE {ROLE} PASSWORD %s" % psycopg.sql.Literal(drill["password"]).as_string(conn))
    with psycopg.connect(app) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM kazma_settings").fetchone()

    _run_script(psycopg, su)
    _run_script(psycopg, su)  # safe to run again

    with psycopg.connect(su) as conn:
        role = conn.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone()
        foreign = conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) <> %s", (ROLE,)).fetchall()
        functions = conn.execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> %s", (ROLE,)).fetchall()
    assert role == (False, False, True)
    assert foreign == [] and functions == [], "every table, index, sequence and function is the role's"

    assert _role_check(psycopg, app) is None, "the boot check is quiet for the new role"
    _every_store_set_up(app)  # every store's setup, as the new role, on the existing tables
    with psycopg.connect(app, autocommit=True) as conn:
        assert conn.execute("SELECT value FROM kazma_settings WHERE key = 'drill.owner'").fetchone()
        conn.execute("UPDATE kazma_settings SET value = %s WHERE key = 'drill.owner'", ('"kazma_app"',))
        # A table a later build adds is created by the role itself.
        from kazma_core.db.pg_schema import ensure_schema

        ensure_schema(conn.cursor(), ["CREATE TABLE IF NOT EXISTS kazma_drill_new (id TEXT PRIMARY KEY)"])


def test_the_backup_and_the_restore_rehearsal_run_as_the_role(drill, tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from kazma_core.backup.restore_drill import DrillResult
    from kazma_core.backup.restore_rehearsal import rehearse_pg_restore
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.migration.pg_bridge import PgToolNotFound, dump_database

    with psycopg.connect(drill["superuser_dsn"], autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone():
            _run_script(psycopg, drill["superuser_dsn"])
            conn.execute(f"ALTER ROLE {ROLE} PASSWORD %s"
                         % psycopg.sql.Literal(drill["password"]).as_string(conn))
    try:
        dump = dump_database(drill["app_dsn"], tmp_path / "kazma.dump", tables=list(KAZMA_PG_TABLES))
    except PgToolNotFound as exc:
        pytest.skip(f"pg_dump is not available here: {exc}")
    res = DrillResult(backup_dir=str(tmp_path))
    rehearse_pg_restore(dump, drill["app_dsn"], res)
    assert res.ok, res.checks
