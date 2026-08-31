"""Detached-reply persistence + unanswered-turn backfill (live incident 2026-08-21).

The user's Web tab disconnected mid-turn (refresh). The detached pump
finished the graph and the reply landed in the checkpoint — but the
done-callback persist failed with a DEBUG-swallowed exception, so the
session store never got the answer and the user waited hours with no
response and no log line. These tests pin the two fixes:

1. ``_persist_detached_reply`` records the reply without disturbing any
   other turn's row (the old code OVERWROTE the previous turn's reply) and
   logs failures at WARNING.
2. ``_checkpoint_backfill_unanswered`` surfaces a checkpointed reply for
   a session whose transcript ends with an unanswered user message.

Both now write through :mod:`kazma_ui.reply_sink`, so the fake store has to
be installed on the sink as well as on ``sse_chat``.
"""

from __future__ import annotations

# Patch the seam at its definition site: these helpers moved into
# kazma_ui.sse_chat._helpers when the module was split (audit O5),
# and the callers resolve them through that module object.
from kazma_ui.sse_chat import _helpers as _sse_helpers

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kazma_ui import reply_sink


@pytest.fixture(autouse=True)
def _reset_turns():
    """Turn identities are process-global; keep tests independent."""
    reply_sink.reset_reply_turns()
    yield
    reply_sink.reset_reply_turns()


def _install(monkeypatch, store):
    """Point both the module helpers and the reply sink at *store*."""
    from kazma_ui import sse_chat

    monkeypatch.setattr(_sse_helpers, "_module_store", lambda: store)
    monkeypatch.setattr(reply_sink, "_store", lambda: store)


class _FakeStore:
    def __init__(self, session):
        self.session = session
        self.puts = 0

    def transact(self, session_id):
        return _Transact(self)


class _Transact:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self._store.session

    def __exit__(self, *exc):
        self._store.puts += 1
        return False


def _fake_graph(final_messages):
    snap = SimpleNamespace(values={"messages": final_messages})
    g = MagicMock()
    g.aget_state = MagicMock(return_value=_async_return(snap))
    return g


def _async_return(value):
    import asyncio

    fut = asyncio.get_event_loop().create_future()
    fut.set_result(value)
    return fut


@pytest.mark.asyncio
async def test_detached_persists_appends_after_trailing_user(monkeypatch):
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1",
        messages=[
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "PREVIOUS ANSWER — must survive"},
            {"role": "user", "content": "what was my XHypert name?"},
        ],
    )
    store = _FakeStore(session)
    _install(monkeypatch, store)

    graph = _fake_graph([
        {"role": "user", "content": "what was my XHypert name?"},
        {"role": "assistant", "content": "Yes — your XHypert name was exactly that."},
    ])

    await sse_chat._persist_detached_reply(
        graph, {"configurable": {"thread_id": "t1"}}, "s1", "t1"
    )

    last = session.messages[-1]
    assert last["role"] == "assistant"
    assert last["content"] == "Yes — your XHypert name was exactly that."
    # Every writer stamps `ts` on appended assistant rows (2026-08-26
    # shape-consistency fix) — presence, not the exact value, is the contract.
    assert "ts" in last
    # The previous turn's answer is intact — the old code overwrote it.
    assert session.messages[1]["content"] == "PREVIOUS ANSWER — must survive"
    assert len(session.messages) == 4
    assert store.puts == 1


