"""A reload mid-stream ends the stream with what is true (FA-262).

The 2026-11 functional audit, a graceful reload with a turn writing its
answer: 90 ms after the shutdown signal the stream sent "⚠️ The agent paused
mid-turn (no approval card could be built). Send a follow-up to continue." --
nothing had paused; the turn ran on detached and its full answer was stored
ten seconds later. In a second drill the stream sent ``done`` with the partial
text, ``interrupted=False``: the client was told a partial answer was the
whole one. The stream broke out of its loop on the shutdown flag, and the
post-stream reading took the checkpoint's next step for a pause.

The real streamer, a graph still streaming when the flag is set: the stream
ends with ``done`` flagged ``stopped_for_shutdown``, no content, the notice in
the page's language; no "paused" text, no reply saved from the cut stream. The
negative control is the same graph's checkpoint read by the old ending.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from kazma_core import shutdown


def _frames(frames: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in frames:
        body = frame.lstrip("id: 0123456789\n")
        if body.startswith("event: "):
            name = body[len("event: "):].split("\n", 1)[0]
            out.append((name, json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])))
    return out


def _still_writing_graph() -> MagicMock:
    """A turn writing its answer: a token every 20 ms until cancelled; its
    checkpoint is mid-turn (the supervisor next, no interrupt)."""
    async def events(*args, **kwargs):
        i = 0
        while True:
            yield {"event": "on_chat_model_stream",
                   "data": {"chunk": SimpleNamespace(content=f"word{i} ")}}
            i += 1
            await asyncio.sleep(0.02)

    graph = MagicMock()
    graph.astream_events.side_effect = events

    async def mid_turn(config):
        return SimpleNamespace(next=("supervisor",), values={"messages": []}, tasks=())

    graph.aget_state.side_effect = mid_turn
    return graph


@pytest.fixture(autouse=True)
def flag():
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()


def test_a_reload_mid_stream_says_kazma_is_restarting(monkeypatch, tmp_path):
    from kazma_ui import turn_runtime
    from kazma_ui.session_manager import reset_session_manager
    from kazma_ui.sse_chat import _stream_langgraph_events

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    reset_session_manager()
    saved: list[str] = []
    monkeypatch.setattr(turn_runtime, "persist_reply", lambda *a, **k: saved.append(str(a[2] if len(a) > 2 else "")))
    graph = _still_writing_graph()

    async def collect() -> list[str]:
        out: list[str] = []
        agen = _stream_langgraph_events(graph, {"messages": []}, {"configurable": {"thread_id": "th-sd"}},
                                        thread_id="th-sd", session_id="", reply_turn_id="turn-sd")
        async for frame in agen:
            out.append(frame)
            if len(out) == 3:
                shutdown.signal_shutdown()  # the guard's CTRL_BREAK, mid-answer
        return out

    frames = _frames(asyncio.run(asyncio.wait_for(collect(), timeout=30)))
    done = [d for name, d in frames if name == "done"]
    assert len(done) == 1 and done[0]["stopped_for_shutdown"] is True, done
    assert done[0]["content"] == "" and "restarting" in done[0]["notice"].lower()
    text = json.dumps(frames, ensure_ascii=False)
    assert "paused mid-turn" not in text
    assert not any(name == "turn_complete" for name, _ in frames)
    assert graph.aget_state.call_count == 0, "the post-stream reading never runs"
    assert saved == [], "no reply saved from a cut stream"


def test_the_old_ending_read_the_checkpoint_as_a_pause(monkeypatch, tmp_path):
    """Negative control: the post-stream reading, given the same mid-turn
    checkpoint and nothing streamed, says the agent paused."""
    from kazma_ui import turn_runtime
    from kazma_ui.session_manager import reset_session_manager
    from kazma_ui.sse_chat import _stream_langgraph_events

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    reset_session_manager()
    monkeypatch.setattr(turn_runtime, "persist_reply", lambda *a, **k: None)

    async def nothing(*args, **kwargs):
        if False:
            yield {}

    graph = _still_writing_graph()
    graph.astream_events.side_effect = nothing

    async def collect() -> list[str]:
        return [f async for f in _stream_langgraph_events(
            graph, {"messages": []}, {"configurable": {"thread_id": "th-old"}},
            thread_id="th-old", session_id="", reply_turn_id="turn-old")]

    text = json.dumps(_frames(asyncio.run(asyncio.wait_for(collect(), timeout=30))), ensure_ascii=False)
    assert "paused mid-turn" in text
