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


# ── a caller that names its store (Spaces S4-01 step 5) ──────────────────


def test_a_caller_that_names_sqlite_gets_its_file_whatever_the_address(monkeypatch: pytest.MonkeyPatch,
                                                                        tmp_path: Path) -> None:
    """A test's world named no store and took Postgres whenever the run's
    address was set (the Postgres job sets it for every test)."""
    from kazma_gateway.stores import checkpoint as module

    monkeypatch.setattr("kazma_core.db.pg_helpers.use_postgres", lambda: True)
    opened: list[str] = []

    async def _no_postgres(dsn: str, **_: Any) -> Any:
        opened.append(dsn)
        raise ImportError("not in this test")

    monkeypatch.setattr("kazma_core.checkpoints_pg.open_postgres_checkpointer", _no_postgres)

    async def _go(backend: str | None) -> Any:
        manager = await module.create_checkpoint_manager(str(tmp_path / f"{backend}.db"), backend=backend)
        await manager.close()
        return manager

    asyncio.run(_go("sqlite"))
    assert opened == [], "named sqlite: Postgres is not tried"
    asyncio.run(_go(None))
    assert len(opened) == 1, "the control: as set up, Postgres is tried first"
    with pytest.raises(ValueError):
        asyncio.run(_go("mysql"))


# ── the step 4 review: the pool's check and a closed pool (L1, L3) ───────


@pytest.mark.parametrize("kept", [True, False])
def test_a_cancel_in_the_pools_check_stays_a_cancel(monkeypatch: pytest.MonkeyPatch, kept: bool) -> None:
    """L1: psycopg_pool counts a cancel in its check as a bad connection, takes
    another and returns: a cancelled turn went on."""
    # CI's light Tests job has no psycopg_pool: skipped there, collected everywhere.
    AsyncConnectionPool = pytest.importorskip("psycopg_pool").AsyncConnectionPool  # noqa: N806
    from kazma_core import checkpoints_pg

    async def _cancelled(conn: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(AsyncConnectionPool, "check_connection", staticmethod(_cancelled))
    check = checkpoints_pg._check_kept_cancel if kept else AsyncConnectionPool.check_connection

    async def _checkout() -> str:
        try:
            await check(object())
        except asyncio.CancelledError:  # what psycopg_pool does with it
            pass
        await asyncio.sleep(0)  # the pool's next wait
        return "went on"

    if kept:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_checkout())
    else:  # the control: the plain check, and the turn goes on
        assert asyncio.run(_checkout()) == "went on"


def test_a_closed_checkpointer_pool_makes_no_space_half() -> None:
    """L3: armed, a checkout after close() made a new half."""
    PoolClosed = pytest.importorskip("psycopg_pool").PoolClosed  # noqa: N806 - see the test above
    from kazma_core import checkpoints_pg

    pool = checkpoints_pg._reported_pool_class()(conninfo="postgresql://x@127.0.0.1:1/x", min_size=0, max_size=1,
                                                    open=False)
    with pytest.raises(PoolClosed):
        asyncio.run(pool._space_half())
    assert pool._kazma_space_half is None
