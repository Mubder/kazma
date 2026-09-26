"""A turn's done frame reports the TURN, across its approval pauses.

Live 2026-09-26: a four-minute turn with three approvals finished
"Completed 0:08" and "0 tokens · $0.0000 · 4.3s". Each segment's done frame
reported its own clock and the LAST LLM call's usage (0 after a resume,
which runs without stream events). Every ledger row now carries the turn id
(observability.llm_ledger), the done frame sums it, and the clock runs from
the question (kazma_ui/turn_usage.py).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from kazma_core.observability import llm_ledger
from kazma_core.observability.correlation import bind_turn_id, reset_turn_id


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    yield llm_ledger
    conn.close()


def test_a_call_carries_the_turn_it_ran_in(ledger):
    token = bind_turn_id("turn-a")
    try:
        ledger.record_llm_call(thread_id="t", prompt_tokens=10, completion_tokens=5, cost_usd=0.01)
    finally:
        reset_turn_id(token)
    ledger.record_llm_call(thread_id="t", prompt_tokens=1, completion_tokens=1, turn_id="turn-b")
    assert ledger.turn_usage("turn-a") == {"calls": 1, "tokens": 15, "cost": 0.01}
    # Negative control: another turn's call is not this turn's.
    assert ledger.turn_usage("turn-b") == {"calls": 1, "tokens": 2, "cost": 0.0}
    assert ledger.turn_usage("") == {"calls": 0, "tokens": 0, "cost": 0.0}


class _LedgerClock:
    """The ledger's clock, one millisecond per reading: rows are ordered
    exactly, with no sleep betting on the real clock's resolution."""

    def __init__(self) -> None:
        self.t = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)

    def now(self, tz=None):
        self.t += timedelta(milliseconds=1)
        return self.t


def test_a_turn_sums_every_segment_and_a_segment_only_itself(ledger, monkeypatch):
    clock = _LedgerClock()
    monkeypatch.setattr(ledger, "datetime", clock)
    for _ in range(2):
        ledger.record_llm_call(prompt_tokens=100, completion_tokens=20, cost_usd=0.001, turn_id="x")
    since = clock.now().isoformat()  # the next segment starts here
    ledger.record_llm_call(prompt_tokens=7, completion_tokens=3, cost_usd=0.0005, turn_id="x")
    whole = ledger.turn_usage("x")
    assert whole["calls"] == 3 and whole["tokens"] == 250
    assert whole["cost"] == pytest.approx(0.0025)
    assert ledger.turn_usage("x", since=since) == {"calls": 1, "tokens": 10, "cost": 0.0005}


def test_a_ledger_from_before_turn_ids_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " thread_id TEXT DEFAULT '', iteration INTEGER DEFAULT 0, provider TEXT DEFAULT '',"
        " model TEXT DEFAULT '', prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER"
        " DEFAULT 0, total_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0, duration_ms"
        " REAL DEFAULT 0.0, status TEXT DEFAULT 'ok', error_kind TEXT DEFAULT '',"
        " failover_from TEXT DEFAULT '')"
    )
    old.execute("INSERT INTO llm_calls (ts, total_tokens) VALUES ('2026-09-01T00:00:00', 5)")
    old.commit()
    old.close()
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        assert "turn_id" in cols
        llm_ledger.record_llm_call(prompt_tokens=1, completion_tokens=1, turn_id="new")
        assert llm_ledger.turn_usage("new")["calls"] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
    finally:
        conn.close()


# ── the clock runs from the question ─────────────────────────────────────


class _Sessions:
    def __init__(self, rows):
        self.rows = rows

    def get(self, session_id):
        return type("S", (), {"messages": self.rows})()


def test_the_turn_started_when_its_question_was_asked(monkeypatch):
    import kazma_ui.session_manager as sm
    from kazma_ui.turn_usage import turn_started_epoch

    rows = [
        {"role": "user", "content": "one", "ts": "2026-09-26T01:00:00+00:00"},
        {"role": "assistant", "turn_id": "A", "ts": "2026-09-26T01:00:02+00:00"},
        {"role": "user", "content": "two", "ts": "2026-09-26T01:05:00+00:00"},
        {"role": "assistant", "turn_id": "B", "ts": "2026-09-26T01:05:03+00:00"},
    ]
    monkeypatch.setattr(sm, "get_session_manager", lambda: _Sessions(rows))
    assert turn_started_epoch("s", "A") == datetime(2026, 9, 26, 1, 0, tzinfo=UTC).timestamp()
    assert turn_started_epoch("s", "B") == datetime(2026, 9, 26, 1, 5, tzinfo=UTC).timestamp()
    assert turn_started_epoch("s", "nope") is None
    assert turn_started_epoch("", "A") is None


