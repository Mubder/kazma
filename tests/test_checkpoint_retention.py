"""Every chat's step history is bounded, on both backends.

LangGraph writes a checkpoint on every superstep (audit M-G1). The retention
kept each chat's newest 200 and an idle chat's newest 10 -- on SQLite only:
Postgres was skipped as "ops-owned", and on the live install on 2026-09-27
the checkpoint tables held 2.9 GB of a 3.0 GB database, one chat 3,969
checkpoints. On SQLite the prune named ``checkpoint_writes`` (the Postgres
table), so the writes of every pruned checkpoint stayed, and the idle rule
read ``metadata.ts``, which LangGraph never writes, so it never applied.

These run real LangGraph graphs on the savers the product uses and check
what a user would notice: the chat's state after a prune is the state
before it, every kept checkpoint still loads as it was, and the next turn
works.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import operator
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated, TypedDict

import pytest
from kazma_core import checkpoint_retention as cr

DAY = 86400.0
#: "Now", an hour after the turns: past the ten-minute grace for a chat a run
#: may still be writing.
LATER = 3600.0


class _State(TypedDict):
    messages: Annotated[list, operator.add]
    counter: int
    note: dict


def _graph(checkpointer):
    from langgraph.graph import END, START, StateGraph

    def step(state: _State) -> dict:
        n = (state.get("counter") or 0) + 1
        return {"messages": [f"m{n}"], "counter": n, "note": {"n": n, "pad": "x" * 40}}

    g = StateGraph(_State)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    return g.compile(checkpointer=checkpointer)


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


async def _sqlite_turns(path: Path, thread: str, turns: int) -> tuple[dict, dict]:
    """Run *turns* turns; return the chat's state and every checkpoint's values."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with aiosqlite.connect(str(path)) as conn:
        graph = _graph(AsyncSqliteSaver(conn))
        for _ in range(turns):
            await graph.ainvoke({"messages": []}, _cfg(thread))
        state = (await graph.aget_state(_cfg(thread))).values
        history = {
            s.config["configurable"]["checkpoint_id"]: s.values
            async for s in graph.aget_state_history(_cfg(thread))
        }
    return state, history


async def _sqlite_view(path: Path, thread: str) -> tuple[dict, dict]:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with aiosqlite.connect(str(path)) as conn:
        graph = _graph(AsyncSqliteSaver(conn))
        state = (await graph.aget_state(_cfg(thread))).values
        history = {
            s.config["configurable"]["checkpoint_id"]: s.values
            async for s in graph.aget_state_history(_cfg(thread))
        }
    return state, history


def _counts(path: Path, thread: str) -> tuple[int, int, int]:
    conn = sqlite3.connect(str(path))
    try:
        cps = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread,)).fetchone()[0]
        writes = conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id = ?", (thread,)).fetchone()[0]
        orphans = conn.execute(
            "SELECT COUNT(*) FROM writes w WHERE thread_id = ? AND NOT EXISTS (SELECT 1 FROM "
            "checkpoints c WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns "
            "AND c.checkpoint_id = w.checkpoint_id)", (thread,),
        ).fetchone()[0]
    finally:
        conn.close()
    return cps, writes, orphans


# ── a checkpoint's age ───────────────────────────────────────────────────


def test_a_checkpoint_id_carries_its_creation_time():
    from langgraph.checkpoint.base.id import uuid6

    born = cr.checkpoint_time(str(uuid6()))
    assert born is not None and abs(born - time.time()) < 5
    assert cr.checkpoint_time(str(uuid.uuid4())) is None, "not a uuid6: age unknown"
    assert cr.checkpoint_time("not-an-id") is None
    assert cr.checkpoint_time(None) is None


# ── SQLite, on the saver the product uses ────────────────────────────────


@pytest.mark.asyncio
async def test_a_busy_chat_keeps_its_newest_checkpoints_and_loses_nothing_it_shows(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "KEEP_PER_THREAD", 12)
    db = tmp_path / "checkpoints.db"
    state, history = await _sqlite_turns(db, "busy", 10)
    assert _counts(db, "busy")[0] == 30

    res = cr._prune_sqlite_checkpoints(db, days=30, now=time.time() + LATER)

    cps, _writes, orphans = _counts(db, "busy")
    assert cps == 12 and orphans == 0
    assert res["threads"] == 1 and res["rows"] > 18
    after, kept = await _sqlite_view(db, "busy")
    assert after == state, "the chat's state is the state before the prune"
    assert len(kept) == 12
    assert all(values == history[cid] for cid, values in kept.items()), "a kept checkpoint changed"
    more, _ = await _sqlite_turns(db, "busy", 1)
    assert more["counter"] == state["counter"] + 1 and len(more["messages"]) == len(state["messages"]) + 1


