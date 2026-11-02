"""An orphan blob row in the idle Postgres catalog is collected (2026-11, FA-205).

The live install keeps its document catalog in SQLite and its document jobs
on Postgres. Postgres' ``document_blobs`` held one row from 2026-08-14, when
the catalog was there for a while: no document, no version, and no GC pass
ever read that table -- GC sweeps the catalog in use. The maintenance pass
now applies the same rule (unreferenced, past the grace) to the idle
Postgres catalog too, and touches nothing else there.

The test found that GC never worked on a Postgres catalog IN USE either: its
mark read the pool's dict rows by position, failed with ``KeyError`` every
pass, and collected nothing. ``test_a_postgres_catalog_in_use_collects_its_orphan``
holds that; the idle-catalog step switched off is the other negative control.
A row inside the grace stays (the rule, not "delete everything"); with no
Postgres the step does nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    # The live shape: the catalog in SQLite whatever the jobs backend.
    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "sqlite")
    service = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    yield service
    service.close()


def _plant_sqlite(svc, *, days_old: int) -> str:
    repo = svc.repository if hasattr(svc, "repository") else svc._repository
    blob_id = str(uuid.uuid4())
    created = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    repo._conn.execute(
        "INSERT INTO document_blobs (id, tenant_id, sha256, byte_size, storage_kind, created_at) "
        "VALUES (?, 'default', ?, 1, 'originals', ?)",
        (blob_id, hashlib.sha256(blob_id.encode()).hexdigest(), created),
    )
    repo._conn.commit()
    return blob_id


def test_the_catalog_in_use_collects_its_orphan(svc):
    gone = _plant_sqlite(svc, days_old=80)
    svc.run_maintenance(dry_run=False)
    repo = svc.repository if hasattr(svc, "repository") else svc._repository
    assert repo._conn.execute("SELECT 1 FROM document_blobs WHERE id = ?", (gone,)).fetchone() is None


def test_without_postgres_the_idle_catalog_step_does_nothing(svc, monkeypatch):
    from kazma_core.db import pg_helpers

    monkeypatch.setattr(pg_helpers, "use_postgres", lambda: False)
    report = svc.run_maintenance(dry_run=False)
    assert not [e for e in report["errors"] if e.startswith("idle_catalog")]


def _pg_pool():
    try:
        from kazma_core.db.postgres_pool import get_postgres_pool

        pool = get_postgres_pool()
    except Exception:  # noqa: BLE001 - no configured pool means skip
        pool = None
    if pool is None:
        pytest.skip("Postgres pool not configured")
    return pool


def _plant_pg(pool, *, days_old: int) -> str:
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)  # its tables
    blob_id = str(uuid.uuid4())
    created = datetime.now(UTC) - timedelta(days=days_old)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO document_blobs (id, tenant_id, sha256, byte_size, storage_kind, created_at) "
                "VALUES (%s, 'default', %s, 1, 'originals', %s)",
                (blob_id, hashlib.sha256(blob_id.encode()).hexdigest(), created),
            )
        conn.commit()
    return blob_id


def _pg_has(pool, blob_id: str) -> bool:
    return pool.execute_one("SELECT 1 AS hit FROM document_blobs WHERE id = %s", (blob_id,)) is not None


@pytest.mark.postgres
def test_an_old_orphan_in_the_idle_postgres_catalog_is_collected(svc, monkeypatch):
    from kazma_core.db import pg_helpers

    pool = _pg_pool()
    monkeypatch.setattr(pg_helpers, "use_postgres", lambda: True)
    old = _plant_pg(pool, days_old=80)
    fresh = _plant_pg(pool, days_old=0)
    try:
        svc.run_maintenance(dry_run=False)
        assert not _pg_has(pool, old)
        assert _pg_has(pool, fresh), "a row inside the grace is not garbage yet"
    finally:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_blobs WHERE id = ANY(%s)", ([old, fresh],))
            conn.commit()


@pytest.mark.postgres
def test_negative_control_without_the_step_the_orphan_stays(svc, monkeypatch):
    from kazma_core.db import pg_helpers
    from kazma_core.documents import retention

    pool = _pg_pool()
    monkeypatch.setattr(pg_helpers, "use_postgres", lambda: True)
    monkeypatch.setattr(retention.DocumentGarbageCollector, "_sweep_idle_catalog",
                        lambda self, cfg, **kw: kw["budget"])
    old = _plant_pg(pool, days_old=80)
    try:
        svc.run_maintenance(dry_run=False)
        assert _pg_has(pool, old)
    finally:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_blobs WHERE id = %s", (old,))
            conn.commit()


@pytest.mark.postgres
def test_a_postgres_catalog_in_use_collects_its_orphan(tmp_path, monkeypatch):
    pool = _pg_pool()
    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
    service = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    old = _plant_pg(pool, days_old=80)
    try:
        report = service.run_maintenance(dry_run=False)
        assert not [e for e in report["errors"] if e.startswith("mark_failed")], report["errors"]
        assert not _pg_has(pool, old)
    finally:
        service.close()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_blobs WHERE id = %s", (old,))
            conn.commit()
