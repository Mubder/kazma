"""Stronger sign-in (Spaces S4-02): a second factor, each person's
sessions, and invitation links that expire.

My account (the person's own, ``account`` routes):

* ``GET /api/account/second-factor`` -- whether the caller has one, since
  when, how many recovery codes are left;
* ``POST /api/account/second-factor/begin`` -- a new secret (shown once, as
  text and an ``otpauth://`` link), pending until confirmed;
* ``POST /api/account/second-factor/confirm`` -- ``{"code"}``: turn it on;
  answers ten recovery codes, shown once; every other session of the
  account ends (this one stays);
* ``POST /api/account/second-factor/off`` -- ``{"code"}`` (a code from the
  app, or a recovery code): turn it off;
* ``POST /api/account/sessions/{session_id}/end`` -- end one of the caller's
  own sessions (``session_id`` is the ``id`` the session list gives). The
  owner's are every kind it has: a sign-in with the secret, the auto-login,
  a browser that sent the secret's header (``web_sessions.sessions_of_account``).

Only a person's own sign-in -- a browser session -- or the shared secret
reach them: every API token is the owner's account
(``space_binding.account_of``), and no token, whatever its role, turns a
second factor on or off (403 ``own_sign_in_only``), in every mode.

People (the admin's, once Spaces is enforced: 409 ``spaces_off`` before):

* ``GET /api/people/{account_id}/sessions`` -- a person's live sessions;
* ``POST /api/people/{account_id}/sessions/{session_id}/end`` -- end one;
  recorded, and they are told;
* ``POST /api/people/{account_id}/second-factor/reset`` -- take a person's
  second factor off (they lost the phone and the codes); recorded, told.
  Only from a signed-in admin's session, never with the install's secret or
  a token (``session_only``), and never the admin's own
  (``own_in_my_account``: that needs their code, in My account);
* ``GET /api/people/invitations``, ``POST /api/people/invitations``
  (``{"name", "hours"}``: the link is answered once), ``POST
  /api/people/invitations/{invitation_id}/revoke``.

Open (before sign-in): ``POST /api/invitations/lookup`` -- ``{"token"}``:
whether the link is open, and the name it suggests; ``POST
/api/invitations/accept`` -- ``{"token", "name", "password"}``: the invited
person's account and personal space (``spaces.invitations.accept``). Both
rate limited. The link is ``/invite#<token>``: the token rides after the
``#``, which a browser never sends, so no server or access log sees it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.security import second_factor
from kazma_core.spaces import invitations, people
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import spaces_enforced

from kazma_ui import auth, space_binding
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.rate_limit import rate_limit

__all__ = ["create_sign_in_router"]

logger = logging.getLogger(__name__)

_NOT_OWN = object()


def _own_account(request: Request) -> Any:
    """The account the caller's own routes act for, None for a sign-in with
    no account, or :data:`_NOT_OWN` for an API token (in every mode: a token
    never turns the owner's second factor on or off). Blocking."""
    principal = auth.get_request_principal(request) or {}
    if principal.get("source") == "api_token":
        return _NOT_OWN
    return space_binding.account_of(principal)


def _own_refused(account: Any) -> JSONResponse | None:
    if account is _NOT_OWN:
        return JSONResponse({"ok": False, "error": "own_sign_in_only",
                             "message": "A second factor is set from a person's own sign-in, never "
                                        "with a token."}, status_code=403)
    if not account:
        return JSONResponse({"ok": False, "error": "no_account"}, status_code=403)
    return None


def _refused(exc: second_factor.SecondFactorRefused | people.PeopleRefused) -> JSONResponse:
    """A refusal as its code; the pages word it (sign_in_settings.js,
    invite.html)."""
    return JSONResponse({"ok": False, "error": exc.code}, status_code=int(exc.status or 400))


def _unavailable() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "unavailable",
                         "message": "Sign-in settings cannot be read right now. Try again."},
                        status_code=503, headers={"Retry-After": "5"})


def _admin_refused(request: Request) -> JSONResponse | None:
    decision = auth.admin_decision(request)
    if decision == "unauthorized":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if decision != "ok":
        return JSONResponse({"ok": False, "error": "admin_required"}, status_code=403)
    if not spaces_enforced():
        return JSONResponse({"ok": False, "error": "spaces_off"}, status_code=409)
    return None


def _reset_refusal(principal: dict[str, Any], account_id: str, by: str) -> JSONResponse | None:
    """Why People may not reset this second factor, or None: only from a
    signed-in admin's session -- the install's secret and every token are
    keys a program holds, and turning a second factor off is a person's
    decision -- and never the admin's own, which needs their code, in My
    account (the review of S4-02, item 2)."""
    if principal.get("source") != "session":
        return JSONResponse({"ok": False, "error": "session_only"}, status_code=403)
    if account_id == by:
        return JSONResponse({"ok": False, "error": "own_in_my_account"}, status_code=409)
    return None


