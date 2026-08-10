"""Trusted attachment references for Web chat transports.

The browser may describe an attachment, but it must never choose a server
filesystem path.  Uploaded files are addressed by an opaque ID and resolved
only beneath :data:`ATTACHMENT_DIR`.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from kazma_gateway.agent_handler.attachments import (
    MAX_ATTACHMENT_COUNT,
    MAX_TOTAL_ATTACHMENT_BYTES,
)
from kazma_gateway.gateway import Attachment

logger = logging.getLogger(__name__)

ATTACHMENT_DIR = Path("kazma-data/attachments")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_UPLOAD_ID_RE = re.compile(r"^att_[0-9a-f]{32}$")


def store_uploaded_attachment(data: bytes, filename: str) -> str:
    """Store upload bytes and return an opaque, server-resolvable reference."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("attachment exceeds upload size limit")
    attachment_id = f"att_{uuid.uuid4().hex}"
    suffix = Path(filename).suffix
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    (ATTACHMENT_DIR / f"{attachment_id}{suffix}").write_bytes(data)
    return attachment_id


def _load_uploaded_attachment(attachment_id: Any) -> bytes | None:
    """Load an upload by its opaque ID, never by a caller-provided path."""
    if not isinstance(attachment_id, str) or not _UPLOAD_ID_RE.fullmatch(attachment_id):
        return None

    root = ATTACHMENT_DIR.resolve()
    matches = list(root.glob(f"{attachment_id}.*")) if root.exists() else []
    if len(matches) != 1:
        return None

    candidate = matches[0].resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    try:
        if candidate.stat().st_size > MAX_UPLOAD_BYTES:
            logger.warning("[chat-attachments] upload reference exceeds size limit: %s", attachment_id)
            return None
        return candidate.read_bytes()
    except OSError:
        logger.warning("[chat-attachments] upload reference could not be read: %s", attachment_id)
        return None


def _decode_supplied_data(value: Any) -> bytes | None:
    """Decode an explicitly supplied base64 payload, bounded like uploads."""
    if not isinstance(value, str):
        return None
    encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    if len(encoded) > (MAX_UPLOAD_BYTES * 4 // 3) + 4:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if len(data) <= MAX_UPLOAD_BYTES else None


def attachments_from_client_payload(raw_attachments: Any) -> list[Attachment]:
    """Normalize client attachment descriptors without trusting ``path`` fields.

    Accepted byte sources are an upload ``id`` issued by this server or an
    explicitly supplied base64 ``data`` value. URL-only attachments remain
    supported; their download is SSRF-guarded by ``build_user_content``.
    """
    if not isinstance(raw_attachments, list):
        return []

    attachments: list[Attachment] = []
    total_bytes = 0
    for raw in raw_attachments[:MAX_ATTACHMENT_COUNT]:
        if not isinstance(raw, dict):
            continue
        data = _decode_supplied_data(raw.get("data"))
        if data is None:
            data = _load_uploaded_attachment(raw.get("id"))
        if data is not None:
            if total_bytes + len(data) > MAX_TOTAL_ATTACHMENT_BYTES:
                logger.warning(
                    "[chat-attachments] ignored attachment exceeding aggregate byte limit"
                )
                continue
            total_bytes += len(data)
        if raw.get("path"):
            logger.warning("[chat-attachments] ignored client-supplied attachment path")
        url = raw.get("url")
        attachments.append(
            Attachment(
                kind=str(raw.get("kind") or "file"),
                mime=str(raw.get("mime") or "application/octet-stream"),
                filename=str(raw.get("filename") or ""),
                data=data,
                url=url if isinstance(url, str) else None,
            )
        )
    if len(raw_attachments) > MAX_ATTACHMENT_COUNT:
        logger.warning(
            "[chat-attachments] ignored %d attachment(s) beyond count limit",
            len(raw_attachments) - MAX_ATTACHMENT_COUNT,
        )
    return attachments
