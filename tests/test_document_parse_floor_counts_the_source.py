"""The parse stage's free-space check counts the document it will copy.

Audit 2026-10-26 AUD-040. Before parsing, the stage asks whether the disk
stays above its free-space floor with room for a staged copy and the run's
output: ``below_floor(extra_bytes=2 * size)``. The size was
``getattr(version, "byte_size", 0)`` and a ``VersionRecord`` has no
``byte_size``, so it always asked for 0 bytes, and a stage started into a
disk the copy would fill. The negative control is the old code: the check
is asked with 0.
"""

from __future__ import annotations

import asyncio
import io

from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState
from kazma_core.documents.worker import DocumentWorker

CONTENT = b"floor headroom\n" * 2000  # 30,000 bytes


def test_the_parse_stage_asks_for_twice_the_source_size(tmp_path, monkeypatch):
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    try:
        result = svc.ingest_stream(io.BytesIO(CONTENT), filename="a.txt", tenant_id="t",
                                   actor_id="alice", workspace_id="w")
        asked: list[int] = []

        def below_floor(extra_bytes: int = 0) -> bool:
            asked.append(extra_bytes)
            return False

        monkeypatch.setattr(svc.capacity, "below_floor", below_floor)
        for _ in range(10):
            job = svc.jobs.get(tenant_id="t", job_id=result.job_id)
            if job.state is DocumentJobState.READY:
                break
            claimed = svc.jobs.claim_next(owner="w", lease_seconds=60)
            assert claimed is not None, job.state
            asyncio.run(DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "w"))
        assert svc.jobs.get(tenant_id="t", job_id=result.job_id).state is DocumentJobState.READY
        assert asked, "the parse stage never asked about free space"
        assert 2 * len(CONTENT) in asked, asked
    finally:
        svc.close()
