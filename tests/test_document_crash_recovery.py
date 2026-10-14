"""A process killed at any intake or job stage leaves nothing half-published.

2026-10-13 (F05/F08/Q01): a reservation stranded by a dead publisher used to
stay charged forever, and the rows it had published before dying stayed
behind (an empty document, a version with no job). Each case below kills a
real child process (``os._exit``: no ``finally``, no close) straight after
one stage of a real intake or a real job stage, then starts a fresh service
the way a restart does and checks what it finds: the reservation released,
the rows the dead intake made removed, and a retry that works. The
recovery's negative controls are an owner that cannot be proven dead (never
taken over) and a recovery switched off (the old behaviour: held forever).

Every case runs on SQLite and, with ``KAZMA_DATABASE_URL`` +
``KAZMA_TEST_ALLOW_REAL_DB=1``, on real Postgres metadata, jobs and
reservations (the ``postgres`` parameters; skipped without a database -- a
skip is not evidence). Each case uses its own tenant, so a shared database's
other rows never decide a count.
"""

from __future__ import annotations

import asyncio
import io
import json
import multiprocessing
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from kazma_core.documents import admission
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState
from kazma_core.documents.worker import DocumentWorker

#: Exit code of a child killed on purpose (anything else is a test bug).
_KILLED = 137

_INTAKE_STAGES = {
    # stage name -> (service attribute, method the child dies right after)
    "reserve": ("admissions", "reserve"),
    "stored": ("storage", "put_stream"),
    "publishing": ("admissions", "mark_publishing"),
    "blob": ("repository", "register_blob"),
    "document": ("repository", "create_document"),
    "version": ("repository", "create_version"),
    "job": ("jobs", "enqueue"),
}


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request, monkeypatch):
    if request.param == "postgres":
        try:
            from kazma_core.db.postgres_pool import get_postgres_pool

            pool = get_postgres_pool()  # never reset: the settings store holds it
        except Exception:  # noqa: BLE001 - no configured pool means skip
            pool = None
        if pool is None:
            pytest.skip("Postgres pool not configured")
        monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
        monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "postgres")
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        previous = store.get("documents.enabled")
        store.set("documents.enabled", True, category="documents")
        yield SimpleNamespace(name="postgres", tenant=f"crash-{uuid.uuid4().hex[:12]}")
        if previous is None:
            store.delete("documents.enabled")
        else:
            store.set("documents.enabled", previous, category="documents")
        return
    yield SimpleNamespace(name="sqlite", tenant=f"crash-{uuid.uuid4().hex[:12]}")


def _config(root) -> DocumentConfig:
    return DocumentConfig(
        storage_root=root,
        security_malware_scan="off",
        security_local_salvage=False,
        ocr_enabled=False,
    )


def _service(root, backend_name: str) -> DocumentIngestionService:
    svc = DocumentIngestionService(config=_config(root))
    if backend_name == "postgres":
        # Never trust a pass on the SQLite fallback as Postgres evidence.
        assert getattr(svc.repository, "backend_name", None) == "postgres"
        assert "Postgres" in type(svc.jobs).__name__
    return svc


def _upload(svc, tenant: str, content: bytes, *, key: str | None, document_id=None):
    return svc.ingest_stream(
        io.BytesIO(content), filename="note.txt", tenant_id=tenant, actor_id="alice",
        workspace_id="w", idempotency_key=key, document_id=document_id,
    )


def _kill_after(target, method: str) -> None:
    real = getattr(target, method)

    def run_then_die(*args, **kwargs):
        real(*args, **kwargs)
        os._exit(_KILLED)

    setattr(target, method, run_then_die)


def _intake_child(root, backend_name, tenant, stage, key, content, document_id):
    svc = _service(root, backend_name)
    holder, method = _INTAKE_STAGES[stage]
    _kill_after(getattr(svc, holder), method)
    _upload(svc, tenant, content, key=key, document_id=document_id)
    os._exit(3)  # the stage was never reached: the test is wrong


