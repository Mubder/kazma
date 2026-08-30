"""A blocked event loop must leave evidence behind.

2026-08-30: one redelivered Telegram message froze the loop completely. The
application log's last line was "Enqueued from ..." and then nothing -- not
the consumer, not even uvicorn logging the health requests arriving. The guard
killed the process three probes later, so the message was never acknowledged,
so Telegram redelivered it on the next boot. Four restarts, one short of the
guard's crash-loop cooldown.

Nothing could be learned from any of it: a blocked loop cannot log what
blocked it, and py-spy could not attach to the process. The watchdog exists so
that never costs a night again.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from kazma_core.observability import loop_stall


@pytest.fixture(autouse=True)
def _enable_watchdog(monkeypatch):
    """conftest turns the watchdog off for the suite; these tests want it on.

    A test process is entitled to block the loop, so a 6,000-test run was
    reporting a 440s stall and writing a dump nobody wanted. The switch is off
    globally and opted back in here, where blocking the loop is the point.
    """
    monkeypatch.delenv("KAZMA_LOOP_STALL_WATCHDOG", raising=False)


@pytest.mark.asyncio
async def test_a_blocked_loop_is_detected_and_dumped(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)
    monkeypatch.setattr(loop_stall, "_REDUMP_EVERY_S", 0.5)

    def the_blocking_call() -> None:
        time.sleep(3.0)

    with caplog.at_level("CRITICAL"):
        task = loop_stall.start_stall_watchdog(threshold_s=1.0, interval_s=0.2)
        assert task is not None
        await asyncio.sleep(0.5)
        the_blocking_call()  # synchronous, on the loop — the whole point
        await asyncio.sleep(0.5)
    task.cancel()

    dumps = list(tmp_path.glob("stall-*.txt"))
    assert dumps, "a stalled loop produced no dump"

    text = dumps[0].read_text(encoding="utf-8")
    assert "unresponsive for" in text
    assert "the_blocking_call" in text, (
        "the dump must name the frame that blocked the loop -- that is the "
        "only thing it is for"
    )
    assert any("[loop-stall]" in r.getMessage() for r in caplog.records), (
        "the stall must also reach the log, from the thread that is not blocked"
    )


@pytest.mark.asyncio
async def test_a_healthy_loop_dumps_nothing(tmp_path, monkeypatch):
    """A watchdog that cries constantly gets switched off."""
    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)

    task = loop_stall.start_stall_watchdog(threshold_s=1.0, interval_s=0.1)
    await asyncio.sleep(2.5)  # loop stays responsive throughout
    task.cancel()

    assert not list(tmp_path.glob("stall-*.txt"))


def test_no_running_loop_is_not_an_error():
    """Startup must never fail because the watchdog could not start."""
    assert loop_stall.start_stall_watchdog() is None


@pytest.mark.asyncio
async def test_a_failing_dump_never_raises(monkeypatch):
    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(loop_stall, "stall_dump_dir", boom)
    assert loop_stall._write_dump(9.0, "x") is None


def test_the_threshold_fires_inside_the_guard_window():
    """Evidence written after the kill is no evidence at all.

    The guard kills after FAILURES_TO_KILL x PROBE_INTERVAL_S, which is
    3 x 30 = 90s by default (scripts/service/kazma_guard.py).
    """
    assert loop_stall.DEFAULT_THRESHOLD_S < 90.0 / 3, (
        "the dump must land well before the guard terminates the process"
    )
    assert loop_stall.DEFAULT_INTERVAL_S < loop_stall.DEFAULT_THRESHOLD_S


@pytest.mark.asyncio
async def test_the_kill_switch_stops_it_starting(monkeypatch, tmp_path):
    """KAZMA_LOOP_STALL_WATCHDOG=0 — the switch conftest uses for the suite."""
    monkeypatch.setenv("KAZMA_LOOP_STALL_WATCHDOG", "0")
    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)

    assert loop_stall.start_stall_watchdog(threshold_s=0.5, interval_s=0.1) is None

    time.sleep(1.5)  # blocks the loop well past the threshold
    assert not list(tmp_path.glob("stall-*.txt")), (
        "the watchdog ran despite the kill-switch"
    )


def test_the_suite_disables_it():
    """Source guard: conftest must keep the suite quiet."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert 'KAZMA_LOOP_STALL_WATCHDOG", "0"' in src


@pytest.mark.asyncio
async def test_cancelling_the_heartbeat_stops_the_watcher(tmp_path, monkeypatch):
    """A stopped watchdog must stop watching.

    Found by the kill-switch test above, which failed because watchdogs from
    earlier tests were still running: the thread outlived its cancelled
    heartbeat task, saw a beat that would never be refreshed, and reported an
    ever-growing stall every 30s for the life of the process.
    """
    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)
    monkeypatch.setattr(loop_stall, "_REDUMP_EVERY_S", 0.2)

    task = loop_stall.start_stall_watchdog(threshold_s=0.5, interval_s=0.1)
    assert task is not None
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    time.sleep(2.0)  # far past the threshold, with no heartbeat running
    assert not list(tmp_path.glob("stall-*.txt")), (
        "the watcher thread kept firing after its heartbeat was cancelled"
    )
