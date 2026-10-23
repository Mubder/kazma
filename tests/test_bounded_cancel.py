"""A cancelled task that does not end must never stop Kazma.

Live 2026-10-23: the Telegram typing indicator's stop cancelled its loop
while a ``sendChatAction`` was in flight, and awaited it holding the
indicator's lock. anyio absorbed the cancel (measured on the install's own
Python, 3.11 with the selector loop: 3 cancels in 4,000 of a request against a
local server; the task stays in the socket read with ``cancelling() == 1``),
the loop -- which catches errors and runs again -- never ended, and every
later message on every chat app waited on that lock before its turn began.
Two hours of "typing..." and no answer; no error anywhere.

Here a cancel-swallowing task stands in for that request: the old shapes hang
on it (the negative controls), the new ones end within their bound.
"""

from __future__ import annotations

import ast
import asyncio
import re
import time
from pathlib import Path

import pytest

from kazma_core.bounded_cancel import cancel_and_wait, wait_for_bounded, wait_then_cancel

ROOT = Path(__file__).resolve().parents[1]


#: A stand-in stops absorbing after this long, so a failing test cannot leave
#: a task that hangs the event loop's teardown.
_ABSORB_AT_MOST_S = 10.0


async def _swallow_cancels(release: asyncio.Event, *, swallow: int | None = None) -> str:
    """Absorb cancels (all, or the first *swallow*) until *release* is set."""
    absorbed = 0
    give_up = time.monotonic() + _ABSORB_AT_MOST_S
    while True:
        try:
            await release.wait()
            return "released"
        except asyncio.CancelledError:
            absorbed += 1
            if (swallow is not None and absorbed > swallow) or time.monotonic() > give_up:
                raise


# ── the helpers ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_old_cancel_then_await_hangs_on_a_task_that_absorbs_its_cancel():
    """Negative control: ``task.cancel(); await task`` never returns."""
    release = asyncio.Event()
    task = asyncio.create_task(_swallow_cancels(release))
    await asyncio.sleep(0)

    async def old_shape() -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    waiter = asyncio.create_task(old_shape())
    done, _ = await asyncio.wait({waiter}, timeout=1.0)
    assert not done, "the old shape returned: the stand-in no longer absorbs cancels"
    release.set()
    await waiter


@pytest.mark.asyncio
async def test_cancel_and_wait_gives_up_within_its_bound_and_says_so(caplog):
    release = asyncio.Event()
    task = asyncio.create_task(_swallow_cancels(release))
    await asyncio.sleep(0)
    start = time.monotonic()
    with caplog.at_level("WARNING", logger="kazma_core.bounded_cancel"):
        ended = await cancel_and_wait(task, what="the stand-in", timeout=0.6)
    assert ended is False
    assert time.monotonic() - start < 2.0
    assert any("the stand-in did not end" in r.getMessage() for r in caplog.records)
    release.set()
    assert await task == "released"


@pytest.mark.asyncio
async def test_cancel_and_wait_delivers_the_cancel_again():
    """A task that absorbs one cancel ends on the next one (the old shape waits)."""
    release = asyncio.Event()
    task = asyncio.create_task(_swallow_cancels(release, swallow=1))
    await asyncio.sleep(0)
    assert await cancel_and_wait(task, what="once-absorbing", timeout=3.0) is True
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_and_wait_reads_a_failure_and_never_raises_it():
    async def boom() -> None:
        raise ValueError("x")

    task = asyncio.create_task(boom())
    await asyncio.sleep(0)
    assert await cancel_and_wait(task, what="failed") is True
    assert await cancel_and_wait(None, what="nothing") is True


@pytest.mark.asyncio
async def test_wait_then_cancel_lets_a_task_finish_then_bounds_the_cancel():
    async def quick() -> int:
        await asyncio.sleep(0.05)
        return 1

    task = asyncio.create_task(quick())
    assert await wait_then_cancel(task, what="quick", wait=2.0) is True
    assert task.result() == 1

    release = asyncio.Event()
    stuck = asyncio.create_task(_swallow_cancels(release))
    start = time.monotonic()
    assert await wait_then_cancel(stuck, what="stuck", wait=0.2, cancel_timeout=0.5) is False
    assert time.monotonic() - start < 2.0
    release.set()
    await stuck


