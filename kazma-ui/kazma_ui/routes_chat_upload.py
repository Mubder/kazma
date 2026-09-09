"""Chat attachment upload endpoint for the Web UI.

Provides:
  POST /api/chat/upload  — accept a media/file upload, persist it under
  ``kazma-data/attachments/``, and return a descriptor the chat client
  attaches to the next ``/api/chat/stream`` turn.

The returned descriptor mirrors the :class:`~kazma_gateway.gateway.Attachment`
shape so the SSE handler and the gateway path both consume the same fields.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES, store_uploaded_attachment
from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Coarse classification by MIME prefix.
def _classify(mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "file"


def _sniff_mime(data: bytes, declared: str) -> str:
    """Lightweight magic-byte check for common upload types (audit L-13).

    The client-declared Content-Type used to be stored verbatim; a renamed
    executable or a mislabelled file kept its false label downstream. Only
    OVERRIDES on a confident signature mismatch — unknown bytes keep the
    declared type (no false positives for rare formats).
    """
    sig = data[:16]
    checks = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF", None),  # handled below (WEBP/WAV/AVI by extension bytes)
        (b"%PDF-", "application/pdf"),
        (b"PK\x03\x04", None),  # zip-family (docx/xlsx/epub…) — keep declared
        (b"\x1a\x45\xdf\xa3", "video/webm"),  # often matroska; webm common
        (b"ID3", "audio/mpeg"),
        (b"OggS", "audio/ogg"),
        (b"fLaC", "audio/flac"),
        (b"\x00\x00\x00\x18ftyp", "video/mp4"),
        (b"\x00\x00\x00\x20ftyp", "video/mp4"),
    )
    for prefix, mime in checks:
        if sig.startswith(prefix):
            if prefix == b"RIFF" and data[8:12] == b"WEBP":
                return "image/webp"
            return mime or declared
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if sig.startswith(b"BM") and declared.startswith("image/"):
        return "image/bmp"
    return declared


@router.post("/upload", dependencies=[Depends(rate_limit("chat_upload", 20))])
async def upload_attachment(file: UploadFile = File(...)) -> dict[str, Any]:
    """Persist an uploaded file and return an attachment descriptor.

    Returns ``{id, kind, mime, filename}``. The opaque ``id`` is the only
    server-side file reference the client may send with a chat turn.
    """
    # Bound the read itself so a missing or dishonest Content-Length cannot
    # make an upload consume arbitrary process memory before rejection.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

    declared = (file.content_type or "application/octet-stream").lower()
    mime = _sniff_mime(data, declared)
    kind = _classify(mime)
    original = file.filename or "upload"
    attach_id = store_uploaded_attachment(data, original)
    logger.info(
        "[chat-upload] stored %s (%s, %d bytes) as %s",
        original, mime, len(data), attach_id,
    )
    return {
        "id": attach_id,
        "kind": kind,
        "mime": mime,
        "filename": original,
    }
