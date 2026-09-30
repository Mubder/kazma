"""Every SQLite store adds a new column the same way, and a failure is seen.

``kazma_core.db.sqlite_columns`` is the one home of "add this column if the
table lacks it" (AUD-027). Before it, most stores tried the ``ALTER`` and
swallowed every error: a locked or read-only database left the column
missing, and every later query failed with "no such column", far from the
cause. The only error that is not a failure is another process adding the
same column first.

Three layers:
* the helper, sync and async, on real SQLite files -- including a read-only
  database, the failure the old pattern hid (its negative control);
* stores upgraded from their pre-column shape, one of each connection style
  (autocommit, ``with conn:`` transactions, aiosqlite);
* a source gate: no SQLite ``ADD COLUMN`` statement outside the helper
  (Postgres's ``ADD COLUMN IF NOT EXISTS`` is idempotent and exempt).
"""

from __future__ import annotations

import ast
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from kazma_core.db.sqlite_columns import add_missing_columns, add_missing_columns_async

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = "kazma-core/kazma_core/db/sqlite_columns.py"


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]


# ── the helper ───────────────────────────────────────────────────────────


def test_adds_only_what_is_missing_and_is_idempotent(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT)")
    added = add_missing_columns(conn, "t", (("a", "TEXT"), ("b", "INTEGER DEFAULT 7")))
    assert added == ["b"]
    conn.execute("INSERT INTO t (id) VALUES ('x')")
    assert conn.execute("SELECT b FROM t").fetchone() == (7,)
    assert add_missing_columns(conn, "t", (("a", "TEXT"), ("b", "INTEGER DEFAULT 7"))) == []
    assert _columns(conn, "t") == ["id", "a", "b"]
    conn.close()


def test_another_process_adding_the_column_first_is_not_a_failure() -> None:
    """The column was missing when read, present by the ALTER."""

    class Racing:
        def execute(self, sql: str, *args):
            if sql.startswith("PRAGMA"):
                return iter(())  # read before the other process added it
            raise sqlite3.OperationalError("duplicate column name: b")

    assert add_missing_columns(Racing(), "t", (("b", "TEXT"),)) == []


def test_a_real_failure_raises_negative_control_read_only(tmp_path) -> None:
    """A database that cannot be written leaves the column missing -- loudly.

    The old pattern (``try: ALTER ... except Exception: pass``) returned
    normally here, and the store opened without the column.
    """
    path = tmp_path / "ro.db"
    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE t (id TEXT)")
    setup.close()
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        add_missing_columns(ro, "t", (("b", "TEXT"),))

    # The swallowing shape it replaced, on the same database: silent.
    try:
        ro.execute("ALTER TABLE t ADD COLUMN b TEXT")
    except Exception:
        pass
    assert _columns(ro, "t") == ["id"]
    ro.close()


def test_only_plain_identifiers_reach_ddl(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (id TEXT)")
    with pytest.raises(ValueError):
        add_missing_columns(conn, "t; DROP TABLE t", (("b", "TEXT"),))
    with pytest.raises(ValueError):
        add_missing_columns(conn, "t", (("b TEXT, c", "TEXT"),))
    conn.close()


@pytest.mark.asyncio
async def test_async_variant_adds_and_raises(tmp_path) -> None:
    aiosqlite = pytest.importorskip("aiosqlite")
    path = tmp_path / "a.db"
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE t (id TEXT)")
        assert await add_missing_columns_async(db, "t", (("b", "TEXT"), ("c", "REAL"))) == ["b", "c"]
        assert await add_missing_columns_async(db, "t", (("b", "TEXT"),)) == []
        await db.commit()
    async with aiosqlite.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as ro:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await add_missing_columns_async(ro, "t", (("d", "TEXT"),))


# ── stores upgraded from their old shape ─────────────────────────────────


def _old_shape(fresh_db: Path, table: str, drop: set[str], old_db: Path) -> None:
    """Recreate *table* as it was before *drop* existed, in *old_db*."""
    fresh = sqlite3.connect(fresh_db)
    info = list(fresh.execute(f"PRAGMA table_info({table})"))
    fresh.close()
    assert drop <= {str(r[1]) for r in info}, "the store no longer has these columns"
    defs, pk = [], []
    for _cid, name, ctype, notnull, default, pk_pos in info:
        if name in drop:
            continue
        col = f"{name} {ctype}".strip()
        if notnull:
            col += " NOT NULL"
        if default is not None:
            col += f" DEFAULT {default}"
        defs.append(col)
        if pk_pos:
            pk.append((pk_pos, name))
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join(n for _, n in sorted(pk)) + ")")
    conn = sqlite3.connect(old_db)
    conn.execute(f"CREATE TABLE {table} ({', '.join(defs)})")
    conn.commit()
    conn.close()


