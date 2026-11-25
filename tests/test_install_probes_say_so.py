"""Kazma's own probes and schema checks say they are the install's (S4-01 step
0a, rows 1, 2, 3, 8, 9, 11, 42).

A ping, a table check and the schema statements run with no space bound, or
inside whichever space a page asked from, and said nothing. Under the
database's second lock each would be refused, or read in one person's space.
The recall canary is the exception: it reads a memory, so it runs as the
owner, whoever asks.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import Scope, bind_scope, current_scope
from kazma_core.tenant_context import get_current_tenant_id

from tests._space_door import Calls, watched


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


class _Cursor:
    """A cursor that remembers what it ran."""

    description = None

    def __init__(self) -> None:
        self.ran: list[str] = []

    def execute(self, sql: str, _params: object = None) -> None:
        self.ran.append(sql)

    def fetchone(self) -> Any:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_a: object) -> None:
        return None


class _Pool:
    """Kazma's pool, with no server behind it."""

    def __init__(self) -> None:
        self.cursor_ = _Cursor()

    def execute_one(self, sql: str, _params: object = None) -> dict[str, Any]:
        self.cursor_.execute(sql)
        return {"ok": 1, "rolname": "kazma_app", "rolsuper": False}

    def connection(self) -> Any:
        conn = SimpleNamespace(cursor=lambda: self.cursor_, commit=lambda: None)
        from contextlib import nullcontext

        return nullcontext(conn)


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> _Pool:
    from kazma_core.db import backend, pg_schema, postgres_pool

    made = _Pool()
    monkeypatch.setattr(postgres_pool, "get_postgres_pool", lambda: made)
    monkeypatch.setattr(backend, "is_postgres", lambda: True)
    monkeypatch.setattr(pg_schema, "ensure_schema", lambda cur, _schema: cur.execute("-- the schema"))
    return made


def _ping() -> Any:
    from kazma_ui.health import check_database

    return check_database()


def _tables(pool: _Pool) -> Any:
    from kazma_core.db import pg_backup

    return pg_backup.verify_required_pg_tables(pool)


def _role() -> Any:
    from kazma_core.security import service_exposure

    return service_exposure._role_answer()


def _memory_ping() -> Any:
    from kazma_core.memory.health import build_memory_health

    return build_memory_health()


def _job_tables(pool: _Pool) -> Any:
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository

    return PostgresDocumentJobRepository(pool)


def _document_tables(pool: _Pool) -> Any:
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    return PostgresDocumentRepository(pool, tenant_quota_bytes=1)


def _audit_guard(pool: _Pool) -> Any:
    from kazma_core.documents.audit import DocumentAuditStore

    return DocumentAuditStore(SimpleNamespace(_conn=None, _pool=pool, _lock=None, backend_name="postgres"))


PROBES = {
    "ping": lambda _pool: _ping(),
    "memory ping": lambda _pool: _memory_ping(),
    "tables": _tables,
    "role": lambda _pool: _role(),
    "job tables": _job_tables,
    "document tables": _document_tables,
    "audit guard": _audit_guard,
}


def _door_of(probe: str, pool: _Pool) -> Calls:
    with watched((_Pool, "execute_one"), (_Pool, "connection")) as calls:
        PROBES[probe](pool)
    # The settings table stays outside the second lock (decision 4).
    return Calls(c for c in calls if "kazma_settings" not in str(c.args))


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("bound", [False, True], ids=["nothing bound", "from a person's page"])
@pytest.mark.parametrize("probe", sorted(PROBES))
def test_a_probe_is_the_installs_whoever_asks(probe: str, bound: bool, pool: _Pool) -> None:
    if bound:
        with bind_scope("family", "layla"):
            calls = _door_of(probe, pool)
    else:
        calls = _door_of(probe, pool)
    assert calls and {c.space for c in calls} == {"<install>"}, "the door's answer, not a space"


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_off_a_probe_binds_nothing_and_asks_the_same(probe: str, pool: _Pool) -> None:
    calls = _door_of(probe, pool)
    assert calls and {c.scope for c in calls} == {None} and get_current_tenant_id() is None


def test_the_role_asked_is_the_one_that_signed_in(pool: _Pool) -> None:
    """A connection of the second lock acts as a smaller role: ``current_user``
    would then say Kazma does not sign in as a superuser when it does."""
    assert _role()["rolname"] == "kazma_app"
    assert pool.cursor_.ran == ["SELECT rolname, rolsuper FROM pg_roles WHERE rolname = session_user"]


def test_the_checkpoint_tables_are_the_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    from kazma_core import checkpoints_pg
    from kazma_core.spaces.row_work import current_door

    aio = pytest.importorskip("langgraph.checkpoint.postgres.aio")
    pool_module = pytest.importorskip("psycopg_pool")

    class _AsyncPool:
        check_connection = None

        def __init__(self, **_kwargs: Any) -> None: ...

        async def open(self) -> None: ...

        async def close(self) -> None: ...

    doors: list[Any] = []

    class _Saver:
        def __init__(self, **_kwargs: Any) -> None: ...

        async def setup(self) -> None:
            doors.append(current_door())

    monkeypatch.setattr(pool_module, "AsyncConnectionPool", _AsyncPool)
    monkeypatch.setattr(aio, "AsyncPostgresSaver", _Saver)

    async def opened_in_a_turn() -> Any:
        with bind_scope("family", "layla"):
            await checkpoints_pg.open_postgres_checkpointer("postgresql://demo@localhost/kazma")
            return current_door()

    after = asyncio.run(opened_in_a_turn())
    assert doors[0].reason and not doors[0].space, "setup is the install's"
    assert after.space == "family", "and only setup"


# ── the canary reads a memory, so it is someone's ────────────────────────


def _canary(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from kazma_core.memory import recall as recall_module
    from kazma_ui import health

    seen: list[Any] = []

    def recall(*_a: Any, **kwargs: Any) -> Any:
        seen.append((current_scope(), get_current_tenant_id(), kwargs["tenant_id"]))
        return SimpleNamespace(beliefs=[], episodes=[], failed=False)

    monkeypatch.setattr(recall_module, "recall", recall)
    assert asyncio.run(health._check_memory_recall())["status"] == "ok"
    return seen


@pytest.mark.usefixtures("enforced")
def test_the_recall_canary_runs_as_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _canary(monkeypatch) == [(Scope("default", "owner"), "default", "default")]
    with bind_scope("family", "layla"):
        assert _canary(monkeypatch) == [(Scope("default", "owner"), "default", "default")]


def test_off_the_recall_canary_binds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _canary(monkeypatch) == [(None, None, "default")]
