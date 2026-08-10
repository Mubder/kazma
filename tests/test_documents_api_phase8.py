"""Phase 8 Documents Web API + lifecycle tests (FastAPI TestClient)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from kazma_ui.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _wait_ready(client: TestClient, job_id: str, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    state = "?"
    while time.time() < deadline:
        resp = client.get(f"/api/documents/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        state = resp.json()["job"]["state"]
        if state in {"ready", "dead_letter", "rejected", "cancelled"}:
            return state
        time.sleep(0.25)
    return state


def test_documents_page_route(client: TestClient) -> None:
    resp = client.get("/documents")
    assert resp.status_code == 200
    assert "documentsPage" in resp.text


def test_health_reports_worker(client: TestClient) -> None:
    resp = client.get("/api/documents/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "worker" in body["health"]
    assert body["health"]["worker"]["running"] is True


def test_upload_process_read_flow(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        headers={
            "X-Document-Filename": "api-note.txt",
            "Content-Type": "application/octet-stream",
        },
        content=b"Kazma Phase 8 web API end-to-end document.\nSecond line.\n",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    doc_id = body["document_id"]
    job_id = body["job_id"]

    state = _wait_ready(client, job_id)
    assert state == "ready", f"job ended in {state}"

    content = client.get(f"/api/documents/{doc_id}/content")
    assert content.status_code == 200
    cbody = content.json()
    assert cbody["ok"] is True
    assert "Phase 8 web API" in cbody["content"]["text"]

    listing = client.get("/api/documents")
    assert listing.status_code == 200
    assert any(d["document_id"] == doc_id for d in listing.json()["documents"])

    events = client.get(f"/api/documents/jobs/{job_id}/events")
    assert events.status_code == 200
    states = [e["to_state"] for e in events.json()["events"]]
    assert "ready" in states


def test_upload_missing_filename_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        headers={"Content-Type": "application/octet-stream"},
        content=b"data",
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get("/api/documents/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_unknown_document_content_returns_404(client: TestClient) -> None:
    resp = client.get(
        "/api/documents/00000000-0000-0000-0000-000000000000/content"
    )
    assert resp.status_code == 404


def test_import_rejects_path_outside_workspace(client: TestClient) -> None:
    resp = client.post(
        "/api/documents/import",
        json={"path": "../../etc/passwd"},
    )
    assert resp.status_code == 400
    assert resp.json().get("code") == "path_outside_workspace"


def test_cancel_then_state(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        headers={
            "X-Document-Filename": "cancel.txt",
            "Content-Type": "application/octet-stream",
        },
        content=b"cancel me",
    )
    job_id = resp.json()["job_id"]
    cancel = client.post(f"/api/documents/jobs/{job_id}/cancel")
    # Either cancelled (pending) or a truthful conflict once it reached ready.
    assert cancel.status_code in {200, 409}


def _upload_ready(client: TestClient, name: str, data: bytes) -> str:
    resp = client.post(
        "/api/documents",
        headers={"X-Document-Filename": name, "Content-Type": "application/octet-stream"},
        content=data,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _wait_ready(client, body["job_id"]) == "ready"
    return body["document_id"]


def test_delete_document_removes_from_library(client: TestClient) -> None:
    """Soft-delete (archive) removes the doc from the default list."""
    up = client.post(
        "/api/documents",
        headers={
            "X-Document-Filename": "to-delete.txt",
            "Content-Type": "application/octet-stream",
        },
        content=b"Delete me please.\n",
    )
    assert up.status_code == 200, up.text
    doc_id = up.json()["document_id"]
    job_id = up.json()["job_id"]
    assert _wait_ready(client, job_id) == "ready"

    listing = client.get("/api/documents")
    assert any(d["document_id"] == doc_id for d in listing.json()["documents"])

    deleted = client.post(
        f"/api/documents/{doc_id}/delete",
        json={"reason": "test_archive"},
    )
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["ok"] is True
    assert body["deleted"] is True

    after = client.get("/api/documents")
    assert not any(d["document_id"] == doc_id for d in after.json()["documents"])

    # REST alias also works (already deleted → 404-style access denial)
    again = client.delete(f"/api/documents/{doc_id}")
    assert again.status_code in {404, 400, 422}


def test_convert_and_download_flow(client: TestClient) -> None:
    doc_id = _upload_ready(client, "web-note.md", b"# Title\n\nBody paragraph.\n")
    resp = client.post(
        f"/api/documents/{doc_id}/convert",
        json={"target_format": "html"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    artifact = body["artifact"]
    assert artifact["artifact_id"]
    # No server path leaks in the API payload.
    assert "storage_path" not in artifact["manifest"]
    assert "export_path" not in artifact["manifest"]

    # Artifact appears in detail and is downloadable by opaque ID.
    detail = client.get(f"/api/documents/{doc_id}").json()["document"]
    assert any(a["artifact_id"] == artifact["artifact_id"] for a in detail["artifacts"])

    dl = client.get(f"/api/documents/artifacts/{artifact['artifact_id']}/download")
    assert dl.status_code == 200
    assert "text/html" in dl.headers.get("content-type", "")
    assert "attachment" in dl.headers.get("content-disposition", "")


def test_convert_missing_target_format_returns_400(client: TestClient) -> None:
    doc_id = _upload_ready(client, "note2.md", b"# X\n\nBody.\n")
    resp = client.post(f"/api/documents/{doc_id}/convert", json={})
    assert resp.status_code == 400


def test_convert_unknown_document_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/documents/00000000-0000-0000-0000-000000000000/convert",
        json={"target_format": "html"},
    )
    assert resp.status_code == 404


def test_download_unknown_artifact_returns_404(client: TestClient) -> None:
    resp = client.get(
        "/api/documents/artifacts/00000000-0000-0000-0000-000000000000/download"
    )
    assert resp.status_code == 404


def test_generate_creates_downloadable_document(client: TestClient) -> None:
    resp = client.post(
        "/api/documents/generate",
        json={
            "target_format": "markdown",
            "payload": {"title": "Gen", "sections": [{"heading": "H", "body": "B"}]},
            "output_name": "gen",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["document_id"] and body["job_id"]
    assert _wait_ready(client, body["job_id"]) == "ready"
    content = client.get(f"/api/documents/{body['document_id']}/content")
    assert content.status_code == 200
    assert "Gen" in content.json()["content"]["text"]


def test_generate_rejects_non_object_payload(client: TestClient) -> None:
    resp = client.post(
        "/api/documents/generate",
        json={"target_format": "markdown", "payload": "not-an-object"},
    )
    assert resp.status_code == 400


def test_merge_requires_two_documents(client: TestClient) -> None:
    resp = client.post("/api/documents/merge", json={"document_ids": ["only-one"]})
    assert resp.status_code == 400


def test_redact_requires_terms(client: TestClient) -> None:
    doc_id = _upload_ready(client, "note3.md", b"# X\n\nBody.\n")
    resp = client.post(f"/api/documents/{doc_id}/redact", json={"terms": []})
    assert resp.status_code == 400

