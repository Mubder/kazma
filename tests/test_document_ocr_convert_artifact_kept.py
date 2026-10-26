"""A scan converted to Word keeps its artifact (audit 2026-10-26 AUD-009).

``convert_document`` exports a document read by OCR from its OCR text
through ``service.generate`` -- with no ``document_id``/``version_id``. The
operations layer records an artifact only for a scope naming both, so the
returned ``artifact_id`` was never stored: the download answered 404 and the
agent's saved copy (``document_platform/tools._saved_copy``) failed, the
2026-10-22 failure §19 G5 calls fixed.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any

from kazma_core.documents.ingestion import DocumentIngestionService


class _Policy:
    def capture(self) -> object:
        return object()

    @contextlib.contextmanager
    def scope(self, snapshot: object = None):
        yield


def _ingestion(seen: dict[str, Any]) -> DocumentIngestionService:
    svc = DocumentIngestionService.__new__(DocumentIngestionService)
    svc._policy = _Policy()
    svc._require_writes_enabled = lambda: None
    record = SimpleNamespace(title="Scan")
    version = SimpleNamespace(id="v-1")
    svc._resolve_current_version = lambda **kw: (record, version)
    svc._ocr_text_payload = lambda *a, **kw: ("docx", {"title": "Scan", "sections": []})
    svc._bounded_payload = lambda payload: payload

    async def generate(fmt, payload, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True)

    svc.service = SimpleNamespace(generate=generate)
    svc._audited_artifact = lambda result, **kw: {"ok": True}
    return svc


def test_the_ocr_export_runs_under_the_documents_scope() -> None:
    seen: dict[str, Any] = {}
    ingestion = _ingestion(seen)
    out = asyncio.run(ingestion.convert_document(
        tenant_id="t", actor_id="a", workspace_id="w",
        document_id="00000000-0000-0000-0000-000000000001", target_format="docx",
    ))
    assert out == {"ok": True}
    assert str(seen.get("document_id")) == "00000000-0000-0000-0000-000000000001"
    assert seen.get("version_id") == "v-1"


def test_negative_control_a_scope_without_them_keeps_no_artifact() -> None:
    """The rule the old call fell foul of (operations.generate)."""
    from kazma_core.documents.operations import OperationScope

    scope = OperationScope("t", "w", "a")
    assert scope.document_id is None and scope.version_id is None
