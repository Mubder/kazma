"""Every LLM ledger row is written off the event loop, and none is lost.

``resilient_chat`` -- the model call of every swarm worker and research step
-- wrote its ledger row (a SQLite insert into ``llm_calls.db``) on the event
loop until 2026-10-15, and the supervisor and respond node read the ledger's
switch (a settings read) there. A SQLite write on the loop stalls every chat
stream and WebSocket, on a busy disk for the full busy timeout (AGENTS §26 E,
§35). Async code now awaits ``llm_ledger.arecord_llm_call``: the write runs
in a worker thread, the caller waits for it (so a turn's totals, read after
its calls returned, include them), and a cancelled caller cannot drop it.

The tracer is a trace callback on the ledger's own connection: it sees each
statement on the thread that runs it, and notes the ones run where an event
loop runs. Each test has its negative control: the old on-the-loop write,
an unawaited write, and an unshielded one.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.agent.nonstop import NonStopConfig
from kazma_core.llm_provider import LLMResponse
from kazma_core.observability import llm_ledger
from kazma_core.observability.correlation import bind_turn_id, reset_turn_id


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A private ledger whose every statement on a loop thread is noted."""
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    on_loop: list[str] = []
    conn.set_trace_callback(
        lambda sql: on_loop.append(" ".join(sql.split())[:60]) if _on_loop() else None
    )
    settings_reads: list[bool] = []

    def _nonstop(*_a: Any, **_k: Any) -> NonStopConfig:
        settings_reads.append(_on_loop())
        return NonStopConfig()  # ledger on, failover off

    monkeypatch.setattr("kazma_core.agent.nonstop.get_nonstop_config", _nonstop)
    yield SimpleNamespace(conn=conn, on_loop=on_loop, settings_reads=settings_reads)
    conn.set_trace_callback(None)
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn.close()


def _rows(conn: sqlite3.Connection, turn_id: str) -> list[tuple]:
    with llm_ledger._lock:
        return conn.execute(
            "SELECT iteration, model, status, failover_from, total_tokens FROM llm_calls"
            " WHERE turn_id = ? ORDER BY id", (turn_id,),
        ).fetchall()


class _Model:
    """An LLMProvider-like client: answers, or raises what it is given."""

    def __init__(self, *, fail: BaseException | None = None, name: str = "m-1") -> None:
        self.fail, self.name = fail, name

    async def chat(self, messages: Any, **kwargs: Any) -> LLMResponse:
        await asyncio.sleep(0)
        if self.fail is not None:
            raise self.fail
        return LLMResponse(content="ok", model=self.name, cost_usd=0.001,
                           usage={"prompt_tokens": 10, "completion_tokens": 5})


async def _in_turn(turn_id: str, coro_factory):
    token = bind_turn_id(turn_id)
    try:
        return await coro_factory()
    finally:
        reset_turn_id(token)


# ── resilient_chat: the worker and research path ────────────────────────


def test_a_worker_call_writes_its_row_off_the_loop(ledger):
    from kazma_core.agent.resilient_chat import resilient_chat

    async def call():
        return await resilient_chat(_Model(), messages=[{"role": "user", "content": "q"}],
                                    thread_id="task-1", iteration=3, label="w")

    response = asyncio.run(_in_turn("turn-w", call))
    assert response.content == "ok"
    assert _rows(ledger.conn, "turn-w") == [(3, "m-1", "ok", "", 15)]
    assert ledger.on_loop == [], ledger.on_loop
    assert ledger.settings_reads == [False], "the ledger's switch is read off the loop"


def test_failover_and_terminal_failure_rows_are_written_off_the_loop(ledger):
    from kazma_core.agent.resilient_chat import resilient_chat

    down = ConnectionError("provider down")
    backup = SimpleNamespace(clients=[("fb-1", _Model(name="fb-1"))], cooldown_seconds=0)
    broken = SimpleNamespace(clients=[("fb-2", _Model(fail=ConnectionError("also down")))],
                             cooldown_seconds=0)

    async def failover():
        return await resilient_chat(_Model(fail=down), messages=[], model="primary",
                                    max_attempts=1, frozen_failover=backup)

    async def terminal():
        return await resilient_chat(_Model(fail=down), messages=[], model="primary",
                                    max_attempts=1, frozen_failover=broken)

    assert asyncio.run(_in_turn("turn-f", failover)).model == "fb-1"
    with pytest.raises(ConnectionError):
        asyncio.run(_in_turn("turn-e", terminal))
    assert _rows(ledger.conn, "turn-f") == [(0, "fb-1", "ok", "primary", 15)]
    assert _rows(ledger.conn, "turn-e") == [(0, "primary", "error", "", 0)]
    assert ledger.on_loop == [], ledger.on_loop


