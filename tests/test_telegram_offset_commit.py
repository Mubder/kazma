"""Committing the Telegram offset must not depend on how big update_id is.

2026-08-30. ``_on_update_done`` advanced the committed offset by walking
integers::

    nxt = self._offset + 1
    while self._offset < self._max_seen_update_id and nxt not in pending:
        self._offset = nxt
        self._unacked_updates.discard(nxt)
        nxt += 1

``self._offset`` starts at 0 on every boot and a Telegram ``update_id`` is a
~10-digit number, so the first message after any restart entered a loop of
hundreds of millions of iterations -- synchronously, in a done-callback, on
the event loop.

Kazma froze solid: no logs at all, not even uvicorn recording the health
probes arriving, until the guard killed it. The offset therefore never
committed, Telegram redelivered the same update on the next boot, and it
froze again. Four restarts, one short of the guard's crash-loop cooldown.
Stack dumps caught it still in that loop at 15s, 45s and 106s.

These tests pin both halves: it must be fast at realistic ids, and it must
still never commit past an update that has not finished.
"""

from __future__ import annotations

import time

import pytest
from kazma_gateway.adapters.telegram import TelegramAdapter


class _Offsets:
    """Just the offset bookkeeping, without constructing a live adapter."""

    def __init__(self) -> None:
        self._offset = 0
        self._max_seen_update_id = 0
        self._pending_updates: set[int] = set()
        self._unacked_updates: set[int] = set()

    _on_update_done = TelegramAdapter._on_update_done


class _DoneTask:
    def cancelled(self) -> bool:
        return False

    def exception(self):
        return None


def _seen(o: _Offsets, *ids: int) -> None:
    for uid in ids:
        o._pending_updates.add(uid)
        o._unacked_updates.add(uid)
        o._max_seen_update_id = max(o._max_seen_update_id, uid)


def test_a_realistic_update_id_commits_instantly():
    """The regression. A real id is ~10 digits; the old loop counted to it."""
    o = _Offsets()
    real_id = 987_654_321  # the shape Telegram actually sends
    _seen(o, real_id)

    start = time.monotonic()
    o._on_update_done(real_id, _DoneTask())
    elapsed = time.monotonic() - start

    assert o._offset == real_id
    assert elapsed < 0.5, (
        f"committing one update took {elapsed:.1f}s -- the offset walk is back, "
        "and it blocks the event loop for the whole duration"
    )
    assert not o._unacked_updates


def test_the_offset_never_passes_an_unfinished_update():
    """At-least-once: a crash must redeliver anything still in flight."""
    o = _Offsets()
    _seen(o, 100, 101, 102)

    o._on_update_done(102, _DoneTask())          # newest finishes first
    assert o._offset == 99, (
        "committing to 102 would drop 100 and 101 if the process died now"
    )
    assert {100, 101} <= o._unacked_updates

    o._on_update_done(100, _DoneTask())
    assert o._offset == 100                       # 101 still running, holds it
    assert 101 in o._unacked_updates

    o._on_update_done(101, _DoneTask())
    assert o._offset == 102                       # everything done, commit all
    assert not o._unacked_updates


def test_the_offset_never_goes_backwards():
    o = _Offsets()
    _seen(o, 500)
    o._on_update_done(500, _DoneTask())
    assert o._offset == 500

    _seen(o, 400)                                  # a late/out-of-order arrival
    o._on_update_done(400, _DoneTask())
    assert o._offset == 500, "the committed mark must never rewind"


def test_gaps_between_ids_do_not_cost_anything():
    """Telegram ids are not contiguous from our side; that must not matter."""
    o = _Offsets()
    _seen(o, 1_000_000, 5_000_000)

    start = time.monotonic()
    o._on_update_done(1_000_000, _DoneTask())
    o._on_update_done(5_000_000, _DoneTask())
    elapsed = time.monotonic() - start

    assert o._offset == 5_000_000
    assert elapsed < 0.5, f"a 4M-wide gap cost {elapsed:.1f}s"


def test_a_failed_task_still_commits():
    """Redelivering an already-failing handler is the poison loop itself."""

    class _Failed:
        def cancelled(self) -> bool:
            return False

        def exception(self):
            return RuntimeError("handler blew up")

    o = _Offsets()
    _seen(o, 777)
    o._on_update_done(777, _Failed())
    assert o._offset == 777, (
        "a failing update that never commits comes back on every reconnect"
    )


def test_the_integer_walk_is_gone():
    """Source guard: the shape of the bug, not just its symptom."""
    import inspect

    src = inspect.getsource(TelegramAdapter._on_update_done)
    assert "nxt += 1" not in src, (
        "the offset is being advanced one integer at a time again"
    )


@pytest.mark.parametrize("pending", [set(), {5}, {5, 9}])
def test_unacked_is_pruned_to_match_the_offset(pending):
    o = _Offsets()
    _seen(o, 1, 5, 9, 12)
    for uid in list(o._pending_updates):
        if uid not in pending:
            o._on_update_done(uid, _DoneTask())
    assert all(uid > o._offset for uid in o._unacked_updates), (
        "an id at or below the committed offset is acknowledged and must not "
        "still be tracked as unacked"
    )
