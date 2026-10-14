"""Restoring BOTH Postgres backups into one database: the drilled order.

2026-10-13 (DOC-A10, from the Documents documentation review). A Postgres
install has two backups of its database: the whole-database dump of Kazma's
tables (``pg_shared_*``, ``db/pg_backup``; it holds the document tables too)
and the coordinated document backup (``document-pg-*``, ``documents/
backup_pg``: the document tables and their content files at ONE point). Each
restore was drilled alone; restoring both into one database had no order:

- the whole-database restore first left document tables, and the
  coordinated restore refused the database ("already has document tables");
- the coordinated restore first, then the whole-database restore
  (``--clean``) replaced the document tables with the dump's rows -- older
  than the content just restored, so a document ingested between the two
  backups lost its catalog row while its files stayed.

The drilled order: whole-database restore, then the coordinated restore with
``replace_document_tables`` (``scripts/document_pg_backup.py restore
--replace-document-tables``), which drops the dump's document tables -- only
after the backup verified and the storage root was found empty -- and puts
the coordinated copy in. Negative controls: both failures above.
"""

from __future__ import annotations

import asyncio
import io
import multiprocessing
import os
import traceback
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres


def _bootstrap(dsn: str) -> None:
    os.environ["KAZMA_DB_BACKEND"] = "postgres"
    os.environ["KAZMA_DATABASE_URL"] = dsn
    os.environ["KAZMA_DOCUMENTS_METADATA_BACKEND"] = "postgres"
    os.environ["KAZMA_DOCUMENTS_JOBS_BACKEND"] = "postgres"
    from kazma_core.config_store import get_config_store

    get_config_store().set("documents.enabled", True, category="documents")


def _service(root: str):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=Path(root), security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
    ))
    assert getattr(svc.repository, "backend_name", None) == "postgres"
    return svc


async def _drain(svc, tenant: str) -> None:
    from kazma_core.documents.worker import DocumentWorker

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(50):
        claimed = svc.jobs.claim_next(owner="order", lease_seconds=120, tenant_id=tenant)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "order")


def _ingest(svc, text: str) -> str:
    result = svc.ingest_stream(io.BytesIO(text.encode()), filename="n.txt", tenant_id="order",
                               actor_id="operator", workspace_id="w")
    asyncio.run(_drain(svc, "order"))
    return str(result.document_id)


def _build_source(dsn: str, root: str, backups: str) -> dict:
    """Document A, the whole-database dump, document B, the coordinated backup."""
    _bootstrap(dsn)
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.documents.backup_pg import perform_pg_document_backup
    from kazma_core.migration.pg_bridge import dump_database

    get_config_store().set("drill.marker", "from-the-whole-database-dump", category="drill")
    svc = _service(root)
    try:
        first = _ingest(svc, "Document A, in both backups.")
        shared = Path(backups) / "pg_shared.dump"
        dump_database(dsn, shared, tables=list(KAZMA_PG_TABLES))
        second = _ingest(svc, "Document B, ingested between the two backups.")
        report = perform_pg_document_backup(dsn=dsn, storage_root=root, dest_dir=backups)
        assert report["ok"], report
        return {"first": first, "second": second, "shared": str(shared), "document_backup": report["path"]}
    finally:
        svc.close()


def _state(dsn: str, root: str, document_id: str) -> dict:
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn) as conn:
        documents = {str(r[0]) for r in conn.execute("SELECT id FROM documents WHERE deleted_at IS NULL")}
        marker = conn.execute(
            "SELECT count(*) FROM kazma_settings WHERE key = 'drill.marker'").fetchone()[0]
    files = sum(1 for p in Path(root).rglob("*") if p.is_file()) if Path(root).exists() else 0
    readable = None
    if document_id in documents:
        _bootstrap(dsn)
        svc = _service(root)
        try:
            readable = svc.get_content(tenant_id="order", actor_id="operator", document_id=document_id)["text"]
        finally:
            svc.close()
    return {"documents": sorted(documents), "marker": marker, "files": files, "readable": readable}


