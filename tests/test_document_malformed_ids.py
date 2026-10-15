"""A malformed id reads exactly like an id nobody holds.

Live 2026-10-15: ``GET /api/documents/readiness`` (the readiness route is
``/ops/readiness``) reached the detail route with the id "readiness". The
service's ``DocumentId("readiness")`` raised a bare ValueError, the route
answered 400 "detail failed (ValueError)" and logged a WARNING -- a request
naming a wrong id is not a server fault, and it answered differently from an
id nobody holds (404 ``document_access_denied``), which tells a caller which
ids are well formed (DOC-B4: no existence oracle). The id types now raise
``InvalidIdError`` (still a ValueError), and the routes answer it as an
unknown id. Negative control: without that mapping the malformed id answers
400 again.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from kazma_core.documents.ingestion import DocumentIngestionError
from kazma_core.documents.models import DocumentId, InvalidIdError, JobId
from kazma_ui import documents_api
from tests.test_document_api_statuses import _app, _call


class _Svc:
    """The real id types, as the service's first lines use them: a
    well-formed id nobody holds is unavailable, a malformed one raises."""

    def __init__(self) -> None:
        import types

        self.config = types.SimpleNamespace(intake_max_bytes=1 << 20, capacity_max_concurrent_uploads=4)

    def get_document_detail(self, *, document_id, **_kw):
        DocumentId(document_id)
        raise DocumentIngestionError("document_access_denied", "Document is unavailable")

    def job_status(self, *, job_id, **_kw):
        JobId(job_id)
        return None  # nobody holds it

    async def convert_document(self, *, document_id, **_kw):
        DocumentId(document_id)
        raise DocumentIngestionError("document_access_denied", "Document is unavailable")


_UNKNOWN = str(uuid.uuid4())
_ROUTES = {
    "detail": ("GET", "/api/documents/{id}", b""),
    "versions": ("GET", "/api/documents/{id}/versions", b""),
    "convert": ("POST", "/api/documents/{id}/convert", b'{"target_format": "pdf"}'),
    "job": ("GET", "/api/documents/jobs/{id}", b""),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("malformed", ["readiness", "D1", _UNKNOWN.upper()])
async def test_a_malformed_id_answers_like_an_unknown_one(monkeypatch, caplog, route, malformed) -> None:
    app = _app(monkeypatch, _Svc())
    method, path, body = _ROUTES[route]
    unknown = await _call(app, method, path.format(id=_UNKNOWN), body)
    with caplog.at_level(logging.WARNING, logger="kazma_ui.documents_api"):
        bad = await _call(app, method, path.format(id=malformed), body)
    assert unknown[0] == 404, unknown
    assert (bad[0], bad[1]) == (unknown[0], unknown[1]), (bad, unknown)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], caplog.text


def test_the_id_error_is_still_a_value_error() -> None:
    with pytest.raises(ValueError):
        DocumentId("nope")
    with pytest.raises(InvalidIdError) as caught:
        JobId("nope")
    assert caught.value.kind == "JobId"


@pytest.mark.asyncio
async def test_negative_control_without_the_mapping_a_malformed_id_is_a_400(monkeypatch) -> None:
    monkeypatch.setattr(documents_api, "_unknown_id", lambda exc: None)
    app = _app(monkeypatch, _Svc())
    status, payload, _headers, _ = await _call(app, "GET", "/api/documents/readiness")
    assert status == 400 and "IdError" in payload["error"], payload
