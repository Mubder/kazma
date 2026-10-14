"""An intake on another host is taken over when its lease lapses -- fenced.

2026-10-13. Recovery rolled back only intakes whose owner was provably dead:
a process on THIS host that is gone. An intake started by another host (a
second replica, a CLI import on another machine) that died held its
reservation, its capacity and any rows it had written until an operator
freed them by hand, because no process could prove it dead -- and taking it
over on a timer alone would race a publisher that is only partitioned or
paused.

Now an owner renews a lease while its intake runs, a recoverer may take a
reservation over once the lease has lapsed, and the stale owner is FENCED:
its job -- the publication -- is created only in a transaction that finds
the reservation still its own, serialized with the takeover. A fenced owner
removes the rows it wrote under its token; rows it wrote and could not
remove (it died) are removed by the recovery sweep for a day after the
takeover.

The drills run a REAL child intake, given another host's identity and no
working heartbeat (a partitioned or paused host), paused at a chosen point;
the parent ages its lease, takes it over, and resumes the child.
Negative controls: without the fence the stale owner publishes a job for
rows the takeover removed; without the sweep the dead stale owner's rows
stay.
"""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.documents import admission
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.models import DocumentId, JobId

_FENCED = 7
_PUBLISHED = 0
_KILLED = 137


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
        yield SimpleNamespace(name="postgres", tenant=f"lease-{uuid.uuid4().hex[:12]}")
        if previous is None:
            store.delete("documents.enabled")
        else:
            store.set("documents.enabled", previous, category="documents")
        return
    yield SimpleNamespace(name="sqlite", tenant=f"lease-{uuid.uuid4().hex[:12]}")


@pytest.fixture(autouse=True)
def _same_install_identity():
    admission._process_identity.cache_clear()
    admission.current_owner()
    yield


def _service(root, backend_name: str) -> DocumentIngestionService:
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=Path(root), security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False,
    ))
    if backend_name == "postgres":
        assert getattr(svc.repository, "backend_name", None) == "postgres"
        assert "Postgres" in type(svc.jobs).__name__
    return svc


# ── the stale owner: a real child process on "another host" ───────────────

def _zombie(root: str, backend_name: str, tenant: str, pause_after: str, gate: str, marker: str,
            fence: bool, die_before_publishing: bool) -> None:
    from kazma_core.documents import admission as child_admission
    from kazma_core.documents.ingestion import DocumentIngestionError

    from unittest import mock

    svc = _service(root, backend_name)
    elsewhere = mock.patch.object(
        child_admission, "_process_identity",
        lambda: ("other-install", "other-host", os.getpid(), "1.000"),
    )
    svc.admissions.heartbeat = lambda token: True  # partitioned: believes it renews, writes nothing
    real_step = getattr(svc.repository, pause_after)

    def step_then_pause(*args, **kwargs):
        result = real_step(*args, **kwargs)
        Path(marker).write_text("paused", encoding="utf-8")
        deadline = time.monotonic() + 120
        while not Path(gate).exists():
            if time.monotonic() > deadline:
                os._exit(9)
            time.sleep(0.05)
        return result

    setattr(svc.repository, pause_after, step_then_pause)
    real_enqueue = svc.jobs.enqueue

    def enqueue(**kwargs):
        if die_before_publishing:
            os._exit(_KILLED)
        if not fence:
            kwargs.pop("intake_fence", None)
        return real_enqueue(**kwargs)

    svc.jobs.enqueue = enqueue
    try:
        with elsewhere:
            svc.ingest_stream(io.BytesIO(b"zombie intake " + tenant.encode()), filename="note.txt",
                              tenant_id=tenant, actor_id="alice", workspace_id="w", idempotency_key="zombie")
    except DocumentIngestionError as exc:
        os._exit(_FENCED if exc.code == "intake_taken_over" else 8)
    os._exit(_PUBLISHED)


def _start_zombie(tmp_path, backend, *, pause_after: str, fence: bool = True,
                  die_before_publishing: bool = False):
    gate, marker = tmp_path / "go", tmp_path / "paused"
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_zombie, args=(
        str(tmp_path / "content"), backend.name, backend.tenant, pause_after, str(gate), str(marker),
        fence, die_before_publishing,
    ))
    child.start()
    deadline = time.monotonic() + 120
    while not marker.exists():
        assert child.is_alive() and time.monotonic() < deadline, "the child intake never reached its pause"
        time.sleep(0.05)
    return child, gate


