"""Count, export and erase one space (Spaces S1-27).

* ``GET /api/spaces/{space_id}/counts`` -- how much the space holds, store by
  store (numbers only): the admin (plan 4.10, "counts of work") or a member.
* ``POST /api/spaces/{space_id}/export`` -- the space as a zip sealed with a
  password the person chooses (``{"password": ...}``, decision N10;
  ``security.sealed_zip``): its owner only. The admin never exports a
  member's space (4.10).
* ``DELETE /api/spaces/{space_id}`` -- erase it: an owner of a shared space,
  or the admin for the closed personal space of a person they deleted.

Moving a space to another install (S4-06, ``spaces.space_move``):

* ``POST /api/spaces/{space_id}/move`` -- the shared space as one file sealed
  whole with a password the person chooses (``{"password": ...}``;
  ``security.sealed_file``): its owner only.
* ``POST /api/spaces/import`` -- the admin brings a bundle in: a form with
  ``bundle`` (the file), ``password`` and ``owner`` (the person who will own
  it here).

Who is in a shared space (S2-12):

* ``GET /api/spaces/{space_id}/members`` -- its members (id, name, role): a
  member or the admin.
* ``POST /api/spaces/{space_id}/members`` -- the admin adds a person
  (``{"account_id": ...}``); they are told at their next page load.
* ``DELETE /api/spaces/{space_id}/members/{account_id}`` -- its owner or the
  admin takes a member out.
* ``POST /api/spaces/{space_id}/leave`` -- the caller leaves.

A member who goes, either way, loses every fact under their own memory entry
in the space (N36); their messages stay (``people.leave_space``).

The space is named in the path, so the routes are ``install`` routes
(``route_scopes``) and check the caller against the space themselves; an
erase in progress (``erasing_at``) takes the space's members away, so the
checks read the memberships, not :func:`identity.is_member`. Only once Spaces
is enforced (409 ``spaces_off`` before). The stores are read in a worker
thread (plain ``def`` routes; the export reads its body, then hands over).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from kazma_core.errors import validation_error
from kazma_core.security.sealed_zip import MIN_PASSWORD
from kazma_core.spaces import people, space_data, space_move
from kazma_core.spaces.identity import IdentityUnavailable, read_registry
from kazma_core.spaces.people import PeopleRefused
from kazma_core.spaces.scope import spaces_enforced
from starlette.background import BackgroundTask
from starlette.formparsers import MultiPartException

from kazma_ui import auth, space_binding
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.session_manager import get_session_manager

__all__ = ["create_spaces_router"]

logger = logging.getLogger(__name__)


def _answer(code: str, status: int, message: str = "") -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "message": message}, status_code=status)


def _role(account: str | None, space_id: str) -> str | None:
    """*account*'s role in *space_id* as the memberships record it."""
    if not account:
        return None
    for membership in read_registry()["memberships"]:
        if membership.get("account") == account and membership.get("space") == space_id:
            return str(membership.get("role") or "member")
    return None


def _refusal(request: Request, space_id: str, action: str) -> JSONResponse | None:
    """Why the caller may not *action* (count, export, erase) the space."""
    if not spaces_enforced():
        return _answer("spaces_off", 409, "This is done once Spaces is on for this install.")
    principal = auth.get_request_principal(request)
    if auth.token_below_admin(principal):
        # Every API token is the owner's account: an operator token, handed
        # to another agent, moved the owner's shared spaces out with the
        # secrets in them, sealed with a password of its own choosing.
        return _answer("own_sign_in_only", 403, "This is done with your own sign-in, not an API token.")
    account = space_binding.account_of(principal)
    role = _role(account, space_id)
    admin = auth.admin_decision(request) == "ok"
    space = read_registry()["spaces"].get(space_id) or {}
    if action in ("count", "members") and (admin or role):
        return None
    if action == "add" and admin:
        return None
    if action == "remove" and (admin or role == "owner"):
        return None
    if action == "leave" and role:
        return None
    if action == "export" and role == "owner":
        return None
    if action == "erase":
        closed = bool(space.get("account_deleted_at"))
        if (role == "owner" and not space.get("personal_of")) or (admin and closed):
            return None
    # One answer for a space that does not exist and one the caller may not
    # act on: the refusal never says which.
    return _answer("not_yours", 403, "That is not yours to do in that space.")


