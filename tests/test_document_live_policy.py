"""Saved document policy reaches existing services at safe operation boundaries."""

import asyncio
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents.capacity import CapacityError
from kazma_core.documents.config import get_document_config, get_document_rollout
from kazma_core.documents.errors import DocumentParseError, DocumentPolicyError
from kazma_core.documents.ingestion import DocumentIngestionError, DocumentIngestionService
from kazma_core.documents.malware import MalwareScannerUnavailable, MalwareScanResult
from kazma_core.documents.policy import DocumentPolicy, policy_operation
from kazma_core.documents.service import DocumentService
from kazma_core.documents.worker import StageContext, _classify_failure


def _settings(root, **values):
    values = {"storage_root": str(root), "intake.max_bytes": 16,
              "security.malware_scan": "off", "security.local_salvage": False,
              "capacity.storage_free_floor_bytes": 0, **values}
    get_config_store().batch_set([(f"documents.{key}", value, "documents") for key, value in values.items()])


def _upload(svc, data=b"hello", **options):
    return svc.ingest_stream(io.BytesIO(data), filename="a.txt", tenant_id="t", actor_id="alice",
                             workspace_id="w", **options)


class _Unreadable:
    def read(self, _):
        raise AssertionError("policy refusal must precede the first source read")


def test_running_intake_and_capacity_use_saved_limits(tmp_path):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    try:
        first = _upload(svc)
        assert first.state.value == "ready_to_parse"
        get_config_store().set("documents.intake.max_bytes", 4)
        with pytest.raises(DocumentIngestionError) as error:
            svc.ingest_stream(_Unreadable(), filename="b.txt", tenant_id="t", actor_id="alice",
                              workspace_id="w", content_length=5)
        assert error.value.code == "intake_too_large"
        get_config_store().set("documents.capacity.max_queued_jobs", 1)
        with pytest.raises(CapacityError) as error:
            _upload(svc, b"x")
        assert error.value.code == "queue_full"
        assert svc.capacity_snapshot()["limits"]["max_queued_jobs"] == 1
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_existing_execution_service_obeys_new_limit_and_explicit_config_is_fixed(tmp_path):
    _settings(tmp_path / "docs")
    source = tmp_path / "a.txt"
    source.write_bytes(b"hello")
    live = DocumentService()
    fixed = DocumentService(config=get_document_config())  # old captured behavior as control
    assert (await live.parse_transient(source, approved_path=source)).pages
    get_config_store().set("documents.intake.max_bytes", 4)
    with pytest.raises(DocumentParseError) as error:
        await live.parse_transient(source, approved_path=source)
    assert error.value.code == "document_limit_exceeded"
    assert (await fixed.parse_transient(source, approved_path=source)).pages


def test_parallel_intakes_keep_their_admitted_policy_until_completion(tmp_path):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    entered, release = Event(), Event()

    class Blocked:
        def __init__(self):
            self.sent = False

        def read(self, _):
            if self.sent:
                return b""
            self.sent = True
            entered.set()
            assert release.wait(20)
            return b"hello"

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(svc.ingest_stream, Blocked(), filename="a.txt", tenant_id="t",
                                 actor_id="alice", workspace_id="w", idempotency_key="old")
            try:
                assert entered.wait(5)
                get_config_store().set("documents.intake.max_bytes", 4)
                second = _upload(svc, b"x", idempotency_key="new")
                assert second.state.value == "ready_to_parse"
            finally:
                release.set()
            first = future.result(timeout=30)
        assert first.state.value == "ready_to_parse"
        assert svc.config.intake_max_bytes == 4
        assert svc.admissions.snapshot(tenant_id="t")["window_bytes"] == 6
    finally:
        release.set()
        svc.close()


def test_root_quota_and_worker_objects_are_pinned_with_restart_report(tmp_path):
    _settings(tmp_path / "installed")
    svc = DocumentIngestionService()
    try:
        initial = svc.config
        _settings(tmp_path / "proposed", **{"quotas.tenant_bytes": 12345, "workers.concurrency": 7,
                                          "workers.retry_base_seconds": 17, "limits.max_pages": 6})
        report = svc.readiness()["policy"]
        assert report["mode"] == "live" and report["restart_required"]
        assert report["configured_revision"] != report["effective_revision"]
        assert len(report["effective_revision"]) == 64
        assert report["pending_restart"]["storage_root"] == {
            "configured": str((tmp_path / "proposed").resolve()), "effective": str(initial.storage_root)}
        assert report["pending_restart"]["quota_tenant_bytes"]["effective"] == initial.quota_tenant_bytes
        assert report["pending_restart"]["worker_concurrency"]["configured"] == 7
        assert svc.config.storage_root == svc.storage.root == initial.storage_root
        assert svc.service.config.storage_root == initial.storage_root
        assert svc.config.max_pages == svc.service.config.max_pages == 6
        assert not (tmp_path / "proposed").exists()
    finally:
        svc.close()


