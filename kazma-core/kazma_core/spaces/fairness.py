"""Fairness between spaces, and between the people of one space (Spaces S4-04).

S2-08 gave each space its own limits and a share of the install's slots for
crawls, research runs and swarm tasks. This adds what S2-08 left: one person
in a shared space cannot take that space's whole share, turns and scheduled
jobs have shares too, and the queues that serve every space take turns.

**A person's share** (:func:`person_share`). In a shared space with more than
one member each person may hold at most half the space's share of any kind of
work, rounded up -- so two people at their share fill it, and one person
never takes it all. In a personal space, or a space with one member, the
person's share is the space's. The admin sets the space's shares (People);
the half is Kazma's rule, not a setting.

**Work running at once** (``spaces.space_limits.RUNNING_KINDS``): crawls,
research runs, swarm tasks, turns and scheduled jobs. A space's share and its
person's are asked before the work takes one of the install's slots, so a
space at its share is refused, or waits, holding nothing another space could
use. Turns and scheduled jobs have no store of their own that knows what
runs: they are counted here, in this process (:class:`_FairSlots`).

* **Turns** (``turn_slots``): a new turn beyond its space's or its person's
  share waits up to :data:`TURN_WAIT_S` for one to end, then ends with an
  honest refusal (``agent.turn_budget.turn_segment_budget``), recorded as
  answered, not failed (a share is the limit working). An approval's
  resume is a turn already admitted: always let in, and counted. A turn
  inside another (a sub-agent's) is part of it and not counted again.
* **Scheduled jobs** (``scheduled_slots``): a due job waits for its space's
  and its person's slot before it takes one of the scheduler's
  (``cron.scheduler``), and the due list takes turns by space, then by
  person (``cron.store``), so a space with hundreds of jobs due never holds
  up another's. A reminder takes no slot: it makes no model call, and
  behind a running task fire it would ring late.

**Queues take turns.** Document jobs are claimed from the space with the
fewest running, then from the person of that space with the fewest running
(``documents.jobs.fair_claim_order``); the memory lane claims round robin by
space (N37, ``memory.task_queue``).

**Uploads.** A space's documents are bounded by its queue and rate budgets
(``documents.admission``, per tenant: a space is a tenant), and every rate
window is per person and per space once Spaces is enforced
(``kazma_ui.rate_limit``): a person's two browsers and an API token share one
window, and a space's people together get twice a person's.

Only where Spaces is enforced: an install that does not enforce it reads
nothing here and counts nothing, and every queue keeps its order.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from kazma_core.spaces.scope import spaces_enforced

__all__ = [
    "SPACE_WINDOW_FACTOR",
    "TURN_WAIT_S",
    "held",
    "holding",
    "person_refusal_text",
    "person_share",
    "scheduled_slots",
    "shares",
    "turn_slots",
]

logger = logging.getLogger(__name__)

#: How long a new turn beyond its share waits for one to end.
TURN_WAIT_S = 20.0
#: A space's rate window is this many people's (``kazma_ui.rate_limit``).
SPACE_WINDOW_FACTOR = 2


def _members(space_id: str, reg: dict[str, Any]) -> int:
    return sum(1 for m in reg.get("memberships") or () if isinstance(m, dict) and m.get("space") == space_id)


def person_share(space_share: int, space_id: str, reg: dict[str, Any] | None = None) -> int:
    """A person's share of *space_id*'s *space_share*: the whole of it in a
    personal space or one with a single member, else half, rounded up (never
    below one). Blocking when *reg* is not given (the identity store)."""
    if reg is None:
        from kazma_core.spaces.identity import read_registry

        reg = read_registry()
    space = (reg.get("spaces") or {}).get(str(space_id or "")) or {}
    if space.get("personal_of") or _members(space_id, reg) <= 1:
        return int(space_share)
    return max(1, math.ceil(int(space_share) / 2))


def shares(kind: str, space_id: str) -> tuple[int, int] | None:
    """``(space's share, a person's share)`` of *kind* in *space_id*, or None
    while the install does not enforce Spaces (or no space is named).
    Blocking (the identity store): async callers use ``asyncio.to_thread``."""
    from kazma_core.spaces.space_limits import RUNNING_KINDS, _limits_of

    if kind not in RUNNING_KINDS:
        raise ValueError(f"not a kind of work a space runs: {kind!r}")
    if not spaces_enforced() or not space_id:
        return None
    from kazma_core.spaces.identity import read_registry

    reg = read_registry()
    space_share = int(getattr(_limits_of(space_id, reg), kind))
    return space_share, person_share(space_share, space_id, reg)


def person_refusal_text(kind: str, share: int) -> str:
    """Why one more of *kind* is refused for the person: they hold their
    share of the space's."""
    from kazma_core.spaces.space_limits import _KIND_WORDS

    return (f"You already run {share} {_KIND_WORDS[kind]} at once, your share of this space's; "
            "try again when one ends.")


