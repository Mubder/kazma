"""Restore drill at a representative size: what RTO/RPO a real store gets.

2026-10-13. ``test_document_pg_backup_restore`` proves the coordinated backup
restores a WORKING store, on a handful of documents; its timings say nothing
about a store an operator would run. This drill fills a private source
database through the real intake with N documents (``KAZMA_TEST_RESTORE_DRILL_DOCUMENTS``,
each ``KAZMA_TEST_RESTORE_DRILL_KIB`` of text): a few processed and readable,
the rest a queued backlog, as a store at backup time has. It then measures
the coordinated backup, its verification, the restore into an empty
database and storage root, the service start that recovers it, the first
document readable, and how fast the restored workers drain the backlog.

``KAZMA_TEST_RESTORE_DRILL_WRITERS`` processes fill it at once (default 1; the
intake is serial per process, so 10,000 documents need several).

It is opt-in (the count is required) and runs only on a real Postgres with
pg_dump/pg_restore; a skip is not evidence. The numbers are this machine's,
recorded in ``KAZMA_DRILL_REPORT`` when set -- never asserted as targets.
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

from tests.test_document_pg_backup_restore import _bootstrap, databases  # noqa: F401 - fixture

pytestmark = pytest.mark.postgres

_PROCESSED = 5
#: An operator holding a backlog this size raises the per-tenant caps; the
#: defaults (500 queued, 120 intakes a minute) refuse the drill's own fill.
_CAPACITY = {
    "capacity_max_queued_jobs": 100_000,
    "capacity_max_tenant_queued_jobs": 100_000,
    "capacity_max_tenant_active_jobs": 100_000,
    "capacity_intake_rate_per_minute": 100_000,
    "capacity_intake_bytes_per_minute": 0,
}


def _service(root: str):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=Path(root), security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False, **_CAPACITY,
    ))
    assert getattr(svc.repository, "backend_name", None) == "postgres"
    return svc


async def _work(svc, tenant: str, owner: str, limit: int, *, until_ready: bool) -> int:
    """Run up to ``limit`` claimable jobs (or until ``limit`` are ready)."""
    from kazma_core.documents.ingestion import SYSTEM_ACTOR
    from kazma_core.documents.worker import DocumentWorker

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    done = 0
    while done < limit:
        claimed = svc.jobs.claim_next(owner=owner, lease_seconds=120, tenant_id=tenant)
        if claimed is None:
            break
        await worker._process_claimed(claimed, owner)
        if not until_ready:
            done += 1
        elif svc.job_status(tenant_id=tenant, job_id=claimed.id, actor_id=SYSTEM_ACTOR)["state"] == "ready":
            done += 1
    return done


def _intake(dsn: str, root: str, tenant: str, first: int, count: int, kib: int) -> list[str]:
    """One writer: documents ``first`` .. ``first + count - 1`` through the real intake."""
    _bootstrap(dsn)
    svc = _service(root)
    try:
        line = "Quarterly inspection record for the northern plant, item {i}, batch {b}.\n"
        ids = []
        for i in range(first, first + count):
            body = "".join(line.format(i=i, b=b) for b in range(kib * 1024 // 60 + 1)).encode()[: kib * 1024]
            result = svc.ingest_stream(io.BytesIO(body), filename=f"record-{i}.txt", tenant_id=tenant,
                                       actor_id="alice", workspace_id="w")
            ids.append(str(result.document_id))
        return ids
    finally:
        svc.close()


def _process_some(dsn: str, root: str, tenant: str) -> dict:
    _bootstrap(dsn)
    svc = _service(root)
    try:
        ready = asyncio.run(_work(svc, tenant, "drill", _PROCESSED, until_ready=True))
        listed = svc.list_documents(tenant_id=tenant, actor_id="alice")
        processed = [d["document_id"] for d in listed if d.get("state") == "ready"]
        return {"ready": ready, "processed": processed}
    finally:
        svc.close()


def _populate(dsn: str, root: str, tenant: str, count: int, kib: int, writers: int) -> dict:
    """Fill the source with ``writers`` processes at once (an install with several
    uploaders), then process a few so the store holds ready and queued work."""
    started = time.monotonic()
    share = -(-count // writers)
    slices = [(first, min(share, count - first)) for first in range(0, count, share)]
    ids = [doc for part in _run_many("_intake", [(dsn, root, tenant, first, size, kib)
                                                  for first, size in slices]) for doc in part]
    intake_seconds = time.monotonic() - started
    done = _run("_process_some", dsn, root, tenant)
    return {"documents": ids, "intake_seconds": round(intake_seconds, 3), "ready": done["ready"],
            "processed": done["processed"] or ids[:1]}


def _backup(dsn: str, root: str, dest: str) -> dict:
    _bootstrap(dsn)
    from kazma_core.documents.backup_pg import perform_pg_document_backup, verify_pg_document_backup

    report = perform_pg_document_backup(dsn=dsn, storage_root=root, dest_dir=dest)
    started = time.monotonic()
    verified = verify_pg_document_backup(report["path"])
    report["verify_seconds"] = round(time.monotonic() - started, 3)
    report["verified"] = verified["ok"]
    report.pop("checksums", None)
    report["backup_bytes"] = sum(p.stat().st_size for p in Path(report["path"]).rglob("*") if p.is_file())
    return report


def _resume(dsn: str, root: str, tenant: str, readable_doc: str, sample: int) -> dict:
    _bootstrap(dsn)
    started = time.monotonic()
    svc = _service(root)  # startup recovery runs here
    start_seconds = time.monotonic() - started
    try:
        text = svc.get_content(tenant_id=tenant, document_id=readable_doc, actor_id="alice", fence=False)["text"]
        first_readable = time.monotonic() - started
        svc.jobs.recover_expired_leases(tenant_id=tenant)
        drained_from = time.monotonic()
        drained = asyncio.run(_work(svc, tenant, "restored", sample, until_ready=False))
        return {
            "service_start_seconds": round(start_seconds, 3),
            "first_readable_seconds": round(first_readable, 3),
            "readable_text_ok": "Quarterly inspection record" in text,
            "drained": drained,
            "drain_seconds": round(time.monotonic() - drained_from, 3),
            "documents_listed": len(svc.list_documents(tenant_id=tenant, actor_id="alice")),
            "admission": svc.admissions.snapshot(tenant_id=tenant),
        }
    finally:
        svc.close()


def _child(name: str, args: tuple, queue) -> None:
    try:
        queue.put(("ok", globals()[name](*args)))
    except BaseException:  # noqa: BLE001 - reported to the parent
        queue.put(("error", traceback.format_exc()))


def _run(name: str, *args, timeout: float = 3600.0):
    return _run_many(name, [args], timeout=timeout)[0]


def _run_many(name: str, arg_sets: list[tuple], timeout: float = 7200.0) -> list:
    """Run ``name`` once per argument tuple, each in its own process, all at once."""
    ctx = multiprocessing.get_context("spawn")
    queues = [ctx.Queue() for _ in arg_sets]
    children = [ctx.Process(target=_child, args=(name, args, queue)) for args, queue in zip(arg_sets, queues)]
    for child in children:
        child.start()
    deadline = time.monotonic() + timeout
    results = []
    try:
        for queue in queues:  # read before join: a large result fills the pipe
            status, value = queue.get(timeout=max(1.0, deadline - time.monotonic()))
            assert status == "ok", value
            results.append(value)
    finally:
        for child in children:
            child.join(timeout=60)
            if child.is_alive():
                child.kill()
                child.join(10)
            child.close()
    return results


@pytest.fixture
def scale():
    raw = os.environ.get("KAZMA_TEST_RESTORE_DRILL_DOCUMENTS", "")
    if not raw:
        pytest.skip("opt-in: set KAZMA_TEST_RESTORE_DRILL_DOCUMENTS (and optionally _KIB)")
    return (int(raw), int(os.environ.get("KAZMA_TEST_RESTORE_DRILL_KIB", "64")),
            max(1, int(os.environ.get("KAZMA_TEST_RESTORE_DRILL_WRITERS", "1"))))


def test_restore_drill_at_scale(tmp_path, databases, scale):  # noqa: F811 - the imported fixture
    from kazma_core.documents.backup_pg import restore_pg_document_backup

    count, kib, writers = scale
    tenant = f"scale-{uuid.uuid4().hex[:8]}"
    source_root, target_root = tmp_path / "source", tmp_path / "target"
    populated = _populate(databases["source"], str(source_root), tenant, count, kib, writers)
    assert len(set(populated["documents"])) == count
    assert populated["ready"] >= 1
    backup = _run("_backup", databases["source"], str(source_root), str(tmp_path / "backups"))
    assert backup["ok"] and backup["verified"], backup
    started = time.monotonic()
    restored = restore_pg_document_backup(backup["path"], target_dsn=databases["target"], storage_root=target_root)
    restore_seconds = time.monotonic() - started
    outcome = _run("_resume", databases["target"], str(target_root), tenant,
                   populated["processed"][0], min(20, count))
    rto = time.monotonic() - started

    assert outcome["readable_text_ok"]
    assert outcome["documents_listed"] == count
    assert outcome["admission"]["pending"] == 0 and outcome["admission"]["stranded"] == 0
    evidence = {
        "capacity_settings": _CAPACITY,
        "documents": count,
        "kib_per_document": kib,
        "intake_writers": writers,
        "content_bytes": count * kib * 1024,
        "processed_before_backup": populated["ready"],
        "intake_seconds": populated["intake_seconds"],
        "backup_seconds": backup["duration_seconds"],
        "backup_bytes": backup["backup_bytes"],
        "verify_seconds": backup["verify_seconds"],
        "restore_seconds": round(restore_seconds, 3),
        "restore_timings": restored["timings"],
        "reindex_required": len(restored["reindex_required"]),
        "service_start_seconds": outcome["service_start_seconds"],
        "first_readable_seconds_after_start": outcome["first_readable_seconds"],
        "rto_seconds_to_first_readable": round(restore_seconds + outcome["first_readable_seconds"], 3),
        "drain": {"jobs": outcome["drained"], "seconds": outcome["drain_seconds"]},
        "rto_seconds_total_measured": round(rto, 3),
        "table_counts": restored["table_counts"],
        "snapshot_at": backup["snapshot_at"],
    }
    report = os.environ.get("KAZMA_DRILL_REPORT")
    if report:
        Path(report).write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