@pytest.mark.asyncio
async def test_an_idle_chat_keeps_its_newest_ten(tmp_path):
    db = tmp_path / "checkpoints.db"
    state, _ = await _sqlite_turns(db, "idle", 6)

    cr._prune_sqlite_checkpoints(db, days=30, now=time.time() + 31 * DAY)

    assert _counts(db, "idle")[0] == cr.INACTIVE_KEEP
    assert (await _sqlite_view(db, "idle"))[0] == state, "still resumable where it stopped"
    # Negative control: not idle long enough, it keeps them all (18 < 200).
    db2 = tmp_path / "other.db"
    await _sqlite_turns(db2, "idle", 6)
    cr._prune_sqlite_checkpoints(db2, days=30, now=time.time() + 29 * DAY)
    assert _counts(db2, "idle")[0] == 18


@pytest.mark.asyncio
async def test_a_chat_a_run_may_be_writing_is_left_for_the_next_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "KEEP_PER_THREAD", 3)
    db = tmp_path / "checkpoints.db"
    await _sqlite_turns(db, "live", 4)

    assert cr._prune_sqlite_checkpoints(db, days=30, now=time.time())["rows"] == 0
    assert _counts(db, "live")[0] == 12
    cr._prune_sqlite_checkpoints(db, days=30, now=time.time() + cr.ACTIVE_GRACE_S + 1)
    assert _counts(db, "live")[0] == 3


@pytest.mark.asyncio
async def test_the_writes_of_a_pruned_checkpoint_go_with_it(tmp_path):
    """The old prune deleted checkpoints and named ``checkpoint_writes``, which
    this saver does not have: their writes stayed, unreadable. Negative
    control: the old shape leaves orphans; the prune removes them."""
    db = tmp_path / "checkpoints.db"
    await _sqlite_turns(db, "t", 8)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "DELETE FROM checkpoints WHERE thread_id = 't' AND checkpoint_id NOT IN (SELECT checkpoint_id "
        "FROM checkpoints WHERE thread_id = 't' ORDER BY checkpoint_id DESC LIMIT 10)"
    )
    conn.commit()
    conn.close()
    assert _counts(db, "t")[2] > 0, "the old prune's shape leaves orphaned writes"

    cr._prune_sqlite_checkpoints(db, days=30, now=time.time() + LATER)

    assert _counts(db, "t")[2] == 0


@pytest.mark.asyncio
async def test_only_the_chats_asked_for_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "KEEP_PER_THREAD", 3)
    db = tmp_path / "checkpoints.db"
    await _sqlite_turns(db, "a", 2)
    await _sqlite_turns(db, "b", 2)
    cr._prune_sqlite_checkpoints(db, days=30, now=time.time() + LATER, thread_ids=["a"])
    assert _counts(db, "a")[0] == 3 and _counts(db, "b")[0] == 6


# ── the setting ──────────────────────────────────────────────────────────


class _Settings:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self, key, default=None):
        assert key == cr.RETENTION_KEY
        return self.value


@pytest.mark.parametrize(("raw", "days"), [
    (30, 30), ("30", 30), (" 7 ", 7), (0, 0), ("0", 0), (3650, 3650),
    (3651, None), (-1, None), ("a month", None), ("2.5", None), (True, None), (None, None),
])
def test_what_counts_as_a_retention(raw, days):
    assert cr.parse_retention_days(raw) == days


def test_the_variable_means_days_and_wins_over_the_setting(monkeypatch, caplog):
    monkeypatch.delenv(cr.RETENTION_ENV, raising=False)
    assert cr.retention_setting(_Settings()) == {"days": 30, "source": "default"}
    assert cr.retention_setting(_Settings(45)) == {"days": 45, "source": "setting"}
    assert cr.retention_setting(_Settings(0)) == {"days": 0, "source": "setting"}
    monkeypatch.setenv(cr.RETENTION_ENV, "7")
    assert cr.retention_setting(_Settings(45)) == {"days": 7, "source": "env"}
    monkeypatch.setenv(cr.RETENTION_ENV, "0")
    assert cr.retention_setting(_Settings(45)) == {"days": 0, "source": "env"}
    # A value that is not a whole number of days is neither "keep nothing"
    # nor "keep everything": it is ignored, once, with a warning.
    monkeypatch.setenv(cr.RETENTION_ENV, "forever")
    caplog.set_level("WARNING", logger=cr.__name__)
    assert cr.retention_setting(_Settings(45)) == {"days": 45, "source": "setting"}
    assert any("forever" in r.getMessage() for r in caplog.records if r.name == cr.__name__)


