"""Postgres schema setup never takes a table lock it does not need (db.pg_schema).

2026-10-13. A 4-writer restore drill deadlocked in the intake store's
constructor: every Postgres store sent ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` (and ``CREATE INDEX IF NOT EXISTS``) at every start, and Postgres
takes the ALTER's ACCESS EXCLUSIVE lock -- the index's SHARE lock -- before it
looks whether the column or index is already there. So starts deadlocked each
other, and a start waited for a whole ``pg_dump`` (or, for an index, any open
writer) while every query on the table queued behind it.

Static gate: no product code sends an ``ADD COLUMN IF NOT EXISTS`` except
through ``ensure_schema`` (enumerated from every tracked product file; the
pre-fix shapes as the negative control). Real-Postgres gates (marked
``postgres``, private databases): each store's real schema setup completes
while a dump-style reader and an open writer hold its tables, and two starts
of a fresh database at once both succeed. The old statements, sent directly,
wait and collide: the negative controls.
"""

from __future__ import annotations

import ast
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PATTERN = "ADD COLUMN IF NOT EXISTS"
_EXEMPT = {"kazma-core/kazma_core/db/pg_schema.py"}


def _product_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(set(listed))


def _direct_sends(source: str) -> list[int]:
    """Lines where an ``execute`` call is given an ADD COLUMN IF NOT EXISTS
    statement: a literal, an f-string, or a module-level string constant."""
    tree = ast.parse(source)
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value

    def text_of(arg: ast.AST) -> str:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            return "".join(text_of(part) for part in arg.values)
        if isinstance(arg, ast.Name):
            return constants.get(arg.id, "")
        if isinstance(arg, ast.BinOp):
            return text_of(arg.left) + text_of(arg.right)
        return ""

    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "_execute"} and node.args):
            if any(_PATTERN in " ".join(text_of(arg).upper().split()) for arg in node.args):
                found.append(node.lineno)
    return found


def test_no_postgres_add_column_is_sent_outside_ensure_schema():
    offenders = []
    for rel in _product_files():
        if rel in _EXEMPT:
            continue
        source = (ROOT / rel).read_text(encoding="utf-8")
        if _PATTERN not in source.upper():
            continue
        offenders += [f"{rel}:{line}" for line in _direct_sends(source)]
    assert not offenders, "send these through kazma_core.db.pg_schema.ensure_schema: " + ", ".join(offenders)


def test_the_scan_finds_the_old_shapes():
    # Negative control: the three shapes the stores used before the fix.
    old = '''
_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_jobs (id TEXT);
ALTER TABLE document_jobs ADD COLUMN IF NOT EXISTS actor_id TEXT NOT NULL DEFAULT '';
"""
def ensure(cur, name, definition):
    cur.execute(_PG_SCHEMA)
    cur.execute("ALTER TABLE kazma_chat_sessions ADD COLUMN IF NOT EXISTS "
                "pinned BOOLEAN DEFAULT FALSE")
    self._execute(cur, f"ALTER TABLE document_intake_reservations ADD COLUMN IF NOT EXISTS {name} {definition}")
'''
    assert len(_direct_sends(old)) == 3


# ── real Postgres ──────────────────────────────────────────────────────────

@pytest.fixture
def private_db():
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    prefix, _, _ = base.rpartition("/")
    name = f"kazma_schema_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"{prefix}/{name}"
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _setups(dsn: str):
    """Each store's real schema setup, as a callable on a fresh pool."""
    from kazma_core.db.postgres_pool import PostgresPool, _ensure_core_schema
    from kazma_core.documents.admission import IntakeAdmissionStore
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    def core():
        pool = PostgresPool(dsn, min_size=1, max_size=2)
        try:
            _ensure_core_schema(pool)
        finally:
            pool.close()

    def documents():
        pool = PostgresPool(dsn, min_size=1, max_size=2)
        try:
            jobs = PostgresDocumentJobRepository(pool)
            PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)
            IntakeAdmissionStore(jobs)
        finally:
            pool.close()

    return {"core": core, "documents": documents}