def _drilled_order(target: str, ids: dict, root: str) -> dict:
    from kazma_core.documents.backup_pg import restore_pg_document_backup
    from kazma_core.migration.pg_bridge import restore_database

    restore_database(ids["shared"], target)
    report = restore_pg_document_backup(ids["document_backup"], target_dsn=target, storage_root=root,
                                        replace_document_tables=True)
    return {"report_ok": report["ok"], "replaced": report.get("replaced_document_tables"),
            **_state(target, root, ids["second"])}


def _without_the_option(target: str, ids: dict, root: str) -> str:
    from kazma_core.documents.backup_pg import restore_pg_document_backup
    from kazma_core.migration.pg_bridge import restore_database

    restore_database(ids["shared"], target)
    try:
        restore_pg_document_backup(ids["document_backup"], target_dsn=target, storage_root=root)
    except RuntimeError as exc:
        return str(exc)
    return "restored"


def _coordinated_then_whole(target: str, ids: dict, root: str) -> dict:
    from kazma_core.documents.backup_pg import restore_pg_document_backup
    from kazma_core.migration.pg_bridge import restore_database

    restore_pg_document_backup(ids["document_backup"], target_dsn=target, storage_root=root)
    restore_database(ids["shared"], target)
    return _state(target, root, ids["second"])


def _child(name: str, args: tuple, queue) -> None:
    try:
        queue.put(("ok", globals()[name](*args)))
    except BaseException:  # noqa: BLE001 - reported to the parent
        queue.put(("error", traceback.format_exc()))


def _run(name: str, *args):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    child = ctx.Process(target=_child, args=(name, args, queue))
    child.start()
    child.join(timeout=300)
    if child.is_alive():
        child.kill()
        child.join(10)
        pytest.fail(f"{name} did not finish")
    status, value = queue.get(timeout=10)
    assert status == "ok", value
    return value


@pytest.fixture
def databases():
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    from kazma_core.migration.pg_bridge import PgToolNotFound, resolve_pg_dump, resolve_pg_restore

    try:
        resolve_pg_dump()
        resolve_pg_restore()
    except PgToolNotFound:
        pytest.skip("pg_dump/pg_restore not available")
    prefix, _, _ = base.rpartition("/")
    names = {role: f"kazma_order_{role}_{uuid.uuid4().hex[:10]}" for role in ("source", "target")}
    with psycopg.connect(base, autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield {role: f"{prefix}/{name}" for role, name in names.items()}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            for name in names.values():
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _source(tmp_path, databases) -> dict:
    backups = tmp_path / "backups"
    backups.mkdir()
    return _run("_build_source", databases["source"], str(tmp_path / "source-store"), str(backups))


def test_both_backups_restore_in_the_drilled_order(tmp_path, databases):
    ids = _source(tmp_path, databases)
    result = _run("_drilled_order", databases["target"], ids, str(tmp_path / "restored"))
    assert result["report_ok"] is True
    assert result["replaced"]  # the dump's older document tables were replaced
    assert sorted([ids["first"], ids["second"]]) == result["documents"]
    assert result["marker"] == 1  # the rest of Kazma's tables came from the dump
    assert result["readable"] and "Document B" in result["readable"]


def test_without_replacing_the_coordinated_restore_refuses(tmp_path, databases):
    # Negative control: the whole-database restore leaves document tables.
    ids = _source(tmp_path, databases)
    message = _run("_without_the_option", databases["target"], ids, str(tmp_path / "restored"))
    assert "already has document tables" in message


def test_the_other_order_loses_the_newer_documents_catalog_row(tmp_path, databases):
    # Negative control: the whole-database dump's --clean puts back its older
    # document tables over the coordinated copy; B's files stay, its row goes.
    ids = _source(tmp_path, databases)
    result = _run("_coordinated_then_whole", databases["target"], ids, str(tmp_path / "restored"))
    assert result["documents"] == [ids["first"]]
    assert result["files"] > 0 and result["readable"] is None