def _label_of(account_id: str) -> str:
    """The name an authenticator app shows: the person's name on Kazma."""
    from kazma_core.spaces import identity

    account = (identity.read_registry()["accounts"].get(account_id) or {})
    return f"{account.get('name') or account_id}"


def _person_sign_in(account_id: str) -> dict[str, Any]:
    """How a person's sessions are found (``web_sessions.sessions_of_account``)."""
    from kazma_core.security.web_sessions import sessions_of_account

    try:
        return sessions_of_account(account_id)
    except LookupError:
        raise people.PeopleRefused("no_such_account", 404, "No account has that id.") from None


def _caller_sessions(request: Request) -> dict[str, Any] | None:
    """How the caller's own sessions are found, or None for a caller with
    none of its own (an API token). Blocking."""
    from kazma_core.security.web_sessions import sessions_of_account

    principal = auth.get_request_principal(request) or {}
    if principal.get("source") not in ("session", "secret"):
        return None
    account = space_binding.account_of(principal)
    if account:
        try:
            return sessions_of_account(account)
        except LookupError:
            return None
    if principal.get("source") != "session":
        return None
    return {"user_id": str(principal.get("user_id") or ""),
            "username": str(principal.get("username") or ""), "actors": ()}


async def _body(request: Request) -> dict[str, Any] | None:
    try:
        body = await read_json(request, allow_empty=True)
    except InvalidJsonBody:
        return None
    return body if isinstance(body, dict) else {}


