"""A native snapshot must restore the bytes durable workers actually execute."""

import asyncio
import io
import shutil
import time
from dataclasses import replace

import pytest
from kazma_core.documents.backup import perform_document_backup, verify_document_backup
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.repository import DocumentAccessError

from tests._document_jobs import wait_for_job


@pytest.mark.asyncio
async def test_native_restore_resumes_queue_and_preserves_content_acl_and_history(tmp_path, record_property):
    cfg = DocumentConfig(storage_root=tmp_path / 'source', security_malware_scan='off',
        security_local_salvage=False, ocr_enabled=False)
    source = DocumentIngestionService(config=cfg)
    try:
        accepted = await asyncio.to_thread(source.ingest_stream, io.BytesIO(b'Restored durable source.\n'),
            filename='note.txt', tenant_id='tenant', workspace_id='workspace', actor_id='alice')
        source.repository.grant_access(tenant_id='tenant', document_id=accepted.document_id,
            actor_id='alice', principal_id='bob', permission='read')
        version = source.repository.get_version(tenant_id='tenant', version_id=accepted.version_id)
        # This is a normal post-promotion GC state: intake provenance still
        # names quarantine, but the durable queue consumes originals.
        source.storage.blob_path(kind='quarantine', sha256=version.source_sha256).unlink()
        report = await asyncio.to_thread(perform_document_backup, storage_root=cfg.storage_root,
            dest_dir=tmp_path / 'backups')
        assert report['ok'], report
        assert any(key.startswith('originals/') for key in report['checksums'])
    finally:
        source.close()
    started = time.monotonic()
    restore_root = tmp_path / 'restore'
    shutil.copytree(report['path'], restore_root)
    restored = DocumentIngestionService(config=replace(cfg, storage_root=restore_root))
    try:
        await restored.start_workers()
        await wait_for_job(restored, 'tenant', accepted.job_id)
        for actor in ['alice', 'bob']:
            content = restored.get_content(tenant_id='tenant', document_id=accepted.document_id,
                actor_id=actor, fence=False)
            assert 'Restored durable source.' in content['text']
        with pytest.raises(DocumentAccessError):
            restored.get_content(tenant_id='tenant', document_id=accepted.document_id, actor_id='carol')
        events = restored.job_events(tenant_id='tenant', job_id=accepted.job_id, actor_id='alice')
        assert events[0]['to_state'] == 'received' and events[-1]['to_state'] == 'ready'
        ready = await asyncio.to_thread(perform_document_backup, storage_root=restore_root,
            dest_dir=tmp_path / 'ready-backups')
        assert ready['ok'] and any(key.startswith('manifests/') for key in ready['checksums'])
        assert verify_document_backup(backup_dir=ready['path'])['ok']
    finally:
        await restored.stop_workers()
        restored.close()
    second_root = tmp_path / 'ready-restore'
    shutil.copytree(ready['path'], second_root)
    second = DocumentIngestionService(config=replace(cfg, storage_root=second_root))
    try:
        assert second.job_status(tenant_id='tenant', job_id=accepted.job_id, actor_id='alice')['state'] == 'ready'
        assert 'Restored durable source.' in second.get_content(tenant_id='tenant',
            document_id=accepted.document_id, actor_id='bob', fence=False)['text']
        assert second.job_events(tenant_id='tenant', job_id=accepted.job_id, actor_id='alice') == events
    finally:
        second.close()
    record_property('small_fixture_restore_seconds', round(time.monotonic() - started, 3))


@pytest.mark.parametrize("postgres_part", ["metadata", "jobs"])
def test_native_snapshot_refuses_live_postgres_fallback_database(tmp_path, postgres_part):
    from types import SimpleNamespace

    from kazma_core.documents.ingestion import peek_ingestion_service, set_ingestion_service
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.migration.bundle import Manifest
    from kazma_core.migration.exporter import _export_document_store
    from kazma_core.migration.importer import _restore_document_store

    root = tmp_path / "live"
    root.mkdir()
    # A real empty fallback SQLite file previously yielded a misleading green
    # backup, even though the serving relational state was elsewhere.
    import sqlite3

    with sqlite3.connect(root / "documents.db") as conn:
        conn.execute("CREATE TABLE fallback_only (id INTEGER)")
    installed = SimpleNamespace(
        storage=SimpleNamespace(root=root),
        repository=SimpleNamespace(backend_name="postgres" if postgres_part == "metadata" else "sqlite"),
        jobs=object.__new__(PostgresDocumentJobRepository) if postgres_part == "jobs" else object(),
    )
    previous = peek_ingestion_service()
    set_ingestion_service(installed)
    try:
        # The scheduled backup takes the coordinated Postgres path (F06,
        # tests/test_document_pg_backup_restore.py); it never snapshots the
        # fallback file, and without a database URL it says so.
        report = perform_document_backup(dest_dir=tmp_path / "backups")
        assert report["ok"] is False
        assert "Postgres" in report["error"]
        assert not (tmp_path / "backups").exists()
        with pytest.raises(RuntimeError, match="document_backup_backend_unsupported"):
            _export_document_store(tmp_path / "staging", Manifest(), lambda *_: None)
        assert not (tmp_path / "staging").exists()
        staging = tmp_path / "import"
        (staging / "document-store").mkdir(parents=True)
        before = (root / "documents.db").read_bytes()
        if postgres_part == "metadata":
            # A Postgres catalog with a SQLite queue: no store can hold both.
            with pytest.raises(RuntimeError, match="document_backup_backend_unsupported"):
                _restore_document_store(staging, staging / "data", SimpleNamespace(), lambda *_: None)
        else:
            # A SQLite catalog beside a Postgres queue (DOC-A6/A7, 2026-10-13):
            # documents.db IS the catalog there, not a fallback; an import with
            # no staged catalog leaves it as it is.
            _restore_document_store(staging, staging / "data", SimpleNamespace(), lambda *_: None)
        assert (root / "documents.db").read_bytes() == before
    finally:
        set_ingestion_service(previous)
