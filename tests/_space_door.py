"""A stand-in for the database door of Spaces S4-01 (its step 1).

The second lock will ask, at each checkout, which space a statement is for:
the bound scope's, what ``spaces.row_work`` said, or the install's. Nothing
asks today, so a caller that says nothing (or says another space) looks right.
:func:`watched` asks at the store's own methods, on either backend, and
:func:`watched_pool` at the Postgres pool's checkout, as step 1 will.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from kazma_core.spaces.row_work import Door
from kazma_core.spaces.scope import current_scope


@dataclass
class Call:
    """One call at the door: the method, what it was said to be for, and who
    was acting."""

    name: str
    door: Door | None
    scope: Any
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    @property
    def space(self) -> str:
        """The space said, ``"<install>"``, or ``""`` when nothing was."""
        if self.door is None:
            return ""
        return self.door.space or "<install>"


class Calls(list):
    """The calls, in order."""

    def of(self, name: str) -> list[str]:
        """What each call of *name* was said to be for."""
        return [c.space for c in self if c.name == name]

    def scopes(self, name: str) -> list[Any]:
        """The scope bound at each call of *name*."""
        return [c.scope for c in self if c.name == name]


def _asking(calls: Calls, name: str, original: Any) -> Any:
    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def asked(*args: Any, **kwargs: Any) -> Any:
            calls.append(Call(name, Door.now(), current_scope(), args, kwargs))
            return await original(*args, **kwargs)
    else:
        @functools.wraps(original)
        def asked(*args: Any, **kwargs: Any) -> Any:
            calls.append(Call(name, Door.now(), current_scope(), args, kwargs))
            return original(*args, **kwargs)
    return asked


@contextmanager
def watched(*targets: tuple[Any, str]) -> Iterator[Calls]:
    """Ask at each ``(class, object or module, name)``, while the block runs."""
    calls = Calls()
    undo: list[tuple[Any, str, Any, bool]] = []
    for owner, name in targets:
        static = inspect.getattr_static(owner, name)
        kind = type(static) if isinstance(static, staticmethod | classmethod) else None
        function = static.__func__ if kind else getattr(owner, name) if not inspect.isclass(owner) else static
        wrapped = _asking(calls, name, function)
        undo.append((owner, name, static, name in vars(owner)))
        setattr(owner, name, kind(wrapped) if kind else wrapped)
    try:
        yield calls
    finally:
        for owner, name, static, own in reversed(undo):
            if own:
                setattr(owner, name, static)
            else:
                delattr(owner, name)


@contextmanager
def watched_pool() -> Iterator[Calls]:
    """Ask at each checkout of Kazma's shared Postgres pool."""
    from kazma_core.db.postgres_pool import PostgresPool

    with watched((PostgresPool, "connection")) as calls:
        yield calls
