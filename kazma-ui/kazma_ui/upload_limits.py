"""Upload routes refuse an oversized body before it is read (2026-11, FA-134).

A route that takes ``UploadFile = File(...)`` is called only after Starlette
has parsed the whole multipart body and spooled the file to disk; the route's
own size check (``file.read(limit + 1)``) then refused a 2 GB upload that had
already been written to the temporary folder. This ASGI layer sits in front
of every such route (:data:`UPLOAD_LIMITS`, held to the routes by
``tests/test_upload_limits.py``):

- a declared ``Content-Length`` past the route's limit is answered 413 with
  nothing read;
- a body with no length (chunked) is counted as it is read, and the request
  ends with 413 the moment it passes the limit.

The limit is the route's file limit plus :data:`MULTIPART_ALLOWANCE` for the
form's boundaries and other fields; the route still checks the file itself.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

#: Room for the multipart framing and the form's small fields.
MULTIPART_ALLOWANCE = 64 * 1024

#: The largest audio file ``POST /api/voice/stt`` takes.
MAX_STT_BYTES = 25 * 1024 * 1024

#: Every route that takes an uploaded file, with the largest file it takes.
UPLOAD_LIMITS: dict[str, int] = {
    "/api/chat/upload": MAX_UPLOAD_BYTES,
    "/api/voice/stt": MAX_STT_BYTES,
}


class _BodyTooLarge(Exception):
    """A body with no declared length passed its route's limit."""


def _declared_length(scope: dict[str, Any]) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _refuse(send: Any, limit: int) -> None:
    body = json.dumps({
        "error": "upload_too_large",
        "detail": f"File too large (max {limit // (1024 * 1024)} MB)",
        "max_bytes": limit,
    }).encode("utf-8")
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})


class UploadSizeLimitMiddleware:
    """Refuses an upload past its route's limit before the body is spooled."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        limit = UPLOAD_LIMITS.get(scope.get("path") or "") if scope.get("type") == "http" else None
        if limit is None or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        allowed = limit + MULTIPART_ALLOWANCE
        declared = _declared_length(scope)
        if declared is not None and declared > allowed:
            logger.info("[upload] refused %s: declared %d bytes, limit %d", scope.get("path"), declared, allowed)
            await _refuse(send, limit)
            return

        seen = 0
        started = False
        refused = False

        async def _counted_receive() -> dict[str, Any]:
            nonlocal seen, refused
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > allowed and not refused:
                    # Answered here: FastAPI turns an error raised while it
                    # parses the form into its own 400, which is dropped.
                    refused = True
                    logger.info("[upload] refused %s: body passed %d bytes", scope.get("path"), allowed)
                    if not started:
                        await _refuse(send, limit)
                    raise _BodyTooLarge()
            return message

        async def _watched_send(message: dict[str, Any]) -> None:
            nonlocal started
            if refused:
                return
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, _counted_receive, _watched_send)
        except _BodyTooLarge:
            # Answered 413 where it was found; the route stopped there.
            logger.debug("[upload] %s stopped at its refused body", scope.get("path"))
