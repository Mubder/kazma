"""Coordinated Postgres + content backup restores a working document store.

2026-10-13 (F06). The drill, on a real Postgres server, in private databases
(the shared test database holds other tests' rows whose content lives in
other folders):

* the source holds a ready document shared with bob, a deleted document, a
  queued document, a document whose worker holds a lease, and an intake that
  died half-way (a real killed process);
* the backup runs while another writer changes the source (a new grant after
  the snapshot): the restored database must not contain it, and its row
  counts must equal the snapshot's;
* the restore goes into an empty database and storage root; a new service
  rolls the dead intake back, takes over the expired lease, drives every job
  to ready and serves the content under the original ACL with the original
  job history.

RTO (restore + start + every job ready) is measured and recorded, not
asserted against a target: it is this machine's number for this fixture.
Negative control: the same backup with the snapshot dropped from the dump is
caught by the restore's row-count check.

Needs ``KAZMA_DATABASE_URL`` + ``KAZMA_TEST_ALLOW_REAL_DB=1`` and pg_dump /
pg_restore (PATH, or ``KAZMA_DB_CONTAINER`` for ``docker exec``); skips
otherwise -- a skip is not evidence.
"""

from __future__ import annotations

import asyncio
import io
import json
import multiprocessing
import os
import time
import traceback
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

_KILLED = 137
_BODY = b"Coordinated restore source text.\n"


# ── helpers run inside child processes (each with its own database URL) ───

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
    assert "Postgres" in type(svc.jobs).__name__
    return svc


def _upload(svc, tenant: str, body: bytes):
    return svc.ingest_stream(io.BytesIO(body), filename="note.txt", tenant_id=tenant,
                             actor_id="alice", workspace_id="w")


async def _drain(svc, tenant: str) -> None:
    from kazma_core.documents.worker import DocumentWorker

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(50):
        claimed = svc.jobs.claim_next(owner="drill", lease_seconds=120, tenant_id=tenant)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "drill")


def _build_source(dsn: str, root: str, tenant: str) -> dict:
    _bootstrap(dsn)
    from kazma_core.documents.ingestion import SYSTEM_ACTOR

    svc = _service(root)
    try:
        ready = _upload(svc, tenant, _BODY)
        deleted = _upload(svc, tenant, b"Deleted before the backup.\n")
        asyncio.run(_drain(svc, tenant))
        svc.repository.grant_access(tenant_id=tenant, document_id=ready.document_id,
                                    actor_id="alice", principal_id="bob", permission="read")
        svc.repository.tombstone_document(tenant_id=tenant, document_id=deleted.document_id,
                                          actor_id="alice", reason="drill")
        queued = _upload(svc, tenant, b"Queued at backup time.\n")
        leased = _upload(svc, tenant, b"Leased by a worker at backup time.\n")
        claimed = svc.jobs.claim_next(owner="source-worker", lease_seconds=3600, tenant_id=tenant)
        assert claimed is not None and str(claimed.id) in {str(queued.job_id), str(leased.job_id)}
        events = svc.job_events(tenant_id=tenant, job_id=ready.job_id, actor_id=SYSTEM_ACTOR)
        return {
            "ready": [str(ready.document_id), str(ready.job_id)],
            "deleted": str(deleted.document_id),
            "queued": [str(queued.document_id), str(queued.job_id)],
            "leased": [str(leased.document_id), str(leased.job_id)],
            "claimed": str(claimed.id),
            "events": events,
        }
    finally:
        svc.close()


def _crash_intake(dsn: str, root: str, tenant: str) -> None:
    _bootstrap(dsn)
    svc = _service(root)
    real = svc.repository.create_document

    def create_then_die(*args, **kwargs):
        real(*args, **kwargs)
        os._exit(_KILLED)

    svc.repository.create_document = create_then_die
    _upload(svc, tenant, b"An intake that never finished.\n")
    os._exit(3)


def _backup(dsn: str, root: str, dest: str, tenant: str, ready_doc: str, keep_snapshot: bool) -> dict:
    _bootstrap(dsn)
    from kazma_core.db.postgres_pool import get_postgres_pool
    from kazma_core.documents.backup_pg import perform_pg_document_backup
    from kazma_core.documents.repository_pg import PostgresDocumentRepository
    from kazma_core.migration.pg_bridge import dump_database

    writer = PostgresDocumentRepository(get_postgres_pool(), tenant_quota_bytes=10**9)

    def dump_during_a_write(dsn_, out, *, tables, snapshot):
        # Another writer changes the source after the snapshot was taken.
        writer.grant_access(tenant_id=tenant, document_id=ready_doc, actor_id="alice",
                            principal_id="dave", permission="read")
        return dump_database(dsn_, out, tables=tables, snapshot=snapshot if keep_snapshot else None)

    return perform_pg_document_backup(dsn=dsn, storage_root=root, dest_dir=dest, dump=dump_during_a_write)


