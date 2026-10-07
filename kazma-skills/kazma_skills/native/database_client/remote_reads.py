"""Bounded remote reads with server-side privilege and relation qualification."""
from __future__ import annotations

import json
import ssl
from urllib.parse import unquote, urlsplit

from kazma_core.errors import safe_error

from .connections import ReadConnection
from .sql_policy import MAX_OUTPUT_CHARS

STATEMENT_MS = 2000
_PG_TYPES = frozenset({"bool", "int2", "int4", "int8", "float4", "float8", "numeric",
                       "text", "varchar", "bpchar", "date", "time", "timestamp",
                       "timestamptz", "interval", "uuid", "bytea"})


def _result(rows) -> str:
    value = json.dumps(rows, ensure_ascii=False, default=str)
    if len(value) > MAX_OUTPUT_CHARS:
        raise ValueError("Query result exceeds the output size limit; select fewer/smaller columns")
    return value


def _check_postgres(cur, capability: ReadConnection, used: frozenset[str]) -> None:
    # The role itself, memberships, database/schema creation and all user-table
    # writes are checked, including column-only grants. Public function EXECUTE
    # is common: it must be revoked from application/extension routines too.
    cur.execute("""
        SELECT current_user, session_user, r.rolsuper, r.rolcreatedb,
               r.rolcreaterole, r.rolreplication, r.rolbypassrls,
               EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=r.oid),
               pg_catalog.has_database_privilege(current_database(), 'CREATE,TEMP'),
               EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
                       WHERE pg_catalog.has_schema_privilege(n.oid, 'CREATE')),
               EXISTS (SELECT 1 FROM pg_catalog.pg_class c
                       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                       AND c.relkind IN ('r','p','v','f','m') AND (
                         pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
                         OR pg_catalog.has_any_column_privilege(c.oid, 'INSERT,UPDATE'))),
               EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                       JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                       WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                       AND pg_catalog.has_function_privilege(p.oid, 'EXECUTE'))
        FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
    """)
    role = cur.fetchone()
    if not role or role[:2] != (capability.role, capability.role) or any(role[2:]):
        raise ValueError("Database role is not a dedicated constrained reader; see database access setup")
    for name in used:
        schema, table = name.split(".")
        cur.execute("""
            SELECT c.relkind, c.relrowsecurity, c.relhassubclass, c.oid
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s
        """, (schema, table))
        relation = cur.fetchone()
        # Views/FDWs/RLS/inherited relations can invoke code or read tables the
        # capability did not name. Dedicated base-table reads have no such path.
        if not relation or relation[0] != "r" or relation[1] or relation[2]:
            raise ValueError("Remote SQL reads require ordinary base tables without RLS or inheritance")
        cur.execute("""
            SELECT n.nspname, t.typname, a.attgenerated
            FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
            JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
            WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped
        """, (relation[3],))
        if any(namespace != "pg_catalog" or kind not in _PG_TYPES or generated
               for namespace, kind, generated in cur.fetchall()):
            raise ValueError("Remote SQL table contains unsupported types or generated columns")


def read_postgres(capability: ReadConnection, sql: str, params: list, limit: int,
                  used: frozenset[str]) -> str:
    """Every statement uses the same bounded, read-only connection."""
    import psycopg

    try:
        remote = urlsplit(capability.dsn).hostname not in ("localhost", "127.0.0.1", "::1")
        with psycopg.connect(capability.dsn, connect_timeout=5,
                             keepalives=1, keepalives_idle=5, keepalives_interval=1,
                             keepalives_count=2, tcp_user_timeout=5000,
                             sslmode="verify-full" if remote else "prefer", options=(
            "-c default_transaction_read_only=on -c statement_timeout=2000 "
            "-c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000 "
            "-c search_path=pg_catalog -c work_mem=1MB"
        )) as conn:
            with conn.cursor() as cur:
                _check_postgres(cur, capability, used)
                cur.execute(sql, params)
                cols = [d.name for d in cur.description or ()]
                rows = [dict(zip(cols, row)) for row in cur.fetchmany(limit)]
                return _result(rows)
    except ImportError:
        raise
    except (ValueError, psycopg.Error, OSError) as exc:
        # No URI, SQL text, server path or administrative detail is model output.
        return "Error: Remote database read refused or failed. " + safe_error(exc)


