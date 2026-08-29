"""The procedural-outcome recorder must use ONE worker, not a thread per call.

It used to spawn a fresh `daemon=True` thread on every tool execution, each
opening its own SQLite connection and running the full schema-ensure (DDL +
FTS5 rebuild probes) before writing one row. Concurrent record threads
crashed the interpreter with a Windows access violation whose traceback moved
between `_ensure_fts5`, `ensure_primary_schema` and `config_store.get` — which
is why it read like three unrelated bugs.

Measured on ``tests/test_truncation_retry.py``: 4/10 runs crashed before,
1/80 after. Disabling the threads entirely gave 0/10, which is what
identified the concurrency between them as the fault.
"""

from __future__ import annotations

import threading
import time

import pytest
from kazma_core.agent import tool_registry as tr

WORKER_NAME = "kazma-procedural-record"


def _workers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == WORKER_NAME]


@pytest.fixture(autouse=True)
def _quiet_worker():
    """Leave the module as we found it — other tests share this process."""
    tr._PROCEDURAL_STOPPING.clear()
    yield
    tr._stop_procedural_worker(timeout=3.0)
    tr._PROCEDURAL_STOPPING.clear()
    tr._PROCEDURAL_WORKER = None


def test_many_outcomes_use_one_worker_thread():
    """The whole point: N tool calls must not become N threads."""
    for i in range(30):
        tr._record_procedural_outcome(f"probe_one_worker_{i}", {"i": i}, success=True)
    time.sleep(0.5)
    assert len(_workers()) <= 1, (
        f"{len(_workers())} record threads alive — the thread-per-call spawn is back, "
        "and with it the concurrent-SQLite crash."
    )


def test_recording_never_raises_into_the_tool_path():
    """Best-effort by contract: a tool result must never fail on this."""
    tr._record_procedural_outcome("probe_no_raise", {"weird": object()}, success=False)
    tr._record_procedural_outcome("probe_no_raise", {}, success=True)


def test_queue_is_bounded_and_drops_rather_than_blocking():
    """A full queue must drop records, not block a tool call or grow memory."""
    assert tr._PROCEDURAL_QUEUE.maxsize > 0, "an unbounded queue is a memory leak"

    tr._PROCEDURAL_STOPPING.set()  # keep the worker from draining
    try:
        started = time.monotonic()
        for i in range(tr._PROCEDURAL_QUEUE.maxsize + 50):
            tr._record_procedural_outcome(f"probe_flood_{i}", {"i": i}, success=True)
        elapsed = time.monotonic() - started
    finally:
        tr._PROCEDURAL_STOPPING.clear()

    assert elapsed < 5.0, f"enqueue blocked a tool call for {elapsed:.1f}s"


def test_no_new_work_is_started_during_shutdown():
    """Starting SQLite work while the interpreter tears down is the race."""
    tr._PROCEDURAL_STOPPING.set()
    try:
        before = tr._PROCEDURAL_QUEUE.qsize()
        tr._record_procedural_outcome("probe_after_stop", {}, success=True)
        assert tr._PROCEDURAL_QUEUE.qsize() == before
    finally:
        tr._PROCEDURAL_STOPPING.clear()


def test_stop_is_idempotent():
    """atexit may run alongside an explicit stop; neither may hang or raise."""
    tr._stop_procedural_worker(timeout=1.0)
    tr._stop_procedural_worker(timeout=1.0)