def _resume(dsn: str, root: str, tenant: str, ids: dict) -> dict:
    _bootstrap(dsn)
    from kazma_core.documents.ingestion import SYSTEM_ACTOR
    from kazma_core.documents.jobs import DocumentJobState
    from kazma_core.documents.repository import DocumentAccessError

    started = time.monotonic()
    svc = _service(root)  # startup recovery runs here
    try:
        admission = svc.admissions.snapshot(tenant_id=tenant)
        titles = sorted(d["document_id"] for d in svc.list_documents(tenant_id=tenant, actor_id="alice"))
        ready_doc, ready_job = ids["ready"]
        readable = {}
        for actor in ("alice", "bob", "carol", "dave"):
            try:
                text = svc.get_content(tenant_id=tenant, document_id=ready_doc, actor_id=actor, fence=False)["text"]
                readable[actor] = _BODY.decode().strip() in text
            except DocumentAccessError:
                readable[actor] = False
        first_readable = time.monotonic() - started
        svc.jobs.recover_expired_leases(tenant_id=tenant)
        asyncio.run(_drain(svc, tenant))
        states = {
            name: svc.job_status(tenant_id=tenant, job_id=ids[name][1], actor_id=SYSTEM_ACTOR)["state"]
            for name in ("ready", "queued", "leased")
        }
        return {
            "admission": admission,
            "documents": titles,
            "readable": readable,
            "states": states,
            "events_equal": svc.job_events(tenant_id=tenant, job_id=ready_job, actor_id=SYSTEM_ACTOR) == ids["events"],
            "deleted_hidden": ids["deleted"] not in titles,
            "first_readable_seconds": round(first_readable, 3),
            "all_ready_seconds": round(time.monotonic() - started, 3),
            "ready_state": DocumentJobState.READY.value,
        }
    finally:
        svc.close()


def _child(name: str, args: tuple, queue) -> None:
    try:
        queue.put(("ok", globals()[name](*args)))
    except BaseException:  # noqa: BLE001 - reported to the parent
        queue.put(("error", traceback.format_exc()))


def _run(name: str, *args, expect_exit: int | None = None):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    child = ctx.Process(target=_child, args=(name, args, queue))
    child.start()
    child.join(timeout=300)
    if child.is_alive():
        child.kill()
        child.join(10)
        pytest.fail(f"{name} did not finish")
    code = child.exitcode
    child.close()
    if expect_exit is not None:
        assert code == expect_exit, f"{name} exited {code}"
        return None
    status, value = queue.get(timeout=10)
    assert status == "ok", value
    return value


# ── the drill ──────────────────────────────────────────────────────────────

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
    names = {role: f"kazma_drill_{role}_{uuid.uuid4().hex[:10]}" for role in ("source", "target")}
    with psycopg.connect(base, autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield {role: f"{prefix}/{name}" for role, name in names.items()}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            for name in names.values():
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _prepare(tmp_path, databases):
    tenant = f"drill-{uuid.uuid4().hex[:8]}"
    source_root = tmp_path / "source"
    ids = _run("_build_source", databases["source"], str(source_root), tenant)
    _run("_crash_intake", databases["source"], str(source_root), tenant, expect_exit=_KILLED)
    return tenant, source_root, ids


def test_coordinated_backup_restores_a_working_store(tmp_path, databases, record_property):
    from kazma_core.documents.backup_pg import restore_pg_document_backup, verify_pg_document_backup

    tenant, source_root, ids = _prepare(tmp_path, databases)
    report = _run("_backup", databases["source"], str(source_root), str(tmp_path / "backups"),
                  tenant, ids["ready"][0], True)
    assert report["ok"], report
    assert verify_pg_document_backup(report["path"])["ok"]
    assert report["table_counts"]["document_intake_reservations"] >= 1
    target_root = tmp_path / "target"
    started = time.monotonic()
    restored = restore_pg_document_backup(report["path"], target_dsn=databases["target"], storage_root=target_root)
    assert restored["expired_leases"] == 1
    assert restored["restored_reservations"] >= 1
    outcome = _run("_resume", databases["target"], str(target_root), tenant, ids)
    rto = time.monotonic() - started

    assert outcome["admission"]["pending"] == 0 and outcome["admission"]["stranded"] == 0
    assert sorted(outcome["documents"]) == sorted([ids["ready"][0], ids["queued"][0], ids["leased"][0]])
    assert outcome["deleted_hidden"]
    assert outcome["readable"] == {"alice": True, "bob": True, "carol": False, "dave": False}
    assert outcome["states"] == {"ready": "ready", "queued": "ready", "leased": "ready"}
    assert outcome["events_equal"]

    evidence = {
        "snapshot_at": report["snapshot_at"],
        "backup_seconds": report["duration_seconds"],
        "restore": restored["timings"],
        "first_readable_seconds": outcome["first_readable_seconds"],
        "all_jobs_ready_seconds": outcome["all_ready_seconds"],
        "rto_seconds": round(rto, 3),
        "table_counts": restored["table_counts"],
    }
    record_property("pg_restore_drill", json.dumps(evidence, sort_keys=True))


def test_without_the_snapshot_the_restore_catches_a_torn_dump(tmp_path, databases):
    # Negative control: the dump taken outside the snapshot carries the grant
    # written after it; the restore's row-count check refuses the result.
    from kazma_core.documents.backup_pg import restore_pg_document_backup

    tenant, source_root, ids = _prepare(tmp_path, databases)
    report = _run("_backup", databases["source"], str(source_root), str(tmp_path / "backups"),
                  tenant, ids["ready"][0], False)
    assert report["ok"], report  # the files and checksums alone look fine
    with pytest.raises(RuntimeError, match="row counts differ"):
        restore_pg_document_backup(report["path"], target_dsn=databases["target"], storage_root=tmp_path / "target")


def test_a_restore_never_merges_into_a_store_that_has_data(tmp_path, databases):
    from kazma_core.documents.backup_pg import restore_pg_document_backup

    tenant, source_root, ids = _prepare(tmp_path, databases)
    report = _run("_backup", databases["source"], str(source_root), str(tmp_path / "backups"),
                  tenant, ids["ready"][0], True)
    with pytest.raises(RuntimeError, match="already has document tables"):
        restore_pg_document_backup(report["path"], target_dsn=databases["source"], storage_root=tmp_path / "empty")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "something").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not empty"):
        restore_pg_document_backup(report["path"], target_dsn=databases["target"], storage_root=occupied)