def _guarded(request: Request, space_id: str, action: str, run: Any) -> Any:
    try:
        refused = _refusal(request, space_id, action)
        if refused:
            return refused
        return run()
    except space_data.SpaceDataRefused as exc:
        return _answer(exc.code, exc.status, validation_error(exc))
    except space_move.SpaceMoveRefused as exc:
        return _answer(exc.code, exc.status, validation_error(exc))
    except PeopleRefused as exc:
        return _answer(exc.code, exc.status, validation_error(exc))
    except IdentityUnavailable:
        return JSONResponse({"ok": False, "error": "identity_unavailable"}, status_code=503,
                            headers={"Retry-After": "5"})


def create_spaces_router() -> APIRouter:
    router = APIRouter(prefix="/api/spaces", tags=["spaces"])

    @router.get("/{space_id}/counts")
    def counts(space_id: str, request: Request) -> Any:
        def _run() -> Any:
            account = space_binding.account_of(auth.get_request_principal(request))
            space = read_registry()["spaces"].get(space_id) or {}
            # What the page offers: export to an owner, delete to an owner of
            # a shared space (the same rules :func:`_refusal` applies).
            return JSONResponse({"ok": True, **space_data.count_space(space_id),
                                 "name": str(space.get("name") or ""),
                                 "personal": bool(space.get("personal_of")),
                                 "role": _role(account, space_id) or ""})

        return _guarded(request, space_id, "count", _run)

    @router.get("/{space_id}/members")
    def members(space_id: str, request: Request) -> Any:
        def _run() -> Any:
            account = space_binding.account_of(auth.get_request_principal(request))
            registry = read_registry()
            space = registry["spaces"].get(space_id) or {}
            names = {key: str(value.get("name") or "") for key, value in registry["accounts"].items()}
            listed = [
                {"id": str(m.get("account")), "name": names.get(str(m.get("account")), ""),
                 "role": str(m.get("role") or "member")}
                for m in registry["memberships"] if m.get("space") == space_id
            ]
            return JSONResponse({
                "ok": True, "members": sorted(listed, key=lambda m: (m["role"] != "owner", m["name"].lower())),
                "me": account or "", "role": _role(account, space_id) or "",
                "personal": bool(space.get("personal_of")),
            })

        return _guarded(request, space_id, "members", _run)

    @router.post("/{space_id}/members")
    async def add_member(space_id: str, request: Request) -> Any:
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        account_id = str(body.get("account_id") or "") if isinstance(body, dict) else ""

        def _run() -> Any:
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            people.add_member(space_id, account_id, by=by)
            return JSONResponse({"ok": True}, status_code=201)

        return await asyncio.to_thread(_guarded, request, space_id, "add", _run)

    @router.delete("/{space_id}/members/{account_id}")
    def remove_member(space_id: str, account_id: str, request: Request) -> Any:
        def _run() -> Any:
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            return JSONResponse({"ok": True, **people.remove_member(space_id, account_id, by=by)})

        return _guarded(request, space_id, "remove", _run)

    @router.post("/{space_id}/leave")
    def leave(space_id: str, request: Request) -> Any:
        def _run() -> Any:
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            return JSONResponse({"ok": True, **people.leave_space(space_id, by=by)})

        return _guarded(request, space_id, "leave", _run)

    @router.post("/{space_id}/export")
    async def export(space_id: str, request: Request) -> Any:
        # Sealed with a password the person chooses (decision N10): the zip
        # holds everything their space holds.
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        password = str(body.get("password") or "") if isinstance(body, dict) else ""

        def _run() -> Any:
            if len(password) < MIN_PASSWORD:
                return _answer("password_too_short", 400,
                               f"The export's password is at least {MIN_PASSWORD} characters.")
            folder = Path(tempfile.mkdtemp(prefix="kazma-space-export-"))
            target = folder / f"kazma-space-{space_id}.zip"
            made = False
            try:
                space_data.export_space(space_id, target, password=password)
                made = True
            finally:
                if not made:
                    shutil.rmtree(folder, ignore_errors=True)
            return FileResponse(target, media_type="application/zip", filename=target.name,
                                background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))

        return await asyncio.to_thread(_guarded, request, space_id, "export", _run)

    @router.post("/import")
    async def bring_in(request: Request) -> Any:
        # The bundle is read to a file of its own before anything is checked;
        # the import itself runs in a worker thread (space_move).
        def _refused() -> JSONResponse | None:
            if not spaces_enforced():
                return _answer("spaces_off", 409, "This is done once Spaces is on for this install.")
            if auth.admin_decision(request) != "ok":
                return _answer("admin_only", 403, "Only the admin brings a space in.")
            return None

        refused = await asyncio.to_thread(_refused)
        if refused:
            return refused
        try:
            form = await request.form(max_files=1, max_fields=4)
        except (MultiPartException, ValueError):  # a body that is no form is the caller's to fix
            return _answer("not_a_bundle", 400, "Send the bundle as a form.")
        upload = form.get("bundle")
        password = str(form.get("password") or "")
        owner = str(form.get("owner") or "")
        if upload is None or isinstance(upload, str):
            return _answer("not_a_bundle", 400, "Choose the bundle to bring in.")
        folder = Path(tempfile.mkdtemp(prefix="kazma-space-bundle-"))
        source = folder / "bundle.zip"

        def _run() -> Any:
            with open(source, "wb") as out:
                shutil.copyfileobj(upload.file, out, 1 << 20)
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            try:
                taken = space_move.import_bundle(source, password, owner=owner, by=by)
            except space_move.SpaceMoveRefused as exc:
                return _answer(exc.code, exc.status, validation_error(exc))
            except space_data.SpaceDataRefused as exc:
                return _answer(exc.code, exc.status, validation_error(exc))
            except PeopleRefused as exc:
                return _answer(exc.code, exc.status, validation_error(exc))
            except IdentityUnavailable:
                return JSONResponse({"ok": False, "error": "identity_unavailable"}, status_code=503,
                                    headers={"Retry-After": "5"})
            # Nothing this process remembers of the space is of the bundle.
            get_session_manager().space_arrived(str(taken["space"]))
            return JSONResponse({"ok": True, **taken}, status_code=201)

        try:
            return await asyncio.to_thread(_run)
        finally:
            await upload.close()
            await asyncio.to_thread(shutil.rmtree, folder, True)

    @router.post("/{space_id}/move")
    async def move(space_id: str, request: Request) -> Any:
        # Sealed with a password the person chooses (decision N10): the
        # bundle holds the space and its secrets' keys, sealed under it.
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        password = str(body.get("password") or "") if isinstance(body, dict) else ""

        def _run() -> Any:
            if len(password) < MIN_PASSWORD:
                return _answer("password_too_short", 400,
                               f"The bundle's password is at least {MIN_PASSWORD} characters.")
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            folder = Path(tempfile.mkdtemp(prefix="kazma-space-move-"))
            target = folder / f"kazma-space-{space_id}.kazmaspace"
            made = False
            try:
                space_move.export_bundle(space_id, target, password, by=by)
                made = True
            finally:
                if not made:
                    shutil.rmtree(folder, ignore_errors=True)
            return FileResponse(target, media_type="application/octet-stream", filename=target.name,
                                background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))

        return await asyncio.to_thread(_guarded, request, space_id, "export", _run)

    @router.delete("/{space_id}")
    def erase(space_id: str, request: Request) -> Any:
        def _run() -> Any:
            by = space_binding.account_of(auth.get_request_principal(request)) or ""
            erased = space_data.erase_space(space_id, by=by)
            get_session_manager().space_erased(space_id)
            return JSONResponse({"ok": True, "erased": erased})

        return _guarded(request, space_id, "erase", _run)

    return router