def read_mysql(capability: ReadConnection, sql: str, params: list, limit: int,
               used: frozenset[str]) -> str:
    import pymysql

    url = urlsplit(capability.dsn)
    remote = url.hostname not in ("localhost", "127.0.0.1", "::1")
    conn = pymysql.connect(host=url.hostname, port=url.port or 3306,
                           user=unquote(url.username), password=unquote(url.password or ""),
                           database=url.path.strip("/"), connect_timeout=5,
                           read_timeout=5, write_timeout=5, autocommit=False,
                           ssl=ssl.create_default_context() if remote else None, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION sql_mode='STRICT_ALL_TABLES'")
            cur.execute("SET SESSION MAX_EXECUTION_TIME=2000")
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute("SELECT CURRENT_USER()")
            if cur.fetchone()[0].split("@", 1)[0] != capability.role:
                raise ValueError("Database role does not match the declared reader")
            cur.execute("SHOW GRANTS FOR CURRENT_USER")
            for (grant,) in cur.fetchall():
                upper = grant.upper()
                if (not upper.startswith(("GRANT SELECT ON ", "GRANT USAGE ON "))
                        or "WITH GRANT OPTION" in upper):
                    raise ValueError("MySQL reader must have only SELECT/USAGE and no role memberships")
            for name in used:
                schema, table = name.split(".")
                cur.execute("SELECT TABLE_TYPE, ENGINE FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (schema, table))
                if cur.fetchone() != ("BASE TABLE", "InnoDB"):
                    raise ValueError("MySQL reads require InnoDB base tables")
                cur.execute("SELECT EXTRA FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (schema, table))
                if any("GENERATED" in extra.upper() for (extra,) in cur.fetchall()):
                    raise ValueError("Generated columns are not supported")
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description or ()]
            return _result([dict(zip(cols, row)) for row in cur.fetchmany(limit)])
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def read_mongo(capability: ReadConnection, filt: dict, collection: str, limit: int) -> str:
    from pymongo import MongoClient

    database = urlsplit(capability.dsn).path.strip("/")
    remote = urlsplit(capability.dsn).hostname not in ("localhost", "127.0.0.1", "::1")
    with MongoClient(capability.dsn, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                     socketTimeoutMS=5000, timeoutMS=5000, tls=remote) as client:
        status = client[database].command("connectionStatus", showPrivileges=True)["authInfo"]
        users = status.get("authenticatedUsers", [])
        if len(users) != 1 or users[0].get("user") != capability.role:
            raise ValueError("MongoDB user does not match the declared reader")
        privileges = status.get("authenticatedUserPrivileges", [])
        if not privileges:
            raise ValueError("MongoDB reader has no qualified find privileges")
        for privilege in privileges:
            resource = privilege.get("resource", {})
            if (set(privilege.get("actions", [])) != {"find"}
                    or resource.get("db") != database
                    or resource.get("collection") not in capability.tables):
                raise ValueError("MongoDB reader needs collection-specific find-only privileges")
        metadata = client[database].command(
            "listCollections", filter={"name": collection},
            nameOnly=True, authorizedCollections=True,
        )["cursor"]["firstBatch"]
        if len(metadata) != 1 or metadata[0].get("type") != "collection":
            raise ValueError("MongoDB reads require ordinary collections; views are refused")
        cursor = client[database][collection].find(filt).limit(limit).max_time_ms(STATEMENT_MS)
        try:
            return _result(list(cursor))
        finally:
            cursor.close()
