"""A stop waits for the intake sweep; an intake this process left is taken back.

Found on the drill VM (2026-11-08) by the 48-hour document soak, which
restarts the service inside one process:

    stranded intake <token> could not be rolled back (ProgrammingError); a later start retries it
    fenced intake sweep failed (ProgrammingError)

1. ``stop_workers`` cancelled the sweep's task, but the sweep's pass runs in a
   thread (``asyncio.to_thread``) and a cancelled task does not stop it:
   ``close()`` took the database away and the thread's next statement met a
   closed database. Now the stop asks the sweep to stop and waits for its
   pass, which stops between intakes.
2. The pass had already CLAIMED the intake: the reservation then named this
   process as its owner, and every later sweep here skipped it as "our own,
   still running" -- it was held until the process exited, and the soak's
   end check fails on a reservation left pending. Now an intake this process
   owns that no code here works on (``admission.is_live``) is taken back.

Each with the old behaviour as its control.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path

import pytest
from kazma_core.documents import admission
from kazma_core.documents import ingestion as ingestion_mod
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService

TENANT = "sweep-stop"


@pytest.fixture(autouse=True)
def _identity():
    admission._process_identity.cache_clear()
    admission.current_owner()
    yield


def _service(root: Path) -> DocumentIngestionService:
    return DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
    ))


def _reserve(svc: DocumentIngestionService) -> str:
    return svc.admissions.reserve(
        tenant_id=TENANT, request_key=f"k-{uuid.uuid4().hex}", request_digest="d",
        job_id=str(uuid.uuid4()), byte_size=1, config=svc.config,
    )


def _pending(root: Path) -> int:
    svc = _service(root)
    try:
        return int(svc.admissions.snapshot(tenant_id=TENANT).get("pending", 0))
    finally:
        svc.close()


async def _stop_mid_rollback(root: Path, monkeypatch, *, old_stop: bool) -> None:
    """The VM's sequence: a sweep pass has claimed a stranded intake and is
    about to roll it back when the service is stopped and its store closed."""
    monkeypatch.setattr(ingestion_mod, "_INTAKE_SWEEP_SECONDS", 0.05)
    monkeypatch.setattr(ingestion_mod, "owner_state", lambda owner: "dead")
    svc = _service(root)
    token = _reserve(svc)
    admission._drop(token)  # its uploader is gone
    claimed, release = threading.Event(), threading.Event()
    real_claim = svc.admissions.claim

    def claim_then_pause(*args, **kwargs):
        result = real_claim(*args, **kwargs)
        claimed.set()
        release.wait(10)
        return result

    svc.admissions.claim = claim_then_pause
    await svc.start_workers()
    assert await asyncio.to_thread(claimed.wait, 10), "the sweep never reached the intake"
    threading.Timer(0.3, release.set).start()
    if old_stop:
        # The stop before 2026-11-08: cancel the task, close at once.
        sweeper, svc._intake_sweeper = svc._intake_sweeper, None
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        svc._intake_sweep_stop.set()
        await svc.stop_workers()
        svc.close()
        await asyncio.to_thread(release.wait, 10)
        await asyncio.sleep(0.5)  # the thread meets the closed store
    else:
        await svc.stop_workers()
        svc.close()


async def test_a_stop_waits_for_the_sweep_before_closing(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="kazma_core.documents.ingestion")
    await _stop_mid_rollback(tmp_path, monkeypatch, old_stop=False)
    assert "could not be rolled back" not in caplog.text, caplog.text
    assert _pending(tmp_path) == 0, "the claimed intake was not rolled back"


async def test_control_the_old_stop_closes_under_the_sweep(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="kazma_core.documents.ingestion")
    await _stop_mid_rollback(tmp_path, monkeypatch, old_stop=True)
    assert "could not be rolled back (ProgrammingError" in caplog.text, caplog.text


def test_an_intake_this_process_left_is_taken_back(tmp_path):
    svc = _service(tmp_path)
    try:
        abandoned = _reserve(svc)
        admission._drop(abandoned)  # claimed by a service here that stopped
        live = _reserve(svc)  # an upload in progress in this process
        assert svc._recover_stranded_intakes() == 1
        snapshot = svc.admissions.snapshot(tenant_id=TENANT)
        assert snapshot["pending"] == 1, snapshot
        assert admission.is_live(live)
        svc.admissions.finish(live)
        assert not admission.is_live(live)
    finally:
        svc.close()


def test_control_the_old_rule_held_it_until_the_process_exited(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        abandoned = _reserve(svc)
        admission._drop(abandoned)
        monkeypatch.setattr(ingestion_mod, "is_live", lambda token: True)  # "self" was always live
        assert svc._recover_stranded_intakes() == 0
        assert svc.admissions.snapshot(tenant_id=TENANT)["pending"] == 1
    finally:
        svc.close()


def test_a_refused_reservation_is_not_left_live(tmp_path):
    svc = _service(tmp_path)
    try:
        key = f"k-{uuid.uuid4().hex}"
        first = svc.admissions.reserve(tenant_id=TENANT, request_key=key, request_digest="d",
                                       job_id=str(uuid.uuid4()), byte_size=1, config=svc.config)
        before = set(admission._LIVE_INTAKES)
        with pytest.raises(Exception):
            svc.admissions.reserve(tenant_id=TENANT, request_key=key, request_digest="d",
                                   job_id=str(uuid.uuid4()), byte_size=1, config=svc.config)
        assert set(admission._LIVE_INTAKES) == before and first in before
    finally:
        svc.close()