@pytest.mark.asyncio
async def test_detached_persist_failure_logs_warning(monkeypatch, caplog):
    import logging

    from kazma_ui import sse_chat

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(_sse_helpers, "_module_store", _boom)
    monkeypatch.setattr(reply_sink, "_store", _boom)
    graph = _fake_graph([{"role": "assistant", "content": "answer"}])

    with caplog.at_level(logging.WARNING):
        # Must not raise — and must log at WARNING (was debug-swallowed).
        await sse_chat._persist_detached_reply(
            graph, {"configurable": {"thread_id": "t1"}}, "s1", "t1",
            reply_turn_id="turn-1",
        )
    assert any("FAILED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_backfill_surfaces_unanswered_checkpoint_reply(monkeypatch):
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1",
        thread_id="t1",
        messages=[
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "what was my XHypert name?"},
        ],
    )
    store = _FakeStore(session)
    graph = _fake_graph([
        {"role": "user", "content": "what was my XHypert name?"},
        {"role": "assistant", "content": "XHypert (variants: HypertX, HypertAI)."},
    ])
    _install(monkeypatch, store)
    monkeypatch.setattr(_sse_helpers, "_module_graph", lambda: graph)

    out = await sse_chat._checkpoint_backfill_unanswered(session)

    assert out[-1]["role"] == "assistant"
    assert "XHypert" in out[-1]["content"]
    # Stored session healed too
    assert session.messages[-1]["content"] == out[-1]["content"]


@pytest.mark.asyncio
async def test_backfill_noop_when_session_is_answered(monkeypatch):
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1",
        thread_id="t1",
        messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ],
    )
    store = _FakeStore(session)
    graph = _fake_graph([])
    _install(monkeypatch, store)
    monkeypatch.setattr(_sse_helpers, "_module_graph", lambda: graph)

    out = await sse_chat._checkpoint_backfill_unanswered(session)
    assert len(out) == 2
    assert store.puts == 0  # no heal write


@pytest.mark.asyncio
async def test_backfill_noop_when_checkpoint_does_not_end_with_assistant(monkeypatch):
    """A stale checkpoint (last state message is a tool/user) must NOT
    fabricate an answer for the pending question."""
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1",
        thread_id="t1",
        messages=[{"role": "user", "content": "q"}],
    )
    store = _FakeStore(session)
    graph = _fake_graph([
        {"role": "user", "content": "older turn"},
        {"role": "assistant", "content": "stale answer to an older question"},
    ])
    # Make the checkpoint END with a user message → guard must refuse.
    graph.aget_state = MagicMock(
        return_value=_async_return(
            SimpleNamespace(
                values={
                    "messages": [
                        {"role": "assistant", "content": "stale answer"},
                        {"role": "user", "content": "q"},
                    ]
                }
            )
        )
    )
    _install(monkeypatch, store)
    monkeypatch.setattr(_sse_helpers, "_module_graph", lambda: graph)

    out = await sse_chat._checkpoint_backfill_unanswered(session)
    assert len(out) == 1
    assert store.puts == 0


@pytest.mark.asyncio
async def test_cancelled_turn_persists_full_streamed_narration(monkeypatch):
    """2026-08-27 incident: a 96-second sweep was stopped mid-stream; the
    checkpoint held only the last 158-char interim segment, and the detached
    persist wrote THAT — discarding the 2,272 streamed chars. The streamed
    accumulation must win when it is the richer text."""
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1", messages=[{"role": "user", "content": "go", "ts": "t"}]
    )
    store = _FakeStore(session)
    _install(monkeypatch, store)

    graph = _fake_graph([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "All 40 lookups done. Next: social sweep"},
    ])
    streamed = "Narration batch 1\n\nbatch 2\n\nfinal table with all greens"
    assert len(streamed) > len("All 40 lookups done. Next: social sweep")

    await sse_chat._persist_detached_reply(
        graph, {}, "s1", "th1", streamed_text=streamed, reply_turn_id="turn-a"
    )
    assert session.messages[-1]["content"] == streamed

    # And the checkpoint text still wins when it is the richer one.
    session.messages = [{"role": "user", "content": "go", "ts": "t"}]
    long_final = "Full final reply that is longer than the short streamed bit"
    graph = _fake_graph([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": long_final},
    ])
    await sse_chat._persist_detached_reply(
        graph, {}, "s1", "th1", streamed_text="short", reply_turn_id="turn-b"
    )
    assert session.messages[-1]["content"] == long_final


