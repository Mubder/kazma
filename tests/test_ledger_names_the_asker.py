"""Spaces S1-22: every model call names the space and the person it was for.

The per-call ledger (``observability.llm_ledger``) stamps each row with the
scope bound where the call was made, read in the caller's own context. The
gate drives every model call of three real kinds of work with Spaces
enforced and nothing bound by the test itself -- each binds its own scope:

* a chat turn through ``run_agent_turn`` over the real supervisor graph (a
  tool round, then the answer): the thread's record names its space and
  opener;
* a scheduled task's fire through the real scheduler: the job's space and
  the account that booked it;
* a swarm task run by the engine through a real in-process worker: the
  task's space and asker.

Every row names that space and person. The controls: the ledger without the
stamp, and a call site that records with nothing bound -- each leaves a row
naming nobody, which the gate's check reports.

The trace feed (the Agents page, the Dashboard socket) and the admin's
spend per person are held here too.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kazma_core.observability import llm_ledger
from kazma_core.spaces import identity, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix

SHARED = "ledger-shared"


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    """No real embedder or model: the turns' models are scripted, and their
    memory hand-off ends inside the test (tests/_no_models.py)."""
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def rows(monkeypatch) -> list[dict[str, Any]]:
    """Every ledger row the work writes (the fields, as the write gets them)."""
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(llm_ledger, "_record_if_enabled", lambda **fields: written.append(fields))
    return written


def unnamed(written: list[dict[str, Any]], space: str, asker: str) -> list[str]:
    """The gate's check: the rows that do not name *space* and *asker*."""
    return [f"{r.get('model')}: space={r.get('space')!r} asker={r.get('asker')!r}"
            for r in written if (r.get("space"), r.get("asker")) != (space, asker)]


# ── a chat turn ──────────────────────────────────────────────────────────


