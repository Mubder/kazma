"""A Web upload is admitted before its body is received; scratch is bounded.

2026-10-13 (F08). The upload route received the whole body (up to the intake
limit) into the system temp directory before any budget was asked: a full
queue, a spent rate window, a disabled platform or a full disk answered only
after the bytes had arrived, any number of uploads could spool at once, and
the spool sat where the free-space floor never looked and nothing swept it.

The route is driven at the ASGI level so the test can count whether the
body was read at all. Negative control: the same request with the early
answer removed reads the body before refusing.
"""

from __future__ import annotations

import asyncio
import io
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from kazma_core.documents.capacity import CapacityError
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState
from kazma_core.documents.worker import DocumentWorker


def _service(root, **changes) -> DocumentIngestionService:
    settings = dict(storage_root=root, security_malware_scan="off", security_local_salvage=False, ocr_enabled=False)
    settings.update(changes)
    return DocumentIngestionService(config=DocumentConfig(**settings))


def _app(svc, monkeypatch) -> FastAPI:
    from kazma_ui import documents_api

    async def scope(request):
        return "t", "alice", "w"

    monkeypatch.setattr(documents_api, "_resolve_scope", scope)
    app = FastAPI()
    app.state.documents = svc
    app.include_router(documents_api.create_documents_router())
    return app


async def _post(app, body: bytes, *, declared: int | None = None) -> tuple[int, dict, int]:
    """POST the body; return (status, json, how many body messages were read)."""
    import json

    reads = {"n": 0}
    pieces = [body[i:i + 4096] for i in range(0, len(body), 4096)] or [b""]

    async def receive():
        if reads["n"] < len(pieces):
            index = reads["n"]
            reads["n"] += 1
            return {"type": "http.request", "body": pieces[index], "more_body": index + 1 < len(pieces)}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    headers = [(b"x-document-filename", b"note.txt"), (b"host", b"test")]
    if declared is not None:
        headers.append((b"content-length", str(declared).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/documents", "raw_path": b"/api/documents", "query_string": b"",
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": "",
        "app": app,
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=60)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload or b"{}"), reads["n"]


def _fill_queue(svc) -> None:
    svc.ingest_stream(io.BytesIO(b"queued\n"), filename="q.txt", tenant_id="t", actor_id="alice", workspace_id="w")


