"""``/api/support/*``: the web app's problem report (2026-11-14, REMAINING 3.1).

``POST /api/support/report/preview`` makes the report and answers it as text,
exactly as it would be sent; ``POST /api/support/report`` makes it again and
sends it (``kazma_core.support.problem_report``). Both are an administrator's
(``platform_rbac._ADMIN_PREFIXES``): a report carries the install's
diagnostics. The ``?`` beside the bell and "Report this" open the dialog that
calls them (``Alpine.store('report')``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["create_support_router"]


class _ReportBody(BaseModel):
    note: str = Field("", max_length=4000)
    where: str = Field("", max_length=200)


def create_support_router() -> APIRouter:
    router = APIRouter(prefix="/api/support", tags=["support"])

    @router.post("/report/preview")
    def preview_report(body: _ReportBody) -> dict[str, Any]:
        """The report as it would be sent (a plain def: FastAPI runs it in a thread)."""
        from kazma_core.support import problem_report

        report = problem_report.collect(body.note, where=body.where)
        return {"preview": problem_report.preview(report)}

    @router.post("/report", dependencies=[Depends(rate_limit("support", 10))])
    def send_report(body: _ReportBody) -> dict[str, Any]:
        """Send it; a report that cannot leave is saved, and the answer says where."""
        from kazma_core.support import problem_report

        result = problem_report.send(problem_report.collect(body.note, where=body.where))
        return {"sent": result.sent, "id": result.report_id, "saved": result.saved, "why": result.why}

    @router.get("/settings")
    def report_settings() -> dict[str, Any]:
        """Where reports go now, and Kazma's own inbox (Settings -> System)."""
        from kazma_core.config_store import get_config_store
        from kazma_core.support import problem_report

        value = get_config_store().get(problem_report.REPORT_URL_KEY, problem_report.DEFAULT_REPORT_URL)
        return {"report_url": "" if value is None else str(value), "default": problem_report.DEFAULT_REPORT_URL}

    return router
