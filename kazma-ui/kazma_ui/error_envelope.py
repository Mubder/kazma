"""One envelope for a route's refusal: ``{"ok": false, "error", "code"}``.

2026-11 functional audit, FA-234 and FA-241. The Documents and Scheduled
routes answer their own refusals in this envelope, but what they RAISE -- a
body reader's 413/422, the rate limiter's 429, a request FastAPI could not
validate -- left as FastAPI's ``{"detail": ...}``, mostly with no code, and
FastAPI's validation answer echoed the input back (an 8,001-character
prompt came back whole). A route class calls these two to answer it in the
envelope instead.
"""

from __future__ import annotations

from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

#: The code a raised refusal carries when it names none.
CODE_BY_STATUS: dict[int, str] = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    422: "invalid_request",
    429: "rate_limited",
    503: "unavailable",
}


def http_envelope(exc: HTTPException) -> JSONResponse:
    """A raised ``HTTPException`` in the envelope, its headers kept."""
    detail: Any = exc.detail
    if isinstance(detail, dict) and detail.get("error"):
        content = {key: value for key, value in detail.items() if key != "ok"}
    else:
        content = {"error": str(detail or "Request refused")}
    content.setdefault("code", CODE_BY_STATUS.get(exc.status_code, "refused"))
    return JSONResponse(status_code=exc.status_code, content={"ok": False, **content},
                        headers=getattr(exc, "headers", None))


def validation_envelope(exc: RequestValidationError) -> JSONResponse:
    """A request FastAPI could not validate: which fields, never their values."""
    fields = sorted({".".join(str(part) for part in err.get("loc", ())) for err in exc.errors()})
    return JSONResponse(status_code=422, content={
        "ok": False, "error": "The request is not valid", "code": "invalid_request", "fields": fields,
    })
