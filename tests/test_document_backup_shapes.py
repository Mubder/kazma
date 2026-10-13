"""Backup, export and import follow where the document CATALOG is.

2026-10-13 (DOC-A6, DOC-A7, DOC-A8; found by the Documents documentation
review).

- DOC-A6: the scheduled backup chose the coordinated Postgres backup by the
  JOBS backend alone, so a SQLite catalog beside a Postgres queue (a
  documented ``KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite`` choice) was "backed
  up" from Postgres tables that were not the catalog. Now the path follows
  the catalog; the SQLite snapshot takes its quarantine->originals promotions
  from the Postgres queue (they are not in ``documents.db`` then); the one
  shape no backup can hold (Postgres catalog, SQLite queue) is refused by
  name. The coordinated backup's folder is renamed into place only at the
  end: a failed run used to leave an empty ``document-pg-*`` folder.
- DOC-A7: ``kazma migrate export`` asked only the running service, which a
  CLI does not have, and exported a Postgres store as its empty local
  ``documents.db``. Now a Postgres catalog's files travel (the catalog itself
  is in ``data/postgres.dump``).
- DOC-A8: a document restore that failed during import was a warning and the
  import said ok. It fails the import now.

Negative controls: the snapshot's own (absent) events require a quarantine
copy GC already removed; the old CLI view (no service = SQLite) exports no
file of a Postgres store; a restore that does not raise lets the import pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import multiprocessing
import os
import traceback
import uuid
import zipfile
from pathlib import Path

import pytest

# ── DOC-A6: no folder named like a backup after a failure ──────────────────


def test_a_failed_coordinated_backup_leaves_no_folder(tmp_path, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from kazma_core.documents import backup_pg

    seen: list[list[str]] = []

    def refuse(*args, **kwargs):
        seen.append(sorted(p.name for p in (tmp_path / "backups").iterdir()))
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)
    report = backup_pg.perform_pg_document_backup(
        dsn="postgresql://nobody@127.0.0.1:1/none", storage_root=tmp_path / "store",
        dest_dir=tmp_path / "backups",
    )
    assert report["ok"] is False and report["path"] is None
    assert list((tmp_path / "backups").iterdir()) == []
    # While it ran, the only folder was the partial one (never "document-pg-*").
    assert len(seen) == 1 and len(seen[0]) == 1
    assert seen[0][0].startswith(".document-pg-") and seen[0][0].endswith(".partial")


# ── DOC-A8: a failed document restore fails the import ─────────────────────


def _sqlite_bundle(path: Path) -> Path:
    from kazma_core.migration.bundle import BUNDLE_VERSION, Manifest

    files: dict[str, bytes | str] = {
        "meta.env": "KAZMA_VAULT_KEY=\n",
        "config.yaml": "agent:\n  name: x\n",
        "pathmap.json": "{}\n",
        "document-store/originals/sha256/ab/cd/abcd": b"document bytes",
    }
    hashes = {name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
              for name, data in files.items()}
    manifest = Manifest(bundle_version=BUNDLE_VERSION, source_workspace_root="", source_data_dir="",
                        file_hashes=hashes)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def _import(tmp_path, monkeypatch, restore):
    from kazma_core.migration import importer

    data = tmp_path / "kazma-data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)
    monkeypatch.setattr(importer, "_restore_document_store", restore)
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    from unittest.mock import patch

    with patch("kazma_core.db.backend.is_postgres", return_value=False):
        return importer.import_bundle(_sqlite_bundle(tmp_path / "b.zip"), target_workspace_root=str(tmp_path))


def test_a_failed_document_restore_fails_the_import(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    report = _import(tmp_path, monkeypatch, broken)
    assert report.ok is False
    assert any("document store restore failed: disk full" in e for e in report.errors)


def test_a_document_restore_that_works_leaves_the_import_ok(tmp_path, monkeypatch):
    # Negative control: the same bundle and import, the restore succeeding.
    report = _import(tmp_path, monkeypatch, lambda *_args, **_kwargs: None)
    assert report.ok is True, report.errors


# ── real Postgres: each database in a child process (the pool is global) ───


def _bootstrap(dsn: str, metadata: str) -> None:
    os.environ["KAZMA_DB_BACKEND"] = "postgres"
    os.environ["KAZMA_DATABASE_URL"] = dsn
    os.environ["KAZMA_DOCUMENTS_METADATA_BACKEND"] = metadata
    os.environ["KAZMA_DOCUMENTS_JOBS_BACKEND"] = "postgres"
    from kazma_core.config_store import get_config_store

    get_config_store().set("documents.enabled", True, category="documents")


def _service(root: str):
    from kazma_core.config_store import get_config_store
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService, set_ingestion_service

    # The installed store's root, as an install configures it (a CLI reads it).
    get_config_store().set("documents.storage_root", root, category="documents")

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=Path(root), security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
    ))
    set_ingestion_service(svc)
    return svc


async def _drain(svc, tenant: str) -> None:
    from kazma_core.documents.worker import DocumentWorker

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(50):
        claimed = svc.jobs.claim_next(owner="shape", lease_seconds=120, tenant_id=tenant)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "shape")


def _ready_document(svc, tenant: str) -> str:
    result = svc.ingest_stream(io.BytesIO(f"shape {uuid.uuid4().hex}".encode()), filename="n.txt",
                               tenant_id=tenant, actor_id="alice", workspace_id="w")
    asyncio.run(_drain(svc, tenant))
    return str(result.version_id)


def _quarantine_gone(root: Path) -> int:
    # What GC does to a promoted source's quarantine copy after its grace.
    gone = 0
    for path in (root / "quarantine").rglob("*"):
        if path.is_file():
            path.unlink()
            gone += 1
    return gone


def _sqlite_catalog_beside_postgres_queue(dsn: str, root: str, dest: str) -> dict:
    _bootstrap(dsn, "sqlite")
    from kazma_core.documents import backup

    svc = _service(root)
    try:
        assert getattr(svc.repository, "backend_name", "sqlite") == "sqlite"
        assert "Postgres" in type(svc.jobs).__name__
        _ready_document(svc, "shape")
        removed = _quarantine_gone(Path(root))
        report = backup.perform_document_backup(dest_dir=dest)
        # Negative control: the old rule read promotions from the snapshot's
        # own events -- none, the queue is in Postgres -- and so required the
        # quarantine copy GC removed.
        old_refs, _ = backup._read_references(Path(report["path"]) / "documents.db")
        old_missing = [f"{kind}/{sha}" for sha, kind in old_refs
                       if not (Path(root) / backup._blob_rel_path(kind, sha)).is_file()]
        return {"ok": report["ok"], "removed": removed, "jobs_backend": report.get("jobs_backend"),
                "old_missing": old_missing, "error": report.get("error"),
                "verification": report.get("verification")}
    finally:
        svc.close()


def _export_postgres_catalog(dsn: str, root: str, staging: str) -> dict:
    _bootstrap(dsn, "postgres")
    from kazma_core.documents.ingestion import set_ingestion_service
    from kazma_core.migration.bundle import Manifest
    from kazma_core.migration.exporter import _export_document_store

    svc = _service(root)
    try:
        assert getattr(svc.repository, "backend_name", None) == "postgres"
        _ready_document(svc, "export")
    finally:
        svc.close()
    set_ingestion_service(None)  # a CLI process has no running service
    # Negative control: the old check asked only the service (none here) and
    # exported the local documents.db: a fallback file with no document in
    # it, so the bundle carried none of the store's files.
    from kazma_core.documents.backup import _read_references

    local = Path(root, "documents.db")
    local_catalog = sum(map(len, _read_references(local))) if local.exists() else 0
    manifest = Manifest()
    _export_document_store(Path(staging), manifest, lambda *_: None)
    files = sorted(str(p.relative_to(staging)).replace("\\", "/")
                   for p in Path(staging, "document-store").rglob("*") if p.is_file()) \
        if Path(staging, "document-store").exists() else []
    return {"counts": manifest.table_counts.get("_document_store"), "files": files,
            "local_catalog": local_catalog}


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
def database():
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    prefix, _, _ = base.rpartition("/")
    name = f"kazma_shape_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"{prefix}/{name}"
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.postgres
def test_a_sqlite_catalog_beside_a_postgres_queue_is_backed_up(tmp_path, database):
    result = _run("_sqlite_catalog_beside_postgres_queue", database, str(tmp_path / "store"),
                  str(tmp_path / "backups"))
    assert result["removed"] >= 1  # the promoted source's quarantine copy is gone
    assert result["ok"] is True, (result["error"], result["verification"])
    assert result["jobs_backend"] == "postgres"
    # Negative control (same run): the old rule needed the removed copy.
    assert any(m.startswith("quarantine/") for m in result["old_missing"]), result


@pytest.mark.postgres
def test_a_postgres_catalogs_files_travel_in_the_bundle(tmp_path, database):
    result = _run("_export_postgres_catalog", database, str(tmp_path / "store"), str(tmp_path / "staging"))
    assert result["counts"]["catalog"] == "postgres"
    assert any(f.startswith("document-store/originals/") for f in result["files"]), result
    assert any(f.startswith("document-store/manifests/") for f in result["files"]), result
    # Negative control: the local catalog the old export copied names nothing.
    assert result["local_catalog"] == 0