def _stage_child(root, backend_name, tenant, state_value):
    """Claim the job and die right after the named stage's work is done,
    before its transition is recorded (the worst point: the effect exists,
    the queue does not know it)."""

    svc = _service(root, backend_name)
    state = DocumentJobState(state_value)
    handlers = svc.stage_handlers()
    real = handlers[state]

    def run_then_die(job, context):
        real(job, context)
        os._exit(_KILLED)

    handlers[state] = run_then_die
    worker = DocumentWorker(svc.jobs, handlers)
    claimed = svc.jobs.claim_next(owner="doomed-worker", lease_seconds=30, tenant_id=tenant)
    if claimed is None:
        os._exit(4)
    asyncio.run(worker._process_claimed(claimed, "doomed-worker"))
    os._exit(5)  # the stage was never reached


def _run_child(target, *args) -> int:
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=target, args=args)
    child.start()
    child.join(timeout=120)
    if child.is_alive():
        child.kill()
        child.join(10)
        pytest.fail("crash child did not finish")
    code = child.exitcode
    child.close()  # drop our process handle: the pid is gone for good
    return code


def _set_owner(svc, tenant: str, owner: str) -> None:
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(
            cur,
            "UPDATE document_intake_reservations SET owner = ? WHERE active = 1 AND tenant_id = ?",
            (owner, tenant),
        )


def _release_all(svc, tenant: str) -> None:
    # A shared database outlives the test: leave no held reservation behind.
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(
            cur, "UPDATE document_intake_reservations SET active = 0 WHERE tenant_id = ?", (tenant,),
        )


@pytest.fixture(autouse=True)
def _same_install_identity():
    # Mint the install id before any child runs, so parent and child agree.
    admission._process_identity.cache_clear()
    admission.current_owner()
    yield
    admission._process_identity.cache_clear()


@pytest.mark.parametrize("keyed", [True, False], ids=["keyed", "unkeyed"])
@pytest.mark.parametrize("stage", list(_INTAKE_STAGES))
def test_an_intake_killed_at_any_stage_is_recovered(tmp_path, backend, stage, keyed):
    tenant = backend.tenant
    key = "upload-1" if keyed else None
    content = b"crash matrix body " + tenant.encode()
    assert _run_child(_intake_child, tmp_path, backend.name, tenant, stage, key, content, None) == _KILLED

    svc = _service(tmp_path, backend.name)
    try:
        snapshot = svc.admissions.snapshot(tenant_id=tenant)
        assert snapshot["pending"] == 0
        assert snapshot["stranded"] == 0
        documents = svc.repository.list_documents(tenant_id=tenant)
        if stage == "job":
            # Publication completed: the job is the proof, nothing is undone,
            # and the interrupted intake job is resumed as at a worker start.
            assert len(documents) == 1
            assert svc.jobs.queue_stats(tenant_id=tenant).non_terminal == 1
            assert svc.recover_interrupted_intake() >= 1
            [job_id] = svc.jobs.document_job_ids(tenant_id=tenant, document_id=documents[0].id)
            assert svc.jobs.get(tenant_id=tenant, job_id=job_id).state is DocumentJobState.READY_TO_PARSE
        else:
            assert documents == []
            assert svc.jobs.queue_stats(tenant_id=tenant).non_terminal == 0
        retry = _upload(svc, tenant, content, key=key)
        assert retry.job_id
        expected = 2 if (stage == "job" and not keyed) else 1
        assert len(svc.repository.list_documents(tenant_id=tenant)) == expected
        if keyed and stage == "job":
            assert retry.reused
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0
    finally:
        svc.close()


