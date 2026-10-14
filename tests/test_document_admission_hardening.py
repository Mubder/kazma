"""Intake must reserve durable budgets before concurrent callers read bytes."""

import io
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from threading import Event

import pytest
from kazma_core.documents.admission import IntakeAdmissionStore
from kazma_core.documents.capacity import CapacityError
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionError, DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobRepository
from kazma_core.documents.models import new_job_id
from kazma_core.documents.repository import DocumentRepository


def _config(root, **changes):
    return DocumentConfig(storage_root=root, intake_max_bytes=16, security_malware_scan="off", **changes)


def _upload(svc, content=b"hello", **changes):
    args = dict(filename="a.txt", tenant_id="t", actor_id="alice", workspace_id="w", idempotency_key="key")
    args.update(changes)
    return svc.ingest_stream(io.BytesIO(content), **args)


class _Unreadable:
    def read(self, _):
        raise AssertionError("refused intake must not read bytes")


def _reserve_process(db, root, barrier, results, limits):
    """Independent processes and SQLite connections, no shared Python lock."""
    repo = DocumentRepository(db)
    try:
        admissions = IntakeAdmissionStore(DocumentJobRepository(repo))
        barrier.wait(timeout=20)
        try:
            token = admissions.reserve(
                tenant_id="t",
                request_key=str(new_job_id()),
                request_digest="digest",
                job_id=str(new_job_id()),
                byte_size=16,
                config=_config(root, **limits),
            )
            results.put(("accepted", token))
            results.close()
            results.join_thread()
            os._exit(17)  # no Python cleanup, close(), or reservation release
        except CapacityError as exc:
            results.put(("refused", exc.code))
    finally:
        repo.close()


@pytest.mark.parametrize(
    "limits, code",
    [
        ({"capacity_max_queued_jobs": 1}, "queue_full"),
        ({"capacity_max_tenant_queued_jobs": 1}, "tenant_queue_full"),
        ({"capacity_max_tenant_active_jobs": 1}, "tenant_active_full"),
        ({"capacity_intake_bytes_per_minute": 16}, "rate_limited"),
        ({"capacity_intake_rate_per_minute": 1}, "rate_limited"),
    ],
)
def test_two_processes_cannot_both_admit_last_queue_slot(tmp_path, limits, code):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    children = [
        ctx.Process(target=_reserve_process, args=(tmp_path / "documents.db", tmp_path, barrier, results, limits))
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        received = [results.get(timeout=30) for _ in children]
        for child in children:
            child.join(timeout=10)
        assert sorted(child.exitcode for child in children) == [0, 17]
        assert sorted(item[0] for item in received) == ["accepted", "refused"]
        assert next(value for status, value in received if status == "refused") == code
        # The winner exited without releasing: age or process exit does not
        # authorize a replacement publisher. A reopened store keeps its slot.
        with closing(DocumentRepository(tmp_path / "documents.db")) as repo:
            jobs = DocumentJobRepository(repo)
            admissions = IntakeAdmissionStore(jobs)
            assert admissions.snapshot()["pending"] == 1
            now = jobs._clock()
            jobs._clock = lambda: now + timedelta(days=10)
            with pytest.raises(CapacityError, match="capacity"):
                admissions.reserve(
                    tenant_id="other",
                    request_key="another",
                    request_digest="digest",
                    job_id=str(new_job_id()),
                    byte_size=16,
                    config=_config(tmp_path, capacity_max_queued_jobs=1),
                )
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
        results.close()


def test_inflight_same_key_is_bound_before_reading_second_source(tmp_path):
    svc = DocumentIngestionService(config=_config(tmp_path))
    entered, release = Event(), Event()

    class Slow(io.BytesIO):
        def read(self, size):
            entered.set()
            assert release.wait(timeout=20)
            return super().read(size)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                svc.ingest_stream,
                Slow(b"hello"),
                filename="a.txt",
                tenant_id="t",
                actor_id="alice",
                workspace_id="w",
                idempotency_key="key",
            )
            try:
                assert entered.wait(timeout=10)
                for actor, code in [("alice", "intake_in_progress"), ("bob", "idempotency_conflict")]:
                    with pytest.raises(CapacityError) as error:
                        svc.ingest_stream(
                            _Unreadable(),
                            filename="a.txt",
                            tenant_id="t",
                            actor_id=actor,
                            workspace_id="w",
                            idempotency_key="key",
                        )
                    assert error.value.status == 409 and error.value.code == code
                assert svc.admissions.snapshot()["pending"] == 1
            finally:
                release.set()
            result = first.result(timeout=30)
        assert _upload(svc).job_id == result.job_id
        assert svc.admissions.snapshot(tenant_id="t") == {
            "pending": 0,
            "reserved_bytes": 0,
            "window_files": 1,
            "window_bytes": 5,
            "stranded": 0,
        }
    finally:
        release.set()
        svc.close()