@pytest.mark.asyncio
async def test_zero_keeps_every_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(cr.RETENTION_ENV, "0")
    monkeypatch.setattr(cr, "KEEP_PER_THREAD", 3)
    await _sqlite_turns(tmp_path / "checkpoints.db", "t", 3)
    await _sqlite_turns(tmp_path / "checkpoints_acme.db", "t", 3)
    assert cr._sqlite_checkpoint_files() == [tmp_path / "checkpoints.db", tmp_path / "checkpoints_acme.db"]

    out = cr.run_checkpoint_retention(now=time.time() + LATER)

    assert out["skipped"] == "keeps every checkpoint"
    assert _counts(tmp_path / "checkpoints.db", "t")[0] == 9
    # Negative control: any other value prunes both stores.
    monkeypatch.setenv(cr.RETENTION_ENV, "30")
    out = cr.run_checkpoint_retention(now=time.time() + LATER)
    assert out["threads"] == 2
    assert _counts(tmp_path / "checkpoints.db", "t")[0] == 3
    assert _counts(tmp_path / "checkpoints_acme.db", "t")[0] == 3


# ── the cadence ──────────────────────────────────────────────────────────


def test_retention_runs_on_the_maintenance_cadence():
    """It was a daily loop of its own that logged its failures at DEBUG; a
    cleanup on no cadence never runs (AGENTS.md §15B)."""
    from kazma_core.memory import worker_bootstrap as wb

    sweeps = dict(wb._MAINTENANCE_SWEEPS)
    assert "checkpoint retention" in sweeps
    tree = ast.parse(inspect.getsource(sweeps["checkpoint retention"]))
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "run_checkpoint_retention" in called
    assert not hasattr(cr, "start_checkpoint_retention_loop"), "one cadence, not a loop of its own"


def test_no_graph_state_uses_a_delta_channel():
    """The prune keeps each chat's newest checkpoints and drops the rest. A
    LangGraph ``DeltaChannel`` rebuilds a value by walking back through older
    checkpoints to a snapshot, and LangGraph warns that such a prune empties
    it without an error. No Kazma graph uses one; this fails the day one does,
    so the prune is made delta-aware first."""
    roots = [Path(__file__).resolve().parents[1] / d for d in (
        "kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
        "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui",
    )]
    hits = [
        str(p) for root in roots for p in root.rglob("*.py")
        if "DeltaChannel" in p.read_text(encoding="utf-8", errors="replace")
        and p.name != "checkpoint_retention.py"
    ]
    assert hits == []


