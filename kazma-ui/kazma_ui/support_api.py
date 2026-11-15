"""``/api/support/*``: the web app's problem report (2026-11-14, REMAINING 3.1).

``POST /api/support/report/preview`` makes the report, answers it as text,
exactly as it would be sent, and keeps it under an id for the person who asked
(``problem_report.hold``); ``POST /api/support/report`` sends that very report
(``problem_report.take``), once. Until 2026-11-15 Send collected the report
again, so log lines written after the preview left unseen. A report no longer
held (sent, past ``HOLD_S``, or asked for by someone else) is 409
``report_expired``: preview it again. Both are an administrator's
(``platform_rbac._ADMIN_PREFIXES``): a report carries the install's
diagnostics. The ``?`` beside the bell and "Report this" open the dialog that
calls them (``Alpine.store('report')``).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["create_support_router"]


class _ReportBody(BaseModel):
    note: str = Field("", max_length=4000)
    where: str = Field("", max_length=200)
    # This chat's last messages, only when the person ticked "include this chat".
    chat_session: str = Field("", max_length=200)


class _SendBody(BaseModel):
    # The id the preview answered: the report shown, sent as it was shown.
    report_id: str = Field(..., min_length=1, max_length=64)


def _owner(request: Request) -> str:
    """Who asked for a preview: their session or token, by its hash (never
    kept or logged as it is), so only they can send the report shown."""
    from kazma_core.security.web_sessions import SESSION_COOKIE

    for kind, value in (("session", request.cookies.get(SESSION_COOKIE) or ""),
                        ("token", request.headers.get("authorization") or "")):
        if value:
            return f"{kind}:{hashlib.sha256(value.encode('utf-8', 'surrogatepass')).hexdigest()}"
    return "local"


def _chat_messages(session_id: str) -> list[dict]:
    """The messages of the chat *session_id* (the chat store's); none when absent."""
    if not session_id:
        return []
    from kazma_ui.session_manager import get_session_manager

    session = get_session_manager().get(session_id)
    return list(getattr(session, "messages", None) or []) if session is not None else []


def create_support_router() -> APIRouter:
    router = APIRouter(prefix="/api/support", tags=["support"])

    @router.post("/report/preview")
    def preview_report(body: _ReportBody, request: Request) -> dict[str, Any]:
        """The report as it would be sent, kept for the person who asked (a
        plain def: FastAPI runs it in a thread)."""
        from kazma_core.support import problem_report

        report = problem_report.collect(body.note, where=body.where, chat=_chat_messages(body.chat_session))
        return {"preview": problem_report.preview(report),
                "report_id": problem_report.hold(report, _owner(request))}

    @router.post("/report", dependencies=[Depends(rate_limit("support", 10))])
    def send_report(body: _SendBody, request: Request) -> Any:
        """Send the report shown; a report that cannot leave is saved, and the
        answer says where."""
        from kazma_core.support import problem_report

        report = problem_report.take(body.report_id, _owner(request))
        if report is None:
            return JSONResponse({"error": "report_expired", "code": "report_expired"}, status_code=409)
        result = problem_report.send(report)
        return {"sent": result.sent, "id": result.report_id, "saved": result.saved, "why": result.why}

    @router.get("/settings")
    def report_settings() -> dict[str, Any]:
        """Where reports go now, and Kazma's own inbox (Settings -> System)."""
        from kazma_core.config_store import get_config_store
        from kazma_core.support import problem_report

        value = get_config_store().get(problem_report.REPORT_URL_KEY, problem_report.DEFAULT_REPORT_URL)
        return {"report_url": "" if value is None else str(value), "default": problem_report.DEFAULT_REPORT_URL}

    return router