def test_waiting_job_obeys_current_malware_policy_before_parsing(tmp_path, monkeypatch):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    try:
        result = _upload(svc)
        get_config_store().set("documents.security.malware_scan", "on")
        monkeypatch.setattr("kazma_core.documents.malware.scan_document_path",
                            lambda path: MalwareScanResult("skipped", None, "test unavailable"))
        monkeypatch.setattr(svc.service, "parse_ingested_blob",
                            lambda *a, **kw: pytest.fail("required scan must precede parser execution"))
        job = svc.jobs.get(tenant_id="t", job_id=result.job_id)
        with pytest.raises(MalwareScannerUnavailable):
            svc._stage_parse(job, StageContext(Event()))
    finally:
        svc.close()


def test_saved_render_and_gc_policy_reaches_constructed_children(tmp_path):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    try:
        get_config_store().batch_set([("documents.limits.max_pages", 3, "documents"),
                                      ("documents.gc.max_deletions_per_run", 2, "documents")])
        assert svc.service.operations.config.max_pages == 3
        report = svc.run_maintenance(dry_run=True)
        assert report["budget"] == 2
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_async_policy_capture_is_off_loop_and_inherits_to_thread(tmp_path, monkeypatch):
    _settings(tmp_path / "docs")
    policy = DocumentPolicy()
    loop_thread = get_ident()
    calls = []
    original = get_config_store().snapshot

    def snapshot(prefix):
        calls.append(get_ident())
        return original(prefix)

    monkeypatch.setattr(get_config_store(), "snapshot", snapshot)

    class Boundary:
        _policy = policy

        @policy_operation
        async def run(self):
            cfg = self._policy.current
            observed = await asyncio.to_thread(lambda: self._policy.current)
            return cfg, observed

    first, threaded = await Boundary().run()
    assert first is threaded
    assert calls and all(thread != loop_thread for thread in calls)
    assert len(calls) == 1


def test_nested_services_share_one_policy_and_other_instances_do_not(tmp_path):
    _settings(tmp_path / "docs")
    live = DocumentService()
    fixed = DocumentService(config=replace(get_document_config(), max_pages=77))
    with live._policy.scope():
        get_config_store().set("documents.limits.max_pages", 9)
        assert live.config.max_pages == live.operations.config.max_pages == 500
        assert fixed.config.max_pages == 77
    assert live.config.max_pages == live.operations.config.max_pages == 9


def test_policy_outage_refuses_execution_and_rollout_never_enables(tmp_path, monkeypatch):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()

    def unavailable(_):
        raise sqlite3.OperationalError("test settings unavailable")

    try:
        monkeypatch.setattr(get_config_store(), "snapshot", unavailable)
        with pytest.raises(DocumentPolicyError) as error:
            svc.ingest_stream(_Unreadable(), filename="b.txt", tenant_id="t", actor_id="alice", workspace_id="w")
        assert _classify_failure(error.value) == ("document_policy_unavailable", str(error.value), True)
        assert get_document_rollout().enabled is False
        assert svc.admissions.snapshot()["pending"] == 0
    finally:
        svc.close()