@pytest.mark.parametrize("stage", ["blob", "version"])
def test_a_new_version_killed_midway_leaves_the_document_on_its_old_version(tmp_path, backend, stage):
    tenant = backend.tenant
    svc = _service(tmp_path, backend.name)
    try:
        first = _upload(svc, tenant, b"version one " + tenant.encode(), key="v1")
    finally:
        svc.close()
    second_body = b"version two " + tenant.encode()
    assert _run_child(
        _intake_child, tmp_path, backend.name, tenant, stage, "v2", second_body, str(first.document_id)
    ) == _KILLED

    svc = _service(tmp_path, backend.name)
    try:
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0
        [document] = svc.repository.list_documents(tenant_id=tenant)
        assert str(document.id) == str(first.document_id)
        assert str(document.current_version_id) == str(first.version_id)
        assert [str(v.id) for v in svc.repository.list_versions(
            tenant_id=tenant, document_id=document.id)] == [str(first.version_id)]
        second = _upload(svc, tenant, second_body, key="v2", document_id=str(first.document_id))
        refreshed = svc.repository.get_document(tenant_id=tenant, document_id=document.id)
        assert str(refreshed.current_version_id) == str(second.version_id)
    finally:
        svc.close()


def test_an_owner_that_cannot_be_proven_dead_is_never_taken_over(tmp_path, backend):
    # Negative control for the recovery: the same dead-at-"document" intake,
    # but its owner rewritten to another host and holding no lease (a row of
    # a build without the intake fence). Nothing is rolled back, the
    # reservation stays charged, and readiness names it for the operator.
    # (An owner that holds a lease is taken over once it lapses:
    # tests/test_document_intake_lease.py.)
    tenant = backend.tenant
    assert _run_child(
        _intake_child, tmp_path, backend.name, tenant, "document", "k", b"elsewhere " + tenant.encode(), None
    ) == _KILLED
    svc = _service(tmp_path, backend.name)
    try:
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0  # recovered as dead
        assert svc.repository.list_documents(tenant_id=tenant) == []
        # Pretend the next intake's owner lives on another host.
        assert _run_child(
            _intake_child, tmp_path, backend.name, tenant, "document", "k2", b"other " + tenant.encode(), None
        ) == _KILLED
        _set_owner(svc, tenant, json.dumps(
            {"install": "other", "host": "other-host", "pid": 1, "started": "1.000"}, sort_keys=True
        ))
        with svc.admissions._transaction() as cur:
            svc.admissions._execute(
                cur, "UPDATE document_intake_reservations SET heartbeat_epoch = 0 WHERE active = 1 AND tenant_id = ?",
                (tenant,),
            )
    finally:
        svc.close()

    svc = _service(tmp_path, backend.name)
    try:
        snapshot = svc.admissions.snapshot(tenant_id=tenant)
        assert snapshot["pending"] == 1
        assert snapshot["stranded"] == 1
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1  # its document is kept
        readiness = svc.readiness()
        assert readiness["stranded_intake_reservations"] >= 1
        assert "document_intake_reservations_stranded" in readiness["degraded_reasons"]
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_without_recovery_a_dead_intake_stays_held(tmp_path, backend, monkeypatch):
    # The old behaviour, as the negative control of the matrix: with the
    # startup recovery switched off the dead intake's reservation and its
    # empty document stay behind.
    tenant = backend.tenant
    assert _run_child(
        _intake_child, tmp_path, backend.name, tenant, "document", "k", b"held " + tenant.encode(), None
    ) == _KILLED
    monkeypatch.setattr(DocumentIngestionService, "_recover_stranded_intakes", lambda self: 0)
    svc = _service(tmp_path, backend.name)
    try:
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 1
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1
    finally:
        monkeypatch.undo()
        svc.close()
    svc = _service(tmp_path, backend.name)  # the next start with recovery on
    try:
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0
        assert svc.repository.list_documents(tenant_id=tenant) == []
    finally:
        svc.close()


_JOB_STAGES = [
    DocumentJobState.PARSING,
    DocumentJobState.NORMALIZING,
    DocumentJobState.INDEXING,
    DocumentJobState.VERIFYING,
]


