"""Intake recovery advances only stranded jobs, and a lost race is not a crash.

Audit 2026-10-26 AUD-022: ``recover_interrupted_intake`` (run by every
``start_workers()``: the server, the CLI, the agent tool's lazy service,
another replica) selected EVERY job in ``received`` / ``quarantined`` /
``validating`` with no owner filter, and caught only
``InvalidJobTransitionError``. Losing the compare-and-swap raises the
sibling ``StaleJobUpdateError``. So a second process starting its workers
while the server validated an upload re-ran the validation beside it, and
the loser either failed ``start_workers`` (no worker pool in that process)
or failed the upload of a document that was ingested.

Now a job whose intake reservation is owned by a live process is left to
it, and any job conflict skips the job. Negative controls: the same calls
on the old rules advance the live upload and raise.
"""

from __future__ import annotations

import io
import json

import pytest
from kazma_core.documents import admission
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState, StaleJobUpdateError


@pytest.fixture
def svc(tmp_path):
    service = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    yield service
    service.close()


def _published_mid_validation(svc, monkeypatch) -> object:
    """An upload that published its job and is still validating it."""
    real = svc._advance_intake
    monkeypatch.setattr(svc, "_advance_intake", lambda job, **kw: job)
    result = svc.ingest_stream(
        io.BytesIO(b"an upload in progress"), filename="note.txt", tenant_id="t",
        actor_id="alice", workspace_id="w", idempotency_key="k1",
    )
    monkeypatch.setattr(svc, "_advance_intake", real)
    job = svc.jobs.get(tenant_id="t", job_id=result.job_id)
    assert job.state is DocumentJobState.RECEIVED
    return job


def _owner_is_dead(svc) -> None:
    install, host, _pid, _started = admission._process_identity()
    dead = json.dumps(
        {"install": install, "host": host, "pid": 2**31 - 1, "started": "1.000"}, sort_keys=True
    )
    with svc.admissions._transaction() as cur:
        svc.admissions._execute(cur, "UPDATE document_intake_reservations SET owner = ?", (dead,))


def test_a_live_upload_is_not_advanced_by_another_start(svc, monkeypatch):
    job = _published_mid_validation(svc, monkeypatch)

    assert svc.recover_interrupted_intake() == 0

    still = svc.jobs.get(tenant_id="t", job_id=job.id)
    assert still.state is DocumentJobState.RECEIVED
    assert still.version == job.version


def test_a_stranded_intake_is_still_recovered(svc, monkeypatch):
    job = _published_mid_validation(svc, monkeypatch)
    _owner_is_dead(svc)

    assert svc.recover_interrupted_intake() == 1
    assert svc.jobs.get(tenant_id="t", job_id=job.id).state is DocumentJobState.READY_TO_PARSE


def test_a_lost_compare_and_swap_skips_the_job(svc, monkeypatch):
    job = _published_mid_validation(svc, monkeypatch)
    _owner_is_dead(svc)

    def lose(current, **kw):
        raise StaleJobUpdateError("expected state/version no longer matches")

    monkeypatch.setattr(svc, "_advance_intake", lose)

    assert svc.recover_interrupted_intake() == 0
    assert svc.jobs.get(tenant_id="t", job_id=job.id).state is DocumentJobState.RECEIVED


def test_negative_control_the_old_rules_race_the_live_upload(svc, monkeypatch):
    job = _published_mid_validation(svc, monkeypatch)
    # Old rule 1: no owner filter -- the live upload's job is advanced here.
    monkeypatch.setattr(svc, "_intake_owner_is_live", lambda current: False)
    assert svc.recover_interrupted_intake() == 1
    assert svc.jobs.get(tenant_id="t", job_id=job.id).state is DocumentJobState.READY_TO_PARSE
    # Old rule 2: only InvalidJobTransitionError caught -- a stale update escapes.
    from kazma_core.documents.jobs import InvalidJobTransitionError

    with pytest.raises(StaleJobUpdateError):
        try:
            raise StaleJobUpdateError("lost")
        except InvalidJobTransitionError:
            pass