def _finish(child, gate) -> int:
    gate.write_text("go", encoding="utf-8")
    child.join(120)
    code = child.exitcode
    child.close()
    return code


def _reservation(svc, tenant: str) -> dict:
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(
            cur, "SELECT * FROM document_intake_reservations WHERE tenant_id = ? AND request_key = ?",
            (tenant, "zombie"),
        )
        return dict(cur.fetchone())


def _age_lease(svc, tenant: str, seconds: float = 10_000.0) -> None:
    """What a partitioned host looks like from here, an hour of silence later."""
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(
            cur,
            "UPDATE document_intake_reservations SET heartbeat_epoch = heartbeat_epoch - ? "
            "WHERE tenant_id = ? AND active = 1",
            (seconds, tenant),
        )


def _release_all(svc, tenant: str) -> None:
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(
            cur, "UPDATE document_intake_reservations SET active = 0, fenced_epoch = 0 WHERE tenant_id = ?",
            (tenant,),
        )


# ── the lease decides ──────────────────────────────────────────────────────

def test_a_remote_intake_renewing_its_lease_is_left_alone(tmp_path, backend):
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="create_version")
    svc = _service(tmp_path / "content", backend.name)
    try:
        assert svc._recover_stranded_intakes() == 0
        row = _reservation(svc, tenant)
        assert row["active"] == 1 and row["fenced_epoch"] == 0
        snapshot = svc.admissions.snapshot(tenant_id=tenant)
        assert snapshot["pending"] == 1 and snapshot["stranded"] == 0  # working, not stranded
        assert _finish(child, gate) == _PUBLISHED
        assert svc.jobs.get(tenant_id=tenant, job_id=JobId(row["job_id"])) is not None
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_a_lapsed_lease_is_taken_over_and_the_stale_owner_is_fenced(tmp_path, backend):
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="create_version")
    svc = _service(tmp_path / "content", backend.name)
    try:
        row = _reservation(svc, tenant)
        assert svc.admissions.snapshot(tenant_id=tenant)["stranded"] == 0
        _age_lease(svc, tenant)
        assert svc.admissions.snapshot(tenant_id=tenant)["stranded"] == 1  # lapsed: about to be taken over
        assert svc._recover_stranded_intakes() == 1
        assert svc.repository.list_documents(tenant_id=tenant) == []  # its rows rolled back
        snapshot = svc.admissions.snapshot(tenant_id=tenant)
        assert snapshot["pending"] == 0 and snapshot["stranded"] == 0  # capacity freed

        # The stale owner wakes and tries to publish: fenced.
        assert _finish(child, gate) == _FENCED
        assert svc.jobs.get(tenant_id=tenant, job_id=JobId(row["job_id"])) is None
        assert svc.repository.list_documents(tenant_id=tenant) == []
        assert _reservation(svc, tenant)["fenced_epoch"] > 0  # kept for the sweep
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_without_the_fence_the_stale_owner_publishes_over_the_takeover(tmp_path, backend):
    # Negative control: the stale owner paused before its document existed,
    # with the fence removed from its enqueue. After the takeover rolled it
    # back and released its capacity, it publishes anyway: a document and a
    # job for a reservation another process closed. (Paused later, after its
    # version, an unfenced job would point at a version the takeover removed;
    # a single SQLite file's foreign key refuses that one, other backends
    # need not.)
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="register_blob", fence=False)
    svc = _service(tmp_path / "content", backend.name)
    try:
        row = _reservation(svc, tenant)
        _age_lease(svc, tenant)
        assert svc._recover_stranded_intakes() == 1
        assert _finish(child, gate) == _PUBLISHED
        taken = _reservation(svc, tenant)
        assert taken["active"] == 0 and taken["fenced_epoch"] > 0  # closed by the takeover
        assert svc.jobs.get(tenant_id=tenant, job_id=JobId(row["job_id"])) is not None
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_rows_the_fenced_owner_writes_after_the_takeover_are_its_to_remove(tmp_path, backend):
    # Paused before its document existed: the takeover had nothing to roll
    # back, and the stale owner writes its document and version afterwards.
    # Fenced at publication, it removes them itself.
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="register_blob")
    svc = _service(tmp_path / "content", backend.name)
    try:
        _age_lease(svc, tenant)
        assert svc._recover_stranded_intakes() == 1
        assert _finish(child, gate) == _FENCED
        assert svc.repository.list_documents(tenant_id=tenant) == []
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_the_sweep_removes_what_a_fenced_owner_wrote_and_left(tmp_path, backend):
    # The stale owner writes its rows after the takeover and dies before it
    # can publish or clean up: the recovery sweep removes them.
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="register_blob", die_before_publishing=True)
    svc = _service(tmp_path / "content", backend.name)
    try:
        _age_lease(svc, tenant)
        assert svc._recover_stranded_intakes() == 1
        assert _finish(child, gate) == _KILLED
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1  # left behind
        svc._recover_stranded_intakes()
        assert svc.repository.list_documents(tenant_id=tenant) == []
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_without_the_sweep_those_rows_stay(tmp_path, backend, monkeypatch):
    # Negative control for the sweep.
    tenant = backend.tenant
    child, gate = _start_zombie(tmp_path, backend, pause_after="register_blob", die_before_publishing=True)
    svc = _service(tmp_path / "content", backend.name)
    try:
        _age_lease(svc, tenant)
        assert svc._recover_stranded_intakes() == 1
        assert _finish(child, gate) == _KILLED
        monkeypatch.setattr(svc.admissions, "fenced_recent", lambda: [])
        svc._recover_stranded_intakes()
        assert len(svc.repository.list_documents(tenant_id=tenant)) == 1
    finally:
        _release_all(svc, tenant)
        for document in svc.repository.list_documents(tenant_id=tenant):
            svc.repository.discard_unpublished_intake(
                tenant_id=tenant, intake_token=str(document.metadata.get("intake_token")),
                document_id=str(document.id), version_id=str(document.current_version_id or ""),
                created_document=True,
            )
        svc.close()