# ── the Settings API ─────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    monkeypatch.delenv(cr.RETENTION_ENV, raising=False)
    cs = ConfigStore(db_path=str(tmp_path / "api.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    test_client = TestClient(app)
    test_client.kazma_config_store = cs
    return test_client


def test_the_api_reports_the_policy(client, monkeypatch):
    assert client.get("/api/settings/checkpoints/retention").json() == {
        "days": 30, "source": "default", "default": 30, "max": 3650,
        "keep_per_chat": 200, "idle_keep": 10,
    }
    monkeypatch.setenv(cr.RETENTION_ENV, "0")
    got = client.get("/api/settings/checkpoints/retention").json()
    assert (got["days"], got["source"]) == (0, "env")


def test_the_api_saves_a_whole_number_of_days(client):
    ok = client.put("/api/settings/single", json={"key": cr.RETENTION_KEY, "value": "0"})
    assert ok.status_code == 200
    assert client.kazma_config_store.get(cr.RETENTION_KEY) == 0
    assert client.get("/api/settings/checkpoints/retention").json()["days"] == 0


@pytest.mark.parametrize("bad", ["forever", -1, 3651, "2.5"])
def test_the_api_refuses_what_the_sweep_could_not_read(client, bad):
    client.put("/api/settings/single", json={"key": cr.RETENTION_KEY, "value": 45})

    resp = client.put("/api/settings/single", json={"key": cr.RETENTION_KEY, "value": bad})

    assert resp.status_code == 400
    assert "0 (keep every step)" in resp.json()["detail"]
    assert client.kazma_config_store.get(cr.RETENTION_KEY) == 45, "a refused save changed nothing"


# ── Postgres, on the saver the server uses ───────────────────────────────


def _pg_dsn() -> str:
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    pytest.importorskip("langgraph.checkpoint.postgres.aio")
    return "postgresql://" + dsn[len("postgres://"):] if dsn.startswith("postgres://") else dsn


class _Pool:
    """The product pool's shape (``PostgresPool.connection``) on a plain DSN."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def connection(self):
        psycopg = pytest.importorskip("psycopg")
        dict_row = pytest.importorskip("psycopg.rows").dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row)


@pytest.mark.postgres
def test_postgres_keeps_every_value_a_kept_checkpoint_names(monkeypatch):
    """The server's saver (AsyncPostgresSaver on an autocommit pool). A value
    lives in ``checkpoint_blobs``, shared by the checkpoints that did not
    change it; a blob no kept checkpoint names goes -- unless it is newer than
    every named one: the saver writes a new checkpoint's blobs first, so such
    a blob belongs to a checkpoint not inserted yet.

    Driven on a selector loop: psycopg's async mode refuses Windows' default
    one (the server forces the same, ``kazma_core.eventloop``).
    """
    dsn = _pg_dsn()
    monkeypatch.setattr(cr, "KEEP_PER_THREAD", 10)
    factory = None
    if sys.platform == "win32":
        import selectors

        factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(_postgres_scenario(dsn))


async def _postgres_scenario(dsn: str) -> None:
    # The main Tests job does not install psycopg (tests/test_postgres_suite.py).
    psycopg = pytest.importorskip("psycopg")
    dict_row = pytest.importorskip("psycopg.rows").dict_row
    AsyncConnectionPool = pytest.importorskip("psycopg_pool").AsyncConnectionPool
    AsyncPostgresSaver = pytest.importorskip("langgraph.checkpoint.postgres.aio").AsyncPostgresSaver

    thread, other = f"cr-{uuid.uuid4().hex[:10]}", f"cr-{uuid.uuid4().hex[:10]}"
    pool = AsyncConnectionPool(conninfo=dsn, min_size=1, max_size=4, open=False, kwargs={
        "autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await pool.open()
    try:
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()
        graph = _graph(saver)
        for t, n in ((thread, 12), (other, 12)):
            for _ in range(n):
                await graph.ainvoke({"messages": []}, _cfg(t))
        before = (await graph.aget_state(_cfg(thread))).values
        history = {s.config["configurable"]["checkpoint_id"]: s.values
                   async for s in graph.aget_state_history(_cfg(thread))}

        inflight, stale = "9" * 32 + ".0.5", "0" * 31 + "1.0.123"
        with psycopg.connect(dsn) as conn:
            for version in (inflight, stale):
                conn.execute(
                    "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
                    "VALUES (%s, '', 'messages', %s, 'empty', NULL)", (thread, version))
            conn.commit()
            # Negative control: without the version rule the in-flight blob
            # would be deleted (rolled back -- nothing is changed here).
            unguarded = cr._PG_DELETE_BLOBS.replace(
                "AND substring(b.version from '^[0-9]+')::numeric < n.v", "")
            assert unguarded != cr._PG_DELETE_BLOBS
            conn.execute(unguarded, {"t": thread, "ns": ""})
            gone = conn.execute("SELECT count(*) FROM checkpoint_blobs WHERE thread_id = %s AND version = %s",
                                (thread, inflight)).fetchone()[0]
            conn.rollback()
            assert gone == 0

        res = cr._prune_postgres_checkpoints(days=30, now=time.time() + LATER, pool=_Pool(dsn),
                                            thread_ids=[thread])

        assert res["threads"] == 1 and res["failed"] == 0
        with psycopg.connect(dsn) as conn:
            count = lambda sql: conn.execute(sql, (thread,)).fetchone()[0]  # noqa: E731
            assert count("SELECT count(*) FROM checkpoints WHERE thread_id = %s") == 10
            assert count("SELECT count(*) FROM checkpoint_blobs WHERE thread_id = %s "
                         f"AND version = '{inflight}'") == 1, "the in-flight blob stays"
            assert count("SELECT count(*) FROM checkpoint_blobs WHERE thread_id = %s "
                         f"AND version = '{stale}'") == 0, "a stale one goes"
            assert conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s",
                                (other,)).fetchone()[0] == 36, "a chat not asked for is untouched"
        after = (await graph.aget_state(_cfg(thread))).values
        assert after == before
        kept = {s.config["configurable"]["checkpoint_id"]: s.values
                async for s in graph.aget_state_history(_cfg(thread))}
        assert len(kept) == 10 and all(v == history[c] for c, v in kept.items())
        await graph.ainvoke({"messages": []}, _cfg(thread))
        assert (await graph.aget_state(_cfg(thread))).values["counter"] == before["counter"] + 1
    finally:
        with psycopg.connect(dsn) as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ANY(%s)", ([thread, other],))
            conn.commit()
        await pool.close()