def _has_columns(db: Path, table: str, wanted: set[str]) -> bool:
    conn = sqlite3.connect(db)
    try:
        return wanted <= set(_columns(conn, table))
    finally:
        conn.close()


def test_the_swarm_task_store_upgrades_an_old_table(tmp_path) -> None:
    from kazma_core.swarm.task_store import TaskStore

    added = {"context", "dependencies", "fallback_chain", "validation_schema",
             "aggregation", "timeout", "workspace_id", "sort_at"}
    TaskStore(db_path=str(tmp_path / "fresh.db")).close()
    old = tmp_path / "old.db"
    _old_shape(tmp_path / "fresh.db", "swarm_tasks", added, old)
    conn = sqlite3.connect(old)
    conn.execute(
        "INSERT INTO swarm_tasks (id, type, prompt, status, created_at, completed_at) "
        "VALUES ('t1', 'dispatch', 'p', 'completed', '2026-01-01', '2026-01-02')"
    )
    conn.commit()
    conn.close()

    TaskStore(db_path=str(old)).close()
    assert _has_columns(old, "swarm_tasks", added)
    conn = sqlite3.connect(old)
    # Rows from before sort_at sort by when they finished.
    assert conn.execute("SELECT sort_at FROM swarm_tasks WHERE id='t1'").fetchone() == ("2026-01-02",)
    conn.close()


def test_the_web_session_store_upgrades_an_old_table(tmp_path) -> None:
    from kazma_ui.session_manager import SessionManager

    added = {"updated_at", "title", "archived", "pinned"}
    fresh = tmp_path / "fresh.db"
    SessionManager(db_path=str(fresh), spool_path=str(tmp_path / "spool1.db")).close()
    old = tmp_path / "old.db"
    _old_shape(fresh, "sessions", added, old)
    SessionManager(db_path=str(old), spool_path=str(tmp_path / "spool2.db")).close()
    assert _has_columns(old, "sessions", added)


@pytest.mark.asyncio
async def test_the_cron_store_upgrades_an_old_table(tmp_path) -> None:
    from kazma_core.cron.scheduler import SQLiteCronStore

    added = {"tenant_id", "delivery_target", "failure_count"}
    fresh = SQLiteCronStore(db_path=str(tmp_path / "fresh.db"))
    await fresh.init()
    await fresh.close()
    old = tmp_path / "old.db"
    _old_shape(tmp_path / "fresh.db", "cron_jobs", added, old)
    store = SQLiteCronStore(db_path=str(old))
    await store.init()
    await store.close()
    assert _has_columns(old, "cron_jobs", added)


# ── the source gate ─────────────────────────────────────────────────────

_SQLITE_ADD_COLUMN = re.compile(r"ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\b", re.IGNORECASE)


def sqlite_add_column_sites(sources: dict[str, str]) -> list[str]:
    """String constants (f-string parts included) holding a SQLite ADD COLUMN."""
    found = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if _SQLITE_ADD_COLUMN.search(value) and "IF NOT EXISTS" not in value.upper():
                    found.append(f"{rel}:{node.lineno}")
    return found


def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    out = {}
    for rel in files:
        if "_tests" in rel or "/tests/" in rel or rel == HELPER:
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_sqlite_add_column_outside_the_helper() -> None:
    sources = _product_sources()
    assert len(sources) > 300, "the source listing is broken"
    sites = sqlite_add_column_sites(sources)
    assert not sites, (
        "SQLite ADD COLUMN outside kazma_core.db.sqlite_columns -- use "
        "add_missing_columns(conn, table, ((name, definition), ...)):\n  " + "\n  ".join(sites)
    )


def test_negative_control_the_gate_sees_each_old_shape() -> None:
    old = {
        "a.py": 'conn.execute("ALTER TABLE t ADD COLUMN b TEXT")\n',
        "b.py": 'conn.execute(f"ALTER TABLE t ADD COLUMN {name} {ddl}")\n',
        "c.py": '_ALTER = "alter table t add column b INTEGER NOT NULL DEFAULT 0"\n',
        # Postgres's idempotent form is not a SQLite migration.
        "d.py": 'cur.execute("ALTER TABLE t ADD COLUMN IF NOT EXISTS b TEXT")\n',
    }
    assert sqlite_add_column_sites(old) == ["a.py:1", "b.py:1", "c.py:1"]