_HELD_TABLES = ("kazma_chat_sessions", "document_jobs", "document_intake_reservations", "documents")


def _hold(dsn: str, statement: str):
    psycopg = pytest.importorskip("psycopg")

    holder = psycopg.connect(dsn)
    holder.execute("BEGIN")
    for table in _HELD_TABLES:
        holder.execute(statement.format(table=table))
    return holder


def _timed(fn) -> float:
    started = time.monotonic()
    fn()
    return time.monotonic() - started


@pytest.mark.postgres
@pytest.mark.parametrize("holder", [
    "SELECT * FROM {table} LIMIT 1",  # what pg_dump holds for the whole dump
    "LOCK TABLE {table} IN ROW EXCLUSIVE MODE",  # any open writer
])
def test_a_start_does_not_wait_behind_a_dump_or_a_writer(private_db, holder):
    setups = _setups(private_db)
    for setup in setups.values():
        setup()  # the schema exists: this is every later start
    held = _hold(private_db, holder)
    try:
        for name, setup in setups.items():
            assert _timed(setup) < 5.0, name  # it used to wait for the holder
    finally:
        held.rollback()
        held.close()


@pytest.mark.postgres
def test_without_the_helper_the_same_statements_wait(private_db):
    # Negative control: the statements the stores sent, sent directly.
    psycopg = pytest.importorskip("psycopg")

    for setup in _setups(private_db).values():
        setup()
    for holder, statement in (
        ("SELECT * FROM {table} LIMIT 1",
         "ALTER TABLE document_jobs ADD COLUMN IF NOT EXISTS actor_id TEXT NOT NULL DEFAULT ''"),
        ("LOCK TABLE {table} IN ROW EXCLUSIVE MODE",
         "CREATE INDEX IF NOT EXISTS idx_pg_document_jobs_claim "
         "ON document_jobs(state, retry_at, lease_expires_at, created_at)"),
    ):
        held = _hold(private_db, holder)
        try:
            with psycopg.connect(private_db) as other:
                other.execute("SET statement_timeout = '1500ms'")
                with pytest.raises(psycopg.errors.QueryCanceled):
                    other.execute(statement)
        finally:
            held.rollback()
            held.close()


@pytest.mark.postgres
def test_starts_at_the_same_moment_on_a_fresh_database_both_succeed(private_db):
    errors: list[BaseException] = []
    setups = _setups(private_db)
    barrier = threading.Barrier(4)

    def start(setup):
        try:
            barrier.wait(10)
            setup()
        except BaseException as exc:  # noqa: BLE001 - collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(setups[name],)) for name in ("documents", "documents", "core", "core")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors, errors


@pytest.mark.postgres
def test_without_the_lock_two_fresh_creates_collide(private_db):
    # Negative control: two transactions creating the same table at once.
    psycopg = pytest.importorskip("psycopg")

    first = psycopg.connect(private_db)
    second = psycopg.connect(private_db)
    outcome: list[BaseException | None] = []
    try:
        first.execute("CREATE TABLE IF NOT EXISTS collide (id INT)")

        def other():
            try:
                second.execute("CREATE TABLE IF NOT EXISTS collide (id INT)")
                second.commit()
                outcome.append(None)
            except psycopg.Error as exc:
                outcome.append(exc)

        pid = second.info.backend_pid
        thread = threading.Thread(target=other)
        thread.start()
        # Until Postgres says the second create waits on the first's catalog row.
        with psycopg.connect(private_db, autocommit=True) as watch:
            deadline = time.monotonic() + 10
            while watch.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                (pid,)).fetchone()[0] != "Lock":
                if time.monotonic() > deadline:
                    raise AssertionError("the second create never waited")
                time.sleep(0.05)
        first.commit()
        thread.join(10)
        assert outcome and isinstance(outcome[0], psycopg.errors.UniqueViolation)
    finally:
        first.close()
        second.close()
