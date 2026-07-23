"""Database Client Native Skill — tools for inspecting and querying SQLite databases."""

from __future__ import annotations

import logging
import sqlite3
import json
import re
from pathlib import Path
from typing import Any

from kazma_core.agent.tool_registry import _workspace_scope_error
from kazma_core.tools.file_write import _get_workspace

logger = logging.getLogger(__name__)


def _is_path_allowed(path_str: str) -> bool:
    """Security helper to restrict paths strictly to allowed directories or workspace."""
    try:
        path = Path(path_str).expanduser().resolve()
        workspace = _get_workspace().resolve()
        _ALLOWED_DB_ROOTS = [
            Path("kazma-data").resolve(),
            Path.home() / ".kazma",
            Path("/tmp").resolve(),
            workspace,
        ]
        return any(
            str(path).lower().startswith(str(root).lower()) or path == root
            for root in _ALLOWED_DB_ROOTS
        )
    except Exception:
        return False


async def inspect_db_schema(db_uri: str) -> str:
    """Extract list of tables, column names, data types, primary/foreign keys, and indexes from SQLite databases.

    Args:
        db_uri: Path to the local sqlite database file.

    Returns:
        Markdown description of the schema structure.
    """
    if db_uri != ":memory:":
        p = Path(db_uri).expanduser().resolve()
        scope_err = _workspace_scope_error(p, db_uri, "reads")
        if scope_err:
            return scope_err
        if not _is_path_allowed(db_uri):
            return f"Error: Database access denied for path: {db_uri}"
        if not p.exists():
            return f"Error: Database file not found: {db_uri}"

    try:
        conn = sqlite3.connect(db_uri)
        cursor = conn.cursor()

        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            conn.close()
            return "No user-defined tables found in the database."

        report = ["# Database Schema Report", ""]
        for table in tables:
            report.append(f"## Table: `{table}`")
            report.append("| Column | Type | Nullable | Default | PK |")
            report.append("| :--- | :--- | :---: | :--- | :---: |")

            cursor.execute(f"PRAGMA table_info({table});")
            columns = cursor.fetchall()
            for col in columns:
                # col matches: (cid, name, type, notnull, dflt_value, pk)
                cid, name, col_type, notnull, dflt_value, pk = col
                nullable = "No" if notnull else "Yes"
                is_pk = "🟢" if pk else ""
                report.append(f"| `{name}` | {col_type or 'BLOB'} | {nullable} | {dflt_value or 'NULL'} | {is_pk} |")
            report.append("")

        conn.close()
        return "\n".join(report)

    except Exception as e:
        logger.error("Error inspecting database schema %s: %s", db_uri, e)
        return f"Error inspecting database: {e}"


async def execute_db_query(
    db_uri: str,
    query: str,
    params: list[Any] | None = None,
    limit: int = 100,
) -> str:
    """Execute a read-only SQL SELECT query against a local SQLite database file.

    Args:
        db_uri: Path to the local sqlite database file, or ':memory:'.
        query: SQL statement (SELECT only).
        params: Optional list of query parameters.
        limit: Max row limit.

    Returns:
        JSON string representing rows, or safety/execution error messages.
    """
    # ── Multi-dialect dispatch: non-SQLite URIs route to the right driver ──
    if _detect_dialect(db_uri) != "sqlite":
        return await execute_db_query_any(db_uri, query, params, limit)

    # ── Safety: only allow SELECT or WITH ──
    def strip_leading_comments(sql: str) -> str:
        while True:
            sql = sql.strip()
            if sql.startswith("--"):
                nl = sql.find("\n")
                if nl == -1:
                    return ""
                sql = sql[nl:]
            elif sql.startswith("/*"):
                end = sql.find("*/")
                if end == -1:
                    break
                sql = sql[end + 2 :]
            else:
                break
        return sql.strip()

    sql_clean = strip_leading_comments(query)
    normalized = sql_clean.upper()
    if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
        return "Error: Only SELECT and WITH read-only queries are allowed for safety."

    # Block multi-statement queries
    if ";" in query.strip().rstrip(";"):
        return "Error: Multi-statement queries are not allowed."

    # Double-layer AST/word-boundary safety check
    forbidden_keywords = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|RENAME|PRAGMA|ATTACH|DETACH|VACUUM)\b",
        re.IGNORECASE,
    )
    if forbidden_keywords.search(query):
        return "Error: Write operations or administrative commands are not allowed."

    if db_uri != ":memory:":
        p = Path(db_uri).expanduser().resolve()
        scope_err = _workspace_scope_error(p, db_uri, "reads")
        if scope_err:
            return scope_err
        if not _is_path_allowed(db_uri):
            return f"Error: Database access denied for path: {db_uri}"
        if not p.exists():
            return f"Error: Database file not found: {db_uri}"

    try:
        conn = sqlite3.connect(db_uri)
        conn.row_factory = sqlite3.Row

        # Set database-level read-only authorizer
        def authorizer_callback(action, arg1, arg2, dbname, trigger_name):
            allowed_actions = {
                sqlite3.SQLITE_SELECT,
                sqlite3.SQLITE_READ,
            }
            if action in allowed_actions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer_callback)

        cursor = conn.execute(query, params or [])
        rows = cursor.fetchmany(limit)
        conn.close()

        if not rows:
            return "[]"

        result = [dict(row) for row in rows]
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("SQL query execution failed: %s", e)
        return f"SQL Error: Query execution failed. Check syntax and permissions. Detail: {e}"


async def sqlite_query(
    query: str,
    db_path: str = "kazma-data/checkpoints.db",
    params: list[Any] | None = None,
    limit: int = 100,
) -> str:
    """Execute a read-only SQL query against the local SQLite database.

    SELECT queries only. Returns rows as JSON.

    Args:
        query: SQL query statement.
        db_path: Path to the local sqlite database file.
        params: Optional query parameters.
        limit: Max row limit.

    Returns:
        JSON string representing rows, or safety/execution error messages.
    """
    return await execute_db_query(db_uri=db_path, query=query, params=params, limit=limit)


