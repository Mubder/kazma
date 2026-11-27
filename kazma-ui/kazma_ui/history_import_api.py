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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kazma_core.errors import refusal_message
from kazma_core.spaces.scope import bound_asker

logger = logging.getLogger(__name__)

__all__ = ["create_history_import_router"]

#: The largest export the page takes (a ChatGPT export with images is big;
#: only its conversations.json is read).
MAX_UPLOAD_BYTES = 2 << 30
_KEEP_S = 3600.0
#: How long an import waits while whose it is cannot be read (the database is
#: away), asking again every few seconds: the file is kept meanwhile.
_ACCOUNTS_WAIT_S = 30 * 60.0
_ACCOUNTS_ASK_S = 5.0
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


#: Each space's import (Spaces S1-09): a history becomes memories of the
#: space it was brought into, and its progress is that space's to see. One
#: import runs at a time for the install (it embeds every turn).
_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()
#: Held across "is an import running anywhere?" and the claim of a space's
#: job, so the answer cannot change between the two: each space has its own
#: job and its own lock, and two spaces could both see "none" and both start.
_START_LOCK = threading.Lock()
#: token -> (file, when, summary, the space that uploaded it, the person who
#: did: ``""`` when no one is bound).
_UPLOADS: dict[str, tuple[Path, float, dict[str, Any], str, str]] = {}
_UPLOADS_LOCK = threading.Lock()


def _space() -> str:
    """The space this request is for (the owner's on an install without Spaces)."""
    from kazma_core.spaces.scope import require_space_id

    return require_space_id()


def _job(space: str) -> _Job:
    with _JOBS_LOCK:
        return _JOBS.setdefault(space, _Job())


def _any_running() -> bool:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    return any(job.view()["state"] == "running" for job in jobs)


def _claim(space: str, token: str, held: tuple[Path, float, dict[str, Any], str, str]) -> bool:
    """Start *space*'s import of *held*, unless an import runs anywhere.
    True when it was started. One lock for the check and the claim."""
    with _START_LOCK:
        if _any_running():
            return False
        job = _job(space)
        with job.lock:
            job.state, job.done, job.total = "running", 0, int(held[2].get("turns") or 0)
            job.report, job.summary, job.error = None, held[2], ""
            job.stop.clear()
            job.thread = threading.Thread(
                target=_run, args=(token, held[0], space, job, held[4]), name="history-import", daemon=True)
            job.thread.start()
    return True


def _folder() -> Path:
    from kazma_core.paths import data_dir

    folder = data_dir() / "history_import"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sweep(now: float) -> None:
    """Delete uploads older than an hour (never the one being imported)."""
    with _UPLOADS_LOCK:
        for token, (path, at, _summary, _space_id, _person) in list(_UPLOADS.items()):
            if now - at > _KEEP_S:
                path.unlink(missing_ok=True)
                _UPLOADS.pop(token, None)
    held = {str(p) for p, _a, _s, _sp, _who in _UPLOADS.values()}
    for leftover in _folder().glob("upload-*"):
        try:
            if str(leftover) not in held and now - leftover.stat().st_mtime > _KEEP_S:
                leftover.unlink(missing_ok=True)
        except OSError:
            logger.debug("[history_import] could not remove %s", leftover, exc_info=True)


def _refusal() -> str | None:
    """Refused on the older multi-user mode, where memory is the install's.
    With Spaces an import goes into the space of the person who brings it."""
    from kazma_core.security.single_operator import single_operator_refusal
    from kazma_core.spaces.scope import spaces_enforced

    return None if spaces_enforced() else single_operator_refusal(_REFUSAL)


def _refused(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "code": code}, status_code=status)


def _why(exc: BaseException) -> str:
    """The code of a failed import, which the page words in its language."""
    from kazma_core.memory.history_import import ImportRefused
    from kazma_core.spaces.identity import AskerLeft, IdentityUnavailable

    if isinstance(exc, AskerLeft):
        return "left_space"
    if isinstance(exc, IdentityUnavailable):
        return "accounts_unavailable"
    return "not_an_export" if isinstance(exc, ImportRefused) else "failed"