@pytest.mark.parametrize("declared", [0, 1, 6])
def test_spoofed_length_never_publishes_cas_or_metadata(tmp_path, declared):
    svc = DocumentIngestionService(config=_config(tmp_path))
    try:
        with pytest.raises(DocumentIngestionError) as error:
            _upload(svc, content_length=declared)
        assert error.value.code == "intake_length_mismatch"
        assert svc.admissions.snapshot()["pending"] == 0
        assert not svc.repository.list_documents(tenant_id="t")
        assert not list(tmp_path.glob("quarantine/sha256/*/*/*"))
        assert not list(tmp_path.glob("quarantine/sha256/.incoming-*"))
    finally:
        svc.close()


def test_replay_at_full_queue_and_rate_limit_uses_no_new_credit(tmp_path):
    svc = DocumentIngestionService(
        config=_config(tmp_path, capacity_max_queued_jobs=1, capacity_intake_rate_per_minute=1)
    )
    try:
        first = _upload(svc)
        replay = _upload(svc)
        assert replay.reused and replay.job_id == first.job_id
        assert svc.admissions.snapshot(tenant_id="t")["window_files"] == 1
        with pytest.raises(CapacityError) as error:
            _upload(svc, idempotency_key="second")
        assert error.value.code == "queue_full"
    finally:
        svc.close()


def test_actual_bytes_and_rate_window_survive_service_restart(tmp_path):
    config = _config(tmp_path, capacity_intake_bytes_per_minute=20)
    svc = DocumentIngestionService(config=config)
    try:
        _upload(svc, content_length=5)
    finally:
        svc.close()
    svc = DocumentIngestionService(config=config)
    try:
        assert svc.capacity_snapshot(tenant_id="t")["tenant"]["window_bytes"] == 5
        with pytest.raises(CapacityError) as error:
            _upload(svc, idempotency_key="other")  # unknown length reserves 16
        assert error.value.code == "rate_limited"
        _upload(svc, idempotency_key="other", content_length=5)
        assert svc.admissions.snapshot(tenant_id="t")["window_bytes"] == 10
    finally:
        svc.close()


def test_write_acl_denial_precedes_reservation_and_bytes(tmp_path):
    svc = DocumentIngestionService(config=_config(tmp_path))
    try:
        first = _upload(svc)
        svc.repository.grant_access(
            tenant_id="t", document_id=first.document_id, actor_id="alice", principal_id="bob", permission="read"
        )
        before = svc.admissions.snapshot(tenant_id="t")
        with pytest.raises(DocumentIngestionError) as error:
            svc.ingest_stream(
                _Unreadable(),
                filename="a.txt",
                tenant_id="t",
                actor_id="bob",
                workspace_id="w",
                document_id=first.document_id,
            )
        assert error.value.code == "document_access_denied"
        assert svc.admissions.snapshot(tenant_id="t") == before
    finally:
        svc.close()


def test_a_partial_publication_is_rolled_back_by_its_owner(tmp_path, monkeypatch):
    # 2026-10-13: a partial publication (document made, version failed) used
    # to stay held after restart -- the reservation charged forever and the
    # empty document left behind. The failing intake now undoes its own rows.
    config = _config(tmp_path)
    svc = DocumentIngestionService(config=config)

    def interrupted(**kwargs):
        raise OSError("inert failure after document creation")

    monkeypatch.setattr(svc.repository, "create_version", interrupted)
    try:
        with pytest.raises(OSError):
            _upload(svc)
        assert svc.admissions.snapshot()["pending"] == 0
        assert svc.repository.list_documents(tenant_id="t") == []
    finally:
        svc.close()
    svc = DocumentIngestionService(config=config)
    try:
        assert svc.capacity_snapshot()["admission"]["pending"] == 0
        assert _upload(svc, b"different").job_id
        assert len(svc.repository.list_documents(tenant_id="t")) == 1
    finally:
        svc.close()