@pytest.mark.asyncio
async def test_interrupted_turn_never_corrupts_another_turns_reply(monkeypatch):
    """2026-08-27 incident (text-swap on app-switch): an interrupted (HITL)
    turn wrote its interim narration over the PREVIOUS turn's answer
    (1,080 -> 225 -> 151 chars), and the client's visibility resync then
    painted the corrupted row over the visible bubble.

    The guard added for it (4d646e2b) never fired — it read a flag set after
    the callback ran. The protection is now structural: the interim text goes
    into the CURRENT turn's own row, so no other turn can be touched. The row
    stays ``open`` so the approval resume replaces the narration with the
    final answer rather than adding a second bubble.
    """
    from kazma_ui import sse_chat

    session = SimpleNamespace(
        session_id="s1",
        messages=[
            {"role": "user", "content": "post the tweets"},
            {
                "role": "assistant",
                "content": "FULL GOOD REPLY (1080 chars worth)",
                "ts": "t1",
                "turn_id": "turn-previous",
            },
        ],
    )
    store = _FakeStore(session)
    _install(monkeypatch, store)

    graph = _fake_graph([
        {"role": "user", "content": "post the tweets"},
        {"role": "assistant", "content": "FULL GOOD REPLY (1080 chars worth)"},
    ])
    narration = "Short interim narration before the next approval"

    await sse_chat._persist_detached_reply(
        graph, {}, "s1", "th1",
        streamed_text=narration,
        interrupted=True,
        reply_turn_id="turn-current",
    )

    # The previous turn's answer is untouched — that is the incident.
    assert session.messages[1]["content"] == "FULL GOOD REPLY (1080 chars worth)"
    # The paused turn's narration is visible on a mid-pause reload, in its
    # own row, still marked open for the resume to complete.
    current = session.messages[-1]
    assert current["turn_id"] == "turn-current"
    assert current["content"] == narration
    assert current["open"] is True

    # The resume writes the final answer INTO that row — one question, one
    # bubble — and closes it.
    graph = _fake_graph([
        {"role": "user", "content": "post the tweets"},
        {"role": "assistant", "content": "Posted all 4 Arabic tweets."},
    ])
    await sse_chat._persist_detached_reply(
        graph, {}, "s1", "th1", reply_turn_id="turn-current"
    )
    assert len(session.messages) == 3
    assert session.messages[-1]["content"] == "Posted all 4 Arabic tweets."
    assert "open" not in session.messages[-1]


@pytest.mark.asyncio
async def test_backfill_replaces_short_closed_interim(monkeypatch):
    """Cancelled SSE flushed a short closed assistant row; the real 2660-char
    answer lives only in the checkpoint. Backfill must replace, not no-op."""
    from kazma_ui import sse_chat

    interim = "Working on it — scanning the tree…"
    final = interim + "\n\n" + ("The full answer. " * 80)
    assert len(final) > len(interim) + 400
    session = SimpleNamespace(
        session_id="s1",
        thread_id="t1",
        messages=[
            {"role": "user", "content": "what happened?"},
            {
                "role": "assistant",
                "content": interim,
                "turn_id": "turn-1",
            },
        ],
    )
    store = _FakeStore(session)
    graph = _fake_graph(
        [
            {"role": "user", "content": "what happened?"},
            {"role": "assistant", "content": final},
        ]
    )
    _install(monkeypatch, store)
    monkeypatch.setattr(_sse_helpers, "_module_graph", lambda: graph)

    out = await sse_chat._checkpoint_backfill_unanswered(session)
    # _user_facing_reply may strip trailing whitespace; the heal must still
    # replace the short interim with the long checkpoint answer.
    assert out[-1]["content"].startswith(interim)
    assert len(out[-1]["content"]) > len(interim) + 400
    assert len(out) == 2
