"""``drain_background`` must not wait on loops that never finish.

Twelve consecutive CI runs failed on ``test_documents_api_phase8.py``, each
reporting **"0 failed"** in its own totals: the job exited 1 on a teardown
timeout rather than an assertion, which is how "9,585 tests, 0 failures" and
"CI is red" were both true at once.

The cause was a ten-second tax, not a hang. ``drain_background`` waited on
every retained task, including ``while True`` loops that can only ever end in
cancellation — so it burned its FULL timeout on every lifespan exit.
``KazmaAppBuilder._stop_background_loops`` existed to pre-cancel them for
exactly this reason, but as a hand-maintained list of three; a fourth loop
(``snapshot-maintenance``) was added later and never put on it.

Teardown measured 15.05s per lifespan. ``TestClient`` enters the lifespan once
per test, so a 17-test file spent ~170s in shutdown alone against a 180s file
cap, and the runner called it a POISON hang.

A list someone must remember to update is the defect, so loops declare
themselves at spawn and the drain cancels them first. These tests lock the
behaviour, not the list.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from kazma_core.background import (
    _background,
    _never_completes,
    drain_background,
    spawn_background,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The module keeps process-wide sets; do not leak between tests."""
    before_bg, before_nc = set(_background), set(_never_completes)
    yield
    for task in set(_background) - before_bg:
        task.cancel()
    _background.intersection_update(before_bg)
    _never_completes.intersection_update(before_nc)


async def test_a_never_ending_loop_does_not_burn_the_drain_timeout() -> None:
    """The whole defect, in one assertion.

    Without the flag this waits the full timeout. With it, the loop is
    cancelled up front and the drain returns immediately.
    """

    async def _forever() -> None:
        while True:
            await asyncio.sleep(3600)

    spawn_background(_forever(), name="test-forever", never_completes=True)

    started = time.monotonic()
    await drain_background(timeout=5.0)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"drain took {elapsed:.2f}s — it is waiting on a loop that can never "
        "finish instead of cancelling it. That cost is paid on EVERY lifespan "
        "exit, once per test."
    )


async def test_ordinary_work_is_still_awaited() -> None:
    """The drain must not become 'cancel everything'.

    In-flight real work — a crawl, an embedding rebuild — still has to finish.
    Only self-declared endless loops are cancelled.
    """
    finished: list[bool] = []

    async def _real_work() -> None:
        await asyncio.sleep(0.2)
        finished.append(True)

    spawn_background(_real_work(), name="test-real-work")
    await drain_background(timeout=5.0)

    assert finished == [True], "drain cancelled genuine in-flight work"


async def test_a_loop_is_cancelled_not_merely_abandoned() -> None:
    """Cancelled, so it stops touching its resources.

    An abandoned loop keeps running against a store the rest of teardown is
    closing, which is the shape that has segfaulted this process before.
    """
    task = spawn_background(
        _sleep_forever(), name="test-cancel-me", never_completes=True
    )
    await drain_background(timeout=5.0)
    await asyncio.sleep(0)
    assert task.cancelled() or task.done(), "loop survived the drain"


async def _sleep_forever() -> None:
    while True:
        await asyncio.sleep(3600)


def test_every_endless_loop_declares_itself() -> None:
    """Source guard: a ``while True`` background loop needs the flag.

    This is the check that would have caught ``snapshot-maintenance``. It is
    deliberately narrow — it only looks at the spawn sites of the known
    long-running loops — because the general question "does this coroutine
    ever return" is undecidable and a broad heuristic here would be noise.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    expected = {
        "kazma-core/kazma_core/checkpoint_retention.py": "checkpoint-retention",
        "kazma-core/kazma_core/time_travel.py": "snapshot-maintenance",
        "kazma-core/kazma_core/swarm/engine.py": "swarm-maintenance",
        "kazma-core/kazma_core/x_api/mentions_fire.py": "x-mentions-poll",
        "kazma-ui/kazma_ui/app.py": "liveness-heartbeat",
    }
    missing = []
    for rel, name in expected.items():
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        idx = src.find(f'name="{name}"')
        if idx < 0:
            missing.append(f"{name}: spawn site not found in {rel}")
            continue
        window = src[max(0, idx - 400): idx + 400]
        if "never_completes=True" not in window:
            missing.append(f"{name} in {rel} is not marked never_completes=True")

    assert not missing, (
        "A never-ending background loop is not declared as one, so "
        "drain_background will wait its full timeout on it at every "
        "shutdown — once per test that builds an app.\n  " + "\n  ".join(missing)
    )
