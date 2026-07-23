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
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

ATTACHMENT_DIR = Path("kazma-data/attachments")

# 20 MB cap to match the vision tool / Telegram ceiling.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

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

    Returns ``{id, kind, mime, filename, path}``. The client sends the
    descriptor (minus ``path``) as part of the next chat-stream request.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

    mime = (file.content_type or "application/octet-stream").lower()
    kind = _classify(mime)
    original = file.filename or f"upload_{uuid.uuid4().hex[:8]}"
    ext = Path(original).suffix or ""
    stored_name = f"{Path(original).stem}_{uuid.uuid4().hex[:6]}{ext}"

    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    dest = ATTACHMENT_DIR / stored_name
    dest.write_bytes(data)

    attach_id = f"att_{uuid.uuid4().hex[:12]}"
    logger.info(
        "[chat-upload] stored %s (%s, %d bytes) as %s",
        original, mime, len(data), dest,
    )
    return {
        "id": attach_id,
        "kind": kind,
        "mime": mime,
        "filename": original,
        "path": str(dest.resolve()),
    }
