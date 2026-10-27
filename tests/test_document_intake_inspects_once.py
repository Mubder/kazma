"""One intake launches the parser worker once to inspect its document.

Audit 2026-10-26 AUD-114. Intake inspected the quarantined bytes for their
type (``_probe_mime``), then its synchronous validation inspected the very
same bytes again, under the same policy, moments later: two parser
launches -- two containers in the container profile -- for one check. The
intake now hands its inspection to validation, which reuses it only for the
same digest, file name and policy revision; anything else (a worker retry,
a policy change) inspects again.

Negative control: the old code inspects twice per intake.
"""

from __future__ import annotations

import io

from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState


def _service(tmp_path) -> DocumentIngestionService:
    return DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))


def _count_inspections(svc, monkeypatch) -> list[str]:
    seen: list[str] = []
    real = svc.service.inspect_path

    def spy(path):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(svc.service, "inspect_path", spy)
    return seen


def test_an_intake_inspects_its_document_once(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        seen = _count_inspections(svc, monkeypatch)
        result = svc.ingest_stream(io.BytesIO(b"# Title\n\nbody\n"), filename="a.md",
                                   tenant_id="t", actor_id="alice", workspace_id="w")
        assert result.state is DocumentJobState.READY_TO_PARSE
        assert len(seen) == 1, seen
        version = svc.repository.get_version(tenant_id="t", version_id=result.version_id)
        assert version.mime_type == "text/markdown"
        assert svc.storage.blob_path(kind="originals", sha256=version.source_sha256).is_file()
    finally:
        svc.close()


def test_a_refused_document_is_still_rejected_by_validation(tmp_path, monkeypatch):
    """No inspection to reuse: validation inspects and records the refusal."""
    svc = _service(tmp_path)
    try:
        seen = _count_inspections(svc, monkeypatch)
        result = svc.ingest_stream(io.BytesIO(b"MZ\x90\x00binary\x00\x01"), filename="tool.exe",
                                   tenant_id="t", actor_id="alice", workspace_id="w")
        assert result.state is DocumentJobState.REJECTED
        assert len(seen) == 2, seen
    finally:
        svc.close()


def test_an_inspection_under_another_policy_is_not_reused(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        seen = _count_inspections(svc, monkeypatch)
        real_probe = svc._probe

        def probe(stored, filename):
            sniff = real_probe(stored, filename)
            # The policy moved on between the inspection and validation.
            return type(sniff)(sha256=sniff.sha256, filename=sniff.filename,
                               policy_revision="an older policy", result=sniff.result)

        monkeypatch.setattr(svc, "_probe", probe)
        result = svc.ingest_stream(io.BytesIO(b"plain words\n"), filename="b.txt",
                                   tenant_id="t", actor_id="alice", workspace_id="w")
        assert result.state is DocumentJobState.READY_TO_PARSE
        assert len(seen) == 2, seen
    finally:
        svc.close()
