"""Database Client Native Skill — tools for inspecting and querying SQLite databases."""

from __future__ import annotations

import asyncio
import logging
import os
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


def _deny_internal(db_uri: str) -> str | None:
    if db_uri == ":memory:":
        return None
    try:
        p = Path(db_uri).expanduser().resolve()
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


def _connect_sqlite(db_uri: str) -> sqlite3.Connection:
    """Connect to SQLite and attempt to load sqlite_vec extension if available."""
    conn = sqlite3.connect(db_uri)
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        pass
    try:
        conn.enable_load_extension(False)
    except Exception:
        pass
    return conn


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
        denied = _deny_internal(db_uri)
        if denied:
            return denied
        if not _is_path_allowed(db_uri):
            return f"Error: Database access denied for path: {db_uri}"
        if not p.exists():
            return f"Error: Database file not found: {db_uri}"

    def _inspect() -> str:
        conn = _connect_sqlite(db_uri)
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
        denied = _deny_internal(db_uri)
        if denied:
            return denied
        if not _is_path_allowed(db_uri):
            return f"Error: Database access denied for path: {db_uri}"
        if not p.exists():
            return f"Error: Database file not found: {db_uri}"

    def _query() -> str:
        conn = _connect_sqlite(db_uri)
        try:
            conn.row_factory = sqlite3.Row
            _install_readonly_authorizer(conn)
            cursor = conn.execute(query, params or [])
            rows = cursor.fetchmany(limit)
            if not rows:
                return "[]"
            return json.dumps(
                [dict(row) for row in rows],
                ensure_ascii=False,
                indent=2,
            )
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


def _remote_host_error(db_uri: str, dialect: str) -> str | None:
    """Gate + log outbound database connections (audit 2026-09-16 F-6).

    The SQLite path is workspace-scoped and path-validated. The remote
    dialects are not scoped to anything: the model supplies a whole URI, so a
    prompt-injected agent can open a connection to any host on the internet
    or the local network, at tool tier ``read`` with no HITL prompt.

    The reads themselves are already constrained (SELECT/WITH only), so this
    is not sized as a block-by-default — an operator legitimately points this
    at localhost, a LAN warehouse, or a managed cloud instance, and defaulting
    to deny would break all three. Instead:

      * every remote connection is logged with its host, so it is *visible*;
      * ``KAZMA_DB_CLIENT_ALLOWED_HOSTS`` (comma-separated hostnames) turns it
        into a real allowlist for anyone who wants one.
    """
    from urllib.parse import urlparse

    host = (urlparse(db_uri).hostname or "").strip().lower()
    if not host:
        return "Error: could not parse a host from the database URI."

    if host in ("localhost", "127.0.0.1", "::1"):
        logger.info("[database_client] %s query -> loopback %r", dialect, host)
        return None

    allowed_raw = (os.environ.get("KAZMA_DB_CLIENT_ALLOWED_HOSTS") or "").strip()
    allowed = {h.strip().lower() for h in allowed_raw.split(",") if h.strip()}
    if host not in allowed:
        logger.warning(
            "[database_client] refused %s connection to %r "
            "(not in KAZMA_DB_CLIENT_ALLOWED_HOSTS)", dialect, host,
        )
        return (
            f"Error: host {host!r} is not in KAZMA_DB_CLIENT_ALLOWED_HOSTS. "
            "Loopback is allowed without an allowlist; add this host there "
            "to allow this connection."
        )

    logger.info("[database_client] %s query -> host %r", dialect, host)
    return None


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
    if dialect != "sqlite":
        host_err = _remote_host_error(db_uri, dialect)
        if host_err:
            return host_err
    if dialect == "mongodb":
        return _fence_result(
            await _query_mongodb(db_uri, query, params, limit),
            source=f"db:mongo:{db_uri}",
        )

    # SQL dialects — enforce read-only.
    err = _validate_readonly_sql(query)
    if err:
        return err

    if dialect == "postgres":
        return _fence_result(
            await _query_postgres(db_uri, query, params, limit),
            source=f"db:postgres:{db_uri}",
        )
    if dialect == "mysql":
        return _fence_result(
            await _query_mysql(db_uri, query, params, limit),
            source=f"db:mysql:{db_uri}",
        )
    # SQLite — delegate to the existing path-validated implementation.
    return await execute_db_query(db_uri=db_uri, query=query, params=params, limit=limit)