def _scripted_turn_graph():
    """The real supervisor graph, its model scripted: a tool call, then the
    answer."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse, ToolCall
    from kazma_core.tracing import KazmaTracer

    from tests._chat_space_cases import _registry

    replies = iter([
        LLMResponse(content="", tool_calls=[ToolCall(id="c1", name="current_datetime", arguments={})],
                    finish_reason="tool_calls", model="stub", usage={"total_tokens": 3}, cost_usd=0.0),
        LLMResponse(content="It is now.", tool_calls=[], finish_reason="stop", model="stub",
                    usage={"total_tokens": 2}, cost_usd=0.0),
    ])

    class Scripted:
        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            return next(replies, LLMResponse(content="Done.", tool_calls=[], finish_reason="stop",
                                             model="stub", usage={"total_tokens": 1}, cost_usd=0.0))

    registry = _registry()
    tool = [d for d in registry.get_tool_definitions() if d["function"]["name"] == "current_datetime"]
    return build_supervisor_graph(
        llm=Scripted(), system_prompt="You are Kazma.", tool_definitions=tool, tool_executor=registry,
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )


def _a_turn(matrix: Matrix) -> None:
    from kazma_core.agent.turn import run_agent_turn

    thread_id = f"t-ledger-{uuid.uuid4().hex[:8]}"
    threads.record_thread(thread_id, Matrix.SPACE_A, matrix.account_a)
    result = asyncio.run(run_agent_turn(graph=_scripted_turn_graph(), thread_id=thread_id,
                                        state={"messages": [{"role": "user", "content": "what time is it?"}]},
                                        persist=False))
    assert not result.error, result.error


def test_every_call_of_a_turn_names_its_space_and_person(matrix, enforced, rows) -> None:
    _a_turn(matrix)
    assert len(rows) >= 2, rows  # the tool round and the answer
    assert unnamed(rows, Matrix.SPACE_A, matrix.account_a) == []


def test_control_the_ledger_without_the_stamp_names_nobody(matrix, enforced, rows, monkeypatch) -> None:
    monkeypatch.setattr(llm_ledger, "_whose", lambda: ("", ""))
    _a_turn(matrix)
    assert rows and len(unnamed(rows, Matrix.SPACE_A, matrix.account_a)) == len(rows)


def test_control_a_call_site_with_nothing_bound_is_reported(matrix, enforced, rows) -> None:
    """A model call recorded where no scope is bound -- the shape of a call
    site that binds nothing -- writes a row naming nobody: the check finds it."""
    asyncio.run(llm_ledger.arecord_llm_call(thread_id="planted", iteration=1, provider="Fake", model="planted"))
    assert unnamed(rows, Matrix.SPACE_A, matrix.account_a) == ["planted: space='' asker=''"]


# ── a scheduled job ──────────────────────────────────────────────────────


async def _a_fire(tmp_path) -> None:
    from kazma_core.cron import scheduler as sm
    from kazma_core.cron.store import ScheduledJob, SQLiteCronStore

    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    try:
        sched = sm.CronScheduler(store=store, graph_builder=_scripted_turn_graph)
        job = ScheduledJob(job_id=f"cron-{secrets.token_hex(6)}", timing="2h", prompt="what time is it?",
                           platform="telegram", thread_id="thread-ledger", kind="task",
                           next_run=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
                           tenant_id=SHARED, delivery_target="telegram:1", creator=OWNER_ACCOUNT_ID)
        await store.insert(job)
        await sched._execute(await store.get(job.job_id))
        done = await store.get(job.job_id)
        assert done is not None and done.last_outcome == "delivered", (done.last_outcome, done.last_result)
    finally:
        await store.close()


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    import importlib

    messages = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    out: list[str] = []

    async def backend(target_id, text, **_kw):
        out.append(target_id)
        return f"sent:{target_id}"

    monkeypatch.setitem(messages._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", lambda *_a, **_k: True)
    return out


def test_every_call_of_a_scheduled_fire_names_its_space_and_person(tmp_path, matrix, enforced, rows,
                                                                     sent) -> None:
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id=SHARED)
    asyncio.run(_a_fire(tmp_path))
    assert len(rows) >= 2, rows
    assert unnamed(rows, SHARED, OWNER_ACCOUNT_ID) == []


# ── a swarm task ─────────────────────────────────────────────────────────


def test_every_call_of_a_swarm_task_names_its_space_and_person(matrix, rows, monkeypatch) -> None:
    from kazma_core.swarm.task import SwarmTask
    from kazma_core.swarm.worker import InProcessWorker

    from tests._swarm_space_cases import world
    from tests.test_swarm_spaces import _reply, _scripted_model

    _scripted_model(monkeypatch, [_reply("summed")])
    with world(matrix) as w:
        w.engine._workers["counter"] = InProcessWorker(name="counter", role="counter")
        task = SwarmTask(prompt="sum the column", workers=["counter"],
                         metadata={"tenant_id": Matrix.SPACE_A, "asker": matrix.account_a})
        # Run as the engine runs a task after a restart: nothing bound.
        contextvars.Context().run(asyncio.run, w.engine.dispatch(task))
    assert rows, "the worker made no model call"
    assert unnamed(rows, Matrix.SPACE_A, matrix.account_a) == []


# ── the ledger itself ────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "llm_calls.db"), check_same_thread=False)
    conn.executescript(llm_ledger._SCHEMA)
    llm_ledger._migrate(conn)
    monkeypatch.setattr(llm_ledger, "_get_conn", lambda db_path=None: conn)
    yield conn
    conn.close()


def test_a_row_written_in_a_scope_names_it(ledger, matrix) -> None:
    with matrix.as_a():
        llm_ledger.record_llm_call(model="m", prompt_tokens=3, completion_tokens=4, cost_usd=0.5)
    llm_ledger.record_llm_call(model="m", prompt_tokens=1, completion_tokens=1)  # Kazma's own work
    named = ledger.execute("SELECT space, asker FROM llm_calls ORDER BY id").fetchall()
    assert named == [(Matrix.SPACE_A, matrix.account_a), ("", "")]


def test_an_older_ledger_gets_its_space_columns(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                 "thread_id TEXT DEFAULT '', iteration INTEGER DEFAULT 0, provider TEXT DEFAULT '', "
                 "model TEXT DEFAULT '', prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, "
                 "total_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0, duration_ms REAL DEFAULT 0.0, "
                 "status TEXT DEFAULT 'ok', error_kind TEXT DEFAULT '', failover_from TEXT DEFAULT '')")
    llm_ledger._migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_calls)")}
    assert {"space", "asker", "turn_id", "priced"} <= columns
    conn.close()


def test_spend_per_person_counts_each_person_and_kazma_apart(ledger, matrix) -> None:
    with matrix.as_a():
        llm_ledger.record_llm_call(model="m", prompt_tokens=10, cost_usd=0.25)
        llm_ledger.record_llm_call(model="m", prompt_tokens=10, cost_usd=0.25)
    with matrix.as_b():
        llm_ledger.record_llm_call(model="m", prompt_tokens=5, priced=False)
    llm_ledger.record_llm_call(model="m", prompt_tokens=1, cost_usd=0.01)
    people = {p["asker"]: p for p in llm_ledger.spend_by_person(datetime.now(UTC) - timedelta(hours=1))}
    assert people[matrix.account_a]["calls"] == 2 and people[matrix.account_a]["cost"] == pytest.approx(0.5)
    assert people[matrix.account_b]["unpriced"] == 1 and people[matrix.account_b]["cost"] == 0.0
    assert people[""]["calls"] == 1  # Kazma's own work


def test_the_admin_sees_spend_per_person_and_no_text(ledger, matrix, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import dashboard

    with matrix.as_a():
        llm_ledger.record_llm_call(model="m", prompt_tokens=10, cost_usd=0.25, thread_id="a secret chat")
    llm_ledger.record_llm_call(model="m", prompt_tokens=1, cost_usd=0.01)
    app = FastAPI()
    app.include_router(dashboard.router)
    with TestClient(app) as client:
        got = client.get("/api/dashboard/spend-by-person")
    assert got.status_code == 200 and got.json()["available"], got.text
    people = got.json()["people"]
    assert {p["name"] for p in people if not p["kazma"]} == {"matrix-person-a"}
    assert any(p["kazma"] for p in people)
    assert "secret" not in got.text and "thread" not in got.text


# ── the trace feed ───────────────────────────────────────────────────────


def _entry(label: str):
    import time

    from kazma_core.tracing import TraceEntry

    return TraceEntry(timestamp=time.time(), trace_type="llm", label=label, status="success",
                      duration_ms=1.0, tokens=5, cost=0.0, details=f"prompt of {label}")


def test_a_trace_names_its_space_and_each_view_keeps_to_its_own(matrix) -> None:
    from kazma_core.tracing import TraceStore

    store = TraceStore()
    with matrix.as_a():
        store.add(_entry("of-a"))
    with matrix.as_b():
        store.add(_entry("of-b"))
    store.add(_entry("of-kazma"))
    assert [e.label for e in store.recent(10, Matrix.SPACE_A)] == ["of-a"]
    assert [e.label for e in store.recent(10, OWNER_SPACE_ID)] == ["of-kazma"]
    assert [e.label for e in store.recent(10)] == ["of-a", "of-b", "of-kazma"]
    assert store.stats(Matrix.SPACE_B)["total_tokens"] == 5
    assert store.stats()["total_tokens"] == 15


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_a_socket_of_a_space_is_sent_that_space_s_traces_only(matrix) -> None:
    from kazma_core.tracing import TraceStore

    store = TraceStore()
    a_socket, every = _Socket(), _Socket()
    store.register_ws(a_socket, Matrix.SPACE_A)
    store.register_ws(every)  # registered as before Spaces: every space's
    with matrix.as_b():
        theirs = _entry("of-b")
        store.add(theirs)
    with matrix.as_a():
        mine = _entry("of-a")
        store.add(mine)

    async def broadcast() -> None:
        await store._broadcast(theirs)
        await store._broadcast(mine)

    asyncio.run(broadcast())
    assert [m["data"]["label"] for m in a_socket.sent] == ["of-a"]
    assert a_socket.sent[0]["metrics"]["total_tokens"] == 5
    # The control: a socket registered as before is sent every space's.
    assert [m["data"]["label"] for m in every.sent] == ["of-b", "of-a"]
