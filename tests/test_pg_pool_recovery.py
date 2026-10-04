"""Kazma's Postgres pools come back when the database does (2026-09-30).

On 2026-09-28 a Docker Desktop update restarted the Docker engine and with it
the database container. Two things kept Kazma down longer than Postgres was:

* every pooled connection was dead, and a caller that drew one got "the
  connection is closed" (the document worker's claims, among others);
* the pools' reconnects waited on psycopg's 130 s default connect timeout,
  because Docker Desktop's port proxy accepts the TCP connection while the
  container is down -- the pools stayed empty for a minute after Postgres
  was back.

Every pool now checks a connection at checkout (a dead one is replaced, not
handed out) and connects with a short timeout (``pool_connection_kwargs``).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_PRODUCT = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
            "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui")
_POOLS = {"ConnectionPool", "AsyncConnectionPool"}


def _unhardened_pools(source: str) -> list[tuple[int, str]]:
    """``(line, why)`` for each pool built without the checkout check or the
    shared connection keywords."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func).split(".")[-1]
        if name not in _POOLS:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if "check" not in kw:
            out.append((node.lineno, "no check= (a dead connection is handed to the caller)"))
        kwargs = kw.get("kwargs")
        if not (isinstance(kwargs, ast.Call) and ast.unparse(kwargs.func).endswith("pool_connection_kwargs")):
            out.append((node.lineno, "kwargs= not built by pool_connection_kwargs (130 s connect timeout)"))
    return out


def test_every_pool_kazma_opens_recovers_from_a_database_restart():
    offenders = []
    for base in _PRODUCT:
        for path in sorted((REPO / base).rglob("*.py")):
            if "_tests" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ConnectionPool(" not in text:
                continue
            offenders += [f"{path.relative_to(REPO).as_posix()}:{line} {why}"
                          for line, why in _unhardened_pools(text)]
    assert not offenders, "\n".join(offenders)


def test_the_pool_gate_sees_a_plain_pool():
    """Negative control: the pools as they were before 2026-09-30."""
    shared = (
        "pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=10, timeout=5,\n"
        "    kwargs={'row_factory': dict_row, 'autocommit': False}, open=True)\n"
    )
    checkpointer = (
        "pool = AsyncConnectionPool(conninfo=dsn, min_size=1, max_size=8,\n"
        "    kwargs={'autocommit': True, 'prepare_threshold': 0}, open=False)\n"
    )
    hardened = (
        "pool = ConnectionPool(conninfo=dsn, kwargs=pool_connection_kwargs(dsn, autocommit=False),\n"
        "    check=ConnectionPool.check_connection, open=True)\n"
    )
    assert len(_unhardened_pools(shared)) == 2
    assert len(_unhardened_pools(checkpointer)) == 2
    assert _unhardened_pools(hardened) == []


def test_a_connect_timeout_is_added_unless_the_dsn_names_one():
    from kazma_core.db.postgres_pool import _CONNECT_TIMEOUT_S, pool_connection_kwargs

    plain = pool_connection_kwargs("postgresql://kazma@localhost:5432/kazma", autocommit=True)
    assert plain == {"autocommit": True, "connect_timeout": _CONNECT_TIMEOUT_S}
    assert 2 <= _CONNECT_TIMEOUT_S <= 10
    # The operator's own choice wins, in either DSN form.
    assert "connect_timeout" not in pool_connection_kwargs(
        "postgresql://kazma@localhost:5432/kazma?connect_timeout=30")
    assert "connect_timeout" not in pool_connection_kwargs(
        "host=localhost dbname=kazma connect_timeout=30")


# ── against a real Postgres (the Postgres CI job) ─────────────────────────


def _dsn() -> str:
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    pytest.importorskip("psycopg_pool")
    return "postgresql://" + dsn[len("postgres://"):] if dsn.startswith("postgres://") else dsn


def _terminate(dsn: str, pid: int) -> None:
    """End one server process, as a database restart ends them all."""
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_terminate_backend(%s)", (pid,))


@pytest.mark.postgres
def test_a_connection_the_server_closed_is_replaced_at_checkout():
    from kazma_core.db.postgres_pool import PostgresPool

    dsn = _dsn()
    pool = PostgresPool(dsn, min_size=1, max_size=1)
    try:
        first = pool.execute_one("SELECT pg_backend_pid() AS pid")["pid"]
        _terminate(dsn, first)
        # The one pooled connection is dead; the caller still gets an answer.
        second = pool.execute_one("SELECT pg_backend_pid() AS pid")["pid"]
        assert second != first
    finally:
        pool.close()


@pytest.mark.postgres
def test_a_boot_schema_lock_fails_bounded_then_recovers(monkeypatch):
    """Hold a lock in the test database, never stop a production dependency."""
    import time

    import kazma_core.config_store as settings
    from kazma_core.config_availability import ConfigStoreUnavailableError
    from kazma_core.db.postgres_pool import get_postgres_pool, reset_postgres_pool

    dsn = _dsn()
    psycopg = pytest.importorskip("psycopg")
    get_postgres_pool()
    reset_postgres_pool()
    monkeypatch.setattr(settings, "_config_store", None)
    monkeypatch.setenv("KAZMA_PG_POOL_RETRIES", "1")
    with psycopg.connect(dsn) as blocker:
        blocker.execute("LOCK TABLE kazma_chat_sessions IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        with pytest.raises(ConfigStoreUnavailableError):
            settings.get_config_store()
        assert time.monotonic() - started < 20
        assert settings.peek_config_store() is None
        blocker.rollback()
    store = settings.get_config_store()
    store.set("test.boot_lock_recovery", "durable", category="test")
    try:
        assert store.get("test.boot_lock_recovery") == "durable"
    finally:
        store.delete("test.boot_lock_recovery")


@pytest.mark.postgres
def test_the_checkpointers_pool_replaces_a_closed_connection():
    import asyncio
    import sys

    pytest.importorskip("langgraph.checkpoint.postgres.aio")
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer

    dsn = _dsn()

    async def scenario() -> None:
        saver = await open_postgres_checkpointer(dsn, max_size=1)
        try:
            pool = saver.conn
            async with pool.connection() as conn:
                first = (await (await conn.execute("SELECT pg_backend_pid() AS pid")).fetchone())["pid"]
            await asyncio.to_thread(_terminate, dsn, first)
            async with pool.connection() as conn:
                second = (await (await conn.execute("SELECT pg_backend_pid() AS pid")).fetchone())["pid"]
            assert second != first
        finally:
            await close_postgres_checkpointer(saver)

    # psycopg's async mode needs a selector loop; Windows defaults to Proactor.
    factory = None
    if sys.platform == "win32":
        import selectors

        factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(scenario())


@pytest.mark.postgres
def test_without_the_check_the_caller_gets_the_dead_connection():
    """Negative control: a plain pool hands the closed connection out."""
    psycopg = pytest.importorskip("psycopg")
    ConnectionPool = pytest.importorskip("psycopg_pool").ConnectionPool

    dsn = _dsn()
    pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=1, open=True)
    try:
        with pool.connection() as conn:
            first = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
        _terminate(dsn, first)
        with pytest.raises(psycopg.OperationalError):
            with pool.connection() as conn:
                conn.execute("SELECT 1")
    finally:
        pool.close()
