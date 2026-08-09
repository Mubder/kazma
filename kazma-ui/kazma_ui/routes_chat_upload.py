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

from fastapi import APIRouter, File, HTTPException, UploadFile

from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES, store_uploaded_attachment

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


@router.post("/upload")
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

    mime = (file.content_type or "application/octet-stream").lower()
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
