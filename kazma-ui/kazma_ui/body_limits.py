"""One body limit for every request, before the body is read (2026-11-15).

``UploadSizeLimitMiddleware`` (FA-134) bounded the two upload routes only.
Everything else read whatever arrived: ``read_json`` called
``request.body()`` with no cap, FastAPI parsed a Pydantic or ``dict`` body of
any size into memory, and ``POST /api/auth/login`` -- open before sign-in --
read a 64 MB chunked body in full before answering 401.

This ASGI layer sits outside every gate and route and gives each request one
limit (:func:`body_limit`):

- a route declared in :func:`_route_body_limits` gets its own (an upload, a
  document, a history export, a settings backup, a file the IDE writes, a
  webhook) -- each larger limit is there because a feature needs it;
- a route open before sign-in (``auth.is_always_open``, or a path the sign-in
  gate does not cover) gets :data:`OPEN_BODY_LIMIT`;
- every other route :data:`DEFAULT_BODY_LIMIT`.

A declared ``Content-Length`` past the limit is answered 413 with nothing
read; a body with no declared length is counted as it is read and the
request ends with 413 the moment it passes the limit. A route keeps any finer
check of its own (the document upload's configured size, a backup's 10 MB).
``tests/test_request_body_limits.py`` holds the table to the app's routes.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_BODY_LIMIT",
    "OPEN_BODY_LIMIT",
    "BodySizeLimitMiddleware",
    "body_limit",
]

#: Routes open before sign-in: a sign-in, a sign-out (a few hundred bytes).
OPEN_BODY_LIMIT = 64 * 1024

#: Every signed-in route not declared below: a chat message, a settings form,
#: a JSON request (the documents API's own cap is the same 2 MiB).
DEFAULT_BODY_LIMIT = 2 * 1024 * 1024

#: A document upload. The route checks the configured intake size
#: (``documents.intake.max_bytes``, 50 MiB by default) itself; this is the
#: ceiling no configured size can pass.
DOCUMENT_UPLOAD_CEILING = 2 * 1024 * 1024 * 1024

#: A webhook Telegram or Meta posts before any sign-in: an update, a message.
WEBHOOK_BODY_LIMIT = 1024 * 1024

#: A JSON body carrying a whole file the IDE writes or compares: the editor's
#: largest file, with room for JSON's escapes (and two files for a diff).
_IDE_FILE_BODY = 4


def _route_body_limits() -> dict[tuple[str, str], int]:
    """``(METHOD, route path)`` -> the largest body that route takes, for the
    routes a feature needs more than :data:`DEFAULT_BODY_LIMIT` (or, open
    before sign-in, more than :data:`OPEN_BODY_LIMIT`). Read at each call:
    the upload limits are ``upload_limits.UPLOAD_LIMITS`` (FA-134's table)."""
    from kazma_core.ide.service import MAX_EDITOR_FILE_BYTES
    from kazma_core.settings_restore import MAX_BACKUP_BYTES

    from kazma_ui import upload_limits
    from kazma_ui.history_import_api import MAX_UPLOAD_BYTES as HISTORY_EXPORT_BYTES

    ide_file = _IDE_FILE_BODY * MAX_EDITOR_FILE_BYTES
    limits: dict[tuple[str, str], int] = {
        ("POST", path): limit + upload_limits.MULTIPART_ALLOWANCE
        for path, limit in upload_limits.UPLOAD_LIMITS.items()
    }
    limits.update({
        ("POST", "/api/documents"): DOCUMENT_UPLOAD_CEILING,
        ("POST", "/api/import/history/preview"): HISTORY_EXPORT_BYTES,
        ("POST", "/api/settings/system/restore"): MAX_BACKUP_BYTES,
        # A dataset document is up to 2,000,000 bytes stored
        # (x_api.datasets); the page's JSON around it needs room.
        ("POST", "/api/x/datasets"): 4 * 1024 * 1024,
        ("POST", "/api/ide/write"): ide_file,
        ("POST", "/api/ide/apply_patch"): ide_file,
        ("POST", "/api/ide/apply_patch_set"): ide_file,
        ("POST", "/api/ide/diff"): ide_file,
        ("POST", "/api/webhooks/telegram"): WEBHOOK_BODY_LIMIT,
        ("POST", "/api/webhooks/whatsapp"): WEBHOOK_BODY_LIMIT,
    })
    return limits


@lru_cache(maxsize=256)
def _route_pattern(template: str) -> re.Pattern[str]:
    from starlette.routing import compile_path

    return compile_path(template)[0]


def _open_before_sign_in(path: str) -> bool:
    from kazma_ui.auth import is_always_open, is_sensitive_path

    return is_always_open(path) or not is_sensitive_path(path)


def body_limit(method: str, path: str) -> int:
    """The largest body ``method path`` takes (*path* as the router matches it)."""
    method = (method or "").upper()
    for (route_method, template), limit in _route_body_limits().items():
        if route_method == method and _route_pattern(template).match(path):
            return limit
    return OPEN_BODY_LIMIT if _open_before_sign_in(path) else DEFAULT_BODY_LIMIT


class _BodyTooLargeError(Exception):
    """A body with no declared length passed its route's limit."""


def _declared_length(scope: dict[str, Any]) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _megabytes(limit: int) -> str:
    if limit >= 1024 * 1024:
        return f"{limit / (1024 * 1024):g} MB"
    return f"{max(1, limit // 1024)} KB"


async def _refuse(send: Any, limit: int) -> None:
    message = f"The request body is too large (at most {_megabytes(limit)} here)."
    body = json.dumps({
        "ok": False,
        "error": message,
        "detail": message,
        "code": "body_too_large",
        "max_bytes": limit,
    }).encode("utf-8")
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Refuses a body past its route's limit before it is read or spooled."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared <= OPEN_BODY_LIMIT:
            # Within the smallest limit any route has: no lookup on the hot
            # path (tests hold every declared limit at or above it).
            await self.app(scope, receive, send)
            return
        from kazma_ui.request_path import judged_path

        path = judged_path(scope)
        limit = body_limit(str(scope.get("method") or ""), path)
        if declared is not None:
            if declared > limit:
                logger.info("[body] refused %s %s: declared %d bytes, limit %d",
                            scope.get("method"), path, declared, limit)
                await _refuse(send, limit)
                return
            # The server holds the body to its declared length.
            await self.app(scope, receive, send)
            return

        seen = 0
        started = False
        refused = False

        async def _counted_receive() -> dict[str, Any]:
            nonlocal seen, refused
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > limit and not refused:
                    # Answered here: FastAPI turns an error raised while it
                    # parses a body into its own 400, which is dropped.
                    refused = True
                    logger.info("[body] refused %s %s: body passed %d bytes",
                                scope.get("method"), path, limit)
                    if not started:
                        await _refuse(send, limit)
                    raise _BodyTooLargeError()
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
        except _BodyTooLargeError:
            # Answered 413 where it was found; the route stopped there.
            logger.debug("[body] %s stopped at its refused body", path)
        except BaseExceptionGroup as group:
            # The refusal wrapped by a middleware's task group.
            if not refused or group.subgroup(_BodyTooLargeError) is None:
                raise
            logger.debug("[body] %s stopped at its refused body", path)
