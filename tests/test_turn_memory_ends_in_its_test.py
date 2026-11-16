"""A turn's memory write ends inside the test that ran the turn (2026-11-16).

A finished turn hands its memory write to a daemon thread
("kazma-v2-extract"). It used to outlive the test: monkeypatch had undone the
test's stand-ins by the time it ran, so it embedded the turn with the real
model, and CI failed at random on whichever test it overlapped
(test_done_frame_names_the_answering_model, run 43871325466). The root
conftest's ``_turn_memory_ends_in_its_test`` waits for those threads before
monkeypatch undoes anything.

The pair below runs in a child pytest: the first test starts such a thread,
which reads a monkeypatched value after the test's body ended; the second
checks what it read. With the guard off (KAZMA_TEST_TURN_MEMORY_GUARD=0) it
reads the restored value: the negative control.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HERE = "tests/test_turn_memory_ends_in_its_test.py"
_PROBE = "KAZMA_TURN_MEMORY_PROBE"

STAND_IN = "the test's stand-in"
SEEN: list[str] = []


class _Target:
    value = "the real thing"


def test_a_turn_memory_thread_starts_here(monkeypatch):
    """First of the pair: a memory thread still running when the body ends."""
    if not os.environ.get(_PROBE):
        return
    monkeypatch.setattr(_Target, "value", STAND_IN)

    def write_memory() -> None:
        time.sleep(0.5)
        SEEN.append(_Target.value)

    threading.Thread(target=write_memory, name="kazma-v2-extract", daemon=True).start()


def test_the_thread_saw_the_tests_stand_in():
    """Second of the pair: the thread ran while the stand-in was in place."""
    if not os.environ.get(_PROBE):
        return
    deadline = time.monotonic() + 5
    while not SEEN and time.monotonic() < deadline:
        time.sleep(0.05)
    assert SEEN == [STAND_IN]


def _pair(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"{HERE}::test_a_turn_memory_thread_starts_here",
         f"{HERE}::test_the_thread_saw_the_tests_stand_in"],
        cwd=str(REPO), env={**os.environ, _PROBE: "1", **env},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )


def test_the_memory_thread_ends_before_the_stand_ins_are_undone():
    if os.environ.get(_PROBE):
        return
    run = _pair()
    assert run.returncode == 0, run.stdout[-2000:]


def test_control_without_the_guard_the_thread_sees_the_real_thing():
    if os.environ.get(_PROBE):
        return
    run = _pair(KAZMA_TEST_TURN_MEMORY_GUARD="0")
    assert run.returncode != 0, run.stdout[-2000:]
    assert "the real thing" in run.stdout
