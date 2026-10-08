"""Track effecting invocations across a worker's retry attempts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class DispatchEffects:
    """Shared by child tasks; an invoked mutator makes whole-worker replay unsafe."""

    invoked: bool = False


_dispatch_effects: ContextVar[DispatchEffects | None] = ContextVar("kazma_dispatch_effects", default=None)


@contextmanager
def track_dispatch_effects(effects: DispatchEffects) -> Iterator[None]:
    """Bind one tracker for all attempts of a logical dispatch."""
    token = _dispatch_effects.set(effects)
    try:
        yield
    finally:
        _dispatch_effects.reset(token)


def note_effect_invocation(tool_name: str) -> None:
    """Call immediately before invocation, after validation and approval."""
    from kazma_core.safety.side_effects import is_read_only

    effects = _dispatch_effects.get()
    if effects is not None and not is_read_only(tool_name):
        effects.invoked = True