# ── the takeover itself ────────────────────────────────────────────────────

def test_a_takeover_refuses_an_intake_that_published(tmp_path, backend):
    # The stale owner publishes in the instant before the takeover: the
    # claim finds its job and refuses, so nothing is rolled back.
    tenant = backend.tenant
    svc = _service(tmp_path / "content", backend.name)
    try:
        result = svc.ingest_stream(io.BytesIO(b"published " + tenant.encode()), filename="note.txt",
                                   tenant_id=tenant, actor_id="alice", workspace_id="w")
        with svc.admissions._transaction() as cur:
            svc.admissions._execute(
                cur,
                "UPDATE document_intake_reservations SET active = 1, owner = ?, heartbeat_epoch = 1 "
                "WHERE tenant_id = ?",
                (json.dumps({"install": "x", "host": "y", "pid": 1, "started": "1.000"}, sort_keys=True), tenant),
            )
            svc.admissions._execute(
                cur, "SELECT token, owner, heartbeat_epoch FROM document_intake_reservations WHERE tenant_id = ?",
                (tenant,),
            )
            row = dict(cur.fetchone())
        assert not svc.admissions.claim(row["token"], expected_owner=row["owner"],
                                        expected_heartbeat=float(row["heartbeat_epoch"]))
        assert svc.repository.get_document(tenant_id=tenant, document_id=DocumentId(result.document_id))
    finally:
        _release_all(svc, tenant)
        svc.close()


def test_a_heartbeat_that_lands_first_defeats_the_takeover(tmp_path, backend):
    tenant = backend.tenant
    svc = _service(tmp_path / "content", backend.name)
    try:
        token = svc.admissions.reserve(
            tenant_id=tenant, request_key="k", request_digest="d", job_id=str(uuid.uuid4()),
            byte_size=1, config=svc.config,
        )
        with svc.admissions._transaction() as cur:
            svc.admissions._execute(
                cur, "UPDATE document_intake_reservations SET heartbeat_epoch = 1 WHERE token = ?", (token,))
        seen = svc.admissions.get(token)  # what a recoverer read: lapsed
        assert svc.admissions.heartbeat(token)  # renewed after it read it
        assert not svc.admissions.claim(token, expected_owner=seen["owner"],
                                        expected_heartbeat=float(seen["heartbeat_epoch"]))
        # Once taken over, the old owner's renewal reports the fence.
        fresh = svc.admissions.get(token)
        assert svc.admissions.claim(token, expected_owner=fresh["owner"],
                                    expected_heartbeat=float(fresh["heartbeat_epoch"]))
        with svc.admissions._transaction() as cur:
            svc.admissions._execute(cur, "UPDATE document_intake_reservations SET owner = ? WHERE token = ?",
                                    ("someone-else", token))
        assert not svc.admissions.heartbeat(token)
    finally:
        _release_all(svc, tenant)
        svc.close()
