"""The database doors in report mode (Spaces S4-01, step 1).

Every checkout of Kazma's own Postgres is counted by the door it goes through
(a space's, the install's by its reason, or none), by its caller, with
nothing refused. Each test fails on the code before step 1, where a checkout
counted nothing (``kazma_core.db.space_doors`` did not exist and no pool
asked ``Door.now``). No database: the pools are stand-ins; the real pools are
in ``tests/test_space_doors_postgres.py``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import statistics
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import space_doors
from kazma_core.spaces.row_work import Door, install_statements, statements_of
from kazma_core.spaces.scope import bind_scope, install_work
from kazma_core.tenant_context import tenant_scope

LOGGER = "kazma_core.db.space_doors"


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("KAZMA_SPACE_DOOR_REPORT", raising=False)
    space_doors.reset()
    yield
    space_doors.reset()


class _Inner:
    """psycopg's pool, as far as ``PostgresPool.connection`` uses it."""

    def __init__(self) -> None:
        self.handed = 0

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[str]:
        self.handed += 1
        yield "a connection"


@pytest.fixture
def pool() -> Any:
    from kazma_core.db.postgres_pool import PostgresPool

    made = object.__new__(PostgresPool)
    made._pool = _Inner()
    made._driver_errors = (ConnectionError,)
    return made


def _checkout(pool: Any) -> str:
    with pool.connection() as conn:
        return conn


def _unbound(work: Any) -> Any:
    """Run *work* where nothing is bound, whatever an earlier test of this
    process left bound in its context."""
    return contextvars.Context().run(work)


def _only(pool_name: str = "shared") -> dict[str, Any]:
    [entry] = [e for e in space_doors.entries() if e["pool"] == pool_name]
    return entry


# ── each door, at the shared pool's checkout ─────────────────────────────


@pytest.mark.parametrize(("enter", "door", "how", "reason"), [
    (lambda: bind_scope("family", "layla"), "space", "scope", ""),
    (lambda: statements_of("family"), "space", "row", ""),
    (lambda: install_statements("a sweep over every space"), "install", "statements", "a sweep over every space"),
    (lambda: install_work("a backup of the install"), "install", "work", "a backup of the install"),
    (lambda: tenant_scope("family"), "none", "a tenant only", ""),
])
def test_a_checkout_is_counted_by_the_door_it_goes_through(pool: Any, enter: Any, door: str, how: str,
                                                           reason: str) -> None:
    def run() -> str:
        with enter():
            return _checkout(pool)

    assert _unbound(run) == "a connection"
    entry = _only()
    assert (entry["door"], entry["how"], entry["reason"], entry["count"]) == (door, how, reason, 1)
    assert pool._pool.handed == 1, "the checkout itself goes on as before"


def test_with_nothing_bound_it_is_the_finding_with_where_it_came_from(pool: Any) -> None:
    def store_method() -> str:
        return _checkout(pool)

    def background_loop() -> str:
        return store_method()

    _unbound(background_loop)
    entry = _only()
    assert (entry["door"], entry["how"]) == ("none", "nothing")
    callers = [c.rsplit(".", 1)[-1].rsplit("::", 1)[-1] for c in entry["callers"]]
    assert callers[:3] == ["_checkout", "store_method", "background_loop"], entry["callers"]
    assert len(callers) == space_doors.CHAIN
    assert not any("postgres_pool" in c or "contextlib" in c for c in callers), "the door is no one's caller"


def test_a_door_is_never_the_caller_kazmas_own_function_is(pool: Any) -> None:
    """The name is the gates' (``file::function``), so the report and the
    declared lists read alike."""
    from kazma_core.db.pg_backup import verify_required_pg_tables

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, sql: str) -> None:
            return None

        def fetchall(self) -> list[dict[str, str]]:
            return [{"tablename": "kazma_settings"}]

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    @contextmanager
    def connection(timeout: float | None = None) -> Iterator[_Conn]:
        yield _Conn()

    pool._pool.connection = connection
    with bind_scope("family", "layla"):
        verify_required_pg_tables(pool)
    entry = _only()
    assert entry["callers"] == ["kazma-core/kazma_core/db/pg_backup.py::verify_required_pg_tables"]
    assert (entry["door"], entry["reason"]) == ("install", "which of Kazma's tables the database has")


def test_execute_counts_its_caller_not_itself(pool: Any) -> None:
    class _Cursor:
        description = None

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, *a: object) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def commit(self) -> None:
            return None

    @contextmanager
    def connection(timeout: float | None = None) -> Iterator[_Conn]:
        yield _Conn()

    pool._pool.connection = connection

    def a_store() -> None:
        pool.execute_one("SELECT 1")

    with statements_of("family"):
        a_store()
    assert _only()["callers"][0].endswith(".a_store"), "not PostgresPool.execute: it only passes it on"


