"""Every Postgres pool Kazma opens is closed before Python shuts down (2026-11-19).

psycopg_pool's ``ConnectionPool.__del__`` stops the pool's worker threads, and
at interpreter shutdown Python 3.14 refuses to join a thread: every ``kazma``
command that touched Postgres ended with "Exception ignored while calling
deallocator ... ConnectionPool.__del__ ... PythonFinalizationError" (four
times while the owner restored the old Kazma). ``postgres_pool.close_at_exit``
registers a pool for one ``atexit`` handler, which runs before finalization
and closes it; a closed pool's ``__del__`` does nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kazma_core.db import postgres_pool

REPO = Path(__file__).resolve().parents[1]
_PRODUCT = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
            "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui")
#: Async pools are closed by the lifespan that opened them, on its loop.
_NOT_SYNC = {"AsyncConnectionPool"}


def _unregistered(source: str) -> list[int]:
    """Lines of sync ``ConnectionPool(`` constructions in a function that
    does not call ``close_at_exit``."""
    out: list[int] = []
    tree = ast.parse(source)
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
        names = {ast.unparse(c.func).split(".")[-1] for c in calls}
        for call in calls:
            if ast.unparse(call.func).split(".")[-1] == "ConnectionPool" and "close_at_exit" not in names:
                out.append(call.lineno)
    return out


def test_every_sync_pool_is_closed_at_exit():
    offenders = []
    for base in _PRODUCT:
        for path in sorted((REPO / base).rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ConnectionPool(" not in text:
                continue
            offenders += [f"{path.relative_to(REPO).as_posix()}:{line}" for line in _unregistered(text)]
    assert not offenders, offenders


def test_control_a_pool_without_the_registration_is_found():
    planted = (
        "def open_pool(dsn):\n"
        "    return ConnectionPool(conninfo=dsn, open=True)\n"
        "def open_async(dsn):\n"
        "    return AsyncConnectionPool(conninfo=dsn)\n"
        "def open_registered(dsn):\n"
        "    pool = ConnectionPool(conninfo=dsn, open=True)\n"
        "    close_at_exit(pool)\n"
        "    return pool\n"
    )
    assert _unregistered(planted) == [2]


class _Pool:
    def __init__(self, fail=False):
        self.closed_with = None
        self.fail = fail

    def close(self, timeout=5.0):
        if self.fail:
            raise RuntimeError("server gone")
        self.closed_with = timeout


def test_the_exit_handler_closes_every_open_pool(monkeypatch):
    registered = []
    monkeypatch.setattr(postgres_pool, "_exit_closer_registered", False)
    monkeypatch.setattr(postgres_pool.atexit, "register", registered.append)
    monkeypatch.setattr(postgres_pool, "_OPEN_POOLS", postgres_pool.weakref.WeakSet())
    broken, first, second = _Pool(fail=True), _Pool(), _Pool()
    for pool in (broken, first, second):
        postgres_pool.close_at_exit(pool)
    # One handler for the process, however many pools.
    assert registered == [postgres_pool._close_open_pools]
    registered[0]()
    # A pool that fails to close stops no other; each is bounded.
    assert first.closed_with == second.closed_with == postgres_pool._EXIT_CLOSE_TIMEOUT_S
    assert len(postgres_pool._OPEN_POOLS) == 0


def test_a_pool_already_gone_is_not_kept_alive(monkeypatch):
    monkeypatch.setattr(postgres_pool, "_exit_closer_registered", True)
    monkeypatch.setattr(postgres_pool, "_OPEN_POOLS", postgres_pool.weakref.WeakSet())
    pool = _Pool()
    postgres_pool.close_at_exit(pool)
    del pool
    import gc

    gc.collect()
    assert len(postgres_pool._OPEN_POOLS) == 0