def _once_readable(ask: Callable[[], Any], job: _Job) -> Any:
    """*ask*'s answer, waiting while the accounts cannot be read: an outage
    is not a failed import, and the file is the person's only copy here. None
    when the import was stopped meanwhile."""
    from kazma_core.spaces.identity import IdentityUnavailable

    until = time.monotonic() + _ACCOUNTS_WAIT_S
    while True:
        try:
            return ask()
        except IdentityUnavailable:
            if time.monotonic() >= until:
                raise
            logger.info("[history_import] whose import this is cannot be read yet; waiting")
        if job.stop.wait(_ACCOUNTS_ASK_S):
            return None


def _run(token: str, path: Path, space: str, job: _Job, person: str = "") -> None:
    """The import, in a thread of its own: in *space* as *person* (S4-01: a
    bare thread holds no scope, so it ran with none, and its turns named
    nobody), who brought the history. Refused when they left meanwhile: what
    a person brings in is never anyone else's. Asked again while it runs."""
    from kazma_core.memory import history_import
    from kazma_core.spaces.identity import AskerLeft, IdentityUnavailable, is_member
    from kazma_core.spaces.row_work import resolve_act_of
    from kazma_core.spaces.scope import spaces_enforced

    asked = time.monotonic()
    left = False

    def over() -> bool:
        """Whether the import ends between turns: stopped, or its person left
        (asked every few seconds; people that cannot be read end nothing)."""
        nonlocal asked, left
        if job.stop.is_set():
            return True
        if time.monotonic() - asked >= _ACCOUNTS_ASK_S:
            asked = time.monotonic()
            try:
                left = spaces_enforced() and not is_member(person, space)
            except IdentityUnavailable:
                logger.debug("[history_import] whose import this is cannot be read now", exc_info=True)
        return left

    try:
        as_its_person = _once_readable(lambda: resolve_act_of(space, person), job)
        if as_its_person is None:
            with job.lock:
                job.state = "stopped"
            return
        history = history_import.read_export(path)

        def progress(done: int, total: int) -> None:
            with job.lock:
                job.done, job.total = done, total

        with as_its_person():
            report = history_import.import_history(
                history, tenant_id=space, progress=progress, should_stop=over)
        if left:
            raise AskerLeft("the person who brought this history left its space while it was imported")
        with job.lock:
            job.report = report.as_dict()
            job.state = "stopped" if report.stopped else "done"
    except (AskerLeft, history_import.ImportRefused, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.warning("[history_import] the import failed", exc_info=True)
        with job.lock:
            job.state, job.error = "failed", _why(exc)
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
        size, buffer, received = 0, bytearray(), False
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
            received = True
        except ValueError:
            return _refused("too_large", "the file is larger than Kazma takes", 413)
        finally:
            await asyncio.to_thread(fh.close)
            if not received:
                # Too large, or the client went away while sending: no half
                # of an export stays on disk.
                await asyncio.to_thread(path.unlink, True)

        def read() -> dict[str, Any]:
            from kazma_core.memory import history_import

            _sweep(time.time())
            return history_import.read_export(path).summary()

        from kazma_core.memory.history_import import ImportRefused

        try:
            summary = await asyncio.to_thread(read)
        except ImportRefused as exc:
            await asyncio.to_thread(path.unlink, True)
            return _refused("not_an_export", refusal_message(exc), 422)
        with _UPLOADS_LOCK:
            _UPLOADS[token] = (path, time.time(), summary, _space(), bound_asker() or "")
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
        space = _space()
        with _UPLOADS_LOCK:
            held = _UPLOADS.get(token)
        # An upload is its space's: another space's token is no upload here.
        if held is None or held[3] != space:
            return _refused("unknown_upload", "choose the file again", 404)
        if not _claim(space, token, held):
            return _refused("busy", "an import is running", 409)
        return JSONResponse({"ok": True, "state": "running"})

    @router.get("/status")
    def status() -> dict[str, Any]:
        return _job(_space()).view()

    @router.post("/stop")
    def stop() -> dict[str, Any]:
        _job(_space()).stop.set()
        return {"ok": True}

    return router
