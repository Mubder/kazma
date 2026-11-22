"""A member's own model keys (Spaces S2-04): Settings -> My account ->
"Use my own key".

* ``GET /api/account/model-keys`` -- the providers the signed-in person may
  bring their own key for, each with ``own`` (a key of theirs is kept).
  Never the key itself.
* ``PUT /api/account/model-keys/{provider}`` ``{"key"}`` -- keep a key of
  theirs, once one short message through it answered
  (``provider_check.check_model``, the providers' one check): a key that
  does not work is not kept.
* ``DELETE /api/account/model-keys/{provider}`` -- forget it; their calls
  use the install's key again.

Each answers for the caller alone: the account the request signed in as.
Only where Spaces is enforced and never for the admin, whose keys are the
install's (409 with the refusal's code). The rules are
:mod:`kazma_core.spaces.own_keys`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.errors import validation_error
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.own_keys import OwnKeyRefused, OwnKeyUnreadable

from kazma_ui import auth, space_binding
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

__all__ = ["create_own_keys_router"]

logger = logging.getLogger(__name__)

#: A refusal's code -> its status.
_STATUS = {"spaces_off": 409, "admin_uses_install_keys": 409, "no_personal_space": 409,
           "no_account": 401, "unknown_provider": 404, "bad_key": 400, "vault_unavailable": 503}


def _refused(code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "error": message}, status_code=_STATUS.get(code, 400))


def _unreadable() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": "identity_unavailable",
         "error": "Your keys cannot be read right now. Try again."},
        status_code=503, headers={"Retry-After": "5"},
    )


def _account(request: Request) -> str:
    return str(space_binding.account_of(auth.get_request_principal(request)) or "")


def _registry() -> Any:
    from kazma_core.model_registry import get_model_registry

    return get_model_registry()


def _prepare(account: str, provider: str) -> tuple[dict[str, Any], str]:
    """The install's row for *provider* as the person would call it -- their
    key in place of the install's, none of the row's other keys or secret
    headers -- and the model the check asks. Raises ``OwnKeyRefused``.
    Blocking."""
    from kazma_core.spaces.own_keys import providers_of, refusal

    why = refusal(account)
    if why:
        raise OwnKeyRefused(*why)
    registry = _registry()
    found = next((p for p in providers_of(account, registry) if p["provider"] == provider), None)
    row = registry.get_provider(provider) if found else None
    if not found or not row:
        raise OwnKeyRefused("unknown_provider", "There is no such provider for your own key.")
    entry = {**row, "api_key": "", "api_keys": [], "secret_headers": {}}
    return entry, str(found.get("model") or "")


def create_own_keys_router() -> APIRouter:
    """The router of the caller's own model keys."""
    router = APIRouter(prefix="/api/account/model-keys", tags=["account"])

    @router.get("")
    def my_model_keys(request: Request) -> JSONResponse:
        # A plain def: the identity store and the vault are read in a worker
        # thread, with the scope the middleware bound.
        from kazma_core.spaces.own_keys import providers_of, refusal

        account = _account(request)
        try:
            why = refusal(account)
            if why:
                return _refused(*why)
            providers = providers_of(account, _registry())
        except (IdentityUnavailable, OwnKeyUnreadable):
            logger.warning("[own-keys] a person's own keys could not be read", exc_info=True)
            return _unreadable()
        return JSONResponse({"ok": True, "providers": providers})

    @router.put("/{provider}")
    async def keep_model_key(request: Request, provider: str) -> JSONResponse:
        from kazma_core.provider_check import check_model
        from kazma_core.spaces.own_keys import set_key

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        key = str((body or {}).get("key") or "").strip() if isinstance(body, dict) else ""
        if not key:
            return _refused("bad_key", "Type the key to keep.")
        # Who signed in reads the user store: off the loop.
        account = await asyncio.to_thread(_account, request)
        try:
            entry, model = await asyncio.to_thread(_prepare, account, provider)
            checked = await check_model({**entry, "api_key": key}, model)
            if not checked.get("ok"):
                return JSONResponse({"ok": False, "code": str(checked.get("error_code") or "failed"),
                                     "error": str(checked.get("error") or "The key did not work.")},
                                    status_code=400)
            await asyncio.to_thread(lambda: set_key(account, provider, key, _registry()))
        except OwnKeyRefused as exc:
            return _refused(exc.code, validation_error(exc))
        except (IdentityUnavailable, OwnKeyUnreadable):
            logger.warning("[own-keys] a person's own key could not be kept", exc_info=True)
            return _unreadable()
        return JSONResponse({"ok": True, "provider": provider, "own": True})

    @router.delete("/{provider}")
    def forget_model_key(request: Request, provider: str) -> JSONResponse:
        from kazma_core.spaces.own_keys import remove_key

        try:
            removed = remove_key(_account(request), provider, _registry())
        except OwnKeyRefused as exc:
            return _refused(exc.code, validation_error(exc))
        except (IdentityUnavailable, OwnKeyUnreadable):
            logger.warning("[own-keys] a person's own key could not be removed", exc_info=True)
            return _unreadable()
        return JSONResponse({"ok": True, "provider": provider, "own": False, "removed": removed})

    return router
