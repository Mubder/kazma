"""The checkpointer's door, without a database (Spaces S4-01 step 4).

* ``row_security.checkpoint_door`` answers whose statements a checkpointer
  checkout is for: today's path while Spaces is off and nothing is armed; a
  space's door once Spaces is enforced, armed or not (review M7); armed,
  nothing said is refused;
* the gateway's ``CheckpointManager`` runs each saver call in the thread's
  own space, and nothing while Spaces is off;
* a listing holds no door across a ``yield``: between two rows and after a
  ``break`` the consumer's door is its own (review M5), the control being a
  listing that holds it;
* a gate over the product's source: no ``pgconn.exec_`` inside an ``async
  def`` (it blocks the loop for the server's whole answer, review H4), with a
  planted control. No door is held across a ``yield`` anywhere:
  ``tests/test_row_work_gates.py`` holds every door form to that.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import row_security
from kazma_core.spaces import scope
from kazma_core.spaces.row_work import Door, install_statements, statements_of
from kazma_core.spaces.scope import NoSpaceBound, bind_scope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def lock(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(row_security, "_armed", False)
    monkeypatch.setattr(scope, "_enforced", False)
    return row_security


def _door_now() -> str | None:
    door = Door.now()
    return None if door is None else (door.space or "install")


# ── whose statements a checkpointer checkout is for ──────────────────────


def test_spaces_off_and_nothing_armed_is_todays_path(lock: Any) -> None:
    assert lock.checkpoint_door() is None
    with statements_of("sa"):
        assert lock.checkpoint_door() is None, "Spaces off: nothing more is sent"


def test_enforced_a_spaces_door_stamps_and_nothing_is_refused(lock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with statements_of("sa"):
        assert lock.checkpoint_door() == "sa"
    with bind_scope("sb", "owner"):
        assert lock.checkpoint_door() == "sb"
    with install_statements("a test"):
        assert lock.checkpoint_door() is None, "the install's: today's path"
    assert lock.checkpoint_door() is None, "nothing said, not armed: not refused"


def test_armed_nothing_said_is_refused(lock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lock, "_armed", True)
    with pytest.raises(NoSpaceBound):
        lock.checkpoint_door()
    with install_statements("a test"):
        assert lock.checkpoint_door() == ""
    with statements_of("sa"):
        assert lock.checkpoint_door() == "sa"


# ── the manager runs each call in the thread's space ─────────────────────


class _Saver:
    """A Postgres-named saver that records the door of each call."""

    def __init__(self) -> None:
        self.doors: list[tuple[str, str | None]] = []

    async def aget_tuple(self, config: Any) -> Any:
        self.doors.append(("get", _door_now()))
        return {"config": config}

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, versions: Any) -> Any:
        self.doors.append(("put", _door_now()))
        return config

    async def alist(self, config: Any, **_: Any) -> Any:
        for i in range(3):
            self.doors.append(("list", _door_now()))
            yield type("T", (), {"config": {"configurable": {"thread_id": f"t{i}"}}})()


_Saver.__name__ = "AsyncPostgresSaver"


def _manager(monkeypatch: pytest.MonkeyPatch, homes: dict[str, str]) -> tuple[Any, _Saver]:
    from kazma_gateway.stores import checkpoint as module

    saver = _Saver()
    manager = module.CheckpointManager.__new__(module.CheckpointManager)
    module.CheckpointManager.__init__(manager, saver)  # type: ignore[arg-type]
    monkeypatch.setattr(module._threads, "known_space", lambda thread_id: homes.get(thread_id))
    return manager, saver


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def test_each_call_runs_in_the_threads_own_space(lock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    manager, saver = _manager(monkeypatch, {"ta": "sa"})

    async def _go() -> None:
        await manager.aput(_config("ta"), {}, {}, {})  # nothing bound: the thread's space
        with bind_scope("sa", "owner"):
            await manager.aget_tuple(_config("ta"))

    asyncio.run(_go())
    assert saver.doors == [("put", "sa"), ("get", "sa")]


def test_spaces_off_the_manager_says_no_door(lock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, saver = _manager(monkeypatch, {"ta": "sa"})
    asyncio.run(manager.aput(_config("ta"), {}, {}, {}))
    assert saver.doors == [("put", None)], "as before: nothing bound by the manager"


# ── a listing holds no door across a yield (review M5) ───────────────────


def test_a_listing_holds_no_door_between_rows(lock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    manager, saver = _manager(monkeypatch, {"ta": "sa"})
    between: list[str | None] = []

    async def _go() -> None:
        async for _row in manager.alist(_config("ta")):
            between.append(_door_now())
            if len(between) == 2:
                break
        between.append(_door_now())  # after the break

    asyncio.run(_go())
    assert [d for kind, d in saver.doors if kind == "list"] == ["sa", "sa"], "each step in the thread's space"
    assert between == [None, None, None], "the consumer's own door, between rows and after a break"


def test_a_listing_that_holds_its_door_leaks_it(lock: Any) -> None:
    # The control: the shape the review measured, a door held across a yield.
    async def _holding() -> Any:
        with statements_of("sa"):
            for i in range(3):
                yield i

    seen: list[str | None] = []

    async def _go() -> None:
        async for _row in _holding():
            seen.append(_door_now())
            break
        seen.append(_door_now())

    asyncio.run(_go())
    assert seen[0] == "sa", "held across the yield: in force in the consumer"


# ── the gate (review H4) ────────────────────────────────────────────

def _product() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                             "kazma-*/kazma_*/*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    return {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed.stdout.split()))}


def _own_nodes(func: ast.AST) -> list[ast.AST]:
    """The nodes of *func*'s own body, not of the functions inside it."""
    out: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(func))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        out.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return out


