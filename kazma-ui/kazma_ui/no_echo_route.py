"""A route whose refused body is not answered back.

FastAPI's 422 carries the ``input`` it refused: a key too long for its field,
or one posted in the wrong shape, came back whole in the answer and in what a
proxy or a page logs of it. A router of routes that take secrets is made with
``route_class=NoEchoRoute``: the same 422, saying where and why, without the
values (``input``, ``ctx``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

__all__ = ["NoEchoRoute"]


class NoEchoRoute(APIRoute):
    """An :class:`APIRoute` whose validation errors hold no value posted."""

    def get_route_handler(self) -> Callable[[Request], Any]:
        handler = super().get_route_handler()

        async def without_the_values(request: Request) -> Any:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                kept = [{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in exc.errors()]
                raise RequestValidationError(kept) from None

        return without_the_values
