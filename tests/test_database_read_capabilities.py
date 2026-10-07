"""Remote SQL targets, syntax and functions are capabilities, never raw credentials."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.tenant_context import tenant_scope
from kazma_skills.native.database_client.connections import CONNECTIONS_KEY, resolve_connection
from kazma_skills.native.database_client.sql_policy import compile_read, validate_mongo_filter
from kazma_skills.native.database_client import remote_reads, tools

TABLES = frozenset({"public.items"})


@pytest.mark.parametrize("sql", [
    "SELECT pg_read_file('/etc/passwd')", "SELECT pg_terminate_backend(1)",
    "SELECT public.mutate()", "SELECT public.lower('x')", "SELECT lo_export(1, '/tmp/x')",
    "SELECT set_config('search_path', 'public', false)", "SELECT pg_sleep(100)",
    "SELECT 1; SELECT 2", "WITH x AS (DELETE FROM public.items RETURNING *) SELECT * FROM x",
    "SELECT * INTO public.copy FROM public.items", "SELECT * FROM public.items FOR UPDATE",
    "SELECT * FROM public.other", "SELECT * FROM items", "SELECT * FROM generate_series(1,1000000)",
    "SELECT 'x'::public.evil", "SELECT id OPERATOR(public.===) 1 FROM public.items",
    "WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x",
    # A CTE name in a different scope is not an allowlist entry.
    "SELECT * FROM hidden, (WITH hidden AS (SELECT 1) SELECT * FROM hidden) x",
])
def test_unsafe_sql_never_compiles(sql):
    with pytest.raises(ValueError):
        compile_read(sql, dialect="postgres", tables=TABLES, limit=10)


@pytest.mark.parametrize("sql", [
    "SELECT id, name FROM public.items WHERE id = %s",
    "WITH x AS (SELECT id FROM public.items) SELECT count(*) FROM x",
    "SELECT count(*), min(id), max(id) FROM public.items",
])
def test_supported_reads_have_an_outer_bound(sql):
    compiled, used = compile_read(sql, dialect="postgres", tables=TABLES, limit=10)
    assert compiled.endswith("LIMIT 10")
    assert used == TABLES


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_parameters_and_quoted_percent_are_preserved(dialect):
    sql, used = compile_read("SELECT '%s' AS literal, id FROM public.items WHERE id=%s",
                             dialect=dialect, tables=TABLES, limit=2)
    assert "'%%s' AS literal" in sql
    assert "id = %s" in sql
    assert used == TABLES


@pytest.mark.parametrize("target", ["postgresql://admin:secret@localhost/db",
                                      "mysql://root:secret@localhost/db",
                                      "mongodb://admin:secret@localhost/db"])
@pytest.mark.asyncio
async def test_raw_remote_credentials_are_refused_before_contact(monkeypatch, target):
    calls = MagicMock()
    monkeypatch.setattr(remote_reads, "read_postgres", calls)
    monkeypatch.setattr(remote_reads, "read_mysql", calls)
    monkeypatch.setattr(remote_reads, "read_mongo", calls)
    result = await tools.execute_db_query(target, "SELECT 1")
    assert result.startswith("Error:")
    assert "connection:<name>" in result
    assert "secret" not in result
    calls.assert_not_called()


def register(**changes):
    entry = {"enabled": True, "dsn": "postgresql://reader:test@localhost/sample",
             "role": "reader", "tenants": ["a"], "tables": ["public.items"]}
    entry.update(changes)
    get_config_store().set(CONNECTIONS_KEY, {"warehouse": entry})


def test_operator_connection_is_tenant_scoped_and_config_protected():
    from kazma_core.safety.protected_config import is_protected_config_key

    register()
    with tenant_scope("a"):
        assert resolve_connection("connection:warehouse").role == "reader"
    with tenant_scope("b"), pytest.raises(ValueError, match="unavailable"):
        resolve_connection("connection:warehouse")
    assert is_protected_config_key(CONNECTIONS_KEY)


@pytest.mark.parametrize("change", [{"tables": []}, {"tenants": []}, {"enabled": False},
                                    {"dsn": "postgresql://reader:test@localhost/sample?options=-cevil"},
                                    {"tables": ["public.*"]}, {"role": ""}])
def test_incomplete_capability_fails_closed(change):
    register(**change)
    with tenant_scope("a"), pytest.raises(ValueError):
        resolve_connection("connection:warehouse")


@pytest.mark.parametrize("limit", [-1, 0, 1001, True, "10"])
@pytest.mark.asyncio
async def test_bad_limit_does_not_open_even_a_local_connection(monkeypatch, limit):
    connect = MagicMock()
    monkeypatch.setattr(tools, "_connect_sqlite", connect)
    result = await tools.execute_db_query(":memory:", "SELECT 1", limit=limit)
    assert result.startswith("Error:")
    connect.assert_not_called()


def test_sqlite_connection_is_readonly_and_custom_functions_are_denied(tmp_path):
    path = tmp_path / "sample.db"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE items (id INTEGER)")
        writer.execute("INSERT INTO items VALUES (1)")
    conn = tools._connect_sqlite(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO items VALUES (2)")
        effects = []
        conn.create_function("custom_effect", 0, lambda: effects.append("called"))
        tools._install_readonly_authorizer(conn)
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (1,)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT custom_effect()")
        assert effects == []
    finally:
        conn.close()


@pytest.mark.parametrize("query", ['{"$where": "evil()"}', '{"x":{"$function":{}}}',
                                    '{"$expr":{"$accumulator":{}}}', '[]'])
def test_mongodb_executable_filters_are_refused(query):
    with pytest.raises(ValueError):
        validate_mongo_filter(query)
