"""The Files panel of the Documents page (Spaces S2-12, piece 7): the files
of the bound space's own folder, and moving one's own between one's own
space and a shared one (``kazma_core.spaces.file_moves``).

* ``GET /api/space-files?offset=&limit=`` -- a page of the bound space's
  files: each one's path, size, time, whether it can move and whether the
  asker shared it (``mine``), and the lines of the files taken out. Only a
  plain space has the panel (``available``).
* ``POST /api/space-files/move`` -- ``{"path", "to_space"}``: move the
  asker's own file to the same place in ``to_space``. Every refusal is a code
  the page words (:data:`REFUSALS`, ``documents.js``): the file is where it
  was, but for ``changed_meanwhile`` (look in both spaces) and ``unfinished``
  (Kazma ends it by itself).

Both are the ``documents`` feature's routes (``spaces.features``): a person
with Documents off has neither the panel nor the routes. The upload size
limit applies to a moved file. A move is always the person's own click.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.spaces.file_moves import FileMoveRefused, move_file
from kazma_core.spaces.files import SpaceKindUnknown
from kazma_core.spaces.identity import IdentityUnavailable, read_registry
from kazma_core.spaces.shared_files import SharedFilesUnreadable, list_files

from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.rate_limit import rate_limit

__all__ = ["REFUSALS", "create_space_files_router"]

logger = logging.getLogger(__name__)

#: Every refusal of the two routes; the page words each
#: (``documents.files.err.<code>``, held by ``tests/test_file_moves.py``).
REFUSALS: tuple[str, ...] = (
    "bad_name", "busy", "changed_meanwhile", "changed_since", "disk_full", "identity_unavailable", "in_use",
    "move_failed", "move_in_progress", "name_taken", "name_too_long", "not_a_file", "not_between_own_and_shared",
    "not_found", "not_the_sharer", "not_yours", "owners_space", "project_folder", "space_limit", "spaces_off", "too_large",
    "unfinished",
)


def _asker() -> str:
    from kazma_core.spaces.scope import bound_asker

    return bound_asker() or ""


def _refused(code: str, status: int) -> JSONResponse:
    headers = {"Retry-After": "5"} if status == 503 else None
    return JSONResponse({"ok": False, "error": code, "code": code}, status_code=status, headers=headers)


def _named(lines: list[dict[str, str]]) -> list[dict[str, str]]:
    """The taken-out lines with the name of who took each out."""
    if not lines:
        return []
    try:
        names = {key: str(value.get("name") or "") for key, value in read_registry()["accounts"].items()}
    except IdentityUnavailable:
        names = {}  # the lines stay; who took each out is shown when accounts can be read
    return [{**line, "by_name": names.get(line["by"], "")} for line in lines]


def _listed(offset: int, limit: int) -> dict[str, Any]:
    from kazma_core.spaces.scope import require_space_id

    listed = list_files(require_space_id(), by=_asker(), offset=offset, limit=limit)
    return {**listed, "taken_out": _named(listed["taken_out"])}


def create_space_files_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/space-files")
    def space_files(offset: int = 0, limit: int = 50) -> JSONResponse:
        # A plain def: the folder is read in a worker thread, in the scope
        # the middleware bound.
        from kazma_core.spaces.scope import spaces_enforced

        if not spaces_enforced():
            return JSONResponse({"ok": True, "available": False, "files": [], "total": 0, "taken_out": []})
        try:
            return JSONResponse({"ok": True, **_listed(offset, limit)})
        except (IdentityUnavailable, SpaceKindUnknown):
            return _refused("identity_unavailable", 503)
        except SharedFilesUnreadable:
            logger.warning("[spaces] a space's folder could not be read for its Files panel")
            return _refused("files_unreadable", 503)

    @router.post("/api/space-files/move", dependencies=[Depends(rate_limit("file_move", 10))])
    async def move_route(request: Request) -> JSONResponse:
        import asyncio

        from kazma_core.spaces.scope import require_space_id, spaces_enforced

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        if not spaces_enforced():
            return _refused("spaces_off", 409)
        by = _asker()
        if not by:
            return _refused("not_yours", 403)
        try:
            moved = await asyncio.to_thread(
                move_file, str(body.get("path") or ""), from_space=require_space_id(),
                to_space=str(body.get("to_space") or "").strip(), by=by, max_bytes=MAX_UPLOAD_BYTES)
        except FileMoveRefused as exc:
            return _refused(exc.code, exc.status)
        except (IdentityUnavailable, SpaceKindUnknown):
            return _refused("identity_unavailable", 503)
        return JSONResponse({"ok": True, **moved})

    return router