def test_negative_control_the_old_write_on_the_loop_is_caught(ledger, monkeypatch):
    """The pre-2026-10-15 shape -- the insert run inline in the async call --
    through the same path: the tracer sees it on the loop."""
    from kazma_core.agent.resilient_chat import resilient_chat

    async def inline(**fields: Any) -> None:
        llm_ledger._record_if_enabled(**fields)

    monkeypatch.setattr(llm_ledger, "arecord_llm_call", inline)

    async def call():
        return await resilient_chat(_Model(), messages=[], thread_id="task-1")

    asyncio.run(_in_turn("turn-old", call))
    assert _rows(ledger.conn, "turn-old"), "the row is written either way"
    assert any(sql.startswith("INSERT INTO llm_calls") for sql in ledger.on_loop), ledger.on_loop
    assert ledger.settings_reads == [True]


# ── ordering: a turn's totals include every call that returned ──────────


def _slow_writes(monkeypatch, seconds: float) -> threading.Event:
    """Every ledger insert takes *seconds*; the event is set when one starts."""
    started = threading.Event()
    real = llm_ledger.record_llm_call

    def slow(**fields: Any) -> None:
        started.set()
        time.sleep(seconds)
        real(**fields)

    monkeypatch.setattr(llm_ledger, "record_llm_call", slow)
    return started


def _sequential_reads(n: int) -> list[int]:
    from kazma_core.agent.resilient_chat import resilient_chat

    async def run() -> list[int]:
        seen: list[int] = []
        for i in range(1, n + 1):
            await resilient_chat(_Model(), messages=[], iteration=i)
            usage = await asyncio.to_thread(llm_ledger.turn_usage, "turn-seq")
            seen.append(usage["calls"])
        return seen

    return asyncio.run(_in_turn("turn-seq", run))


def test_each_call_is_in_the_totals_when_it_returns(ledger, monkeypatch):
    _slow_writes(monkeypatch, 0.05)
    assert _sequential_reads(3) == [1, 2, 3]
    assert [r[0] for r in _rows(ledger.conn, "turn-seq")] == [1, 2, 3], "in the order made"
    assert ledger.on_loop == []


def test_negative_control_an_unawaited_write_misses_the_totals(ledger, monkeypatch):
    """Fire-and-forget would keep the loop free too, but the turn's totals,
    read as soon as its calls returned, would miss rows still being written."""
    from kazma_core.background import spawn_background

    _slow_writes(monkeypatch, 0.2)

    async def unawaited(**fields: Any) -> None:
        spawn_background(asyncio.to_thread(llm_ledger._record_if_enabled, **fields),
                         name="ledger-unawaited")

    monkeypatch.setattr(llm_ledger, "arecord_llm_call", unawaited)
    assert _sequential_reads(1) == [0]


def test_concurrent_calls_of_one_turn_are_all_counted(ledger):
    from kazma_core.agent.resilient_chat import resilient_chat

    async def run() -> dict[str, Any]:
        await asyncio.gather(*(resilient_chat(_Model(), messages=[], iteration=i)
                               for i in range(8)))
        return await asyncio.to_thread(llm_ledger.turn_usage, "turn-par")

    usage = asyncio.run(_in_turn("turn-par", run))
    assert usage == {"calls": 8, "tokens": 120, "cost": pytest.approx(0.008)}
    assert sorted(r[0] for r in _rows(ledger.conn, "turn-par")) == list(range(8))
    assert ledger.on_loop == []


# ── cancellation never drops a row ──────────────────────────────────────


