"""A write that fails on a kept SQLite connection leaves no transaction open.

The companion of tests/test_sqlite_kept_connections.py, which reads the
source. Here each store's real write is made to fail INSIDE the statement --
a trigger that raises, after the write lock was taken -- and another
connection must then get the write lock at once. Until 2026-09-27 each of
these kept the lock until the next successful write on that connection
(the memory writer is shared by every chat turn), and the disclosure store
could commit a report without its first history row at its next write.
The negative control makes the same failure on a plain connection in
Python's default mode and shows the lock held.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from pathlib import Path

import pytest


def _write_lock_free(path: Path) -> bool:
    other = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def _fail_inserts(path: Path, table: str) -> None:
    """Every INSERT into *table* now raises -- after taking the write lock."""
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TRIGGER fail_{table} BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'injected'); END")
    conn.commit()
    conn.close()


def test_negative_control_a_failed_write_in_the_default_mode_keeps_the_lock(tmp_path):
    path = tmp_path / "plain.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    _fail_inserts(path, "t")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            conn.execute("INSERT INTO t VALUES (1)")
        assert conn.in_transaction and not _write_lock_free(path)
        conn.rollback()
        assert _write_lock_free(path)
    finally:
        conn.close()


def test_the_memory_writer_releases_the_lock_when_a_write_fails(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    mirror = dual_write.DualWriteMirror()
    try:
        assert mirror.mirror_belief("user", "likes", "tea")  # the writer works
        _fail_inserts(tmp_path / "memory_state.db", "beliefs")
        assert mirror.mirror_belief("user", "likes", "coffee") is None  # the write failed
        assert not mirror._primary.in_transaction  # noqa: SLF001
        assert _write_lock_free(tmp_path / "memory_state.db")
    finally:
        mirror.close()


def test_the_semantic_cache_releases_the_lock_when_a_store_fails(tmp_path, monkeypatch):
    from kazma_core.swarm import semantic_cache

    monkeypatch.setattr(semantic_cache, "get_encoder", lambda: None)
    path = tmp_path / "semantic_cache.db"
    cache = semantic_cache.SemanticCache(db_path=str(path))
    try:
        cache.store("warm", {"ok": 1})
        _fail_inserts(path, "semantic_cache")
        cache.store("hello", {"answer": 42})  # logged, not raised
        assert _write_lock_free(path)
    finally:
        cache.close()


def test_the_llm_ledger_releases_the_lock_when_a_record_fails(tmp_path, monkeypatch):
    from kazma_core.observability import llm_ledger

    path = tmp_path / "llm_calls.db"
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(path))  # the ledger writes here from now on
    try:
        llm_ledger.record_llm_call(thread_id="t", model="m")
        _fail_inserts(path, "llm_calls")
        llm_ledger.record_llm_call(thread_id="t", model="m")  # swallowed: observability is invisible
        assert not conn.in_transaction and _write_lock_free(path)
        assert len(llm_ledger.query_recent(thread_id="t")) == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_the_disclosure_store_writes_a_report_and_its_history_together(tmp_path):
    from kazma_core.security.disclosure import VulnerabilityDisclosure

    path = tmp_path / "disclosure.db"
    store = VulnerabilityDisclosure(db_path=path)
    report = {"title": "t", "description": "d", "severity": "low"}
    try:
        await store.submit_report(report)
        _fail_inserts(path, "status_history")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            await store.submit_report(report)
        assert _write_lock_free(path)
        check = sqlite3.connect(path)
        try:
            check.execute("DROP TRIGGER fail_status_history")
            check.commit()
        finally:
            check.close()
        await store.submit_report(report)
        check = sqlite3.connect(path)
        try:
            reports = check.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            history = check.execute("SELECT COUNT(DISTINCT report_id) FROM status_history").fetchone()[0]
        finally:
            check.close()
        assert (reports, history) == (2, 2), "the failed report must not have landed without its history"
    finally:
        if store._conn is not None:  # noqa: SLF001
            store._conn.close()  # noqa: SLF001


def _functional_kw() -> dict:
    return {
        "confidence": 0.9, "importance": 1, "trust": 1.0, "extraction_method": "user_explicit",
        "tenant_id": "default", "source_session": None, "source_turn": None, "mem_class": "semantic",
        "now": time.time(), "recorded": time.time(), "cfg": {}, "private": True,
    }


def test_a_fact_change_that_fails_ends_its_transaction(tmp_path, monkeypatch):
    from kazma_core.memory import belief_mutation
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    path = tmp_path / "memory_state.db"
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)

    def boom(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(belief_mutation, "_insert_belief", boom)
    try:
        with contextlib.suppress(RuntimeError):
            belief_mutation.mutate_belief(
                conn, "user", "lives_in", "London", predicate_type="functional",
                extraction_method="user_explicit", private=True,
            )
        assert not conn.in_transaction and _write_lock_free(path)

        # Negative control: the same failure in the body with nothing around
        # it to end the transaction -- the shape before 2026-09-27.
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="injected"):
            belief_mutation._mutate_functional_locked(
                conn, None, "user", "lives_in", "Paris", began=True, **_functional_kw()
            )
        assert conn.in_transaction and not _write_lock_free(path)
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_write_transaction_commits_the_group_or_none_of_it(tmp_path):
    """The helper the aiosqlite stores group their writes with (RBAC's seed,
    a hub skill and its dependency list)."""
    import aiosqlite

    from kazma_core.db.sqlite_session import write_transaction

    path = tmp_path / "grouped.db"
    db = await aiosqlite.connect(path, isolation_level=None)
    try:
        await db.execute("CREATE TABLE t (a)")
        async with write_transaction(db):
            await db.execute("INSERT INTO t VALUES (1)")
            await db.execute("INSERT INTO t VALUES (2)")
        with pytest.raises(RuntimeError, match="half way"):
            async with write_transaction(db):
                await db.execute("INSERT INTO t VALUES (3)")
                raise RuntimeError("half way")
        assert not db.in_transaction and _write_lock_free(path)
        async with db.execute("SELECT a FROM t ORDER BY a") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [1, 2]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_groups_on_one_connection_wait_for_each_other(tmp_path):
    """In autocommit mode a second BEGIN on a connection whose first group is
    still open fails; write_transaction makes the second group wait."""
    import asyncio

    import aiosqlite

    from kazma_core.db.sqlite_session import write_transaction

    db = await aiosqlite.connect(tmp_path / "groups.db", isolation_level=None)
    try:
        await db.execute("CREATE TABLE t (a)")

        async def group(n: int) -> None:
            async with write_transaction(db):
                await db.execute("INSERT INTO t VALUES (?)", (n,))
                await asyncio.sleep(0.01)  # the other group tries to start here
                await db.execute("INSERT INTO t VALUES (?)", (n,))

        await asyncio.gather(group(1), group(2))
        async with db.execute("SELECT a, COUNT(*) FROM t GROUP BY a ORDER BY a") as cursor:
            assert [tuple(r) for r in await cursor.fetchall()] == [(1, 2), (2, 2)]

        # Negative control: the same two groups without the helper.
        async def raw(n: int) -> None:
            await db.execute("BEGIN IMMEDIATE")
            await asyncio.sleep(0.01)
            await db.execute("COMMIT")

        results = await asyncio.gather(raw(3), raw(4), return_exceptions=True)
        assert any(isinstance(r, sqlite3.OperationalError) and "within a transaction" in str(r) for r in results)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rbac_hands_out_one_connection_and_only_once_seeded(tmp_path):
    import asyncio

    from kazma_core.rbac import RBACEngine

    engine = RBACEngine(db_path=str(tmp_path / "rbac.db"))
    try:
        first, second = await asyncio.gather(engine._get_db(), engine._get_db())  # noqa: SLF001
        assert first is second
        async with first.execute("SELECT COUNT(*) FROM division_permissions") as cursor:
            seeded = (await cursor.fetchone())[0]
        assert seeded > 0 and not first.in_transaction
    finally:
        await engine.close()