class _FairSlots:
    """The slots of one kind of work this process runs (turns, scheduled
    jobs), counted per space and per person (Spaces S4-04).

    :meth:`take` admits one when its space and its person are under their
    shares (:func:`shares`), waiting for one to end up to ``wait_s``; else it
    raises ``space_limits.SpaceOverLimit``. It returns the release, or None
    while the install does not enforce Spaces (nothing counted). ``force``
    admits and counts it whatever the shares: work already admitted -- an
    approval's resume -- is never refused for a slot.

    Counts are process-wide (any thread, any event loop); a waiter is woken
    on its own loop.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._lock = threading.Lock()
        self._space: Counter[str] = Counter()
        self._person: Counter[tuple[str, str]] = Counter()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []

    def running(self, space_id: str) -> int:
        """How many *space_id* holds now (this process)."""
        with self._lock:
            return self._space.get(str(space_id or ""), 0)

    def running_for(self, space_id: str, person: str) -> int:
        """How many *person* holds in *space_id* now (this process)."""
        with self._lock:
            return self._person.get((str(space_id or ""), str(person or "")), 0)

    def _try(self, space: str, person: str, cap: tuple[int, int], force: bool) -> str | None:
        """Take one under the lock; None when taken, else the refusal."""
        from kazma_core.spaces.space_limits import share_refusal

        if not force:
            refused = share_refusal(self.kind, self._space.get(space, 0), cap, person=person,
                                    person_running=self._person.get((space, person), 0))
            if refused:
                return refused
        self._space[space] += 1
        if person:
            self._person[(space, person)] += 1
        return None

    def _release(self, space: str, person: str) -> Callable[[], None]:
        released = threading.Event()

        def release() -> None:
            if released.is_set():
                return
            released.set()
            with self._lock:
                self._space[space] -= 1
                if self._space[space] <= 0:
                    del self._space[space]
                if person:
                    self._person[(space, person)] -= 1
                    if self._person[(space, person)] <= 0:
                        del self._person[(space, person)]
                waiters, self._waiters = self._waiters, []
            for loop, future in waiters:
                try:
                    loop.call_soon_threadsafe(_wake, future)
                except RuntimeError:  # its loop is closed: nobody waits there
                    continue

        return release

    async def take(self, space_id: str, person: str, *, wait_s: float = 0.0,
                   force: bool = False) -> Callable[[], None] | None:
        # Spaces off: nothing counted, and no thread hop for every turn and
        # due job (the switch is in memory; ``shares`` reads the identity
        # store only once it is on).
        if not spaces_enforced():
            return None
        space = str(space_id or "")
        who = str(person or "")
        cap = await asyncio.to_thread(shares, self.kind, space)
        if cap is None:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(wait_s))
        while True:
            with self._lock:
                refused = self._try(space, who, cap, force)
                if refused is None:
                    return self._release(space, who)
                future: asyncio.Future[None] = loop.create_future()
                self._waiters.append((loop, future))
            left = deadline - loop.time()
            timed_out = left <= 0
            try:
                if not timed_out:
                    async with asyncio.timeout(left):
                        await future
            except TimeoutError:
                timed_out = True
            finally:
                # Woken, timed out or cancelled: this waiter is done.
                with self._lock:
                    self._waiters = [w for w in self._waiters if w[1] is not future]
            if timed_out:
                from kazma_core.spaces.space_limits import SpaceOverLimit

                raise SpaceOverLimit(self.kind, refused)


#: Set while the work running holds a slot of :data:`turn_slots` or
#: :data:`scheduled_slots`: a turn run inside it -- a sub-agent's, a
#: scheduled task's own turn -- is part of it and takes no turn slot (it
#: would wait on its own work).
_HOLDING: ContextVar[bool] = ContextVar("kazma_fair_slot_held", default=False)


def holding() -> bool:
    """Whether the work running already holds a slot (:func:`held`)."""
    return _HOLDING.get()


@contextmanager
def held() -> Iterator[None]:
    """Mark the block as work that holds a slot."""
    token = _HOLDING.set(True)
    try:
        yield
    finally:
        _HOLDING.reset(token)


def _wake(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


#: The turns this process runs, per space and per person.
turn_slots = _FairSlots("turns")
#: The scheduled jobs this process fires, per space and per person.
scheduled_slots = _FairSlots("scheduled")