async def _drive(svc, tenant: str) -> None:
    """Run the tenant's claimable work on a fresh worker, as after a restart."""

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(20):
        claimed = svc.jobs.claim_next(owner="replacement", lease_seconds=60, tenant_id=tenant)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "replacement")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _JOB_STAGES, ids=lambda s: s.value)
async def test_a_worker_killed_inside_a_stage_is_recovered_by_lease_expiry(tmp_path, backend, monkeypatch, state):
    tenant = backend.tenant
    svc = _service(tmp_path, backend.name)
    try:
        result = _upload(svc, tenant, b"stage crash body " + tenant.encode(), key="stage")
        assert result.state is DocumentJobState.READY_TO_PARSE
    finally:
        svc.close()

    assert _run_child(_stage_child, tmp_path, backend.name, tenant, state.value) == _KILLED

    svc = _service(tmp_path, backend.name)
    try:
        crashed = svc.jobs.get(tenant_id=tenant, job_id=result.job_id)
        assert crashed.state is state and crashed.lease_owner == "doomed-worker"
        clock = {"now": datetime.now(UTC) + timedelta(hours=1)}
        monkeypatch.setattr(svc.jobs, "_clock", lambda: clock["now"])
        assert svc.jobs.recover_expired_leases(tenant_id=tenant) == 1
        waiting = svc.jobs.get(tenant_id=tenant, job_id=result.job_id)
        assert waiting.state is DocumentJobState.RETRY_WAIT
        assert waiting.stage == state.value  # resumes at the stage that died
        clock["now"] += timedelta(hours=1)
        await _drive(svc, tenant)
        job = svc.jobs.get(tenant_id=tenant, job_id=result.job_id)
        assert job.state is DocumentJobState.READY, (job.state, job.error_code)
        # No duplicate effects: one document, one version, one job.
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1
        assert len(svc.repository.list_versions(tenant_id=tenant, document_id=result.document_id)) == 1
        assert svc.jobs.document_job_ids(tenant_id=tenant, document_id=result.document_id) == [result.job_id]
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_running_service_recovers_another_process_intake(tmp_path, backend, monkeypatch):
    # Start-up recovery alone left an intake killed in ANOTHER process holding
    # its capacity until this service restarted (found by the soak harness).
    from kazma_core.documents import ingestion

    monkeypatch.setattr(ingestion, "_INTAKE_SWEEP_SECONDS", 0.2)
    tenant = backend.tenant
    svc = _service(tmp_path, backend.name)
    await svc.start_workers()
    try:
        assert _run_child(
            _intake_child, tmp_path, backend.name, tenant, "document", "live", b"live " + tenant.encode(), None
        ) == _KILLED
        for _ in range(100):
            if svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0:
                break
            await asyncio.sleep(0.1)
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 0
        assert svc.repository.list_documents(tenant_id=tenant) == []
    finally:
        await svc.stop_workers()
        svc.close()


@pytest.mark.asyncio
async def test_without_the_sweep_the_other_process_intake_stays_held(tmp_path, backend, monkeypatch):
    # Negative control: the same crash with the sweep idled (start-up recovery
    # only, the old behaviour) keeps the reservation until a restart.
    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(DocumentIngestionService, "_sweep_stranded_intakes", idle)
    tenant = backend.tenant
    svc = _service(tmp_path, backend.name)
    await svc.start_workers()
    try:
        assert _run_child(
            _intake_child, tmp_path, backend.name, tenant, "document", "held", b"held " + tenant.encode(), None
        ) == _KILLED
        # The child has exited; with the sweep idled nothing else recovers it.
        assert svc.admissions.snapshot(tenant_id=tenant)["pending"] == 1
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1
    finally:
        await svc.stop_workers()
        svc._recover_stranded_intakes()  # leave the shared database clean
        svc.close()