# ════════════════════════════════════════════════════════════════════════
# Multi-dialect support — Postgres / MySQL / MongoDB
# ════════════════════════════════════════════════════════════════════════


def _detect_dialect(db_uri: str) -> str:
    """Return 'postgres' | 'mysql' | 'mongodb' | 'sqlite' from the URI scheme."""
    u = (db_uri or "").lower()
    if u.startswith(("postgresql://", "postgres://")):
        return "postgres"
    if u.startswith(("mysql://", "mariadb://")):
        return "mysql"
    if u.startswith("mongodb://") or u.startswith("mongodb+srv://"):
        return "mongodb"
    return "sqlite"


def _validate_readonly_sql(query: str) -> str | None:
    """Return an error string if *query* is not a safe read-only SELECT/WITH."""
    def _strip(sql: str) -> str:
        while True:
            sql = sql.strip()
            if sql.startswith("--"):
                nl = sql.find("\n")
                if nl == -1:
                    return ""
                sql = sql[nl:]
            elif sql.startswith("/*"):
                end = sql.find("*/")
                if end == -1:
                    break
                sql = sql[end + 2:]
            else:
                break
        return sql.strip()

    sql_clean = _strip(query)
    normalized = sql_clean.upper()
    if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
        return "Error: Only SELECT and WITH read-only queries are allowed for safety."
    if ";" in query.strip().rstrip(";"):
        return "Error: Multi-statement queries are not allowed."
    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|RENAME|PRAGMA|ATTACH|DETACH|VACUUM|TRUNCATE|GRANT|REVOKE|MERGE)\b",
        re.IGNORECASE,
    )
    if forbidden.search(query):
        return "Error: Write operations or administrative commands are not allowed."
    return None


async def _query_postgres(db_uri: str, query: str, params: list | None, limit: int) -> str:
    try:
        import psycopg  # psycopg3
    except ImportError:
        return "Error: psycopg not installed. Run: pip install 'psycopg[binary]'"
    try:
        # psycopg3 is sync; run in a worker thread to stay non-blocking.
        import asyncio

        def _run() -> str:
            with psycopg.connect(db_uri) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params or [])
                    cols = [d.name for d in (cur.description or [])]
                    rows = cur.fetchmany(limit)
                    if not rows:
                        return "[]"
                    return json.dumps(
                        [dict(zip(cols, r)) for r in rows], ensure_ascii=False, indent=2, default=str
                    )
        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        return f"SQL Error: Postgres query failed. Detail: {exc}"


async def _query_mysql(db_uri: str, query: str, params: list | None, limit: int) -> str:
    try:
        import pymysql
    except ImportError:
        return "Error: pymysql not installed. Run: pip install pymysql"
    try:
        import asyncio

        def _run() -> str:
            from urllib.parse import urlparse

            p = urlparse(db_uri)
            conn = pymysql.connect(
                host=p.hostname or "localhost",
                port=p.port or 3306,
                user=p.username or "root",
                password=p.password or "",
                database=(p.path or "/").lstrip("/"),
            )
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cur:
                    cur.execute(query, params or ())
                    rows = cur.fetchmany(limit)
                    if not rows:
                        return "[]"
                    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
            finally:
                conn.close()
        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        return f"SQL Error: MySQL query failed. Detail: {exc}"


async def _query_mongodb(db_uri: str, query: str, params: list | None, limit: int) -> str:
    """Run a MongoDB find() from a JSON *query* document.

    For Mongo, ``query`` is a JSON filter document (not SQL). ``params`` is
    ignored. The default database is taken from the URI path.
    """
    try:
        from pymongo import MongoClient
        from urllib.parse import urlparse
    except ImportError:
        return "Error: pymongo not installed. Run: pip install pymongo"
    try:
        import asyncio

        def _run() -> str:
            try:
                filt = json.loads(query) if query.strip() else {}
            except json.JSONDecodeError as exc:
                return f"Error: MongoDB filter must be valid JSON — {exc}"
            p = urlparse(db_uri)
            db_name = (p.path or "/test").lstrip("/")
            client = MongoClient(db_uri, serverSelectionTimeoutMS=5000)
            try:
                # Infer collection: prefer params[0], else 'documents'.
                coll_name = (params[0] if params else "documents")
                docs = list(client[db_name][coll_name].find(filt).limit(limit))
                if not docs:
                    return "[]"
                return json.dumps(docs, ensure_ascii=False, indent=2, default=str)
            finally:
                client.close()
        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        return f"Mongo Error: query failed. Detail: {exc}"


async def execute_db_query_any(
    db_uri: str,
    query: str,
    params: list[Any] | None = None,
    limit: int = 100,
) -> str:
    """Dialect-aware read-only query (Postgres/MySQL/Mongo/SQLite).

    For SQL dialects, *query* must be a SELECT/WITH. For Mongo, *query* is a
    JSON filter document and ``params[0]`` (optional) names the collection.
    """
    dialect = _detect_dialect(db_uri)
    if dialect == "mongodb":
        return await _query_mongodb(db_uri, query, params, limit)

    # SQL dialects — enforce read-only.
    err = _validate_readonly_sql(query)
    if err:
        return err

    if dialect == "postgres":
        return await _query_postgres(db_uri, query, params, limit)
    if dialect == "mysql":
        return await _query_mysql(db_uri, query, params, limit)
    # SQLite — delegate to the existing path-validated implementation.
    return await execute_db_query(db_uri=db_uri, query=query, params=params, limit=limit)