def test_the_settings_table_is_the_installs_inside_a_request() -> None:
    """Decision 4: the settings are outside every space's lock. A settings
    read in a person's request is not that space's statement."""
    from kazma_core.config_store import ConfigStore

    store = object.__new__(ConfigStore)
    store._pg_down = False
    with bind_scope("family", "layla"), store._pg_access():
        door = Door.now()
    assert door is not None and door.space == "" and door.how == "statements"
    assert "settings table" in door.reason
    with bind_scope("family", "layla"):
        assert Door.now() == Door(space="family"), "the request's own statements stay its space's"


# ── quiet, bounded, never in the way ─────────────────────────────────────


def test_one_line_per_new_caller_never_one_per_statement(pool: Any, caplog: pytest.LogCaptureFixture) -> None:
    def a_loop() -> None:
        _checkout(pool)

    def another_loop() -> None:
        _checkout(pool)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(50):
            _unbound(a_loop)
        _unbound(another_loop)
        with bind_scope("family", "layla"):
            a_loop()
    lines = [r for r in caplog.records if r.name == LOGGER]
    assert len(lines) == 2 and all(r.levelno == logging.INFO for r in lines)
    assert "a_loop" in lines[0].getMessage() and "no door" in lines[0].getMessage()
    assert sum(e["count"] for e in space_doors.entries()) == 52


