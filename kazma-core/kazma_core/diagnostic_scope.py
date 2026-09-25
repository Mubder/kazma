"""A diagnostic reads. This makes that a rule instead of a convention.

Pressing **Test** on a provider once deleted every saved API key: the health
write round-tripped the whole credential list through a view that could not
decrypt it. The specific damage is guarded (``_prepare_value_for_storage``
refuses to blank a stored secret), but ``docs/KNOWN_GAPS.md`` recorded the
class as open: "no test or lint asserts that a diagnostic path may not call
a mutating one". One already did. ``/health/deep`` ran a real ``recall()``,
and recall bumps ``access_count``/``last_accessed`` on whatever it returns —
so a polled canary kept the memory that best matched "health canary probe"
permanently "in use": never archived by macro-sleep, and penalised in real
ranking for being surfaced so often. The check was changing what it checked.

:func:`read_only_diagnostic` marks the current context as a diagnostic. It is
a ContextVar, so it follows ``asyncio.to_thread`` and ``create_task``. Inside:

* **Explicit writes raise** :class:`DiagnosticWriteRefused` at the store
  chokepoints — every ConfigStore mutation and the vault — unless the key
  matches the scope's ``allow`` patterns (``/health/deep`` may write its one
  canary key; that roundtrip is the point of the check).
* **Bookkeeping writes a read would make are skipped** — the recall access
  bump, ConfigStore's lazy plaintext→vault migration. A read must not fail
  because it wanted to write as a side effect, and must not write either.

Nested scopes only narrow: a write must be allowed by every enclosing scope.
"""

from __future__ import annotations

import contextvars
import fnmatch
import logging
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "DiagnosticWriteRefused",
    "active_diagnostic",
    "read_only_diagnostic",
    "refuse_write",
    "writes_suppressed",
]


@dataclass(frozen=True)
class _Scope:
    name: str
    allow: frozenset[str]
    parent: _Scope | None

    def allows(self, target: str) -> bool:
        if not any(fnmatch.fnmatchcase(target, pattern) for pattern in self.allow):
            return False
        return self.parent is None or self.parent.allows(target)


_current: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
    "kazma_read_only_diagnostic", default=None,
)
_reported: set[tuple[str, str, str]] = set()
_reported_lock = threading.Lock()


class DiagnosticWriteRefused(PermissionError):
    """A write attempted inside :func:`read_only_diagnostic` and not allowed."""

    def __init__(self, diagnostic: str, kind: str, target: str) -> None:
        super().__init__(
            f"{diagnostic} is a read-only diagnostic and may not write {kind} "
            f"{target!r}. If that write is the point of the check, add the key "
            f"to the scope's allow= list."
        )
        self.diagnostic = diagnostic
        self.kind = kind
        self.target = target


@contextmanager
def read_only_diagnostic(name: str, *, allow: Iterable[str] = ()) -> Iterator[None]:
    """Run the block as a diagnostic: no store writes except ``allow`` patterns."""
    parent = _current.get()
    label = f"{parent.name} > {name}" if parent is not None else name
    token = _current.set(_Scope(label, frozenset(allow), parent))
    try:
        yield
    finally:
        _current.reset(token)


def active_diagnostic() -> str | None:
    """The name of the enclosing diagnostic, or ``None`` outside one."""
    scope = _current.get()
    return scope.name if scope is not None else None


def writes_suppressed() -> bool:
    """True inside a diagnostic: skip the bookkeeping writes a read would make."""
    return _current.get() is not None


def refuse_write(kind: str, target: str) -> None:
    """Raise :class:`DiagnosticWriteRefused` if this write is not allowed here.

    Called at the top of every store mutation. A no-op outside a diagnostic.
    The refusal is also logged once per (diagnostic, kind, target): callers
    inside checks tend to catch broad exceptions, and a refusal swallowed
    that way would otherwise read as a check that passed.
    """
    scope = _current.get()
    if scope is None or scope.allows(target):
        return
    marker = (scope.name, kind, target)
    with _reported_lock:
        first = marker not in _reported
        _reported.add(marker)
    if first:
        logger.warning(
            "[diagnostic] %s tried to write %s %r — refused (diagnostics are read-only)",
            scope.name, kind, target,
        )
    raise DiagnosticWriteRefused(scope.name, kind, target)
