"""End-to-end regression for the 2026-08-28 incident, at the streamer level.

The sequence that broke, verbatim from the live logs and the Postgres
transcript:

    23:59:00  user: "why not sending them now?"
    00:00:21  turn pauses on a 4-tool approval card
              -> detached callback persists 125 chars   (00:00:21.405)
              -> HITL interrupt detected                (00:00:21.413)   <- 8 ms
              -> live final persist appends the SAME 125 chars again
    00:00:58  user approves; 4 tweets post (HTTP 201 x4)
    00:01:15  server produces the 1,781-char final answer
              -> POST /api/approve streams it and persists NOTHING

Stored result: two identical narration rows, no final answer. What the user
saw after refreshing: their question answered by a stub, twice.

This test drives the real ``_stream_langgraph_events`` through both halves —
the interrupted prompt turn and the approve-resume turn — against a fake
graph and a fake store, and asserts the transcript a reload would render.
"""

from __future__ import annotations

# Patch the seam at its definition site: these helpers moved into
# kazma_ui.sse_chat._helpers when the module was split (audit O5),
# and the callers resolve them through that module object.
from kazma_ui.sse_chat import _helpers as _sse_helpers

import asyncio
from types import SimpleNamespace

import pytest
from kazma_ui import reply_sink
from kazma_ui.reply_sink import resolve_reply_turn
from kazma_ui.sse_chat import _stream_langgraph_events
from langgraph.types import Command

NARRATION = "The state is now clear from the DBs - let me pull the exact texts."
FINAL = "Posted all 4 Arabic tweets. " * 60  # a long real answer


class _Session:
    def __init__(self):
        self.session_id = "sess-1"
        self.thread_id = "thread-1"
        self.messages = [{"role": "user", "content": "why not sending them now?"}]


class _Transact:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self._store.session

    def __exit__(self, *exc):
        return False


class _Store:
    def __init__(self):
        self.session = _Session()

    def transact(self, session_id):
        return _Transact(self)

    def get(self, session_id):
        return self.session

    def add_usage(self, session_id, tokens, cost):
        return tokens, cost


class _Snap:
    """Graph snapshot: paused on an approval, or finished."""

    def __init__(self, text, paused):
        self.values = {
            "messages": [
                {"role": "user", "content": "why not sending them now?"},
                {"role": "assistant", "content": text},
            ]
        }
        if paused:
            interrupt = SimpleNamespace(
                value={
                    "type": "hitl_approval",
                    "kind": "security",
                    "tool": "4 tools",
                    "args": {},
                }
            )
            self.tasks = [SimpleNamespace(interrupts=[interrupt])]
            self.next = ("ToolWorker",)
        else:
            self.tasks = []
            self.next = ()


class _Graph:
    """Pauses on the first run, completes on the resume."""

    def __init__(self):
        self.snap = _Snap(NARRATION, paused=True)

    def astream_events(self, *a, **k):
        async def _gen():
            return
            yield  # pragma: no cover - empty async generator

        return _gen()

    async def ainvoke(self, *a, **k):
        self.snap = _Snap(FINAL, paused=False)
        return {}

    async def aget_state(self, config):
        return self.snap


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(reply_sink, "_store", lambda: st)
    from kazma_ui import sse_chat

    monkeypatch.setattr(_sse_helpers, "_module_store", lambda: st)
    reply_sink.reset_reply_turns()
    yield st
    reply_sink.reset_reply_turns()


async def _drain(gen):
    async for _ in gen:
        pass


def _assistant_rows(store):
    return [m for m in store.session.messages if m.get("role") == "assistant"]


def test_pause_then_approve_leaves_one_bubble_holding_the_final_answer(store):
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    graph = _Graph()

    async def _run():
        # ── half 1: the prompt turn, which pauses for approval ──
        prompt_turn = reply_sink.open_reply_turn("thread-1")
        await _drain(
            _stream_langgraph_events(
                graph,
                {"messages": []},
                config,
                thread_id="thread-1",
                session_id="sess-1",
                reply_turn_id=prompt_turn,
            )
        )

        rows = _assistant_rows(store)
        assert len(rows) == 1, f"pause must not duplicate: {rows}"
        assert rows[0]["content"] == NARRATION
        assert rows[0]["open"] is True, "a paused turn stays open for its resume"

        # ── half 2: POST /api/approve resumes the SAME turn ──
        # This is what the endpoint now does; before, it passed neither a
        # session nor a turn and wrote nothing at all.
        resume_turn = resolve_reply_turn("thread-1", "sess-1")
        assert resume_turn == prompt_turn, "the resume must continue the turn"

        await _drain(
            _stream_langgraph_events(
                graph,
                Command(resume={"approved": True}),
                config,
                thread_id="thread-1",
                session_id="sess-1",
                reply_turn_id=resume_turn,
            )
        )

    asyncio.run(_run())

    rows = _assistant_rows(store)
    assert len(rows) == 1, f"one question must leave one bubble, got {len(rows)}"
    assert rows[0]["content"] == FINAL.strip(), "the reload must show the FINAL answer"
    assert "open" not in rows[0], "a completed turn must not stay open"
    assert "pending" not in rows[0]


def test_resume_without_a_session_is_loud_not_silent(store):
    """A resume that cannot persist must not look like a success.

    The endpoint logs a warning when it cannot resolve a session; the sink
    refuses the write rather than inventing a row.
    """
    from kazma_ui.reply_sink import upsert_reply

    assert upsert_reply("", "turn-1", "text") is False
    assert _assistant_rows(store) == []


def test_paused_turn_row_stays_open_for_a_restarted_process(store):
    """The pause marker must survive every writer in the turn.

    The streamer keeps the row open when it detects the interrupt, but the
    caller's final stamp (activity/usage/model) runs afterwards — if that
    write closes the row, a server restart during the approval pause leaves
    the resume with nothing to adopt, and the answer lands in a second
    bubble. Both writers must agree the turn is still open.
    """
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    graph = _Graph()  # pauses on the first run

    async def _run():
        turn = reply_sink.open_reply_turn("thread-1")
        await _drain(
            _stream_langgraph_events(
                graph,
                {"messages": []},
                config,
                thread_id="thread-1",
                session_id="sess-1",
                reply_turn_id=turn,
            )
        )
        # Caller's post-stream stamp, as _event_generator performs it for an
        # interrupted turn.
        reply_sink.upsert_reply(
            "sess-1", turn, NARRATION, open_turn=True, model="deepseek-v4-flash"
        )

    asyncio.run(_run())

    row = _assistant_rows(store)[-1]
    assert row["open"] is True

    # Process restart: in-memory identity is gone, the row carries it.
    reply_sink.reset_reply_turns()
    assert reply_sink.resolve_reply_turn("thread-1", "sess-1") == row["turn_id"]
