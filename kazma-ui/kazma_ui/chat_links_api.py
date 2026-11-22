"""A person's own chat-app accounts, from My account (Spaces S3-01).

* ``GET /api/account/chat-links`` -- the caller's linked chat-app accounts
  and the chat apps this install runs (each with where to find its bot);
* ``POST /api/account/chat-links/code`` -- ``{"platform"}``: a new code to
  send that chat app's bot in a direct chat (one use, ten minutes).

They answer for the caller only (an ``account`` route): every signed-in
person reaches them once Spaces is enforced, and an install that does not
enforce it lists nothing and issues nothing (409 ``spaces_off``). The code is
shown once, in the answer; Kazma keeps only its hash.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.spaces import chat_links
from kazma_core.spaces.identity import IdentityUnavailable
from kazma_core.spaces.scope import spaces_enforced

from kazma_ui import auth, space_binding
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

__all__ = ["create_chat_links_router"]

logger = logging.getLogger(__name__)


def _running_platforms() -> list[dict[str, Any]]:
    """The chat apps this install runs, in :data:`chat_links.PLATFORMS`
    order: ``{"id", "label", "bot"}`` -- ``bot`` is where to find the bot
    (Telegram's ``https://t.me/<name>``), or "". Read from the running
    gateway; none when no gateway runs."""
    from kazma_core.service_container import get_container

    try:
        from kazma_gateway.gateway import GatewayManager
    except ImportError:
        return []
    container = get_container()
    if not container.has(GatewayManager):
        return []
    running: dict[str, Any] = {}
    for adapter in getattr(container.get(GatewayManager), "adapters", None) or []:
        name = str(getattr(adapter, "name", "") or "")
        if name in chat_links.PLATFORMS:
            running[name] = adapter
    out: list[dict[str, Any]] = []
    for name in chat_links.PLATFORMS:
        if name not in running:
            continue
        bot = ""
        if name == "telegram":
            username = str(getattr(running[name], "_bot_username", "") or "").lstrip("@")
            if username and username.replace("_", "").isalnum():
                bot = f"https://t.me/{username}"
        out.append({"id": name, "label": chat_links.PLATFORM_LABELS[name], "bot": bot})
    return out


def _caller(request: Request) -> str | None:
    return space_binding.account_of(auth.get_request_principal(request))


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "identity_unavailable",
         "message": "Accounts cannot be read right now. Try again."},
        status_code=503, headers={"Retry-After": "5"},
    )


def create_chat_links_router() -> APIRouter:
    """The router of the caller's own chat-app links."""
    router = APIRouter(prefix="/api/account", tags=["account"])

    @router.get("/chat-links")
    def my_chat_links(request: Request) -> JSONResponse:
        # A plain def: the identity store is read in a worker thread.
        account = _caller(request)
        if account is None:
            return JSONResponse({"ok": False, "error": "no_account"}, status_code=403)
        if not spaces_enforced():
            return JSONResponse({"ok": True, "enforced": False, "links": [], "platforms": []})
        try:
            links = chat_links.links_of(account)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({
            "ok": True,
            "enforced": True,
            "links": [
                {"platform": link.get("platform"),
                 "label": chat_links.PLATFORM_LABELS.get(str(link.get("platform")), ""),
                 "user_id": link.get("user_id"), "linked_at": link.get("linked_at")}
                for link in links
            ],
            "platforms": _running_platforms(),
            "code_ttl_s": chat_links.CODE_TTL_S,
        })

    def _issue(request: Request, platform: Any) -> JSONResponse:
        account = _caller(request)
        if account is None:
            return JSONResponse({"ok": False, "error": "no_account"}, status_code=403)
        from kazma_ui.i18n import current_language

        try:
            issued = chat_links.issue_code(account, str(platform or ""), language=current_language())
        except chat_links.LinkRefused as exc:
            return JSONResponse({"ok": False, "error": exc.code, "message": str(exc)},
                                status_code=exc.status)
        except IdentityUnavailable:
            return _unavailable()
        return JSONResponse({"ok": True, **issued},
                            headers={"Cache-Control": "no-store"})

    @router.post("/chat-links/code")
    async def new_chat_link_code(request: Request) -> JSONResponse:
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return invalid_json_response()
        return await asyncio.to_thread(_issue, request, body.get("platform"))

    return router