#: The sync helpers that send with libpq's blocking ``exec_`` (step 4 review L4).
_SYNC_SENDERS = ("exec_", "begin_space", "end_space", "check_space_role")


def _blocking_in_async(sources: dict[str, str]) -> list[str]:
    """``.exec_(...)`` (libpq's blocking send), or a sync helper that sends
    with it, called inside an ``async def``."""
    out = []
    for rel, text in sources.items():
        if not any(f"{name}(" in text for name in _SYNC_SENDERS):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.AsyncFunctionDef):
                for inner in _own_nodes(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) \
                        else ""
                    if name in _SYNC_SENDERS:
                        out.append(f"{rel}::{node.name}")
    return sorted(set(out))


def test_no_blocking_libpq_send_runs_on_the_loop() -> None:
    assert _blocking_in_async(_product()) == [], "await conn.execute(..., prepare=False), never pgconn.exec_"
    planted = {"kazma-core/kazma_core/planted.py": "async def begin(conn):\n    conn.pgconn.exec_(b'BEGIN')\n",
               "kazma-core/kazma_core/helper.py": ("async def begin(conn, space):\n"
                                                   "    row_security.begin_space(conn, space)\n")}
    assert _blocking_in_async(planted) == ["kazma-core/kazma_core/helper.py::begin",
                                           "kazma-core/kazma_core/planted.py::begin"]
    # Its control: the awaited sender is no sync one.
    awaited = {"kazma-core/kazma_core/ok.py": ("async def begin(conn, space):\n"
                                               "    await row_security.begin_space_async(conn, space, role=True)\n")}
    assert _blocking_in_async(awaited) == []


# ── the step 4 review: the pool's check and a closed pool (L1, L3) ───────


def _pool_like(check: Any) -> Any:
    """psycopg_pool 3.3.1's checkout as far as its check: a client's error --
    ``CancelledError`` among them -- puts the connection back, waits its
    backoff and checks another; that second check answers."""

    async def checkout() -> str:
        for _ in range(2):
            try:
                await check(object())
                return "a connection"
            except (asyncio.CancelledError, OSError):  # psycopg_pool's CLIENT_EXCEPTIONS
                await asyncio.sleep(0.01)  # its backoff: the pool's next wait
        raise AssertionError("no connection was checked")

    return checkout


