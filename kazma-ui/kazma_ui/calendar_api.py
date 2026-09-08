"""Calendar integration API — Google Calendar OAuth status + connect/disconnect.

Security mirrors ``email_api``: mutating POSTs sit on a protected router
with Origin + ``X-Requested-With``. The Google callback is the Gmail
callback (one Cloud Console redirect URI); this module only serves
status, start, and disconnect.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from kazma_ui.email_api import _request_base, _safe_error, _verify_same_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
protected_router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/status")
async def calendar_status() -> JSONResponse:
    try:
        from kazma_skills.native.calendar.credentials import status_summary
        from kazma_skills.native.calendar.router import detect_available_provider

        data = status_summary()
        data["active_provider"] = detect_available_provider()
        return JSONResponse(data)
    except Exception as exc:
        return _safe_error(exc)


@router.get("/oauth/google/start")
async def google_calendar_oauth_start(request: Request) -> Any:
    from kazma_skills.native.calendar.oauth_google import start_google_calendar_oauth

    result = start_google_calendar_oauth(_request_base(request))
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return RedirectResponse(result["authorize_url"], status_code=302)


@router.get("/oauth/google/start.json")
async def google_calendar_oauth_start_json(request: Request) -> JSONResponse:
    from kazma_skills.native.calendar.oauth_google import start_google_calendar_oauth

    result = start_google_calendar_oauth(_request_base(request))
    code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=code)


@protected_router.post(
    "/oauth/google/disconnect", dependencies=[Depends(_verify_same_origin)]
)
async def google_calendar_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.calendar.credentials import clear_google_tokens

        return JSONResponse(clear_google_tokens())
    except Exception as exc:
        return _safe_error(exc)
