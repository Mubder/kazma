"""One way a route reads a JSON body by hand (2026-11, FA-274).

Twenty routes called ``await request.json()`` themselves, and a malformed
body got whatever their ``except`` gave: a 400, a 422, a 200 with an error
inside, a 503 from a catch-all (the chat session bind), or -- in five -- an
empty body, so a request the client sent wrong ran as if it had sent
nothing. Every one now reads through :func:`read_json`: a body that is not
JSON is :class:`InvalidJsonBody`, answered 400 by :func:`invalid_json_response`
in the routes' envelope. ``tests/test_json_body_reader.py`` holds every
route to it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class InvalidJsonBody(ValueError):
    """The request's body is not JSON (or not the JSON the route takes)."""


async def read_json(request: Request, *, allow_empty: bool = False) -> Any:
    """The request body, parsed.

    ``allow_empty``: a request with no body reads as ``{}`` (a route whose
    fields are all optional); a body that is there must still be JSON.
    """
    raw = await request.body()
    if allow_empty and not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise InvalidJsonBody("The request body is not valid JSON") from exc


def invalid_json_response() -> JSONResponse:
    """The 400 a route answers for :class:`InvalidJsonBody`."""
    return JSONResponse(
        {"ok": False, "error": "The request body is not valid JSON", "code": "invalid_json"},
        status_code=400,
    )
