"""``/api/import/history/*``: the Memory page's "Bring your history" (REMAINING 4.2).

``POST .../preview`` takes the export (the body is the file: the ``.zip`` or
``conversations.json``), keeps it under ``<data dir>/history_import`` and
answers what it holds and a token; ``POST .../start`` imports it in a thread
of its own (``kazma_core.memory.history_import``); ``GET .../status`` says how
far it is; ``POST .../stop`` ends it between turns. One import at a time; the
file is deleted when its import ends, and any left over after an hour.

An administrator's (``platform_rbac._ADMIN_PREFIXES``), and refused on an
install with platform users (the memory is the install's; decision D1).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["create_history_import_router"]

#: The largest export the page takes (a ChatGPT export with images is big;
#: only its conversations.json is read).
MAX_UPLOAD_BYTES = 2 << 30
_KEEP_S = 3600.0
_REFUSAL = ("Bringing your history in works on a single-operator install: this install has "
            "platform users, and memory is the install's.")


class _Job:
    """The one import: what it holds, how far it is, and how it ended."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "idle"
        self.done = 0
        self.total = 0
        self.report: dict[str, Any] | None = None
        self.summary: dict[str, Any] | None = None
        self.error = ""
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def view(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self.state, "done": self.done, "total": self.total,
                    "report": self.report, "summary": self.summary, "error": self.error}


_JOB = _Job()
_UPLOADS: dict[str, tuple[Path, float, dict[str, Any]]] = {}
_UPLOADS_LOCK = threading.Lock()


def _folder() -> Path:
    from kazma_core.paths import data_dir

    folder = data_dir() / "history_import"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sweep(now: float) -> None:
    """Delete uploads older than an hour (never the one being imported)."""
    with _UPLOADS_LOCK:
        for token, (path, at, _summary) in list(_UPLOADS.items()):
            if now - at > _KEEP_S:
                path.unlink(missing_ok=True)
                _UPLOADS.pop(token, None)
    held = {str(p) for p, _a, _s in _UPLOADS.values()}
    for leftover in _folder().glob("upload-*"):
        try:
            if str(leftover) not in held and now - leftover.stat().st_mtime > _KEEP_S:
                leftover.unlink(missing_ok=True)
        except OSError:
            logger.debug("[history_import] could not remove %s", leftover, exc_info=True)


def _refusal() -> str | None:
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(_REFUSAL)


def _refused(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "code": code}, status_code=status)


def _run(token: str, path: Path) -> None:
    from kazma_core.memory import history_import

    try:
        history = history_import.read_export(path)

        def progress(done: int, total: int) -> None:
            with _JOB.lock:
                _JOB.done, _JOB.total = done, total

        report = history_import.import_history(history, progress=progress, should_stop=_JOB.stop.is_set)
        with _JOB.lock:
            _JOB.report = report.as_dict()
            _JOB.state = "stopped" if report.stopped else "done"
    except (history_import.ImportRefused, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("[history_import] the import failed", exc_info=True)
        with _JOB.lock:
            _JOB.state, _JOB.error = "failed", type(exc).__name__
    finally:
        path.unlink(missing_ok=True)
        with _UPLOADS_LOCK:
            _UPLOADS.pop(token, None)


def create_history_import_router() -> APIRouter:
    router = APIRouter(prefix="/api/import/history", tags=["memory"])

    @router.post("/preview")
    async def preview(request: Request) -> JSONResponse:
        """Keep the export and say what it holds."""
        refusal = await asyncio.to_thread(_refusal)
        if refusal:
            return _refused("single_operator", refusal, 409)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            return _refused("too_large", "the file is larger than Kazma takes", 413)
        token = secrets.token_urlsafe(16)
        path = (await asyncio.to_thread(_folder)) / f"upload-{token}"
        fh = await asyncio.to_thread(path.open, "wb")
        size, buffer = 0, bytearray()
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("too_large")
                buffer.extend(chunk)
                if len(buffer) >= 4 << 20:
                    await asyncio.to_thread(fh.write, bytes(buffer))
                    buffer.clear()
            if buffer:
                await asyncio.to_thread(fh.write, bytes(buffer))
        except ValueError:
            await asyncio.to_thread(fh.close)
            await asyncio.to_thread(path.unlink, True)
            return _refused("too_large", "the file is larger than Kazma takes", 413)
        finally:
            if not fh.closed:
                await asyncio.to_thread(fh.close)

        def read() -> dict[str, Any]:
            from kazma_core.memory import history_import

            _sweep(time.time())
            return history_import.read_export(path).summary()

        from kazma_core.memory.history_import import ImportRefused

        try:
            summary = await asyncio.to_thread(read)
        except ImportRefused as exc:
            await asyncio.to_thread(path.unlink, True)
            return _refused("not_an_export", str(exc), 422)
        with _UPLOADS_LOCK:
            _UPLOADS[token] = (path, time.time(), summary)
        return JSONResponse({"ok": True, "token": token, "summary": summary})

    @router.post("/start")
    async def start(request: Request) -> JSONResponse:
        """Import the export a preview kept (one import at a time)."""
        from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        refusal = await asyncio.to_thread(_refusal)
        if refusal:
            return _refused("single_operator", refusal, 409)
        token = str((body or {}).get("token") or "")
        with _UPLOADS_LOCK:
            held = _UPLOADS.get(token)
        if held is None:
            return _refused("unknown_upload", "choose the file again", 404)
        with _JOB.lock:
            if _JOB.state == "running":
                return _refused("busy", "an import is running", 409)
            _JOB.state, _JOB.done, _JOB.total = "running", 0, int(held[2].get("turns") or 0)
            _JOB.report, _JOB.summary, _JOB.error = None, held[2], ""
            _JOB.stop.clear()
            _JOB.thread = threading.Thread(target=_run, args=(token, held[0]), name="history-import", daemon=True)
            _JOB.thread.start()
        return JSONResponse({"ok": True, "state": "running"})

    @router.get("/status")
    def status() -> dict[str, Any]:
        return _JOB.view()

    @router.post("/stop")
    def stop() -> dict[str, Any]:
        _JOB.stop.set()
        return {"ok": True}

    return router
