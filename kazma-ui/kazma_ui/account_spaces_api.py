"""``GET /api/account/spaces``: the spaces the signed-in person is in (S1-06).

The page's space switcher reads it. It answers for the caller only: their
account, the spaces they are a member of, and the space this request ran in.
It never lists a space the caller is not in.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import require_space_id, spaces_enforced

from kazma_ui import auth, space_binding

__all__ = ["create_account_spaces_router"]

logger = logging.getLogger(__name__)


def create_account_spaces_router() -> APIRouter:
    """The router of the caller's own spaces."""
    router = APIRouter(prefix="/api/account", tags=["account"])

    @router.get("/spaces")
    def my_spaces(request: Request) -> JSONResponse:
        # A plain def: the identity store is read in a worker thread, with
        # the scope the middleware bound.
        try:
            account = space_binding.account_of(auth.get_request_principal(request))
            spaces: list[dict[str, Any]] = (
                space_binding.spaces_for_account(account) if account else []
            )
        except IdentityUnavailable:
            logger.warning("[spaces] the caller's spaces could not be read", exc_info=True)
            return JSONResponse(
                {"ok": False, "error": "Accounts cannot be read right now. Try again.",
                 "code": "identity_unavailable"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        return JSONResponse({
            "ok": True,
            "account": account,
            "spaces": spaces,
            "current": require_space_id(),
            "enforced": spaces_enforced(),
        })

    return router
