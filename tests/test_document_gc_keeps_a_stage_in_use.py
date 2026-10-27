"""Garbage collection never takes the staged copy a running job still reads.

Audit 2026-10-26 AUD-023: ``_sweep_scratch`` deleted ``.stage/<dir>`` by
age (older than ``gc_grace_seconds``, an hour). A long durable OCR run holds
one staged copy of its document for its whole life and never writes to it,
so a scan of more than about 110 pages lost its source to a GC pass mid-job:
the next batch could not read it and the job restarted from page 1.

Now a stage is held while in use (``documents.live_scratch``): the sweep in
this process skips it, and a keeper refreshes its time so a sweep in another
process reads it as fresh. Negative control: the same sweep without the hold
deletes it.
"""

from __future__ import annotations

import os
import time

import pytest
from kazma_core.documents import live_scratch
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService


@pytest.fixture
def svc(tmp_path):
    service = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    yield service
    service.close()


def _age(folder, seconds: float) -> None:
    old = time.time() - seconds
    for path in [*folder.iterdir(), folder]:
        os.utime(path, (old, old))


def test_a_stage_in_use_survives_a_sweep_and_goes_after(svc, tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"a long scan")
    with svc._materialize(blob, "scan.pdf") as staged:
        stage = staged.parent
        _age(stage, 7 * 24 * 3600)  # as old as any crash's leftover
        svc.run_maintenance(dry_run=False)
        assert staged.read_bytes() == b"a long scan", "the running job's source was swept"
    assert not stage.exists()  # removed by its owner on the way out


def test_the_keeper_refreshes_a_held_stage(tmp_path, monkeypatch):
    # A sweep in ANOTHER process cannot see this registry: the held folder's
    # time is kept fresh instead.
    monkeypatch.setattr(live_scratch, "TOUCH_SECONDS", 0.05)
    folder = tmp_path / ".stage" / "doc-stage-held"
    folder.mkdir(parents=True)
    _age(folder, 7 * 24 * 3600)
    before = folder.stat().st_mtime
    live_scratch.hold(folder)
    try:
        deadline = time.monotonic() + 5
        while folder.stat().st_mtime == before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert folder.stat().st_mtime > time.time() - 60
    finally:
        live_scratch.release(folder)
    assert not live_scratch.is_held(folder)


def test_negative_control_an_unheld_old_stage_is_swept(svc, tmp_path):
    stage = tmp_path / ".stage" / "doc-stage-running"
    stage.mkdir(parents=True)
    (stage / "source.pdf").write_bytes(b"a long scan")
    _age(stage, 7 * 24 * 3600)
    svc.run_maintenance(dry_run=False)
    assert not stage.exists()
