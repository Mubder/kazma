"""Task-local correlation of the existing X client audit with a send operation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_operation_id: ContextVar[str] = ContextVar("x_publication_operation", default="")


def current_operation_id() -> str:
    return _operation_id.get()


@contextmanager
def operation_scope(ident: str) -> Iterator[None]:
    token = _operation_id.set(ident)
    try:
        yield
    finally:
        _operation_id.reset(token)