@pytest.mark.asyncio
async def test_wait_for_bounded_returns_raises_and_times_out_within_its_bound():
    async def value() -> int:
        return 7

    async def fails() -> None:
        raise KeyError("k")

    assert await wait_for_bounded(value(), 1.0, what="value") == 7
    with pytest.raises(KeyError):
        await wait_for_bounded(fails(), 1.0, what="fails")

    release = asyncio.Event()
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await wait_for_bounded(_swallow_cancels(release), 0.2, what="stuck", cancel_timeout=0.5)
    assert time.monotonic() - start < 2.0
    release.set()


@pytest.mark.asyncio
async def test_wait_for_bounded_cancels_its_awaitable_when_the_caller_is_cancelled():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def inner() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    outer = asyncio.create_task(wait_for_bounded(inner(), 30, what="inner"))
    await started.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert cancelled.is_set()


# ── the typing indicator, the live case ────────────────────────────────────


class _OldTypingKeepalive:
    """The indicator as it was until 2026-10-23 (the negative control)."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._refs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def start(self, target_id, typing_fn):
        async with self._lock:
            self._refs[target_id] = self._refs.get(target_id, 0) + 1
            self._tasks[target_id] = asyncio.create_task(self._loop(target_id, typing_fn))

    async def stop(self, target_id):
        async with self._lock:
            self._refs.pop(target_id, None)
            task = self._tasks.pop(target_id, None)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self, target_id, typing_fn):
        try:
            while True:
                try:
                    await typing_fn(target_id)
                except Exception:
                    pass
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            return


def _absorbing_typing(release: asyncio.Event, calls: list[str], in_flight: asyncio.Event):
    """A sendChatAction whose cancel is absorbed until *release* (anyio's read)."""

    async def typing(target_id: str) -> None:
        calls.append(target_id)
        if target_id != "telegram:stuck":
            return
        in_flight.set()
        give_up = time.monotonic() + _ABSORB_AT_MOST_S
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                if time.monotonic() > give_up:
                    raise

    return typing


async def _a_later_message_starts(keepalive, typing):
    """Does another chat's turn get past ``start()`` while the stuck stop runs?

    Returns (it did, the stop task, the later start task)."""
    stopper = asyncio.create_task(keepalive.stop("telegram:stuck"))
    await asyncio.sleep(0.05)
    later = asyncio.create_task(keepalive.start("discord:other", typing))
    done, _ = await asyncio.wait({later}, timeout=1.0)
    return bool(done), stopper, later


@pytest.mark.asyncio
async def test_old_indicator_blocks_every_later_message_behind_an_absorbed_cancel():
    release = asyncio.Event()
    calls: list[str] = []
    in_flight = asyncio.Event()
    typing = _absorbing_typing(release, calls, in_flight)
    old = _OldTypingKeepalive(interval=0.05)
    await old.start("telegram:stuck", typing)
    stuck_loop = old._tasks["telegram:stuck"]
    await in_flight.wait()  # the request is in flight
    started, stopper, later = await _a_later_message_starts(old, typing)
    assert started is False
    # Clean up: the request ends, a fresh cancel ends the loop, the lock frees.
    release.set()
    for _ in range(40):  # re-cancel until the loop is past the absorbing request
        if stopper.done():
            break
        stuck_loop.cancel()
        await asyncio.wait({stopper}, timeout=0.05)
    await asyncio.wait({stopper, later}, timeout=2.0)
    assert stopper.done() and later.done()
    await old.stop("discord:other")


@pytest.mark.asyncio
async def test_indicator_never_holds_a_later_message_and_its_loop_ends():
    from kazma_gateway.typing_keepalive import TypingKeepalive

    release = asyncio.Event()
    calls: list[str] = []
    in_flight = asyncio.Event()
    typing = _absorbing_typing(release, calls, in_flight)
    keepalive = TypingKeepalive(interval=0.05, stop_timeout=0.5)
    await keepalive.start("telegram:stuck", typing)
    await in_flight.wait()
    loop_task = keepalive._tasks["telegram:stuck"]

    start = time.monotonic()
    started, stopper, _later = await _a_later_message_starts(keepalive, typing)
    assert started is True
    await stopper  # returns within its bound, the loop left running
    assert time.monotonic() - start < 2.0
    assert not loop_task.done()

    # The request ends (in production: its 30 s read timeout); the stop flag
    # then ends the loop at once -- no "typing..." forever.
    release.set()
    await asyncio.wait({loop_task}, timeout=1.0)
    assert loop_task.done() and not loop_task.cancelled()  # ended by its stop flag
    await keepalive.stop("discord:other")


# ── shutdown: a stuck handler no longer holds the server's stop ───────────


@pytest.mark.asyncio
async def test_gateway_stop_bounds_its_wait_for_a_stuck_handler(monkeypatch):
    """Live, the old drain waited on the stuck handlers until the guard killed
    the server 60 s into its stop, and the rest of the shutdown never ran."""
    from kazma_gateway import gateway as gateway_mod
    from kazma_gateway.gateway import GatewayManager

    monkeypatch.setattr(gateway_mod, "_HANDLER_DRAIN_S", 0.3)
    release = asyncio.Event()
    gm = GatewayManager(max_queue_size=10)
    gm._started = True
    stuck = asyncio.create_task(_swallow_cancels(release), name="stuck-handler")
    gm._handler_tasks.add(stuck)
    await asyncio.sleep(0)

    try:
        # Negative control: the old drain does not end.
        old = asyncio.gather(stuck, return_exceptions=True)
        done, _ = await asyncio.wait({old}, timeout=0.5)
        assert not done

        start = time.monotonic()
        await gm.stop()
        assert time.monotonic() - start < 8.0
        assert not gm._handler_tasks and gm._started is False
    finally:
        release.set()
        await asyncio.wait({stuck}, timeout=2.0)


# ── the gate: no unbounded cancel-and-wait in product code ────────────────

#: ``asyncio.wait_for`` on something that is not a task doing work.
DECLARED_WAIT_FOR = {
    ("kazma-core/kazma_core/cli/ask.py", "fut"):
        "a plain Future from loop.create_future(); nothing runs inside it, so its cancel is immediate",
}


def _product_files() -> list[Path]:
    files: list[Path] = []
    for pkg in sorted(ROOT.glob("kazma-*/kazma_*")):
        if pkg.name.endswith("_tests") or not pkg.is_dir():
            continue
        files.extend(sorted(pkg.rglob("*.py")))
    return files


def unbounded_cancel_waits(source: str, rel: str) -> list[str]:
    """Every ``X.cancel()`` followed by ``await X``, and every
    ``asyncio.wait_for(<task>)``, in *source*."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody", "handlers"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, st in enumerate(block):
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
                        and isinstance(st.value.func, ast.Attribute)
                        and st.value.func.attr == "cancel" and not st.value.args):
                    continue
                target = ast.unparse(st.value.func.value)
                pattern = re.compile(r"\bawait\s+" + re.escape(target) + r"(?![\w.(\[])")
                for nxt in block[i + 1:i + 3]:
                    if pattern.search(ast.unparse(nxt)):
                        found.append(f"{rel}:{st.lineno} {target}.cancel() then await {target}")
        if (isinstance(node, ast.Call) and ast.unparse(node.func) in ("asyncio.wait_for", "wait_for")
                and node.args and isinstance(node.args[0], (ast.Name, ast.Attribute))):
            arg = ast.unparse(node.args[0])
            if (rel, arg) not in DECLARED_WAIT_FOR:
                found.append(f"{rel}:{node.lineno} asyncio.wait_for({arg})")
    return found


def test_no_unbounded_cancel_and_wait_in_product_code():
    offenders: list[str] = []
    for path in _product_files():
        rel = path.relative_to(ROOT).as_posix()
        offenders.extend(unbounded_cancel_waits(path.read_text(encoding="utf-8"), rel))
    assert not offenders, (
        "Waits forever on a task whose cancel can be absorbed (2026-10-23 Telegram "
        "silence). Use kazma_core.bounded_cancel.cancel_and_wait / wait_then_cancel / "
        "wait_for_bounded:\n  " + "\n  ".join(offenders)
    )


def test_the_gate_finds_each_old_shape():
    """Negative control: the shapes this change removed are found."""
    planted = '''
async def a(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

async def b(self):
    self._t.cancel()
    with suppress(asyncio.CancelledError):
        await self._t

async def c(self):
    await asyncio.wait_for(self._task, timeout=5.0)

async def d(t, other):
    t.cancel()
    await other            # not the cancelled task
    await asyncio.wait_for(make(), timeout=1)   # a fresh awaitable
'''
    found = unbounded_cancel_waits(planted, "planted.py")
    assert len(found) == 3, found
    assert any("task.cancel() then await task" in f for f in found)
    assert any("self._t.cancel() then await self._t" in f for f in found)
    assert any("wait_for(self._task)" in f for f in found)


def test_every_declared_wait_for_is_still_there():
    for (rel, arg), why in DECLARED_WAIT_FOR.items():
        assert why
        assert f"wait_for({arg}" in (ROOT / rel).read_text(encoding="utf-8"), (rel, arg)
