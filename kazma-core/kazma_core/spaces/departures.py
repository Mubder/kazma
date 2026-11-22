"""Who has just left a space, for what is still open on their behalf (the
S2-12 review, finding 7).

Membership is checked on every request (S1-06), but a WebSocket stays open
and a stream keeps sending after its person left the space. The identity
store, the one writer of memberships, tells this module every change it
writes (:func:`note`): a membership that went is a departure, one that came
back clears it. The web app listens (``kazma_ui.live_connections``) and
closes that person's sockets and streams in that space; a stream asks
:func:`departed` each time it wakes.

In this process only, and in memory: a restart closes every connection
anyway, and every new request is checked against the store.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

__all__ = ["departed", "note", "on_departure"]

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DEPARTED: set[tuple[str, str]] = set()
_LISTENERS: list[Callable[[str, str], None]] = []
#: Departures kept at most (a leave is rare; a store of every one is not needed).
_MAX = 10_000


def note(before: Iterable[tuple[str, str]], after: Iterable[tuple[str, str]]) -> None:
    """The memberships ``(account, space)`` before and after a change the
    identity store wrote. Tells every listener of each departure."""
    before_set, after_set = set(before), set(after)
    gone = before_set - after_set
    back = after_set - before_set
    with _LOCK:
        _DEPARTED.difference_update(back)
        if gone:
            if len(_DEPARTED) + len(gone) > _MAX:
                _DEPARTED.clear()
            _DEPARTED.update(gone)
        listeners = list(_LISTENERS)
    for account, space in sorted(gone):
        for listener in listeners:
            try:
                listener(account, space)
            except (RuntimeError, OSError, ValueError):  # a listener's fault never undoes the change
                logger.warning("[spaces] a departure listener failed for %s in %s", account, space,
                               exc_info=True)


def departed(account: str | None, space: str | None) -> bool:
    """Whether *account* left *space* since this process started (and has
    not come back). No I/O."""
    if not account or not space:
        return False
    with _LOCK:
        return (str(account), str(space)) in _DEPARTED


def on_departure(listener: Callable[[str, str], None]) -> Callable[[], None]:
    """Call *listener(account, space)* for every departure from now on, in
    the thread that wrote the change. Returns what stops it."""
    with _LOCK:
        _LISTENERS.append(listener)

    def stop() -> None:
        with _LOCK:
            if listener in _LISTENERS:
                _LISTENERS.remove(listener)

    return stop
