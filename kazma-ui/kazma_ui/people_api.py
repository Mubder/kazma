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
* ``PUT /api/people/{account_id}/daily-limit`` -- a person's own daily limit
  (``{"tokens", "usd"}``; no tokens: none of their own), and
  ``PUT /api/people/default-limit`` -- the limit for people who have none of
  their own (S2-02). ``GET /api/people`` says each person's limit and what
  they spent today (counts and money, never a word of what they asked, N11).
* ``PUT /api/people/{account_id}/features`` -- switch a person's features
  (``{"features": {id: on}}``, S2-11); ``GET /api/people`` lists each
  person's features.
* ``PUT /api/people/{account_id}/tools`` -- the single tools switched off
  for a person inside their features (``{"off": [tool, ...]}``, S2-01);
  ``GET /api/people`` lists each person's switchable tools, each on or off.
* ``PUT /api/people/{account_id}/enabled`` -- disable or enable a person
  (``{"enabled": bool}``, S2-07): disabled, they cannot sign in and their
  work waits.
* ``PUT /api/people/{account_id}/models`` -- the models a person may use
  and their default (``{"allowed": [ref, ...] or null, "default": ref}``,
  S2-03); ``GET /api/people`` lists each person's.
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
    body: dict[str, Any] = {"ok": False, "error": exc.code, "message": str(exc)}
    if exc.missing:
        body["missing"] = exc.missing
    return JSONResponse(body, status_code=exc.status)


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


_NOTHING_SPENT = {"calls": 0, "tokens": 0, "cost": 0.0, "unpriced": 0}


def _spent_today() -> tuple[dict[str, dict[str, Any]] | None, str]:
    """What each person spent today, by account (None: the ledger cannot be
    read), and today's date. Blocking."""
    from kazma_core.observability.llm_ledger import spend_by_person
    from kazma_core.spaces.daily_limits import today_start

    since, _zone = today_start()
    rows = spend_by_person(since)
    if rows is None:
        return None, since.date().isoformat()
    return ({row["asker"]: {key: row[key] for key in _NOTHING_SPENT} for row in rows if row["asker"]},
            since.date().isoformat())


def _set_limit(request: Request, account_id: str | None, body: dict[str, Any]) -> JSONResponse:
    raw = body.get("limit")
    if raw is not None and not isinstance(raw, dict):
        return JSONResponse({"ok": False, "error": "bad_limit"}, status_code=400)
    try:
        if account_id is None:
            people.set_default_daily_limit(raw, by=_caller(request) or "")
        else:
            people.set_daily_limit(account_id, raw, by=_caller(request) or "")
    except people.PeopleRefused as exc:
        return _refused(exc)
    except IdentityUnavailable:
        return _unavailable()
    return JSONResponse({"ok": True})


async def _limit_route(request: Request, account_id: str | None) -> JSONResponse:
    denied = await asyncio.to_thread(_not_admin, request)
    if denied:
        return denied
    try:
        body = await read_json(request)
    except InvalidJsonBody:
        return invalid_json_response()
    if not isinstance(body, dict):
        return invalid_json_response()
    return await asyncio.to_thread(_set_limit, request, account_id, body)


def create_people_router() -> APIRouter:
    router = APIRouter(tags=["people"])

    @router.get("/api/people")
    def list_people(request: Request) -> JSONResponse:
        denied = _not_admin(request)
        if denied:
            return denied
        try:
            listed = people.list_people()
            shared = people.list_shared_spaces()
            closed = people.closed_spaces()
            broken = people.broken_at()
            limits = people.limits_for_people()
        except IdentityUnavailable:
            return _unavailable()
        spent, today = _spent_today()
        for person in listed:
            person["spent_today"] = (spent.get(person["id"], _NOTHING_SPENT)
                                     if spent is not None else None)
        return JSONResponse({"ok": True, "enforced": spaces_enforced(), "people": listed,
                             "shared_spaces": shared, "closed_spaces": closed, "limit_for_people": limits, "today": today,
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

    def _create_space(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            space = people.create_shared_space(
                str(body.get("name") or ""), kind=str(body.get("kind") or "sandboxed"),
                project_folder=str(body.get("project_folder") or ""), by=_caller(request) or "",
            )
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "space": space}, status_code=201)

    @router.post("/api/people/spaces")
    async def create_shared_space(request: Request) -> JSONResponse:
        """A new shared space (Spaces S2-12): plain, or with a project folder."""
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_create_space, request, body)

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

    @router.put("/api/people/default-limit")
    async def set_default_limit(request: Request) -> JSONResponse:
        return await _limit_route(request, None)

    @router.put("/api/people/{account_id}/daily-limit")
    async def set_daily_limit(account_id: str, request: Request) -> JSONResponse:
        return await _limit_route(request, account_id)

    def _set_features(request: Request, account_id: str, body: dict[str, Any]) -> JSONResponse:
        try:
            people.set_features(account_id, body.get("features") or {}, by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.put("/api/people/{account_id}/features")
    async def set_features(account_id: str, request: Request) -> JSONResponse:
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_set_features, request, account_id, body)

    def _set_tools(request: Request, account_id: str, body: dict[str, Any]) -> JSONResponse:
        try:
            off = people.set_tools(account_id, body.get("off"), by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "off": off})

    @router.put("/api/people/{account_id}/tools")
    async def set_tools(account_id: str, request: Request) -> JSONResponse:
        """The single tools switched off for a person (Spaces S2-01):
        ``{"off": [tool, ...]}``, the whole list."""
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_set_tools, request, account_id, body)

    def _set_enabled(request: Request, account_id: str, body: dict[str, Any]) -> JSONResponse:
        try:
            people.set_enabled(account_id, body.get("enabled"), by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.put("/api/people/{account_id}/enabled")
    async def set_enabled(account_id: str, request: Request) -> JSONResponse:
        """Disable or enable a person (Spaces S2-07): ``{"enabled": bool}``."""
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_set_enabled, request, account_id, body)

    def _set_models(request: Request, account_id: str, body: dict[str, Any]) -> JSONResponse:
        try:
            kept = people.set_models(account_id, body.get("allowed"), body.get("default"),
                                     by=_caller(request) or "")
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, **kept})

    @router.put("/api/people/{account_id}/models")
    async def set_models(account_id: str, request: Request) -> JSONResponse:
        """The models a person may use and their default (Spaces S2-03):
        ``{"allowed": [ref, ...] or null, "default": ref}``."""
        denied = await asyncio.to_thread(_not_admin, request)
        if denied:
            return denied
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_set_models, request, account_id, body)

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
