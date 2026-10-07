"""Opt-in real PostgreSQL proof; fixture owns only its disposable DB/schema/role."""
from __future__ import annotations

import os
import uuid

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.tenant_context import tenant_scope
from kazma_skills.native.database_client.connections import CONNECTIONS_KEY
from kazma_skills.native.database_client import remote_reads, tools

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_reader():
    dsn = os.environ.get("KAZMA_AUDIT_POSTGRES_DSN")
    if not dsn:
        pytest.skip("needs a disposable KAZMA_AUDIT_POSTGRES_DSN (never an install database)")
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    conninfo_to_dict = pytest.importorskip("psycopg.conninfo").conninfo_to_dict
    from urllib.parse import quote

    suffix = uuid.uuid4().hex[:12]
    database, role = "kazma_read_" + suffix, "kazma_reader_" + suffix
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(database)))
    admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic-reader-only'").format(sql.Identifier(role)))
    info = conninfo_to_dict(dsn)
    info["dbname"] = database
    setup = psycopg.connect(**info, autocommit=True)
    try:
        setup.execute(sql.SQL("REVOKE CREATE, TEMP ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        setup.execute("CREATE SCHEMA audit_data")
        setup.execute("CREATE TABLE audit_data.items(id integer, name text)")
        setup.execute("INSERT INTO audit_data.items SELECT x, 'item' FROM generate_series(1,1000) x")
        setup.execute("CREATE TABLE audit_data.effects(id integer)")
        setup.execute("CREATE FUNCTION audit_data.mutate() RETURNS integer LANGUAGE sql AS "
                      "'INSERT INTO audit_data.effects VALUES (1) RETURNING id'")
        setup.execute("REVOKE ALL ON FUNCTION audit_data.mutate() FROM PUBLIC")
        setup.execute(sql.SQL("GRANT USAGE ON SCHEMA audit_data TO {}").format(sql.Identifier(role)))
        setup.execute(sql.SQL("GRANT SELECT ON audit_data.items TO {}").format(sql.Identifier(role)))
        uri = (f"postgresql://{quote(role)}:synthetic-reader-only@{info.get('host', 'localhost')}:"
               f"{info.get('port', '5432')}/{database}")
        entry = {"enabled": True, "dsn": uri, "role": role, "tenants": ["audit"],
                 "tables": ["audit_data.items"]}
        get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
        yield setup, role, entry
    finally:
        setup.close()
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()


@pytest.mark.asyncio
async def test_real_constrained_role_reads_and_rejects_privileged_functions(postgres_reader, monkeypatch):
    setup, role, entry = postgres_reader
    calls = []
    original_read = remote_reads.read_postgres

    def read(*args):
        calls.append(args)
        return original_read(*args)

    monkeypatch.setattr(remote_reads, "read_postgres", read)
    with tenant_scope("audit"):
        answer = await tools.execute_db_query("connection:proof",
                                             "SELECT id FROM audit_data.items WHERE id = %s", [1])
        assert '"id": 1' in answer, answer
        cte = await tools.execute_db_query("connection:proof",
                                          "WITH x AS (SELECT id FROM audit_data.items) SELECT count(*) FROM x")
        assert "1000" in cte, cte
        literal = await tools.execute_db_query("connection:proof",
            "SELECT '%s 100%' AS literal FROM audit_data.items WHERE id=%s", [1])
        assert '"literal": "%s 100%"' in literal, literal
        for query in ("SELECT pg_read_file('/etc/passwd')", "SELECT pg_terminate_backend(1)",
                      "SELECT audit_data.mutate()", "SELECT audit_data.mutate(1)"):
            assert (await tools.execute_db_query("connection:proof", query)).startswith("Error:")
    assert setup.execute("SELECT count(*) FROM audit_data.effects").fetchone() == (0,)
    assert len(calls) == 3  # each valid query reached the real constrained connection


@pytest.mark.asyncio
async def test_real_views_rls_and_custom_types_are_refused(postgres_reader):
    sql = pytest.importorskip("psycopg.sql")

    setup, role, entry = postgres_reader
    setup.execute("CREATE VIEW audit_data.summary AS SELECT id FROM audit_data.items")
    setup.execute("CREATE TYPE audit_data.custom AS ENUM ('one')")
    setup.execute("CREATE TABLE audit_data.typed(value audit_data.custom)")
    for table in ("summary", "typed"):
        setup.execute(sql.SQL("GRANT SELECT ON audit_data.{} TO {}").format(
            sql.Identifier(table), sql.Identifier(role)))
        entry["tables"].append(f"audit_data.{table}")
    get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
    with tenant_scope("audit"):
        for table in ("summary", "typed"):
            answer = await tools.execute_db_query("connection:proof", f"SELECT * FROM audit_data.{table}")
            assert answer.startswith("Error:"), answer
        setup.execute("ALTER TABLE audit_data.items ENABLE ROW LEVEL SECURITY")
        answer = await tools.execute_db_query("connection:proof", "SELECT * FROM audit_data.items")
        assert answer.startswith("Error:"), answer
    assert setup.execute("SELECT count(*) FROM pg_stat_activity WHERE usename=%s", (role,)).fetchone() == (0,)


@pytest.mark.asyncio
async def test_real_reader_times_out_and_closes_connection(postgres_reader):
    setup, role, entry = postgres_reader
    with tenant_scope("audit"):
        answer = await tools.execute_db_query("connection:proof",
            "SELECT count(*) FROM audit_data.items a CROSS JOIN audit_data.items b "
            "CROSS JOIN audit_data.items c")
        assert answer.startswith("Error:"), answer
    assert setup.execute("SELECT count(*) FROM pg_stat_activity WHERE usename=%s", (role,)).fetchone() == (0,)


@pytest.mark.asyncio
async def test_real_role_drift_to_write_or_custom_function_fails_closed(postgres_reader):
    sql = pytest.importorskip("psycopg.sql")

    setup, role, entry = postgres_reader
    for grant, revoke in (
        ("GRANT INSERT ON audit_data.items TO {}", "REVOKE INSERT ON audit_data.items FROM {}"),
        ("GRANT EXECUTE ON FUNCTION audit_data.mutate() TO {}",
         "REVOKE EXECUTE ON FUNCTION audit_data.mutate() FROM {}"),
    ):
        setup.execute(sql.SQL(grant).format(sql.Identifier(role)))
        with tenant_scope("audit"):
            answer = await tools.execute_db_query("connection:proof", "SELECT id FROM audit_data.items")
            assert answer.startswith("Error:"), answer
        setup.execute(sql.SQL(revoke).format(sql.Identifier(role)))
    assert setup.execute("SELECT count(*) FROM audit_data.effects").fetchone() == (0,)
