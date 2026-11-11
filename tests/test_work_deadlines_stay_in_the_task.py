"""A deadline on a worker's or a fire's work keeps that work in its caller's task.

On Python 3.11 -- the version CI runs, and the live install's -- the
``asyncio.wait_for(coroutine, timeout)`` that bounded a swarm run, a worker
dispatch, a pattern step, a worker's tool call and a scheduled fire's turn
ran the work in a task of its own. When the caller was cancelled a second
time while that task was still ending (a shutdown after a user's stop; work
that takes a moment to stop), ``wait_for`` gave up waiting and the work went
on in a task nobody owned: the run had ended, its worker still counted a
dispatch in flight, and the fire's turn could not be seen from the fire. The
Swarm soak caught it on Linux CI ("workers with a dispatch in flight
['longrun']" after the engine stopped, 2026-11-10), and the Scheduled soak's
fake clock could not see a fire's turn parked in a task of its own. Python
3.12's ``wait_for`` already runs the work in the caller's task, which is why
it passed on the owner's machine.

``asyncio.timeout`` (3.11+) keeps the work in the caller's task on every
supported Python. The behaviour is held through the product's own
:class:`TimeoutGuard`, with a faithful copy of 3.11's ``wait_for`` as the
negative control on every Python; the gate holds the swarm and the scheduler
to it, each other ``wait_for`` there declared with why it is not work.
"""

from __future__ import annotations

import ast
import asyncio
import functools
from pathlib import Path

import pytest

from kazma_core.swarm.reliability import TimeoutGuard

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "kazma-core" / "kazma_core"


async def _wait_for_311(coro, timeout):
    """Python 3.11's ``asyncio.wait_for`` on a coroutine, as it ran then (the
    old shape, on any Python): the work in a task of its own, and a cancel of
    the caller while that task ends abandons it."""
    loop = asyncio.get_running_loop()
    fut = asyncio.ensure_future(coro)
    waiter = loop.create_future()

    def release(_):
        if not waiter.done():
            waiter.set_result(None)

    fut.add_done_callback(release)
    handle = loop.call_later(timeout, release, None)
    try:
        try:
            await waiter
        except asyncio.CancelledError:
            if fut.done():
                return fut.result()
            fut.remove_done_callback(release)
            ended = loop.create_future()
            fut.add_done_callback(functools.partial(lambda f, _: f.done() or f.set_result(None), ended))
            fut.cancel()
            await ended  # a second cancel lands here: fut is left running
            raise
        if fut.done():
            return fut.result()
        fut.cancel()
        raise TimeoutError
    finally:
        handle.cancel()


class _Work:
    """Work that takes a moment to stop: the first cancel starts its cleanup,
    which runs on until it ends or is cancelled again."""

    def __init__(self) -> None:
        self.running = False
        self.cleanup_s = 2.0

    async def __call__(self) -> str:
        self.running = True
        try:
            await asyncio.sleep(3600)
            return "done"
        except asyncio.CancelledError:
            await asyncio.sleep(self.cleanup_s)  # stopping takes a while
            raise
        finally:
            self.running = False


async def _stop_twice(run: asyncio.Task) -> None:
    await asyncio.sleep(0.05)
    run.cancel()  # a user's stop
    await asyncio.sleep(0.05)
    run.cancel()  # then a shutdown
    with pytest.raises(asyncio.CancelledError):
        await run


@pytest.mark.asyncio
async def test_a_guarded_dispatch_stopped_twice_leaves_no_work_running() -> None:
    work = _Work()
    run = asyncio.create_task(TimeoutGuard(default_timeout=600).execute(work, worker_name="w"))
    await _stop_twice(run)
    await asyncio.sleep(0)
    assert not work.running, "the work outlived the dispatch that was stopped"


@pytest.mark.asyncio
async def test_control_the_311_wait_for_leaves_the_work_running() -> None:
    """Negative control: the old shape abandons the work on the second stop."""
    work = _Work()
    run = asyncio.create_task(_wait_for_311(work(), 600))
    await _stop_twice(run)
    await asyncio.sleep(0)
    try:
        assert work.running, "the 3.11 shape should have left the work running"
    finally:
        work.cleanup_s = 0
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()


@pytest.mark.asyncio
async def test_a_guarded_dispatch_still_times_out() -> None:
    work = _Work()
    work.cleanup_s = 0
    result = await TimeoutGuard(default_timeout=600).execute(work, timeout=0.05, worker_name="w")
    assert result["status"] == "timeout" and not work.running


# ── the gate ───────────────────────────────────────────────────────────────

#: ``wait_for`` calls in the swarm and the scheduler that do not bound a
#: worker's or a turn's work, with why each may keep it.
DECLARED = {
    ("swarm/bus.py", "request_approval"):
        "a backstop on an approval adapter's own request, which has its own deadline; no run counts it",
    ("swarm/durable.py", "_wait_done"): "waits on an event under shield: no work runs inside",
    ("swarm/durable.py", "_cancel_workflow"): "asks Temporal to cancel, best effort, after the work stopped",
    ("swarm/durable.py", "stop_temporal_worker"): "stops Temporal's worker at shutdown, then cancels it",
    ("swarm/shared_approvals.py", "_wait_outcome"): "polls an event for a decision: no work runs inside",
    ("swarm/shared_approvals.py", "await_card"): "edits a platform card, bounded, after the decision",
    ("cron/scheduler.py", "_no_alert_pass"): "waits for the alert lock, not for work",
}


def _wait_for_calls(path: Path, rel: str) -> list[tuple[str, str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []

    def walk(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            called = child.func if isinstance(child, ast.Call) else None
            if ((isinstance(called, ast.Attribute) and called.attr == "wait_for")
                    or (isinstance(called, ast.Name) and called.id == "wait_for")):
                found.append((rel, func, child.lineno))
            walk(child, name)

    walk(tree, "<module>")
    return found


def _undeclared(root: Path) -> list[tuple[str, str, int]]:
    calls = []
    for pkg in ("swarm", "cron"):
        for path in sorted((root / pkg).rglob("*.py")):
            calls += _wait_for_calls(path, path.relative_to(root).as_posix())
    return [c for c in calls if (c[0], c[1]) not in DECLARED]


def test_no_work_in_the_swarm_or_the_scheduler_runs_under_wait_for() -> None:
    assert _undeclared(CORE) == []


def test_every_declaration_names_a_wait_for_that_exists() -> None:
    calls = set()
    for pkg in ("swarm", "cron"):
        for path in sorted((CORE / pkg).rglob("*.py")):
            calls |= {(rel, func) for rel, func, _ in _wait_for_calls(path, path.relative_to(CORE).as_posix())}
    assert set(DECLARED) - calls == set()


def test_control_the_gate_finds_a_planted_wait_for(tmp_path: Path) -> None:
    planted = tmp_path / "swarm"
    planted.mkdir()
    (planted / "engine.py").write_text(
        "import asyncio\n\nasync def run(work):\n    return await asyncio.wait_for(work(), timeout=5)\n",
        encoding="utf-8")
    (tmp_path / "cron").mkdir()
    assert _undeclared(tmp_path) == [("swarm/engine.py", "run", 4)]
