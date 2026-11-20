"""The People routes (Spaces S1-25): the admin creates, resets and deletes
people; each person reads the changes made to their account.

* ``GET /api/people`` -- every account (id, name, role, enabled, when made,
  its personal space) and whether the record of changes is whole. Never a
  word of a space's data (plan 4.10).
* ``POST /api/people`` -- a new person and their personal space.
* ``POST /api/people/{account_id}/reset`` -- a new password; their sessions
  end and they are told at their next sign-in.
* ``DELETE /api/people/{account_id}`` -- the person goes; their personal
  space waits, with no member, to be erased (S1-27).
* ``GET /api/account/notices``, ``POST /api/account/notices/seen`` -- the
  signed-in person's own notices: a reset of their password someone else
  made.

The People routes are the admin's (``platform_rbac._ADMIN_PREFIXES``) and act
only once Spaces is enforced (409 ``spaces_off`` before). Every change is
recorded in the identity store, in the same step as the change
(``kazma_core.spaces.people``). Plain ``def`` routes: the store is read in a
worker thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.spaces import people
from kazma_core.spaces.identity import IdentityError, IdentityUnavailable
from kazma_core.spaces.scope import spaces_enforced

from kazma_ui import auth, space_binding
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

__all__ = ["create_people_router"]

logger = logging.getLogger(__name__)

_UNAVAILABLE = {"ok": False, "error": "identity_unavailable",
                "message": "Accounts cannot be read right now. Try again."}


def _refused(exc: people.PeopleRefused) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.code, "message": str(exc)}, status_code=exc.status)


def _unavailable() -> JSONResponse:
    return JSONResponse(_UNAVAILABLE, status_code=503, headers={"Retry-After": "5"})


def _not_admin(request: Request) -> JSONResponse | None:
    decision = auth.admin_decision(request)
    if decision == "ok":
        return None
    if decision == "unauthorized":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return JSONResponse({"ok": False, "error": "admin_required"}, status_code=403)


def _caller(request: Request) -> str | None:
    return space_binding.account_of(auth.get_request_principal(request))


def create_people_router() -> APIRouter:
    router = APIRouter(tags=["people"])

    @router.get("/api/people")
    def list_people(request: Request) -> JSONResponse:
        denied = _not_admin(request)
        if denied:
            return denied
        try:
            listed = people.list_people()
            broken = people.broken_at()
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "enforced": spaces_enforced(), "people": listed,
                             "record_whole": broken is None, "record_broken_at": broken})

    def _create(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            person = people.create_person(str(body.get("name") or ""), str(body.get("password") or ""),
                                          by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "person": person}, status_code=201)

    @router.post("/api/people")
    async def create_person(request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_create, request, body)

    def _reset(request: Request, account_id: str, body: dict[str, Any]) -> JSONResponse:
        try:
            people.reset_password(account_id, str(body.get("password") or ""), by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.post("/api/people/{account_id}/reset")
    async def reset_password(account_id: str, request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_reset, request, account_id, body)

    @router.delete("/api/people/{account_id}")
    def delete_person(account_id: str, request: Request) -> JSONResponse:
        denied = _not_admin(request)
        if denied:
            return denied
        try:
            people.delete_person(account_id, by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.get("/api/account/notices")
    def my_notices(request: Request) -> JSONResponse:
        account = _caller(request)
        if account is None or not spaces_enforced():
            return JSONResponse({"ok": True, "notices": []})
        try:
            return JSONResponse({"ok": True, "notices": people.notices_for(account)})
        except IdentityUnavailable:
            return _unavailable()

    def _seen(request: Request, body: dict[str, Any]) -> JSONResponse:
        account = _caller(request)
        if account is None:
            return JSONResponse({"ok": False, "error": "no_account"}, status_code=403)
        try:
            people.mark_seen(account, int(body.get("through") or 0))
        except IdentityError as exc:
            return JSONResponse({"ok": False, "error": exc.code}, status_code=404)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad_through"}, status_code=400)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.post("/api/account/notices/seen")
    async def notices_seen(request: Request) -> JSONResponse:
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_seen, request, body)

    return router