def test_a_caller_the_report_holds_is_not_said_again(pool: Any, tmp_path: Path,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    def a_loop() -> None:
        _checkout(pool)

    report = tmp_path / "space_doors.json"
    _unbound(a_loop)
    space_doors.flush(report)
    space_doors.reset()  # the next start of the server
    space_doors.start(report)
    # Beside other files a logger may already be at INFO, and the first
    # call's line captured (prepush, 2026-11-26): only what follows counts.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _unbound(a_loop)
    assert not [r for r in caplog.records if r.name == LOGGER]
    assert _only()["count"] == 1, "still counted"


def test_the_counter_is_bounded_and_says_so_once(pool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(space_doors, "MAX_ENTRIES", 3)
    reasons = [f"a sweep number {n} of the install" for n in range(6)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for reason in reasons:
            with install_statements(reason):
                _checkout(pool)
    assert len(space_doors.entries()) == 3
    assert len([r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]) == 1
    space_doors.flush(tmp_path / "r.json")
    kept = space_doors.read_report(tmp_path / "r.json")
    assert (kept["dropped"], kept["checkouts"]) == (3, 6)


def test_a_failure_inside_the_counter_never_reaches_the_checkout(pool: Any, monkeypatch: pytest.MonkeyPatch,
                                                                 tmp_path: Path,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    def broken(depth: int) -> tuple[str, ...]:
        raise RuntimeError("a frame that cannot be read")

    monkeypatch.setattr(space_doors, "_callers", broken)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _checkout(pool) == "a connection"
        assert _checkout(pool) == "a connection"
    assert len([r for r in caplog.records if r.name == LOGGER]) == 1, "said once"
    space_doors.flush(tmp_path / "r.json")
    assert space_doors.read_report(tmp_path / "r.json")["failures"] == 2


def test_switched_off_it_counts_nothing(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SPACE_DOOR_REPORT", "0")
    space_doors.reset()
    assert _checkout(pool) == "a connection"
    assert space_doors.entries() == [] and not space_doors.reporting()


def test_a_checkout_costs_microseconds() -> None:
    """Measured, not assumed: the counter is on every live checkout (about
    1 ms each against a local server). Generous bound: a loaded machine."""
    def batch(n: int = 2000) -> float:
        began = time.perf_counter()
        for _ in range(n):
            space_doors.checkout("shared")
        return (time.perf_counter() - began) / n * 1e6

    with bind_scope("family", "layla"):
        bound = min(batch() for _ in range(5))
    unbound = min(batch() for _ in range(5))
    assert bound < 50 and unbound < 50, (bound, unbound)
    assert statistics.fmean([bound, unbound]) > 0


# ── the report on disk ───────────────────────────────────────────────────


def test_the_report_adds_up_across_restarts(pool: Any, tmp_path: Path) -> None:
    report = tmp_path / "space_doors.json"

    def a_loop() -> None:
        _checkout(pool)

    for _ in range(3):
        _unbound(a_loop)
    assert space_doors.flush(report) == 1
    assert space_doors.flush(report) == 0, "nothing new: nothing written"
    _unbound(a_loop)
    space_doors.flush(report)
    space_doors.reset()  # a restart: the count of this process starts again
    for _ in range(2):
        _unbound(a_loop)
    with bind_scope("family", "layla"):
        a_loop()
    space_doors.flush(report)
    kept = space_doors.read_report(report)
    [none] = [e for e in kept["entries"] if e["door"] == "none"]
    assert none["count"] == 6 and none["first"] <= none["last"]
    assert kept["checkouts"] == 7
    assert kept["entries"][0]["door"] == "none", "the findings first"
    assert json.loads(report.read_text(encoding="utf-8"))["version"] == 1


def test_a_report_that_cannot_be_written_keeps_its_counts(pool: Any, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.atomic_files as atomic_files

    report = tmp_path / "space_doors.json"
    _checkout(pool)
    real = atomic_files.atomic_write_text

    def full_disk(*a: object, **k: object) -> None:
        raise OSError("no space left on the device")

    monkeypatch.setattr(atomic_files, "atomic_write_text", full_disk)
    with pytest.raises(OSError):
        space_doors.flush(report)
    monkeypatch.setattr(atomic_files, "atomic_write_text", real)
    _checkout(pool)
    space_doors.flush(report)
    assert space_doors.read_report(report)["checkouts"] == 2


def test_an_unreadable_report_is_said_and_started_again(pool: Any, tmp_path: Path,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    report = tmp_path / "space_doors.json"
    report.write_text("{ not json", encoding="utf-8")
    _checkout(pool)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        space_doors.flush(report)
    assert any("cannot be read" in r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert space_doors.read_report(report)["checkouts"] == 1


def test_the_maintenance_runner_keeps_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pool: Any) -> None:
    from kazma_core.memory import worker_bootstrap

    report = tmp_path / "space_doors.json"
    monkeypatch.setattr(space_doors, "report_path", lambda: report)
    _checkout(pool)
    dict(worker_bootstrap._MAINTENANCE_SWEEPS)["space door report"]()
    assert space_doors.read_report(report)["checkouts"] == 1


def test_no_space_and_no_statement_is_kept(pool: Any, tmp_path: Path) -> None:
    with bind_scope("family-of-layla", "layla"):
        _checkout(pool)
    with statements_of("omar-space"):
        _checkout(pool)
    space_doors.flush(tmp_path / "r.json")
    text = (tmp_path / "r.json").read_text(encoding="utf-8")
    assert "family-of-layla" not in text and "omar-space" not in text and "layla" not in text


# ── the other pools ──────────────────────────────────────────────────────


async def test_the_checkpointer_pool_counts_each_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("psycopg_pool")
    from contextlib import asynccontextmanager

    from kazma_core.checkpoints_pg import _reported_pool_class
    from psycopg_pool import AsyncConnectionPool

    @asynccontextmanager
    async def handed(self: Any, timeout: float | None = None) -> Any:
        yield "a connection"

    monkeypatch.setattr(AsyncConnectionPool, "connection", handed)
    made = _reported_pool_class()("postgresql://nowhere/none", open=False)
    assert isinstance(made, AsyncConnectionPool), "LangGraph's saver takes it as its own"

    async def aget_tuple() -> str:
        async with made.connection() as conn:
            return conn

    with bind_scope("family", "layla"):
        assert await aget_tuple() == "a connection"
    entry = _only("checkpoints")
    assert (entry["door"], entry["how"]) == ("space", "scope")
    assert entry["callers"][0].endswith(".aget_tuple"), "not the pool, nor psycopg's or contextlib's frames"


@pytest.mark.parametrize("same", [True, False])
def test_the_memory_mirror_counts_kazmas_own_database_only(monkeypatch: pytest.MonkeyPatch, same: bool) -> None:
    pytest.importorskip("psycopg")
    from kazma_core.memory.state_backend import PostgresStateBackend

    kazmas = "postgresql://kazma@db.example:5432/kazma"
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: kazmas)
    backend = PostgresStateBackend(kazmas if same else "postgresql://kazma@db.example:5432/memory")
    monkeypatch.setattr(backend, "_open_connection", lambda: "a connection")
    assert backend._connect() == "a connection"
    seen = [e for e in space_doors.entries() if e["pool"] == "memory mirror"]
    assert len(seen) == (1 if same else 0), "a mirror elsewhere is outside the lock (design section 3)"


@pytest.mark.parametrize("same", [True, False])
def test_pgvector_counts_kazmas_own_database_only(monkeypatch: pytest.MonkeyPatch, same: bool) -> None:
    psycopg = pytest.importorskip("psycopg")
    from kazma_core.memory.backends import PgvectorBackend

    kazmas = "postgresql://kazma@db.example:5432/kazma"
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: kazmas)
    backend = PgvectorBackend(dsn=kazmas if same else "postgresql://kazma@other.example:5432/kazma")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: "a connection")
    with install_statements("a ping of the vectors"):
        assert backend._connect() == "a connection"
    seen = [e for e in space_doors.entries() if e["pool"] == "pgvector"]
    assert len(seen) == (1 if same else 0)
    if same:
        assert seen[0]["door"] == "install"


# ── the review's findings (2026-11-26) ───────────────────────────────────


def test_two_equal_functions_of_two_modules_keep_their_own_names(pool: Any) -> None:
    """L1: code objects compare by value -- same name, bytecode and first
    line in two files are equal -- so a cache keyed by them named one
    function by the other's name (and hashed each code object whole)."""
    source = "def read(pool):\n    with pool.connection() as conn:\n        return conn\n"
    found = []
    for module in ("kazma_core.planted_a", "kazma_core.planted_b"):
        space: dict[str, Any] = {"__name__": module}
        exec(compile(source, f"{module.replace('.', '/')}.py", "exec"), space)  # noqa: S102 - a planted module
        found.append(space["read"])
    assert found[0].__code__ == found[1].__code__, "the premise: equal by value"
    for read in found:
        _unbound(lambda read=read: read(pool))
    names = sorted(e["callers"][0] for e in space_doors.entries())
    assert names == ["kazma-core/kazma_core/planted_a.py::read", "kazma-core/kazma_core/planted_b.py::read"]


def test_a_report_with_an_odd_item_is_said_and_started_again(pool: Any, tmp_path: Path,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    """L4: valid JSON holding an item flush() did not write stopped every
    later flush (a sweep WARNING each 15 minutes, nothing kept)."""
    report = tmp_path / "space_doors.json"
    report.write_text(json.dumps({"version": 1, "checkouts": 1, "entries": [
        {"pool": "shared", "door": "none", "how": "nothing", "reason": "", "callers": "not a list",
         "count": 1, "first": "x", "last": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        space_doors.read_report(report)
    _checkout(pool)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        space_doors.flush(report)
    assert any("cannot be read" in r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert space_doors.read_report(report)["checkouts"] == 1


@pytest.mark.parametrize("broken", [ImportError("a module renamed"), LookupError("a name gone"),
                                    NameError("a name gone")])
def test_whatever_fails_inside_the_counter_never_reaches_the_checkout(pool: Any, monkeypatch: pytest.MonkeyPatch,
                                                                     broken: Exception) -> None:
    """L7: "never raises" rested on a list of seven exception types."""
    def fails(depth: int) -> tuple[str, ...]:
        raise broken

    monkeypatch.setattr(space_doors, "_callers", fails)
    assert _checkout(pool) == "a connection"


def test_the_mirror_knows_whose_database_it_is_before_it_says_it_knows(monkeypatch: pytest.MonkeyPatch) -> None:
    """L6: _ours was set before _kazmas_database, so a second thread between
    the two read "not Kazma's database" and its checkout went uncounted."""
    pytest.importorskip("psycopg")
    from kazma_core.db import availability
    from kazma_core.memory.state_backend import PostgresStateBackend

    backend = PostgresStateBackend("postgresql://kazma@db.example:5432/kazma")
    seen: list[object] = []

    def answer(dsn: object, *, or_its_server: bool = False) -> bool:
        seen.append(backend._ours)  # what another thread would read now
        return True

    monkeypatch.setattr(availability, "is_kazma_database", answer)
    monkeypatch.setattr(backend, "_open_connection", lambda: "a connection")
    assert backend._connect() == "a connection"
    assert seen == [None, None], "_ours is set last"
    assert backend._kazmas_database is True and backend._ours is True


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "[" * 100_000, "null"])
def test_a_mirror_rows_odd_metadata_reads_as_none(raw: str) -> None:
    """The handler L7's ``except Exception`` was paid for with (the debt
    ratchet only goes down): a row's metadata is read by ``json.loads`` alone,
    whose failures are a ValueError or, nested past its depth, a
    RecursionError -- each still reads as no metadata."""
    from kazma_core.memory.state_backend import _parse_meta

    assert _parse_meta(raw) == {}
    assert _parse_meta('{"region": "kw"}') == {"region": "kw"}
