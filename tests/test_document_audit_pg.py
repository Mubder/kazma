"""The document audit on Postgres is append-only and pruned by retention alone.

2026-10-13 (DOC-A1, found by the Documents documentation review). On SQLite
two triggers make the audit immutable and let only the retention prune
delete; the Postgres table had neither, and ``prune_older_than`` used the
SQLite connection -- ``None`` on Postgres -- so every retention pass failed
and the table grew without bound.

Now one row trigger (``kazma_document_audit_guard``) refuses every UPDATE and
every DELETE outside a transaction marked by the prune, and the prune runs on
Postgres. ``pg_dump -t`` carries the trigger but not its function, so every
restore creates the function first (``pg_bridge.restore_database``,
``db/pg_backup.KAZMA_PG_FUNCTIONS``); a restore into a fresh database is
drilled here. Negative controls: the old prune raises; without the trigger an
UPDATE goes through; without the function step a strict restore fails.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── static gate: every trigger function product code calls is restorable ──

_CALLS = re.compile(r"EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(?:\w+\.)?(\w+)\s*\(", re.I)


def _trigger_functions(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.update(_CALLS.findall(node.value))
    return names


def test_every_trigger_function_is_declared_for_restore():
    from kazma_core.db.pg_backup import KAZMA_PG_FUNCTIONS

    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    missing = {}
    for rel in sorted(set(listed)):
        for name in _trigger_functions((ROOT / rel).read_text(encoding="utf-8")):
            if name not in KAZMA_PG_FUNCTIONS:
                missing[name] = rel
    assert not missing, f"declare in db/pg_backup.KAZMA_PG_FUNCTIONS so restores recreate them: {missing}"


def test_the_gate_sees_a_trigger_function():
    # Negative control.
    source = 'DDL = "CREATE TRIGGER t BEFORE DELETE ON x FOR EACH ROW EXECUTE FUNCTION public.not_declared()"'
    assert _trigger_functions(source) == {"not_declared"}


# ── real Postgres ──────────────────────────────────────────────────────────

def _base_dsn() -> str:
    pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    return base


@pytest.fixture
def fresh_dbs():
    psycopg = pytest.importorskip("psycopg")
    base = _base_dsn()
    prefix, _, _ = base.rpartition("/")
    names = [f"kazma_audit_{uuid.uuid4().hex[:10]}" for _ in range(2)]
    with psycopg.connect(base, autocommit=True) as admin:
        for name in names:
            admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield [f"{prefix}/{name}" for name in names]
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            for name in names:
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _store(dsn):
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    pool = PostgresPool(dsn, min_size=1, max_size=2)
    repository = PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)
    return pool, DocumentAuditStore(repository)


def _seed(dsn, tenant):
    psycopg = pytest.importorskip("psycopg")
    old = datetime.now(UTC) - timedelta(days=400)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO document_audit_events (tenant_id, event_type, action, outcome, created_at) "
                "VALUES (%s, 'access', 'read_content', 'success', %s)", (tenant, old + timedelta(minutes=i)))


@pytest.mark.postgres
def test_the_audit_is_append_only_and_retention_prunes_it(fresh_dbs):
    psycopg = pytest.importorskip("psycopg")
    dsn = fresh_dbs[0]
    tenant = "audit-pg"
    pool, store = _store(dsn)
    try:
        _seed(dsn, tenant)
        store.record(tenant_id=tenant, event_type="access", action="read_content")  # today: kept
        with psycopg.connect(dsn, autocommit=True) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
                conn.execute("UPDATE document_audit_events SET action = 'x'")
            with pytest.raises(psycopg.errors.RaiseException, match="pruned by retention"):
                conn.execute("DELETE FROM document_audit_events")
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        assert store.prune_older_than(cutoff_iso=cutoff, max_rows=2) == 2  # bounded batch
        assert store.prune_older_than(cutoff_iso=cutoff, max_rows=10) == 1
        assert store.count(tenant_id=tenant) == 1
        second_pool, _ = _store(dsn)  # a second start finds the guard and adds nothing
        second_pool.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            triggers = conn.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'document_audit_guard'").fetchone()[0]
        assert triggers == 1
    finally:
        pool.close()


@pytest.mark.postgres
def test_the_old_prune_cannot_run_on_postgres(fresh_dbs, monkeypatch):
    # Negative control: the SQLite path the prune always took.
    import threading

    pool, store = _store(fresh_dbs[0])
    try:
        monkeypatch.setattr(store, "_pg", False)
        monkeypatch.setattr(store, "_lock", threading.RLock())
        with pytest.raises(AttributeError):
            store.prune_older_than(cutoff_iso=datetime.now(UTC).isoformat(), max_rows=10)
    finally:
        pool.close()


@pytest.mark.postgres
def test_without_the_trigger_an_update_goes_through(fresh_dbs):
    # Negative control: the guard is what refuses the update.
    psycopg = pytest.importorskip("psycopg")
    dsn = fresh_dbs[0]
    pool, _ = _store(dsn)
    try:
        _seed(dsn, "audit-pg")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP TRIGGER document_audit_guard ON document_audit_events")
            assert conn.execute("UPDATE document_audit_events SET action = 'x'").rowcount == 3
    finally:
        pool.close()


def _dump_tools():
    from kazma_core.migration.pg_bridge import PgToolNotFound, resolve_pg_dump, resolve_pg_restore

    try:
        resolve_pg_dump()
        resolve_pg_restore()
    except PgToolNotFound:
        pytest.skip("pg_dump/pg_restore not available")


@pytest.mark.postgres
def test_a_restore_into_a_fresh_database_recreates_the_guard(fresh_dbs, tmp_path):
    psycopg = pytest.importorskip("psycopg")
    _dump_tools()
    from kazma_core.migration.pg_bridge import dump_database, restore_database

    source, target = fresh_dbs
    pool, _ = _store(source)
    try:
        _seed(source, "audit-pg")
    finally:
        pool.close()
    dump = tmp_path / "audit.dump"
    dump_database(source, dump, tables=["document_audit_events"])
    restore_database(dump, target, strict=True)
    with psycopg.connect(target, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM document_audit_events").fetchone()[0] == 3
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("UPDATE document_audit_events SET action = 'x'")


@pytest.mark.postgres
def test_without_the_function_step_a_strict_restore_fails(fresh_dbs, tmp_path, monkeypatch):
    # Negative control: pg_dump -t carries the trigger, not the function.
    _dump_tools()
    from kazma_core.migration import pg_bridge

    source, target = fresh_dbs
    pool, _ = _store(source)
    pool.close()
    dump = tmp_path / "audit.dump"
    pg_bridge.dump_database(source, dump, tables=["document_audit_events"])
    monkeypatch.setattr(pg_bridge, "_create_kazma_functions", lambda dsn: None)
    with pytest.raises(pg_bridge.PgBridgeError, match="kazma_document_audit_guard"):
        pg_bridge.restore_database(dump, target, strict=True)
