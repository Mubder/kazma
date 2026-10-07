"""Database Client Native Skill — tools for inspecting and querying SQLite databases."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import json
import re
import time
from pathlib import Path
from typing import Any

from kazma_core.agent.tool_scope import _workspace_scope_error
from kazma_core.tools.file_write import _get_workspace
from kazma_core.workspace.binding import resolve_tool_path

logger = logging.getLogger(__name__)


def _is_path_allowed(path_str: str) -> bool:
    """Security helper to restrict paths strictly to allowed directories or workspace."""
    try:
        path = Path(path_str).expanduser().resolve()
        workspace = _get_workspace().resolve()
        # No fallback list: when the real roots cannot be computed the outer
        # handler denies. The old fallback allowed a CWD-relative
        # "kazma-data" -- widening an allowlist on an error path.
        from kazma_core.paths import data_dir, legacy_user_home, user_home

        _ALLOWED_DB_ROOTS = [
            data_dir().resolve(),
            user_home().resolve(),
            legacy_user_home().resolve(),
            Path("/tmp").resolve(),
            workspace,
        ]
        return any(
            str(path).lower().startswith(str(root).lower()) or path == root
            for root in _ALLOWED_DB_ROOTS
        )
    except Exception:
        return False


def _quote_ident(name: str) -> str:
    """Quote a SQLite identifier. Never interpolate raw table names."""
    return '"' + str(name).replace('"', '""') + '"'


def _is_internal_kazma_db(path: Path) -> bool:
    """True for Kazma's own SQLite files (conversation/secrets/state).

    The one predicate every door shares (``kazma_core.store_registry``).
    This used to be a hand-kept list of 15 names; ``x_posts.db``,
    ``x_scheduled.db`` and ``kazma.db`` were not on it, and the model read
    them raw on 2026-09-25 while hunting for its own drafts.
    """
    from kazma_core.store_registry import is_kazma_store

    return is_kazma_store(path)


def _deny_internal(resolved: str) -> str | None:
    if resolved == ":memory:":
        return None
    try:
        p = Path(resolved).expanduser().resolve()
    except Exception:
        return None
    if _is_internal_kazma_db(p):
        from kazma_core.store_registry import store_refusal

        # "Pass a workspace SQLite file" was the whole message; it named no
        # way to the data, and the model went looking through file_read and
        # an approved python_exec instead.
        return "Error: " + store_refusal(p, door="the SQL tools")
    return None


def _install_readonly_authorizer(conn: sqlite3.Connection) -> None:
    _SQLITE_FUNCTION = getattr(sqlite3, "SQLITE_FUNCTION", 31)
    _SAFE_SQL_FUNCTIONS = frozenset(
        {
            "count", "sum", "avg", "min", "max", "total", "group_concat",
            "length", "lower", "upper", "substr", "substring", "trim",
            "ltrim", "rtrim", "replace", "instr", "like", "glob", "ifnull",
            "coalesce", "nullif", "abs", "round", "typeof", "hex", "quote",
            "printf", "unicode", "char", "date", "time", "datetime",
            "julianday", "strftime", "json", "json_extract",
            "json_array_length", "json_type", "json_valid", "bm25",
            "highlight", "snippet", "rank",
        }
    )

    def authorizer_callback(action, arg1, arg2, dbname, trigger_name):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
            return sqlite3.SQLITE_OK
        if action == getattr(sqlite3, "SQLITE_PRAGMA", 19):
            # inspect_db_schema needs table_info; execute_db_query forbids PRAGMA
            # via the SQL keyword gate. Allow table_info / index_list only.
            pragma = str(arg1 or "").lower()
            if pragma in ("table_info", "index_list", "foreign_key_list"):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == _SQLITE_FUNCTION:
            fname = (arg2 or arg1 or "").lower()
            if fname in _SAFE_SQL_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer_callback)


def _fence_result(text: str, source: str) -> str:
    if not text or text.startswith("Error"):
        return text
    try:
        from kazma_core.safety.prompt_fence import fence_untrusted

        return fence_untrusted(text, source=source)
    except Exception:
        logger.debug("prompt fence unavailable for database_client", exc_info=True)
        return text


def _connect_sqlite(resolved: str) -> sqlite3.Connection:
    """Connect to SQLite and attempt to load sqlite_vec extension if available."""
    uri = resolved if resolved == ":memory:" else Path(resolved).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=resolved != ":memory:", timeout=2)
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (ImportError, OSError, sqlite3.Error):
        logger.debug("SQLite vector extension unavailable", exc_info=True)
    try:
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.Error):
        conn.close()
        raise
    return conn


def _checked_sqlite_file(db_uri: str) -> tuple[str, str | None]:
    """The SQLite file *db_uri* names, and the refusal to answer with (None: allowed).

    A relative path means the active workspace (``resolve_tool_path``), as in
    every file tool; it meant the server's working directory until
    2026-10-02, so ``sqlite_query(db_path="app.db")`` opened a file there,
    not the project's. The workspace, the grants and the store registry are
    read here: the tools run this in a worker thread.
    """
    if db_uri == ":memory:":
        return db_uri, None
    p = resolve_tool_path(db_uri)
    resolved = str(p)
    # One of Kazma's own stores first: the workspace check refuses it too,
    # in the file tools' words ("file tools may not open it"), which is not
    # the door the model just tried (live 2026-10-02).
    denied = _deny_internal(resolved)
    if denied:
        return resolved, denied
    scope_err = _workspace_scope_error(p, db_uri, "reads")
    if scope_err:
        return resolved, scope_err
    if not _is_path_allowed(resolved):
        return resolved, f"Error: Database access denied for path: {db_uri}"
    if not p.exists():
        return resolved, f"Error: Database file not found: {db_uri}"
    return resolved, None


async def inspect_db_schema(db_uri: str) -> str:
    """Extract list of tables, column names, data types, primary/foreign keys, and indexes from SQLite databases.

    Args:
        db_uri: Path to the local sqlite database file.

    Returns:
        Markdown description of the schema structure.
    """
    db_file, refusal = await asyncio.to_thread(_checked_sqlite_file, db_uri)
    if refusal:
        return refusal

    def _inspect() -> str:
        conn = _connect_sqlite(db_file)
        try:
            _install_readonly_authorizer(conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%';"
            )
            tables = [row[0] for row in cursor.fetchall()]
            if not tables:
                return "No user-defined tables found in the database."
            report = ["# Database Schema Report", ""]
            for table in tables:
                report.append(f"## Table: `{table}`")
                report.append("| Column | Type | Nullable | Default | PK |")
                report.append("| :--- | :--- | :---: | :--- | :---: |")
                cursor.execute(f"PRAGMA table_info({_quote_ident(table)});")
                columns = cursor.fetchall()
                for col in columns:
                    cid, name, col_type, notnull, dflt_value, pk = col
                    nullable = "No" if notnull else "Yes"
                    is_pk = "🟢" if pk else ""
                    report.append(
                        f"| `{name}` | {col_type or 'BLOB'} | {nullable} | "
                        f"{dflt_value or 'NULL'} | {is_pk} |"
                    )
                report.append("")
            return "\n".join(report)
        finally:
            conn.close()

    try:
        text = await asyncio.to_thread(_inspect)
        return _fence_result(text, source=f"db_schema:{db_uri}")
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

    READ-ONLY. Cannot INSERT/UPDATE/DELETE/DROP. Not for V2 memory cleanup —
    use ``memory_list_beliefs`` / ``memory_invalidate`` / ``memory_search``.

    Args:
        db_uri: Local SQLite file, ':memory:', or operator-defined connection:<name>.
        query: SQL statement (SELECT only).
        params: Optional list of query parameters.
        limit: Max row limit.

    Returns:
        JSON string representing rows, or safety/execution error messages.
    """
    # ── Multi-dialect dispatch: non-SQLite URIs route to the right driver ──
    if _detect_dialect(db_uri) != "sqlite" or db_uri.startswith("connection:"):
        return await execute_db_query_any(db_uri, query, params, limit)

    from .sql_policy import MAX_OUTPUT_CHARS, MAX_QUERY_CHARS, bounded_rows

    try:
        bounded_rows(limit)
    except ValueError as exc:
        return "Error: " + str(exc)
    if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
        return "Error: SQL query exceeds the query size limit"

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

    db_file, refusal = await asyncio.to_thread(_checked_sqlite_file, db_uri)
    if refusal:
        return refusal

    def _query() -> str:
        conn = _connect_sqlite(db_file)
        try:
            conn.row_factory = sqlite3.Row
            _install_readonly_authorizer(conn)
            deadline = time.monotonic() + 2
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            cursor = conn.execute(query, params or [])
            rows = cursor.fetchmany(limit)
            if not rows:
                return "[]"
            result = json.dumps(
                [dict(row) for row in rows],
                ensure_ascii=False,
                indent=2,
            )
            if len(result) > MAX_OUTPUT_CHARS:
                return "Error: Query result exceeds the output size limit"
            return result
        finally:
            conn.close()

    try:
        text = await asyncio.to_thread(_query)
        return _fence_result(text, source=f"db_query:{db_uri}")
    except Exception as e:
        logger.error("SQL query execution failed: %s", e)
        return f"SQL Error: Query execution failed. Check syntax and permissions. Detail: {e}"