@pytest.fixture()
def slow_check(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The plain check, its first round trip ten seconds long: a cancel lands
    inside it. CI's light Tests job has no psycopg_pool: skipped there,
    collected everywhere."""
    pool_class = pytest.importorskip("psycopg_pool").AsyncConnectionPool
    calls: list[Any] = []

    async def _check(conn: Any) -> None:
        calls.append(conn)
        if len(calls) == 1:
            await asyncio.sleep(10)

    monkeypatch.setattr(pool_class, "check_connection", staticmethod(_check))
    return pool_class


def _the_check(pool_class: Any, kept: bool) -> Any:
    from kazma_core import checkpoints_pg

    return checkpoints_pg._check_kept_cancel if kept else pool_class.check_connection


@pytest.mark.parametrize("kept", [True, False], ids=["kept", "the plain check"])
def test_a_stop_in_the_pools_check_stops_the_turn_by_its_one_cancel(slow_check: Any, kept: bool) -> None:
    """L1: psycopg_pool counts a cancel in its check as a bad connection, takes
    another and returns: a turn stopped at that moment went on. Delta review
    N1: the cancel it swallows is given back, not a second one added -- the
    task stopped, counted as cancelled once."""
    check = _the_check(slow_check, kept)

    async def stop() -> tuple[str, int]:
        task = asyncio.create_task(_pool_like(check)())
        await asyncio.sleep(0.05)  # inside the check's round trip
        task.cancel()
        try:
            outcome = await task
        except asyncio.CancelledError:
            outcome = "stopped"
        return outcome, task.cancelling()

    outcome, cancelling = asyncio.run(stop())
    if kept:
        assert (outcome, cancelling) == ("stopped", 1)
    else:  # the control: the plain check, and the turn goes on
        assert outcome == "a connection"


@pytest.mark.parametrize("window", ["asyncio.timeout", "wait_for"])
@pytest.mark.parametrize("kept", [True, False], ids=["kept", "the plain check"])
def test_a_window_ending_in_the_pools_check_is_a_timeout(slow_check: Any, kept: bool, window: str) -> None:
    """Delta review N1: a window (``asyncio.timeout``, ``wait_for``; the
    turn's 30 minutes among them) ending in the check. With a second cancel
    added, the window took back its own and let ``CancelledError`` out: the
    turn's limit read as a Stop, with no honest end written."""
    check = _the_check(slow_check, kept)

    async def run() -> str:
        if window == "wait_for":
            return await asyncio.wait_for(_pool_like(check)(), 0.05)
        async with asyncio.timeout(0.05):
            return await _pool_like(check)()

    async def inside_a_task() -> tuple[str, int]:
        task = asyncio.create_task(run())
        try:
            outcome = await task
        except TimeoutError:
            outcome = "timed out"
        return outcome, task.cancelling()

    outcome, cancelling = asyncio.run(inside_a_task())
    if kept:
        assert (outcome, cancelling) == ("timed out", 0)
    else:  # the control: the plain check, and the window runs past its end
        assert outcome == "a connection"


def test_a_closed_checkpointer_pool_makes_no_space_half() -> None:
    """L3: armed, a checkout after close() made a new half."""
    PoolClosed = pytest.importorskip("psycopg_pool").PoolClosed  # noqa: N806 - see the test above
    from kazma_core import checkpoints_pg

    pool = checkpoints_pg._reported_pool_class()(conninfo="postgresql://x@127.0.0.1:1/x", min_size=0, max_size=1,
                                                    open=False)
    with pytest.raises(PoolClosed):
        asyncio.run(pool._space_half())
    assert pool._kazma_space_half is None


def test_a_pool_closed_while_the_half_waits_for_its_lock_makes_none() -> None:
    """Delta review N2: open at the first look, closed by the time the lock
    was had (another task making the half, then ``close()``): a half was made
    on a closed pool, and nothing ever closed it."""
    PoolClosed = pytest.importorskip("psycopg_pool").PoolClosed  # noqa: N806 - see the tests above
    from kazma_core import checkpoints_pg

    class _ClosedMeanwhile(checkpoints_pg._reported_pool_class()):  # type: ignore[misc,valid-type]
        looks: list[bool]

        @property
        def closed(self) -> bool:
            return self.looks.pop(0) if self.looks else True

    pool = _ClosedMeanwhile(conninfo="postgresql://x@127.0.0.1:1/x", min_size=0, max_size=1, open=False)
    pool.looks = [False, True]
    with pytest.raises(PoolClosed):
        asyncio.run(pool._space_half())
    assert pool._kazma_space_half is None
