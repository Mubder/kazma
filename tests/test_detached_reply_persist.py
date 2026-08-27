"""Detached-reply persistence + unanswered-turn backfill (live incident 2026-08-21).

The user's Web tab disconnected mid-turn (refresh). The detached pump
finished the graph and the reply landed in the checkpoint — but the
done-callback persist failed with a DEBUG-swallowed exception, so the
session store never got the answer and the user waited hours with no
response and no log line. These tests pin the two fixes:

1. ``_persist_detached_reply`` appends after a trailing user message
   (the old code OVERWROTE the previous turn's reply) and logs failures
   at WARNING.
2. ``_checkpoint_backfill_unanswered`` surfaces a checkpointed reply for
   a session whose transcript ends with an unanswered user message.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


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
    monkeypatch.setattr(sse_chat, "_module_store", lambda: store)

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

    session = SimpleNamespace(session_id="s1", messages=[])
    monkeypatch.setattr(
        sse_chat, "_module_store", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    graph = _fake_graph([{"role": "assistant", "content": "answer"}])

    with caplog.at_level(logging.WARNING, logger="kazma_ui.sse_chat"):
        # Must not raise — and must log at WARNING (was debug-swallowed).
        await sse_chat._persist_detached_reply(
            graph, {"configurable": {"thread_id": "t1"}}, "s1", "t1"
        )
    assert any("persist FAILED" in r.message for r in caplog.records)


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
    monkeypatch.setattr(sse_chat, "_module_store", lambda: store)
    monkeypatch.setattr(sse_chat, "_module_graph", lambda: graph)

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
    monkeypatch.setattr(sse_chat, "_module_store", lambda: store)
    monkeypatch.setattr(sse_chat, "_module_graph", lambda: graph)

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
    monkeypatch.setattr(sse_chat, "_module_store", lambda: store)
    monkeypatch.setattr(sse_chat, "_module_graph", lambda: graph)

    out = await sse_chat._checkpoint_backfill_unanswered(session)
    assert len(out) == 1
    assert store.puts == 0


@pytest.mark.asyncio
async def test_cancelled_turn_persists_full_streamed_narration():
    """2026-08-27 incident: a 96-second sweep was stopped mid-stream; the
    checkpoint held only the last 158-char interim segment, and the detached
    persist wrote THAT — discarding the 2,272 streamed chars. The streamed
    accumulation must win when it is the richer text."""
    import kazma_ui.sse_chat as sc

    class _Snap:
        values = {"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "All 40 lookups done. Next: social sweep"},
        ]}

    class _Graph:
        async def aget_state(self, config):
            return _Snap()

    class _Sess:
        messages = [{"role": "user", "content": "go", "ts": "t"}]
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Store:
        def transact(self, sid):
            return _Sess()

    sc._module_store = lambda: _Store()
    streamed = "Narration batch 1 …\n\nbatch 2 …\n\nfinal table with all greens"
    assert len(streamed) > len("All 40 lookups done. Next: social sweep")
    await sc._persist_detached_reply(_Graph(), {}, "s1", "th1", streamed_text=streamed)
    assert _Sess.messages[-1]["content"] == streamed

    # And the checkpoint text still wins when it is the richer one.
    _Sess.messages = [{"role": "user", "content": "go", "ts": "t"}]
    long_final = "Full final reply that is longer than the short streamed bit"
    _Snap.values = {"messages": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": long_final},
    ]}
    await sc._persist_detached_reply(_Graph(), {}, "s1", "th1", streamed_text="short")
    assert _Sess.messages[-1]["content"] == long_final


@pytest.mark.asyncio
async def test_interrupted_turn_never_mutates_history():
    """2026-08-27 incident (text-swap on app-switch): an interrupted (HITL)
    turn persisted its interim narration as durable truth. In a resume cycle
    the trailing message is already an assistant row, so the else-branch
    REPLACED the previous good reply with a short pre-approval segment
    (1,080 -> 225 -> 151 chars). The client's visibility resync then painted
    that corrupted row over the visible bubble. An interrupted turn must
    never append or replace history — only settle a pending bubble."""
    import kazma_ui.sse_chat as sc

    class _Snap:
        values = {"messages": [
            {"role": "user", "content": "post the tweets"},
            {"role": "assistant", "content": "FULL GOOD REPLY (1080 chars worth)"},
        ]}

    class _Graph:
        async def aget_state(self, config):
            return _Snap()

    class _Sess:
        messages = [
            {"role": "user", "content": "post the tweets", "ts": "t0"},
            {"role": "assistant", "content": "FULL GOOD REPLY (1080 chars worth)", "ts": "t1"},
        ]
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Store:
        def transact(self, sid):
            return _Sess()

    sc._module_store = lambda: _Store()
    await sc._persist_detached_reply(
        _Graph(), {}, "s1", "th1",
        streamed_text="Short interim narration before the next approval",
        interrupted=True,
    )
    # History untouched — no append, no replacement.
    assert len(_Sess.messages) == 2
    assert _Sess.messages[-1]["content"] == "FULL GOOD REPLY (1080 chars worth)"

    # But a lingering pending bubble still gets settled with the narration,
    # so a refresh mid-pause shows text instead of an empty in-progress row.
    _Sess.messages.append({"role": "assistant", "content": "", "pending": True})
    await sc._persist_detached_reply(
        _Graph(), {}, "s1", "th1",
        streamed_text="Short interim narration before the next approval",
        interrupted=True,
    )
    assert _Sess.messages[-1].get("pending") is None
    assert _Sess.messages[-1]["content"] == "Short interim narration before the next approval"
    assert _Sess.messages[1]["content"] == "FULL GOOD REPLY (1080 chars worth)"
