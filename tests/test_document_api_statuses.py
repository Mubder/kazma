"""One status per document error code, on every route; refusals before the body.

2026-10-13 (DOC-A4, DOC-A9; found by the Documents documentation review).
Three route helpers in ``documents_api`` kept three code->status tables that
had drifted: writes switched off answered 503 on upload, import, index,
unindex, delete and retry, and 422 on the action routes; a required malware
scanner that could not run answered 400 -- after the whole body was read --
as if the file were at fault. Now one table (``_STATUS_BY_CODE``) answers
every route, server-state refusals are 503 with Retry-After, and the upload
precheck refuses for an unavailable scanner before reading a byte. Negative
controls: the old action table's 422; an upload whose precheck does not ask
reads the body.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from kazma_core.documents.ingestion import DocumentIngestionError
from kazma_ui import documents_api


class _Svc:
    """Every operation the three route helpers front raises ``code``."""

    def __init__(self, code: str) -> None:
        import types

        self.code = code
        self.config = types.SimpleNamespace(intake_max_bytes=1 << 20, capacity_max_concurrent_uploads=4)

    def _raise(self, *args, **kwargs):
        raise DocumentIngestionError(self.code, f"refused: {self.code}")

    precheck_upload = _raise
    delete_document = _raise

    async def convert_document(self, **kwargs):
        self._raise()


def _app(monkeypatch, svc) -> FastAPI:
    async def scope(request):
        return "t", "operator", "w"

    monkeypatch.setattr(documents_api, "_resolve_scope", scope)
    app = FastAPI()
    app.state.documents = svc
    app.include_router(documents_api.create_documents_router())
    return app


async def _call(app, method: str, path: str, body: bytes = b"", headers=()) -> tuple[int, dict, dict, int]:
    reads = {"bytes": 0, "done": False}

    async def receive():
        if not reads["done"]:
            reads["done"] = True
            reads["bytes"] += len(body)
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    raw_headers = [(b"host", b"test"), (b"content-length", str(len(body)).encode()), *headers]
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": raw_headers, "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": "",
        "app": app,
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=60)
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    response_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    return start["status"], json.loads(payload or b"{}"), response_headers, reads["bytes"]


_ROUTES = {
    "upload (intake helper)": ("POST", "/api/documents", b"hello", ((b"x-document-filename", b"a.txt"),)),
    "convert (action helper)": ("POST", "/api/documents/d1/convert", b'{"target_format": "pdf"}', ()),
    "delete (lifecycle helper)": ("POST", "/api/documents/d1/delete", b"", ()),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(documents_api._STATUS_BY_CODE))
async def test_every_route_answers_a_code_with_the_same_status(monkeypatch, code):
    app = _app(monkeypatch, _Svc(code))
    expected = documents_api._STATUS_BY_CODE[code]
    for name, (method, path, body, headers) in _ROUTES.items():
        status, payload, response_headers, _ = await _call(app, method, path, body, headers)
        if code == "intake_too_large" and name.startswith("upload"):
            assert status == 413  # the upload's own too-large answer
            continue
        assert status == expected, (name, status, payload)
        assert payload.get("code") == code, (name, payload)
        if expected == 503:
            assert response_headers.get("retry-after") == "30", name


@pytest.mark.asyncio
async def test_writes_off_was_a_422_on_the_action_routes():
    # Negative control: the action helper's old table had no entry.
    old_action_table = {
        "invalid_request": 400, "invalid_document_request": 400, "invalid_document_scope": 400,
        "invalid_document_encoding": 400, "idempotency_conflict": 409, "intake_too_large": 413,
        "quota_exceeded": 413, "not_ready": 409, "document_access_denied": 404,
        "artifact_access_denied": 404, "document_engine_unavailable": 503,
        "unsupported_document_operation": 422, "repository_unavailable": 503,
    }
    assert old_action_table.get("document_platform_disabled", 422) == 422
    assert documents_api._STATUS_BY_CODE["document_platform_disabled"] == 503


# ── DOC-A9: an unavailable required scanner is refused before the body ─────

def _scanner_off(monkeypatch):
    from kazma_core.documents import malware

    monkeypatch.setattr(malware, "probe_malware_scanner", lambda: {"available": False})


@pytest.mark.asyncio
async def test_a_required_scanner_that_cannot_run_refuses_the_upload_unread(monkeypatch, tmp_path):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    _scanner_off(monkeypatch)
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "store", security_malware_scan="on", ocr_enabled=False,
        security_local_salvage=False,
    ))
    try:
        body = b"x" * 4096
        status, payload, headers, read = await _call(
            _app(monkeypatch, svc), "POST", "/api/documents", body, ((b"x-document-filename", b"a.txt"),))
        assert status == 503 and payload["code"] == "malware_scanner_unavailable"
        assert headers.get("retry-after") == "30"
        assert read == 0
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_without_the_precheck_question_the_body_is_read(monkeypatch, tmp_path):
    # Negative control: the precheck before DOC-A9 never asked, so the body
    # was received and spooled, and intake refused afterwards.
    from kazma_core.documents import malware
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    _scanner_off(monkeypatch)
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "store", security_malware_scan="on", ocr_enabled=False,
        security_local_salvage=False,
    ))
    monkeypatch.setattr(malware, "malware_blocks_intake", lambda config, probe=None: None)
    try:
        body = b"x" * 4096
        status, payload, _headers, read = await _call(
            _app(monkeypatch, svc), "POST", "/api/documents", body, ((b"x-document-filename", b"a.txt"),))
        assert read == len(body)
        assert payload.get("code") == "malware_scanner_unavailable"  # refused only after
    finally:
        svc.close()