@pytest.mark.asyncio
async def test_a_full_queue_answers_before_the_body_is_read(tmp_path, monkeypatch):
    svc = _service(tmp_path, capacity_max_queued_jobs=1)
    try:
        _fill_queue(svc)
        status, payload, reads = await _post(_app(svc, monkeypatch), b"x" * 20000, declared=20000)
        assert status == 503 and payload["code"] == "queue_full"
        assert reads == 0
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_without_the_early_answer_the_body_is_read_first(tmp_path, monkeypatch):
    # Negative control: the instrument sees a body that IS read.
    svc = _service(tmp_path, capacity_max_queued_jobs=1)
    try:
        _fill_queue(svc)
        monkeypatch.setattr(svc, "precheck_upload", lambda **kwargs: None)
        status, payload, reads = await _post(_app(svc, monkeypatch), b"x" * 20000, declared=20000)
        assert status == 503 and payload["code"] == "queue_full"
        assert reads == 5
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_declared_length_past_the_limit_is_refused_unread(tmp_path, monkeypatch):
    svc = _service(tmp_path, intake_max_bytes=1000)
    try:
        status, payload, reads = await _post(_app(svc, monkeypatch), b"x" * 5000, declared=5000)
        assert status == 413 and payload["code"] == "intake_too_large"
        assert reads == 0
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_body_longer_than_it_declared_is_refused(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        status, payload, _reads = await _post(_app(svc, monkeypatch), b"x" * 9000, declared=100)
        assert status == 413 and payload["code"] == "intake_too_large"
        assert svc.repository.list_documents(tenant_id="t") == []
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_disk_below_its_floor_answers_before_the_body(tmp_path, monkeypatch):
    svc = _service(tmp_path, capacity_storage_free_floor_bytes=1000)
    try:
        monkeypatch.setattr(svc.capacity, "_free_bytes", lambda: 1500)
        status, payload, reads = await _post(_app(svc, monkeypatch), b"x" * 900, declared=900)
        # 1500 free - 900 incoming < 1000 floor: the file itself counts.
        assert status == 507 and payload["code"] == "storage_full"
        assert reads == 0
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_concurrent_uploads_beyond_the_spool_slots_are_refused(tmp_path, monkeypatch):
    from kazma_ui import documents_api

    svc = _service(tmp_path, capacity_max_concurrent_uploads=2)
    taken = 0
    try:
        for _ in range(2):
            assert documents_api._UPLOAD_SLOTS.acquire(2)
            taken += 1
        status, payload, reads = await _post(_app(svc, monkeypatch), b"x" * 100, declared=100)
        assert status == 503 and payload["code"] == "uploads_busy"
        assert reads == 0
    finally:
        for _ in range(taken):
            documents_api._UPLOAD_SLOTS.release()
        svc.close()
    assert documents_api._UPLOAD_SLOTS.held == 0


@pytest.mark.asyncio
async def test_an_accepted_upload_spools_under_the_storage_root_and_cleans_up(tmp_path, monkeypatch):
    from kazma_ui import documents_api

    svc = _service(tmp_path)
    seen: list[str] = []
    real = svc.ingest_stream

    def spy(source, **kwargs):
        seen.append(os.path.dirname(source.name))
        return real(source, **kwargs)

    monkeypatch.setattr(svc, "ingest_stream", spy)
    try:
        status, payload, _ = await _post(_app(svc, monkeypatch), b"accepted body\n" * 200, declared=2800)
        assert status == 200 and payload["ok"], payload
        assert seen == [str(tmp_path / ".upload-staging")]
        assert list((tmp_path / ".upload-staging").iterdir()) == []
        assert documents_api._UPLOAD_SLOTS.held == 0
    finally:
        svc.close()


def test_garbage_collection_sweeps_stale_scratch_and_keeps_fresh(tmp_path):
    svc = _service(tmp_path)
    try:
        old = time.time() - 7 * 24 * 3600
        stale = {
            "upload": tmp_path / ".upload-staging" / "doc-upload-crashed",
            "stage": tmp_path / ".stage" / "doc-stage-crashed",
            "parse": tmp_path / "parser-runs" / "parse-crashed",
            "op": tmp_path / "operation-runs" / "convert-crashed",
        }
        stale["upload"].parent.mkdir(parents=True)
        stale["upload"].write_bytes(b"partial")
        for key in ("stage", "parse", "op"):
            stale[key].mkdir(parents=True)
            (stale[key] / "inner.bin").write_bytes(b"x")
            os.utime(stale[key] / "inner.bin", (old, old))
        for path in stale.values():
            os.utime(path, (old, old))
        fresh = tmp_path / "parser-runs" / "parse-running"
        fresh.mkdir()
        (fresh / "out.json").write_bytes(b"{}")
        dry = svc.run_maintenance(dry_run=True)
        assert dry["deleted_staging"] >= 4 and all(path.exists() for path in stale.values())
        report = svc.run_maintenance(dry_run=False)
        assert all(not path.exists() for path in stale.values()), report
        assert fresh.exists()
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_stage_never_starts_into_a_disk_below_its_floor(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        result = svc.ingest_stream(io.BytesIO(b"floor\n"), filename="a.txt", tenant_id="t",
                                   actor_id="alice", workspace_id="w")
        monkeypatch.setattr(svc.capacity, "below_floor", lambda extra_bytes=0: True)
        claimed = svc.jobs.claim_next(owner="w", lease_seconds=60)
        await DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "w")
        job = svc.jobs.get(tenant_id="t", job_id=result.job_id)
        assert job.state is DocumentJobState.RETRY_WAIT and job.error_code == "storage_full"
        monkeypatch.setattr(svc.capacity, "below_floor", lambda extra_bytes=0: False)
        later = datetime.now(UTC) + timedelta(hours=1)
        monkeypatch.setattr(svc.jobs, "_clock", lambda: later)
        claimed = svc.jobs.claim_next(owner="w", lease_seconds=60)
        await DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "w")
        assert svc.jobs.get(tenant_id="t", job_id=result.job_id).state is DocumentJobState.READY
    finally:
        svc.close()


def test_intake_floor_counts_the_incoming_file(tmp_path, monkeypatch):
    svc = _service(tmp_path, capacity_storage_free_floor_bytes=1000)
    try:
        monkeypatch.setattr(svc.capacity, "_free_bytes", lambda: 1500)
        svc.capacity.check_intake(tenant_id="t", byte_size=400)  # 1100 left: fine
        with pytest.raises(CapacityError) as error:
            svc.capacity.check_intake(tenant_id="t", byte_size=600)  # 900 left
        assert error.value.code == "storage_full"
    finally:
        svc.close()
