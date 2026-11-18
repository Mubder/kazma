"""Documents seeded straight into a space, with no parser run.

The spaces tests and the space matrix's Documents cases need ready documents
in two spaces many times over. A real intake inspects and parses each in the
sandboxed parser worker (a subprocess per step, seconds each on a loaded
machine), and none of that is what those tests are about: they check which
space and which account a surface reads as. So a document is written the way
a finished intake leaves it -- its original's bytes in the space's content
store, its blob, document, version and job rows, and the parsed manifest
whose text is the marker -- through the product's own storage and
repositories. ``tests/test_document_spaces.py`` keeps one real intake per
surface beside these.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.models import (
    BlockType,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    Provenance,
)

__all__ = ["Seeded", "seed_document", "seeded_service"]


@dataclass(frozen=True)
class Seeded:
    tenant_id: str
    document_id: str
    version_id: str
    job_id: str
    sha256: str


def seeded_service(root: Path) -> DocumentIngestionService:
    """A service on a private store, workers not started."""
    return DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))


def seed_document(
    svc: DocumentIngestionService, *, tenant_id: str, owner: str, marker: str, title: str = "",
) -> Seeded:
    """A ready text document of *tenant_id*, owned by *owner*, whose one
    paragraph is *marker*."""
    body = marker.encode("utf-8")
    sha = hashlib.sha256(body).hexdigest()
    svc.storage.for_space(tenant_id).put_stream(io.BytesIO(body), kind="originals", max_bytes=1 << 20)
    blob = svc.repository.register_blob(
        tenant_id=tenant_id, sha256=sha, byte_size=len(body), storage_kind="originals",
    )
    record = svc.repository.create_document(
        tenant_id=tenant_id, owner_id=owner, title=title or f"note {marker[:12]}",
        metadata={"original_filename": "note.txt", "workspace_id": "w"},
    )
    version = svc.repository.create_version(
        tenant_id=tenant_id, document_id=record.id, actor_id=owner, source_blob_id=blob.id,
        source_sha256=sha, original_filename="note.txt", mime_type="text/plain",
    )
    job = svc.jobs.enqueue(
        tenant_id=tenant_id, workspace_id="w", document_id=record.id, version_id=version.id,
        idempotency_key=f"seed-{version.id}", actor_id=owner,
    )
    ir = DocumentIR(
        document_id=record.id, version_id=version.id,
        pages=(DocumentPage(page_number=1, blocks=(DocumentBlock("p1", BlockType.PARAGRAPH, marker),)),),
        provenance=Provenance(source="note.txt", parser="seed", parser_version="1",
                              metadata={"sha256": sha}),
        metadata={"source_sha256": sha, "language": "en"},
    )
    svc.storage.for_space(tenant_id).write_manifest(
        document_id=record.id, version_id=version.id,
        manifest={"policy_revision": "seed", "ir": ir.to_dict()},
    )
    return Seeded(tenant_id=tenant_id, document_id=str(record.id), version_id=str(version.id),
                  job_id=str(job.id), sha256=sha)