def _cancel_while_queued(record) -> int:
    """Cancel a caller whose write waits behind a busy thread pool; return
    the rows written once the pool frees."""

    async def run() -> int:
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        busy = threading.Event()
        blocker = loop.run_in_executor(None, busy.wait)  # the pool's only thread
        token = bind_turn_id("turn-cancel")
        try:
            task = asyncio.create_task(record(thread_id="t", prompt_tokens=1))
        finally:
            reset_turn_id(token)
        await asyncio.sleep(0.05)
        task.cancel()
        loop.call_later(0.1, busy.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        await blocker
        await asyncio.sleep(0.2)  # time for any write the pool still holds
        return llm_ledger.turn_usage("turn-cancel")["calls"]

    return asyncio.run(run())


def test_a_cancelled_caller_does_not_lose_its_row(ledger):
    assert _cancel_while_queued(llm_ledger.arecord_llm_call) == 1


def test_negative_control_an_unshielded_write_is_dropped_with_the_cancel(ledger):
    async def unshielded(**fields: Any) -> None:
        await asyncio.to_thread(llm_ledger._record_if_enabled, **fields)

    assert _cancel_while_queued(unshielded) == 0


def test_a_cancellation_waits_for_a_write_in_progress(ledger, monkeypatch):
    """The row is in before the cancellation goes on, so whatever runs after
    it (an aborted turn's totals) reads it."""
    started = _slow_writes(monkeypatch, 0.3)

    async def run(record, turn_id: str) -> int:
        token = bind_turn_id(turn_id)
        try:
            task = asyncio.create_task(record(thread_id="t"))
        finally:
            reset_turn_id(token)
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        calls = llm_ledger.turn_usage(turn_id)["calls"]
        await asyncio.sleep(0.5)  # the unjoined write lands now, after the read
        return calls

    assert asyncio.run(run(llm_ledger.arecord_llm_call, "turn-join")) == 1

    async def unjoined(**fields: Any) -> None:
        await asyncio.to_thread(llm_ledger._record_if_enabled, **fields)

    started.clear()
    # Negative control: without the join the cancellation overtakes the row.
    assert asyncio.run(run(unjoined, "turn-unjoined")) == 0
    assert llm_ledger.turn_usage("turn-unjoined")["calls"] == 1, "written, but too late"


def test_the_ledger_switch_off_writes_nothing(ledger, monkeypatch):
    monkeypatch.setattr("kazma_core.agent.nonstop.get_nonstop_config",
                        lambda *a, **k: NonStopConfig(ledger_enabled=False))

    async def call():
        await llm_ledger.arecord_llm_call(thread_id="t", prompt_tokens=1)

    asyncio.run(_in_turn("turn-off", call))
    assert _rows(ledger.conn, "turn-off") == []


# ── the supervisor and the respond node ─────────────────────────────────


def test_a_real_turn_ledgers_its_calls_off_the_loop(ledger, monkeypatch):
    """The actual supervisor graph, a fake model: the supervisor's row is
    written in a thread, under the turn's id."""
    from kazma_core.agent_evaluation import evaluate_case
    from kazma_core.config_store import get_config_store

    for key in ("KAZMA_SELF_IMPROVEMENT", "KAZMA_COMMITMENT_ENABLED", "KAZMA_LLM_STREAM"):
        monkeypatch.setenv(key, "0")
    get_config_store().set("memory.enabled", False)

    class _Client:
        async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="The answer.", model="test",
                               usage={"prompt_tokens": 7, "completion_tokens": 3})

    case = {"id": "ledger-loop", "language": "en", "split": "development",
            "group_id": "ledger", "source": "regression", "human_labeled": False,
            "rubric": "Answers.", "prompt": "Say something.", "tools": [], "fixtures": {},
            "required_tools": []}

    async def turn():
        return await evaluate_case(case, _Client(), model="test", system_prompt="Identity.")

    result = asyncio.run(_in_turn("turn-real", turn))
    assert result["answer"] == "The answer."
    rows = _rows(ledger.conn, "turn-real")
    assert rows and all(r[2] == "ok" for r in rows), rows
    assert ledger.on_loop == [], ledger.on_loop

    # Negative control: the same turn with the write run inline is caught.
    async def inline(**fields: Any) -> None:
        llm_ledger._record_if_enabled(**fields)

    monkeypatch.setattr(llm_ledger, "arecord_llm_call", inline)
    asyncio.run(_in_turn("turn-real-old", turn))
    assert _rows(ledger.conn, "turn-real-old")
    assert any(sql.startswith("INSERT INTO llm_calls") for sql in ledger.on_loop)


def test_the_respond_synthesis_row_is_written_off_the_loop(ledger):
    from kazma_core.agent.graph_respond import _ledger_synthesis_call

    resp = LLMResponse(content="x", model="syn", usage={"prompt_tokens": 4, "completion_tokens": 1})

    async def call():
        await _ledger_synthesis_call({"thread_id": "t", "iteration": 2}, object(), resp, 12.0)

    asyncio.run(_in_turn("turn-syn", call))
    assert _rows(ledger.conn, "turn-syn") == [(2, "syn", "ok", "", 5)]
    assert ledger.on_loop == []
    assert ledger.settings_reads == [False]