def test_a_failed_rollback_keeps_the_reservation_held(tmp_path, monkeypatch):
    # Negative control: when the rollback itself fails, nothing is released
    # early -- the reservation stays held and is never taken over while its
    # owner (this process) lives.
    config = _config(tmp_path)
    svc = DocumentIngestionService(config=config)

    def interrupted(**kwargs):
        raise OSError("inert failure after document creation")

    def no_rollback(**kwargs):
        raise OSError("metadata store unavailable during rollback")

    monkeypatch.setattr(svc.repository, "create_version", interrupted)
    monkeypatch.setattr(svc.repository, "discard_unpublished_intake", no_rollback)
    try:
        with pytest.raises(OSError, match="after document creation"):
            _upload(svc)
        assert svc.admissions.snapshot()["pending"] == 1
        assert len(svc.repository.list_documents(tenant_id="t")) == 1
        assert svc._recover_stranded_intakes() == 0  # its owner is alive
        with pytest.raises(CapacityError) as error:
            _upload(svc, b"different")
        assert error.value.code == "intake_in_progress"
    finally:
        svc.close()


def test_job_publication_proves_stranded_reservation_can_be_reconciled(tmp_path, monkeypatch):
    config = _config(tmp_path)
    svc = DocumentIngestionService(config=config)
    monkeypatch.setattr(svc.admissions, "finish", lambda token: None)
    try:
        first = _upload(svc)
        assert svc.admissions.snapshot()["pending"] == 1
    finally:
        svc.close()
    svc = DocumentIngestionService(config=config)
    try:
        assert svc.admissions.snapshot()["pending"] == 0
        assert _upload(svc).job_id == first.job_id
        assert svc.admissions.snapshot(tenant_id="t")["window_files"] == 1
    finally:
        svc.close()


def test_unavailable_reservation_database_refuses_before_source_read(tmp_path, monkeypatch):
    svc = DocumentIngestionService(config=_config(tmp_path))
    original = svc.admissions._execute

    def failed(cur, sql, params=()):
        if "SELECT request_digest" in sql:
            raise OSError("inert admission outage")
        return original(cur, sql, params)

    monkeypatch.setattr(svc.admissions, "_execute", failed)
    try:
        with pytest.raises(CapacityError) as error:
            svc.ingest_stream(_Unreadable(), filename="a.txt", tenant_id="t", actor_id="alice", workspace_id="w")
        assert error.value.code == "capacity_unavailable"
        assert svc.admissions.snapshot()["pending"] == 0
    finally:
        svc.close()


def test_per_tenant_pending_capacity_leaves_other_tenant_headroom(tmp_path):
    svc = DocumentIngestionService(config=_config(tmp_path, capacity_max_tenant_queued_jobs=1))

    def reserve(tenant, key):
        return svc.admissions.reserve(
            tenant_id=tenant,
            request_key=key,
            request_digest="d",
            job_id=str(new_job_id()),
            byte_size=16,
            config=svc.config,
        )

    try:
        reserve("a", "one")
        with pytest.raises(CapacityError) as error:
            reserve("a", "two")
        assert error.value.code == "tenant_queue_full"
        reserve("b", "one")
        assert svc.admissions.snapshot()["pending"] == 2
    finally:
        svc.close()


def test_postgres_sql_adapter_exercises_reservation_lifecycle(tmp_path, monkeypatch):
    # This adapter proves the query/control paths, not Postgres locking. The
    # marked live-PG test runs the race on separate real pool connections.
    from tests.test_document_operations_phase9 import _pg_repo

    repo, pool = _pg_repo()
    store = IntakeAdmissionStore(repo)
    now = repo._clock().timestamp()
    monkeypatch.setattr(store, "_epoch", lambda cur: now)
    cfg = _config(tmp_path, capacity_intake_rate_per_minute=1)
    identifier = new_job_id()
    token = store.reserve(
        tenant_id="t", request_key="key", request_digest="digest", job_id=str(identifier), byte_size=16, config=cfg
    )
    store.mark_publishing(token, byte_size=5, source_sha256="a" * 64, document_id="d",
                          version_id="v", created_document=True)
    store.finish(token)
    assert store.snapshot(tenant_id="t")["pending"] == 1  # no job proof
    with pytest.raises(CapacityError) as error:
        store.reserve(
            tenant_id="t",
            request_key="key",
            request_digest="digest",
            job_id=str(new_job_id()),
            byte_size=16,
            config=cfg,
        )
    assert error.value.code == "intake_in_progress"
    assert any("document_intake_mutex" in sql and "FOR UPDATE" in sql for sql in pool.sql_log)
    from kazma_core.documents.models import new_document_id, new_version_id

    published = repo.enqueue(
        tenant_id="t",
        workspace_id="w",
        document_id=new_document_id(),
        version_id=new_version_id(),
        idempotency_key="key",
        job_id=identifier,
        request_digest="digest",
    )
    assert repo.get_by_idempotency_key(tenant_id="t", idempotency_key="key").id == published.id
    store.finish(token)
    assert store.snapshot(tenant_id="t")["pending"] == 0


