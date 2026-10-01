"""Research progress and cancel cross threads safely (2026-10-01).

``asyncio`` objects are not thread-safe. The research pipeline writes progress
through ``asyncio.to_thread(update_session, ...)``, and the Research routes
are plain ``def`` handlers in FastAPI's threadpool, so ``update_session``,
``cancel_session`` and ``delete_session`` run in worker threads. They used to
``put_nowait`` onto the SSE readers' ``asyncio.Queue`` and ``cancel()`` the
run's task from that thread. Now a queue is only put to, and a task only
cancelled, on its own loop (``call_soon_threadsafe``).
"""

from __future__ import annotations

import asyncio
import threading

import pytest


@pytest.fixture()
def rs(tmp_path, monkeypatch):
    import kazma_core.tools.research_session as mod

    def _db_path():
        tmp_path.mkdir(parents=True, exist_ok=True)
        return tmp_path / "research_sessions.db"

    monkeypatch.setattr(mod, "_db_path", _db_path)
    mod._SUBS.clear()
    mod._RUNNING.clear()
    yield mod
    mod._SUBS.clear()
    mod._RUNNING.clear()


def _record_puts(monkeypatch, q: asyncio.Queue) -> list[int]:
    """The thread of every put onto *q*."""
    threads: list[int] = []
    real = q.put_nowait

    def recording(item):
        threads.append(threading.get_ident())
        real(item)

    monkeypatch.setattr(q, "put_nowait", recording)
    return threads


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_progress_from_a_worker_thread_is_put_on_the_readers_loop(rs, monkeypatch):
    s = await asyncio.to_thread(rs.create_session, "threads")
    q = rs.subscribe_progress(s.id)
    assert (await asyncio.wait_for(q.get(), 5))["type"] == "snapshot"
    puts = _record_puts(monkeypatch, q)

    await asyncio.to_thread(rs.update_session, s.id, status="running", stage="plan", message="Planning")
    event = await asyncio.wait_for(q.get(), 5)

    assert event["type"] == "progress" and event["stage"] == "plan"
    assert puts == [threading.get_ident()]


async def test_subscribing_from_a_thread_seeds_the_snapshot_on_the_loop(rs, monkeypatch):
    """The stream route subscribes in a thread, so its snapshot read stays
    off the loop; the snapshot still lands on the reader's loop."""
    s = await asyncio.to_thread(rs.create_session, "seed")
    loop = asyncio.get_running_loop()
    q = await asyncio.to_thread(rs.subscribe_progress, s.id, loop=loop)
    snapshot = await asyncio.wait_for(q.get(), 5)
    assert snapshot["type"] == "snapshot" and snapshot["session"]["id"] == s.id


async def test_the_old_direct_put_ran_on_the_worker_thread(rs, monkeypatch):
    """Negative control: the old broadcast put onto the queue from whatever
    thread called ``update_session``."""
    s = await asyncio.to_thread(rs.create_session, "old")
    q = rs.subscribe_progress(s.id)
    await asyncio.wait_for(q.get(), 5)
    puts = _record_puts(monkeypatch, q)

    def old_publish(session_id, event):
        for _loop, queue in list(rs._SUBS.get(session_id, [])):
            queue.put_nowait(event)

    monkeypatch.setattr(rs, "_publish", old_publish)
    await asyncio.to_thread(rs.update_session, s.id, status="running", stage="plan", message="x")
    await _settle()
    assert puts and puts[0] != threading.get_ident()


class _FakeRun:
    """Stands in for the run's asyncio.Task and records where it is cancelled."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.cancelled_on: list[int] = []

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def done(self) -> bool:
        return False

    def cancel(self, msg=None) -> bool:
        self.cancelled_on.append(threading.get_ident())
        return True


async def test_cancel_from_a_worker_thread_cancels_on_the_runs_loop(rs):
    s = await asyncio.to_thread(rs.create_session, "cancel")
    await asyncio.to_thread(rs.update_session, s.id, status="running", stage="plan", message="x")
    run = _FakeRun(asyncio.get_running_loop())
    rs._RUNNING[s.id] = run

    out = await asyncio.to_thread(rs.cancel_session, s.id)
    await _settle()

    assert out is not None and out.status == "cancelled"
    assert run.cancelled_on == [threading.get_ident()]


async def test_the_old_cancel_ran_on_the_worker_thread(rs, monkeypatch):
    """Negative control: the old cancel called ``task.cancel()`` in place."""
    s = await asyncio.to_thread(rs.create_session, "old cancel")
    await asyncio.to_thread(rs.update_session, s.id, status="running", stage="plan", message="x")
    run = _FakeRun(asyncio.get_running_loop())
    rs._RUNNING[s.id] = run
    monkeypatch.setattr(rs, "_cancel_on_its_loop", lambda task: task.cancel())

    await asyncio.to_thread(rs.cancel_session, s.id)
    await _settle()
    assert run.cancelled_on and run.cancelled_on[0] != threading.get_ident()


async def test_a_reader_on_a_closed_loop_does_not_break_progress(rs):
    """A subscriber whose loop has closed is skipped, not an error."""
    s = await asyncio.to_thread(rs.create_session, "closed")
    dead = asyncio.new_event_loop()
    dead.close()
    rs._SUBS.setdefault(s.id, []).append((dead, asyncio.Queue()))
    out = await asyncio.to_thread(rs.update_session, s.id, status="running", stage="plan", message="x")
    assert out is not None and out.stage == "plan"