def test_http_policy_outage_returns_503_before_body_staging(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import documents_api

    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    app = FastAPI()
    app.state.documents = svc
    app.include_router(documents_api.create_documents_router())

    def unavailable(_):
        raise sqlite3.OperationalError("test unavailable")

    async def scope(request):
        return "t", "alice", "w"

    try:
        monkeypatch.setattr(documents_api, "_resolve_scope", scope)
        monkeypatch.setattr(get_config_store(), "snapshot", unavailable)
        monkeypatch.setattr(documents_api.tempfile, "mkstemp",
                            lambda **kw: pytest.fail("policy refusal must precede body staging"))
        with TestClient(app) as client:
            response = client.post("/api/documents", content=b"hello", headers={"X-Document-Filename": "a.txt"})
            readiness = client.get("/api/documents/ops/readiness")
        assert response.status_code == readiness.status_code == 503
        assert response.json()["code"] == "document_policy_unavailable"
        assert response.headers["Retry-After"] == "30"
    finally:
        svc.close()


@pytest.mark.parametrize("key,value", [("intake.max_bytes", -1),
                                       ("security.malware_fail_closed", "perhaps"),
                                       ("indexing.chunk_tokens", 1)])
def test_invalid_saved_policy_refuses_execution_instead_of_using_defaults(tmp_path, key, value):
    _settings(tmp_path / "docs")
    live = DocumentService()
    get_config_store().set(f"documents.{key}", value)
    with pytest.raises(DocumentPolicyError):
        live.inspect_path(tmp_path / "absent.txt")
    assert get_document_rollout().enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("detach", [True, False])
async def test_worker_tasks_do_not_keep_startups_policy(tmp_path, monkeypatch, detach):
    from contextlib import nullcontext

    from tests._document_jobs import wait_for_job

    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    entered, release = Event(), Event()
    original = svc.jobs.claim_next

    def claim(**kwargs):
        entered.set()
        assert release.wait(20)
        return original(**kwargs)

    monkeypatch.setattr(svc.jobs, "claim_next", claim)
    if not detach:
        # Negative control: startup's captured ContextVar otherwise lives in
        # every worker task and makes nested stage snapshots stale forever.
        monkeypatch.setattr(svc._policy, "detached", nullcontext)
    try:
        await svc.start_workers()
        assert await asyncio.to_thread(entered.wait, 5)
        result = await asyncio.to_thread(_upload, svc)
        get_config_store().set("documents.intake.max_bytes", 4)
        release.set()
        expected = "rejected" if detach else "ready"
        status = await wait_for_job(svc, "t", result.job_id, expected)
        assert status["state"] == expected
        version = svc.repository.get_version(tenant_id="t", version_id=result.version_id)
        assert len(version.metadata["intake_policy_revision"]) == 64
        if detach:
            assert status["error_code"] == "document_limit_exceeded"
        else:
            manifest = svc.storage.read_manifest(document_id=result.document_id, version_id=result.version_id)
            assert manifest["policy_revision"] == version.metadata["intake_policy_revision"]
    finally:
        release.set()
        await svc.stop_workers()
        svc.close()



def test_pending_root_change_cannot_redirect_backup_or_export(tmp_path, monkeypatch):
    from kazma_core.documents.backup import perform_document_backup
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.documents.ingestion import peek_ingestion_service, set_ingestion_service
    from kazma_core.migration.bundle import Manifest
    from kazma_core.migration.exporter import _export_document_store

    _settings(tmp_path / "installed")
    svc = DocumentIngestionService()
    previous = peek_ingestion_service()
    set_ingestion_service(svc)
    try:
        _upload(svc)
        get_config_store().set("documents.storage_root", str(tmp_path / "proposed"))
        assert get_document_config().storage_root == (tmp_path / "proposed").resolve()
        assert get_document_storage_root() == svc.storage.root
        backup = perform_document_backup(dest_dir=tmp_path / "backups")
        assert backup["ok"] and "skipped" not in backup
        assert backup["verification"]["ok"] and backup["referenced_blobs"] > 0
        staging = tmp_path / "migration"
        (staging / "data").mkdir(parents=True)
        manifest = Manifest()
        _export_document_store(staging, manifest, lambda *_: None)
        assert (staging / "data" / "documents.db").is_file()
        assert manifest.table_counts["_document_store"]["blobs"] > 0

        def unavailable(_):
            raise sqlite3.OperationalError("test settings unavailable")

        monkeypatch.setattr(get_config_store(), "snapshot", unavailable)
        assert get_document_storage_root() == svc.storage.root
        assert perform_document_backup(dest_dir=tmp_path / "backups")["ok"]
        assert not (tmp_path / "proposed").exists()
    finally:
        set_ingestion_service(previous)
        svc.close()


def test_offline_storage_binding_is_strict_without_creating_a_service(tmp_path, monkeypatch):
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.documents.ingestion import peek_ingestion_service, set_ingestion_service

    previous = peek_ingestion_service()
    set_ingestion_service(None)
    try:
        _settings(tmp_path / "offline")
        assert get_document_storage_root() == (tmp_path / "offline").resolve()
        assert peek_ingestion_service() is None
        assert not (tmp_path / "offline").exists()

        def unavailable(_):
            raise sqlite3.OperationalError("test settings unavailable")

        monkeypatch.setattr(get_config_store(), "snapshot", unavailable)
        with pytest.raises(DocumentPolicyError):
            get_document_storage_root()
        assert peek_ingestion_service() is None
    finally:
        set_ingestion_service(previous)


def test_saved_strict_malware_policy_changes_readiness_to_degraded(tmp_path, monkeypatch):
    _settings(tmp_path / "docs")
    svc = DocumentIngestionService()
    try:
        monkeypatch.setattr("kazma_core.documents.malware.probe_malware_scanner",
                            lambda: {"available": False})
        assert svc.readiness()["status"] == "ready"
        get_config_store().set("documents.security.malware_scan", "on")
        data = svc.readiness()
        assert data["status"] == "degraded"
        assert data["accepting_durable_writes"] is False
        assert "malware_scanner_unavailable" in data["degraded_reasons"]
    finally:
        svc.close()
