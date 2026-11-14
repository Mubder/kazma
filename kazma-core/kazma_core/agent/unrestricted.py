"""Whether the work running now was started under ``/unrestricted``.

``/unrestricted`` (``long_task.enable_long_task(unified=True)``) is the
owner's "no limits" switch for one chat: mission budgets and no approval
cards. Work the chat starts elsewhere -- a swarm task it dispatches -- runs
in another task, where the chat's thread is not bound, so the answer is
carried in two ways:

* a chat turn reads its own thread's record (:func:`_thread_is_unrestricted`);
* a dispatched swarm task records the answer in its metadata when it is
  dispatched (``unrestricted``), and the swarm binds it around the worker's
  run (:func:`unrestricted_scope`).

What it lifts beyond the chat turn (live 2026-11-14: a data-collection task
sent from an ``/unrestricted`` Telegram chat ran under the default worker
budget and ended "partial"):

* a swarm worker's tool rounds, time limit and the size of each tool result
  it reads (``swarm.worker``, ``tool_builtins.system``);
* the browser runs in the explicit compatibility mode where this host has no
  OS network isolation (``web_acquire.browser_isolation``): the request
  broker's checks still apply to every request; only the OS boundary is
  missing, which the operator accepted by asking for no limits.

It never lifts the always-ask tools (X posts, folder access), the outbound
allowlist, the protected configuration keys or the deny-before-card
denylist: those are not limits on the work, they are what keeps it Kazma's.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar

logger = logging.getLogger(__name__)

__all__ = [
    "is_unrestricted",
    "unrestricted_scope",
]

_BOUND: ContextVar[bool | None] = ContextVar("kazma_unrestricted", default=None)


def _thread_is_unrestricted(thread_id: str | None) -> bool:
    """The chat *thread_id* has ``/unrestricted`` on now (not paused, not
    expired). Reads the settings store: call it off the event loop."""
    if not thread_id:
        return False
    try:
        from kazma_core.agent.long_task import long_task_status

        status = long_task_status(thread_id)
    except (RuntimeError, OSError, ValueError, LookupError, TypeError) as exc:  # a store that cannot answer: "no"
        logger.warning("[unrestricted] could not read thread %s: %s", thread_id, type(exc).__name__,
                       exc_info=True)
        return False
    return bool(status.get("active") and status.get("unified"))


def is_unrestricted() -> bool:
    """The work running now is under ``/unrestricted``: the value a swarm
    run bound, else the bound chat thread's record. Off the event loop."""
    bound = _BOUND.get()
    if bound is not None:
        return bound
    from kazma_core.safety.hitl import get_current_thread_id

    return _thread_is_unrestricted(get_current_thread_id())


@contextlib.contextmanager
def unrestricted_scope(on: bool | None) -> Iterator[None]:
    """Bind the answer for the work inside (a swarm worker's run). ``None``
    binds nothing."""
    if on is None:
        yield
        return
    token = _BOUND.set(bool(on))
    try:
        yield
    finally:
        _BOUND.reset(token)