async def sqlite_query(
    query: str,
    db_path: str = "",
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
    if not str(db_path or "").strip():
        return (
            "Error: db_path is required. Pass a workspace SQLite file — "
            "Kazma internal databases are not queryable."
        )
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


async def execute_db_query_any(
    db_uri: str,
    query: str,
    params: list[Any] | None = None,
    limit: int = 100,
) -> str:
    """Read an operator-defined Postgres/MySQL/Mongo capability or local SQLite.

    Raw remote URIs are refused. Remote SQL is compiled to supported SELECT
    syntax and its role/table privileges verified. Mongo uses a restricted
    JSON filter and ``params[0]`` names an explicitly allowed collection.
    """
    if _detect_dialect(db_uri) == "sqlite" and not db_uri.startswith("connection:"):
        return await execute_db_query(db_uri=db_uri, query=query, params=params, limit=limit)

    from kazma_core.errors import safe_error
    from .connections import resolve_connection
    from .remote_reads import read_mongo, read_mysql, read_postgres
    from .sql_policy import bounded_rows, compile_read, validate_mongo_filter

    try:
        bounded_rows(limit)
        capability = await asyncio.to_thread(resolve_connection, db_uri)
        if not isinstance(params, (list, type(None))) or len(params or []) > 256:
            return "Error: params must be a list of at most 256 scalar values"
        if capability.dialect == "mongodb":
            collection = params[0] if params else "documents"
            if collection not in capability.tables:
                return "Error: Collection is outside this connection's capability"
            filt = validate_mongo_filter(query)
            text = await asyncio.to_thread(read_mongo, capability, filt, collection, limit)
        else:
            if any(type(value) not in (str, int, float, bool, type(None))
                   or (isinstance(value, str) and len(value) > 16384) for value in params or []):
                return "Error: SQL params must contain bounded scalar values"
            sql, used = compile_read(query, dialect=capability.dialect,
                                     tables=capability.tables, limit=limit)
            read = read_postgres if capability.dialect == "postgres" else read_mysql
            text = await asyncio.to_thread(read, capability, sql, params or [], limit, used)
        return _fence_result(text, source=f"db_connection:{capability.name}")
    except ValueError as exc:
        return "Error: " + str(exc)
    except ImportError:
        from kazma_core.install_hint import extra_install_hint

        return "Error: Remote database reads require " + extra_install_hint("database")
    except Exception as exc:
        return "Error: Remote database read refused or failed. " + safe_error(exc)
