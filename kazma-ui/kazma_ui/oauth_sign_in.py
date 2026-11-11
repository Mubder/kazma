"""The HTTP side of an OAuth sign-in: the start's answer and the callback's check.

The attempts themselves -- one per sign-in, ten minutes, bounded, taken once --
are ``kazma_core.security.oauth_attempts``. This module is the one place the
start routes (GitHub, Gmail, Google Calendar, Microsoft mail and Outlook
Calendar) and their callbacks talk HTTP about them:

* A start is a POST from Kazma's own page (security audit 2026-11-10: the
  starts were GETs that wrote state, so any link could start one). The CSRF
  middleware's same-origin rule applies (AGENTS §24 C). It answers JSON with
  the provider's URL, and the page goes there in the same tab -- no popup, no
  ``window.opener``, so the pages can send ``Cross-Origin-Opener-Policy``.
* Its answer sets the binding cookie (``oauth_attempts.binding_cookie_name``):
  HttpOnly, SameSite=Lax (the provider's redirect back is a top-level GET,
  which carries it), sent to the callback's path only, for the attempt's ten
  minutes.
* A sign-in must start on the origin the provider sends the browser back to,
  or the cookie never comes back: a page on ``http://localhost:9090`` with the
  callback on ``http://127.0.0.1:9090`` or ``KAZMA_PUBLIC_URL``. Such a start
  is refused (409 :data:`SIGN_IN_ELSEWHERE`, naming the origin to open)
  before anything is kept, rather than failing at the callback.
* A callback takes the attempt with the browser's cookie
  (:func:`take_attempt`) and forgets the cookie in its answer
  (:func:`forget_binding`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from kazma_core.security.oauth_attempts import (
    TTL_S,
    binding_cookie_name,
    binding_cookie_path,
    take,
)
from kazma_ui.browser_origins import normalize_origin

logger = logging.getLogger(__name__)

__all__ = [
    "SIGN_IN_ELSEWHERE",
    "answer_start",
    "forget_binding",
    "take_attempt",
]

#: The start's refusal when the page is on another origin than the callback.
SIGN_IN_ELSEWHERE = "sign_in_from_callback_origin"


def _callback_origin_problem(request: Request, callback_url: str) -> str:
    """The callback's origin, when the page that started this sign-in is on
    another one (the binding cookie would never reach the callback); else ``""``.

    The page's origin is what the browser names (``Origin``, else the
    ``Referer``'s origin). A client that names neither (curl, a script) gets
    the cookie in its own answer and is not judged.
    """
    page = normalize_origin(
        request.headers.get("origin") or request.headers.get("referer") or "",
        allow_path=True,
    )
    callback = normalize_origin(callback_url, allow_path=True)
    if not page or not callback or page == callback:
        return ""
    return callback


async def answer_start(
    request: Request, *, callback_url: str, start: Callable[[], dict[str, Any]]
) -> JSONResponse:
    """Run a provider's *start* in a thread and answer the page.

    *start* records the attempt (``oauth_attempts.begin``) and returns
    ``{"ok": True, "authorize_url": ..., "state": ...}`` or ``{"ok": False,
    ...}``. The state never goes back in the JSON: it is in the binding cookie
    and in the provider's URL.
    """
    elsewhere = _callback_origin_problem(request, callback_url)
    if elsewhere:
        return JSONResponse(
            {
                "ok": False,
                "code": SIGN_IN_ELSEWHERE,
                "origin": elsewhere,
                "error": f"Open Kazma at {elsewhere} to sign in: the provider sends you back there.",
            },
            status_code=409,
        )
    result = dict(await asyncio.to_thread(start))
    state = str(result.pop("state", "") or "")
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    if not state or not result.get("authorize_url"):
        logger.error("[oauth] a start answered ok without an attempt")
        return JSONResponse({"ok": False, "error": "internal_error"}, status_code=500)
    from kazma_ui.auth import _is_https

    response = JSONResponse(result)
    response.set_cookie(
        key=binding_cookie_name(state),
        value=state,
        httponly=True,
        samesite="lax",
        path=binding_cookie_path(callback_url),
        secure=_is_https(request),
        max_age=TTL_S,
    )
    return response


async def take_attempt(request: Request, state: str, *, providers: Iterable[str]) -> dict[str, Any]:
    """The callback's attempt, taken once with this browser's binding cookie.

    Raises ``oauth_attempts.AttemptRefused`` (its ``reason`` is the code the
    pages word). A settings write, in a thread.
    """
    bound = request.cookies.get(binding_cookie_name(state), "") if state else ""
    return await asyncio.to_thread(take, state, bound_state=bound, providers=tuple(providers))


def forget_binding(response: Response, request: Request, state: str, attempt: dict[str, Any] | None = None) -> Response:
    """Expire the attempt's binding cookie in the callback's *response*."""
    if state:
        callback = str((attempt or {}).get("redirect_uri") or request.url.path)
        response.delete_cookie(binding_cookie_name(state), path=binding_cookie_path(callback))
    return response
