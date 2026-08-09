"""Focused security tests for Web chat attachment handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kazma_gateway.agent_handler.attachments import _fetch_attachment_url
from kazma_ui import chat_attachments
from kazma_ui.routes_chat_upload import router as upload_router


def test_client_attachment_path_is_ignored(tmp_path, monkeypatch) -> None:
    """A browser descriptor cannot cause a server-side filesystem read."""
    monkeypatch.setattr(chat_attachments, "ATTACHMENT_DIR", tmp_path)

    attachments = chat_attachments.attachments_from_client_payload(
        [{
            "kind": "file",
            "mime": "text/plain",
            "filename": "secret.txt",
            "path": r"C:\Windows\win.ini",
        }]
    )

    assert len(attachments) == 1
    assert attachments[0].data is None
    assert attachments[0].url is None


def test_uploaded_attachment_id_resolves_without_client_path(tmp_path, monkeypatch) -> None:
    """Only an opaque server-issued upload ID resolves persisted bytes."""
    monkeypatch.setattr(chat_attachments, "ATTACHMENT_DIR", tmp_path)
    attachment_id = chat_attachments.store_uploaded_attachment(b"trusted upload", "notes.txt")

    attachments = chat_attachments.attachments_from_client_payload(
        [{
            "id": attachment_id,
            "kind": "file",
            "mime": "text/plain",
            "filename": "notes.txt",
            "path": r"C:\Windows\win.ini",
        }]
    )

    assert attachments[0].data == b"trusted upload"


def test_explicit_base64_attachment_data_is_accepted() -> None:
    attachments = chat_attachments.attachments_from_client_payload(
        [{
            "kind": "file",
            "mime": "text/plain",
            "filename": "inline.txt",
            "data": "dHJ1c3RlZCBieXRlcw==",
        }]
    )

    assert attachments[0].data == b"trusted bytes"


@pytest.mark.asyncio
async def test_upload_reads_no_more_than_configured_limit() -> None:
    """The upload endpoint bounds the incoming read before checking its size."""
    from kazma_ui.routes_chat_upload import upload_attachment

    file = MagicMock()
    file.read = AsyncMock(return_value=b"x" * (chat_attachments.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(HTTPException) as exc_info:
        await upload_attachment(file)

    assert getattr(exc_info.value, "status_code", None) == 413
    file.read.assert_awaited_once_with(chat_attachments.MAX_UPLOAD_BYTES + 1)


def test_upload_endpoint_returns_only_an_opaque_reference() -> None:
    app = FastAPI()
    app.include_router(upload_router)

    with patch(
        "kazma_ui.routes_chat_upload.store_uploaded_attachment",
        return_value="att_0123456789abcdef0123456789abcdef",
    ):
        response = TestClient(app).post(
            "/api/chat/upload",
            files={"file": ("notes.txt", b"trusted upload", "text/plain")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "att_0123456789abcdef0123456789abcdef",
        "kind": "file",
        "mime": "text/plain",
        "filename": "notes.txt",
    }


def test_attachment_fetch_checks_redirect_destination_before_request() -> None:
    redirect = MagicMock(status_code=302, headers={"location": "http://127.0.0.1/private"})

    with patch(
        "kazma_core.security.ssrf.validate_url",
        side_effect=[None, ValueError("private address")],
    ) as validate_url, patch(
        "httpx.get",
        return_value=redirect,
    ) as get:
        with pytest.raises(ValueError, match="private address"):
            _fetch_attachment_url("https://public.example/image.png")

    assert validate_url.call_count == 2
    get.assert_called_once_with(
        "https://public.example/image.png",
        timeout=30.0,
        follow_redirects=False,
    )


def test_attachment_fetch_validates_and_follows_safe_redirect() -> None:
    redirect = MagicMock(status_code=302, headers={"location": "/image.png"})
    success = MagicMock(status_code=200, headers={}, content=b"image")
    success.raise_for_status = MagicMock()

    with patch(
        "kazma_core.security.ssrf.validate_url",
    ) as validate_url, patch(
        "httpx.get",
        side_effect=[redirect, success],
    ) as get:
        assert _fetch_attachment_url("https://public.example/start") == b"image"

    assert [call.args[0] for call in validate_url.call_args_list] == [
        "https://public.example/start",
        "https://public.example/image.png",
    ]
    assert all(call.kwargs["block_unresolved"] is True for call in validate_url.call_args_list)
    assert all(call.kwargs["follow_redirects"] is False for call in get.call_args_list)