@pytest.mark.parametrize("key", ["", " ", "x" * 257, 123])
def test_invalid_request_key_refuses_before_admission(tmp_path, key):
    svc = DocumentIngestionService(config=_config(tmp_path))
    try:
        with pytest.raises(DocumentIngestionError) as error:
            svc.ingest_stream(
                _Unreadable(), filename="a.txt", tenant_id="t", actor_id="alice", workspace_id="w", idempotency_key=key
            )
        assert error.value.code == "invalid_request"
        assert svc.admissions.snapshot()["pending"] == 0
    finally:
        svc.close()


def test_retry_cannot_bypass_queue_capacity_or_write_acl(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.documents.repository import DocumentAccessError
    from kazma_ui import documents_api

    svc = DocumentIngestionService(config=_config(tmp_path, capacity_max_queued_jobs=1))
    try:
        rejected = _upload(svc, b"bad", filename="unknown.zzz")
        assert rejected.state.value == "rejected"
        svc.repository.grant_access(
            tenant_id="t", document_id=rejected.document_id, actor_id="alice", principal_id="bob", permission="read"
        )
        before = svc.admissions.snapshot(tenant_id="t")
        with pytest.raises(DocumentAccessError):
            svc.retry_job(tenant_id="t", job_id=rejected.job_id, actor_id="bob")
        assert svc.admissions.snapshot(tenant_id="t") == before
        _upload(svc, idempotency_key="valid")
        with pytest.raises(CapacityError) as error:
            svc.retry_job(tenant_id="t", job_id=rejected.job_id, actor_id="alice")
        assert error.value.code == "queue_full"

        async def scope(request):
            return "t", "alice", "w"

        monkeypatch.setattr(documents_api, "_resolve_scope", scope)
        app = FastAPI()
        app.state.documents = svc
        app.include_router(documents_api.create_documents_router())
        with TestClient(app) as client:
            response = client.post(f"/api/documents/jobs/{rejected.job_id}/retry")
        assert response.status_code == 503
        assert response.json()["code"] == "queue_full"
        assert int(response.headers["Retry-After"]) > 0
        svc.repository.tombstone_document(
            tenant_id="t", document_id=rejected.document_id, actor_id="alice", reason="test deleted retry"
        )
        # A deleted document's job reads as absent (F05: unreadable == unknown).
        from kazma_core.documents.jobs import JobNotFoundError

        with pytest.raises(JobNotFoundError):
            svc.retry_job(tenant_id="t", job_id=rejected.job_id, actor_id="alice")
    finally:
        svc.close()


def test_delimiter_ambiguity_cannot_alias_two_tenants(tmp_path):
    import uuid

    from kazma_core.documents.ingestion import _INGEST_NS

    # Negative control: the old concatenation assigned one global UUID to
    # two distinct tenant/key pairs, despite tenant-scoped admission rows.
    left, right = ("t", "key:other"), ("t:key", "other")

    def old_identifier(pair):
        return uuid.uuid5(_INGEST_NS, f"{pair[0]}:{pair[1]}:job")

    assert left != right and old_identifier(left) == old_identifier(right)
    svc = DocumentIngestionService(config=_config(tmp_path))
    try:
        first = _upload(svc, tenant_id="t", idempotency_key="key:other")
        second = _upload(svc, tenant_id="t:key", idempotency_key="other")
        assert first.job_id != second.job_id and first.document_id != second.document_id
        assert _upload(svc, tenant_id="t", idempotency_key="key:other").job_id == first.job_id
        assert _upload(svc, tenant_id="t:key", idempotency_key="other").job_id == second.job_id
    finally:
        svc.close()


def test_replay_looks_up_stored_key_and_preserves_legacy_job_id(tmp_path):
    import uuid

    from kazma_core.documents.ingestion import _INGEST_NS
    from kazma_core.documents.models import JobId

    svc = DocumentIngestionService(config=_config(tmp_path))
    try:
        first = _upload(svc)
        original = svc.jobs.get(tenant_id="t", job_id=first.job_id)
        legacy_id = JobId(uuid.uuid5(_INGEST_NS, "t:legacy:job"))
        svc.jobs.enqueue(
            tenant_id="t",
            workspace_id="w",
            document_id=first.document_id,
            version_id=first.version_id,
            idempotency_key="legacy",
            job_id=legacy_id,
            actor_id="alice",
            request_digest=original.request_digest,
            max_attempts=original.max_attempts,
            force_ocr=original.force_ocr,
        )
        before = svc.admissions.snapshot(tenant_id="t")
        replay = _upload(svc, idempotency_key="legacy")
        assert replay.reused and replay.job_id == legacy_id
        assert svc.admissions.snapshot(tenant_id="t") == before
    finally:
        svc.close()