def create_sign_in_router() -> APIRouter:
    router = APIRouter()

    # ── My account: the second factor ────────────────────────────────────

    @router.get("/api/account/second-factor")
    def my_second_factor(request: Request) -> JSONResponse:
        account = _own_account(request)
        if (refused := _own_refused(account)) is not None:
            return refused
        try:
            held = second_factor.state(account)
            from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

            # Where Kazma signs the owner in by itself (this computer, the
            # auto-login: before Spaces, or with one account), no code is
            # asked there; and the install's secret is a key for programs the
            # second factor does not cover. My account says both.
            owner = account == OWNER_ACCOUNT_ID
            held["auto_login_here"] = owner and not auth.everyone_signs_in()
            held["install_secret"] = owner
        except (second_factor.SecondFactorUnavailable, IdentityUnavailable):
            return _unavailable()
        return JSONResponse({"ok": True, **held})

    def _begin(request: Request) -> JSONResponse:
        account = _own_account(request)
        if (refused := _own_refused(account)) is not None:
            return refused
        try:
            made = second_factor.begin(account, _label_of(account))
        except second_factor.SecondFactorRefused as exc:
            return _refused(exc)
        except (second_factor.SecondFactorUnavailable, IdentityUnavailable):
            return _unavailable()
        return JSONResponse({"ok": True, **made})

    @router.post("/api/account/second-factor/begin")
    def begin_second_factor(request: Request) -> JSONResponse:
        return _begin(request)

    def _confirm(request: Request, body: dict[str, Any]) -> JSONResponse:
        account = _own_account(request)
        if (refused := _own_refused(account)) is not None:
            return refused
        try:
            codes = second_factor.confirm(account, str(body.get("code") or ""))
        except second_factor.SecondFactorRefused as exc:
            return _refused(exc)
        except (second_factor.SecondFactorUnavailable, IdentityUnavailable):
            return _unavailable()
        # Every other session of the account ends: one opened before the
        # second factor (by a password alone) must not stay signed in. This
        # one stays.
        from kazma_core.security.web_sessions import SESSION_COOKIE, revoke_user_sessions

        ended = 0
        sessions = _caller_sessions(request)
        if sessions is not None:
            ended = revoke_user_sessions(**sessions, keep=request.cookies.get(SESSION_COOKIE))
        return JSONResponse({"ok": True, "recovery_codes": codes, "other_sessions_ended": ended})

    @router.post("/api/account/second-factor/confirm",
                 dependencies=[Depends(rate_limit("second_factor", 10))])
    async def confirm_second_factor(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return invalid_json_response()
        return await asyncio.to_thread(_confirm, request, body)

    def _off(request: Request, body: dict[str, Any]) -> JSONResponse:
        account = _own_account(request)
        if (refused := _own_refused(account)) is not None:
            return refused
        try:
            second_factor.turn_off(account, str(body.get("code") or ""))
        except second_factor.SecondFactorRefused as exc:
            return _refused(exc)
        except (second_factor.SecondFactorUnavailable, IdentityUnavailable):
            return _unavailable()
        return JSONResponse({"ok": True})

    @router.post("/api/account/second-factor/off",
                 dependencies=[Depends(rate_limit("second_factor", 10))])
    async def second_factor_off(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return invalid_json_response()
        return await asyncio.to_thread(_off, request, body)

    # ── My account: one of my sessions ───────────────────────────────────

    @router.post("/api/account/sessions/{session_id}/end")
    def end_my_session(session_id: str, request: Request) -> JSONResponse:
        """End one of the caller's own sessions (from the list Settings ->
        My account shows)."""
        from kazma_core.security.web_sessions import end_one_session

        sessions = _caller_sessions(request)
        if sessions is None:
            return JSONResponse({"ok": False, "error": "own_sign_in_only"}, status_code=403)
        ended = end_one_session(session_id, **sessions)
        if not ended:
            return JSONResponse({"ok": False, "error": "no_such_session"}, status_code=404)
        return JSONResponse({"ok": True})

    # ── People: a person's sessions and second factor ────────────────────

    @router.get("/api/people/{account_id}/sessions")
    def person_sessions(account_id: str, request: Request) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        from kazma_core.security.web_sessions import list_sessions

        try:
            sessions = _person_sign_in(account_id)
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "sessions": list_sessions(**sessions)})

    @router.post("/api/people/{account_id}/sessions/{session_id}/end")
    def end_person_session(account_id: str, session_id: str, request: Request) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        from kazma_core.security.web_sessions import end_one_session
        from kazma_core.spaces import identity

        try:
            sessions = _person_sign_in(account_id)
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        if not end_one_session(session_id, **sessions):
            return JSONResponse({"ok": False, "error": "no_such_session"}, status_code=404)
        by = space_binding.account_of(auth.get_request_principal(request)) or ""

        def _change(reg: dict[str, Any]) -> None:
            if account_id in reg["accounts"]:
                people.record_event(reg, "session_ended", account_id, by)

        try:
            identity.update_registry(_change)
        except IdentityUnavailable:
            logger.warning("[people] a session ended was not recorded", exc_info=True)
        return JSONResponse({"ok": True})

    @router.post("/api/people/{account_id}/second-factor/reset")
    def reset_second_factor(account_id: str, request: Request) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        principal = auth.get_request_principal(request) or {}
        by = space_binding.account_of(principal) or ""
        if (refused := _reset_refusal(principal, account_id, by)) is not None:
            return refused
        try:
            second_factor.reset(account_id, by=by)
        except second_factor.SecondFactorRefused as exc:
            return _refused(exc)
        except (second_factor.SecondFactorUnavailable, IdentityUnavailable):
            return _unavailable()
        return JSONResponse({"ok": True})

    # ── People: invitations ──────────────────────────────────────────────

    @router.get("/api/people/invitations")
    def list_invitations(request: Request) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        return JSONResponse({"ok": True, "invitations": invitations.list_invitations(),
                             "hours": invitations.HOURS})

    def _invite(request: Request, body: dict[str, Any]) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        by = space_binding.account_of(auth.get_request_principal(request)) or ""
        try:
            made = invitations.create(by=by, name=str(body.get("name") or ""),
                                      hours=body.get("hours") or invitations.HOURS)
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        # After the '#': a browser never sends it, so no log holds the token
        # (the review of S4-02, item 6).
        return JSONResponse({"ok": True, "invitation": made, "path": f"/invite#{made['token']}"})

    @router.post("/api/people/invitations")
    async def make_invitation(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return invalid_json_response()
        return await asyncio.to_thread(_invite, request, body)

    @router.post("/api/people/invitations/{invitation_id}/revoke")
    def revoke_invitation(invitation_id: str, request: Request) -> JSONResponse:
        if (refused := _admin_refused(request)) is not None:
            return refused
        by = space_binding.account_of(auth.get_request_principal(request)) or ""
        try:
            invitations.revoke(invitation_id, by=by)
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True})

    # ── Open: an invitation's page asks, then accepts ────────────────────

    def _lookup(body: dict[str, Any]) -> JSONResponse:
        closed = JSONResponse({"ok": False, "error": "invitation_closed"}, status_code=410)
        if not spaces_enforced():
            return closed
        try:
            found = invitations.lookup(str(body.get("token") or ""))
        except (IdentityUnavailable, RuntimeError, OSError):
            # The store away (``ConfigStoreUnavailableError`` is a RuntimeError).
            logger.warning("[invite] an invitation could not be read", exc_info=True)
            return _unavailable()
        if not found:
            return closed
        return JSONResponse({"ok": True, "name": str(found.get("name") or "")})

    @router.post("/api/invitations/lookup", dependencies=[Depends(rate_limit("invitation", 10))])
    async def lookup_invitation(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return invalid_json_response()
        return await asyncio.to_thread(_lookup, body)

    def _accept(body: dict[str, Any]) -> JSONResponse:
        if not spaces_enforced():
            return JSONResponse({"ok": False, "error": "invitation_closed"}, status_code=410)
        try:
            made = invitations.accept(str(body.get("token") or ""), str(body.get("name") or ""),
                                      str(body.get("password") or ""))
        except people.PeopleRefused as exc:
            return _refused(exc)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, "name": str(made.get("name") or "")})

    @router.post("/api/invitations/accept", dependencies=[Depends(rate_limit("invitation", 10))])
    async def accept_invitation(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return invalid_json_response()
        return await asyncio.to_thread(_accept, body)

    return router
