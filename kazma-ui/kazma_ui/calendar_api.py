"""Calendar integration API — Google and Outlook Calendar status,
connect and disconnect.

Security mirrors ``email_api``: mutating POSTs sit on a protected router
with Origin + ``X-Requested-With``. Each sign-in comes back through its mail
callback (one redirect URI per provider console); this module serves
status, start, and disconnect. Every vault read and write runs off the
event loop.

Outlook had no disconnect until 2026-09-28, and its only connect was the
mail sign-in (which reconnects mail too). A disconnect turns the calendar
OFF (``calendar.credentials``): the mail grant covers Calendar, so deleting
the calendar's own tokens alone left it connected.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kazma_ui.email_api import _request_base, _safe_error, _verify_same_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
protected_router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/status")
async def calendar_status() -> JSONResponse:
    try:
        from kazma_skills.native.calendar.credentials import status_summary

        return JSONResponse(await asyncio.to_thread(status_summary))
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/oauth/google/start", dependencies=[Depends(_verify_same_origin)])
async def google_calendar_oauth_start(request: Request) -> JSONResponse:
    """Start a Google Calendar sign-in from Kazma's own page: the consent URL
    (opened in the same tab) and the binding cookie (``kazma_ui.oauth_sign_in``).
    It comes back through the Gmail callback."""
    from kazma_skills.native.calendar.oauth_google import start_google_calendar_oauth
    from kazma_skills.native.email_manager.oauth_gmail import gmail_redirect_uri

    from kazma_ui.oauth_sign_in import answer_start

    base = _request_base(request)
    return await answer_start(
        request,
        callback_url=gmail_redirect_uri(base),
        start=lambda: start_google_calendar_oauth(base),
    )


@protected_router.post(
    "/oauth/google/disconnect", dependencies=[Depends(_verify_same_origin)]
)
async def google_calendar_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.calendar.credentials import clear_google_tokens

        result = await asyncio.to_thread(clear_google_tokens)
        return JSONResponse(result, status_code=200 if result.get("ok") else 500)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/oauth/microsoft/start", dependencies=[Depends(_verify_same_origin)])
async def outlook_calendar_oauth_start(request: Request) -> JSONResponse:
    """A Microsoft sign-in for Outlook Calendar only: mail is left as it is.
    The sign-in URL (opened in the same tab) and the binding cookie; it comes
    back through the Microsoft callback (``purpose=calendar``)."""
    from kazma_skills.native.email_manager.oauth_ms_browser import ms_redirect_uri, start_ms_browser_oauth

    from kazma_ui.oauth_sign_in import answer_start

    base = _request_base(request)
    return await answer_start(
        request,
        callback_url=ms_redirect_uri(base),
        start=lambda: start_ms_browser_oauth(base, purpose="calendar"),
    )


@protected_router.post(
    "/oauth/microsoft/device/start", dependencies=[Depends(_verify_same_origin)]
)
async def outlook_calendar_device_start() -> JSONResponse:
    """The same calendar-only sign-in by code, for a redirect Microsoft
    refuses; the page polls ``/api/email/oauth/microsoft/device/poll``."""
    from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

    result = await start_device_code_flow(purpose="calendar")
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@protected_router.post(
    "/oauth/microsoft/disconnect", dependencies=[Depends(_verify_same_origin)]
)
async def outlook_calendar_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.calendar.credentials import (
            clear_microsoft_calendar_tokens,
        )

        result = await asyncio.to_thread(clear_microsoft_calendar_tokens)
        return JSONResponse(result, status_code=200 if result.get("ok") else 500)
    except Exception as exc:
        return _safe_error(exc)
