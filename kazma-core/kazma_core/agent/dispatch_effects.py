"""Track effecting invocations: per worker dispatch, per step, per task.

A worker dispatch keeps one tracker across its retry attempts: a mutating
tool invoked makes whole-worker replay unsafe. A swarm task and each pattern
step bind trackers of their own (Swarm audit SW-E04, 2026-10-14): when a
deadline, a cancel, the watchdog or a shutdown interrupts the work, the task
records which tools that change things had run (``execution_outcome:
unknown``) -- the interruption used to drop that evidence, and a step that
timed out after a write was labelled safe to retry.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

__all__ = [
    "DispatchEffects",
    "note_effect_invocation",
    "track_dispatch_effects",
    "track_effects",
]

#: Tool names kept per tracker (the evidence, bounded).
_MAX_TOOLS = 32


@dataclass
class DispatchEffects:
    """Shared by child tasks; an invoked mutator makes replay unsafe."""

    invoked: bool = False
    tools: list[str] = field(default_factory=list)

    def note(self, tool_name: str) -> None:
        self.invoked = True
        if tool_name not in self.tools and len(self.tools) < _MAX_TOOLS:
            self.tools.append(tool_name)


_dispatch_effects: ContextVar[DispatchEffects | None] = ContextVar("kazma_dispatch_effects", default=None)
#: The task's and the current step's trackers (outermost first).
_scoped_effects: ContextVar[tuple[DispatchEffects, ...]] = ContextVar("kazma_scoped_effects", default=())


@contextmanager
def track_dispatch_effects(effects: DispatchEffects) -> Iterator[None]:
    """Bind one tracker for all attempts of a logical dispatch."""
    token = _dispatch_effects.set(effects)
    try:
        yield
    finally:
        _dispatch_effects.reset(token)


@contextmanager
def track_effects(effects: DispatchEffects) -> Iterator[None]:
    """Add a task- or step-level tracker: it sees every invocation in its
    scope, child tasks included (they copy the context)."""
    token = _scoped_effects.set((*_scoped_effects.get(), effects))
    try:
        yield
    finally:
        _scoped_effects.reset(token)


def note_effect_invocation(tool_name: str) -> None:
    """Call immediately before invocation, after validation and approval."""
    trackers = [t for t in (_dispatch_effects.get(), *_scoped_effects.get()) if t is not None]
    if not trackers:
        return
    from kazma_core.safety.side_effects import is_read_only

    if is_read_only(tool_name):
        return
    for tracker in trackers:
        tracker.note(tool_name)