def test_a_session_read_that_fails_is_no_clock_not_a_broken_turn(monkeypatch):
    import kazma_ui.session_manager as sm
    from kazma_ui.turn_usage import turn_started_epoch

    class _Down:
        def get(self, session_id):
            raise RuntimeError("database gone")

    monkeypatch.setattr(sm, "get_session_manager", lambda: _Down())
    assert turn_started_epoch("s", "A") is None


# ── the done frame, end to end through the streamer ──────────────────────


def _frames_of(frames: list[str], event: str) -> list[dict[str, Any]]:
    out = []
    for frame in frames:
        body = frame.lstrip("id: 0123456789\n")
        if body.startswith(f"event: {event}"):
            out.append(json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0]))
    return out


def test_the_done_frame_reports_the_whole_turn(ledger, tmp_path, monkeypatch):
    """Segment 2 of a turn that paused once: the done frame carries all three
    calls and the clock since the question; the session is charged only the
    segment's call, because segment 1 was charged when it ended."""
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    from kazma_ui.session_manager import get_session_manager, reset_session_manager
    from kazma_ui.sse_chat import _stream_langgraph_events

    reset_session_manager()
    asked = datetime.now(UTC) - timedelta(minutes=4)
    sess = get_session_manager().get_or_create("sess-u")
    sess.messages = [
        {"role": "user", "content": "q", "ts": asked.isoformat()},
        {"role": "assistant", "turn_id": "turn-u", "ts": asked.isoformat(), "content": ""},
    ]
    # Segment 1 (before the pause): two calls, already charged to the session.
    for _ in range(2):
        llm_ledger.record_llm_call(prompt_tokens=1000, completion_tokens=100,
                                   cost_usd=0.01, turn_id="turn-u")
    charged: list[tuple[int, float]] = []
    manager = get_session_manager()
    real_add = manager.add_usage

    def add_usage(session_id, tokens, cost):
        charged.append((tokens, round(cost, 6)))
        return real_add(session_id, tokens, cost)

    monkeypatch.setattr(manager, "add_usage", add_usage)

    async def _segment(*args, **kwargs):
        # Segment 2's own LLM call happens inside the stream, under the turn id.
        await asyncio.sleep(0.01)
        llm_ledger.record_llm_call(prompt_tokens=40, completion_tokens=10, cost_usd=0.002)
        yield {
            "event": "on_chain_end",
            "data": {"output": {"last_tokens": 50, "last_cost_usd": 0.002,
                                "messages": [{"role": "assistant", "content": "done."}]}},
            "name": "__end__",
        }

    graph = MagicMock()
    graph.astream_events.side_effect = _segment

    async def _collect():
        return [f async for f in _stream_langgraph_events(
            graph, {"messages": []}, {"configurable": {"thread_id": "th-u"}},
            thread_id="th-u", session_id="sess-u", reply_turn_id="turn-u",
        )]

    frames = asyncio.run(_collect())
    done = _frames_of(frames, "done")[-1]
    assert done["tokens"] == 2250, done  # 2 x 1100 + 50
    assert done["cost"] == pytest.approx(0.022)
    assert done["duration_ms"] >= 239_000, "the clock runs from the question"
    assert charged == [(50, 0.002)], "the session is charged this segment only"
    # ...and the row keeps the turn's numbers, so a reload shows them too.
    row = next(m for m in get_session_manager().get("sess-u").messages
               if m.get("role") == "assistant" and m.get("turn_id") == "turn-u")
    assert row["tokens"] == 2250 and row["cost"] == pytest.approx(0.022)
    assert row["duration_ms"] >= 239_000


def test_negative_control_without_the_ledger_the_last_call_is_all_there_is(
    tmp_path, monkeypatch
):
    """With the ledger off (no rows), the frame keeps what the stream saw --
    the behaviour the totals replace, shown so the test above means something."""
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    monkeypatch.setattr(llm_ledger, "turn_usage",
                        lambda turn_id, since="": {"calls": 0, "tokens": 0, "cost": 0.0})
    from kazma_ui.session_manager import reset_session_manager
    from kazma_ui.sse_chat import _stream_langgraph_events

    reset_session_manager()

    async def _segment(*args, **kwargs):
        yield {
            "event": "on_chain_end",
            "data": {"output": {"last_tokens": 50, "last_cost_usd": 0.002,
                                "messages": [{"role": "assistant", "content": "done."}]}},
            "name": "__end__",
        }

    graph = MagicMock()
    graph.astream_events.side_effect = _segment

    async def _collect():
        return [f async for f in _stream_langgraph_events(
            graph, {"messages": []}, {"configurable": {"thread_id": "th-n"}},
            thread_id="th-n", session_id="", reply_turn_id="turn-n",
        )]

    done = _frames_of(asyncio.run(_collect()), "done")[-1]
    assert done["tokens"] == 50
