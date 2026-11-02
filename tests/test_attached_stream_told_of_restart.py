"""A tab reading its turn through the journal is told Kazma is restarting.

Roadmap theme 2 (2026-11), found by the graceful reload drill
(``tests/test_drill_graceful_reload.py``): FA-262 made the turn's own stream
end with ``done`` flagged ``stopped_for_shutdown`` when Kazma restarts under
it, and its gate drove that stream directly. But the tab that sent the turn
reads it through the journal-attached tail (``_sse_attach_stream``), and the
tail returned the moment the shutdown began (FA-261) -- before the
producer's ``done`` came through. On a real reload the page kept a cut
answer and no word of the restart.

The tail now passes on what is queued, the turn's own ``done`` included,
and sends the same frame itself if the producer is late. A paused turn (an
approval waiting) still ends quietly: the page re-attaches when Kazma is
back and the approval returns with it. The old early return is the negative
control.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kazma_core import shutdown
from kazma_ui.active_turns import register_turn, unregister_turn
from kazma_ui.delivery import get_turn_broker, reset_turn_broker
from kazma_ui.sse_chat import _streaming as streaming


@pytest.fixture(autouse=True)
def _clean():
    reset_turn_broker()
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()
    reset_turn_broker()
    streaming.mark_thread_unpaused("tR")


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        event = next((ln[6:].strip() for ln in chunk.splitlines() if ln.startswith("event:")), "")
        data = next((ln[5:].strip() for ln in chunk.splitlines() if ln.startswith("data:")), "")
        if event:
            out.append((event, json.loads(data or "{}")))
    return out


async def _attach_then_stop(*, producer_done: bool, paused: bool = False) -> list[tuple[str, dict]]:
    broker = get_turn_broker()
    await broker.emit("tR", {"type": "token", "data": {"content": "word0 ", "turn_id": "turn-1"}})
    turn = asyncio.create_task(asyncio.sleep(3600))
    register_turn("tR", turn)
    if paused:
        streaming.mark_thread_paused("tR")
    chunks: list[str] = []

    async def read() -> None:
        async for chunk in streaming._sse_attach_stream("tR", "sess", 0, replay_is_history=False):
            chunks.append(chunk)

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.1)
        await broker.emit("tR", {"type": "token", "data": {"content": "word1 ", "turn_id": "turn-1"}})
        await asyncio.sleep(0.05)
        shutdown.signal_shutdown()  # the guard's CTRL_BREAK
        if producer_done:
            # The turn's own stream sends its done a moment after the flag.
            await asyncio.sleep(0.05)
            await broker.emit("tR", {"type": "done", "data": {**streaming._shutdown_done_payload("turn-1")}})
        await asyncio.wait_for(reader, timeout=10)
    finally:
        turn.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
        unregister_turn("tR", turn)
    return _frames(chunks)


@pytest.mark.asyncio
async def test_the_turns_own_done_reaches_the_attached_tab():
    frames = await _attach_then_stop(producer_done=True)
    done = [d for e, d in frames if e == "done"]
    assert len(done) == 1, frames
    assert done[0]["stopped_for_shutdown"] is True and done[0]["content"] == ""
    assert done[0].get("turn_id") == "turn-1"
    assert [d["content"] for e, d in frames if e == "token"] == ["word0 ", "word1 "]


@pytest.mark.asyncio
async def test_a_late_producer_still_leaves_the_tab_told():
    """The producer's done does not arrive in time: the tail sends it."""
    frames = await _attach_then_stop(producer_done=False)
    done = [d for e, d in frames if e == "done"]
    assert len(done) == 1 and done[0]["stopped_for_shutdown"] is True, frames
    assert done[0].get("turn_id") == "turn-1"
    assert done[0]["notice"]


@pytest.mark.asyncio
async def test_a_paused_turn_ends_quietly_for_the_page_to_come_back():
    frames = await _attach_then_stop(producer_done=False, paused=True)
    assert not [d for e, d in frames if e == "done"], frames


@pytest.mark.asyncio
async def test_negative_control_the_old_tail_dropped_the_done(monkeypatch):
    async def old_end(queue, thread_id, last_yielded, turn_id=""):  # the early return
        return
        yield  # pragma: no cover

    monkeypatch.setattr(streaming, "_end_attach_for_shutdown", old_end)
    frames = await _attach_then_stop(producer_done=True)
    assert not [d for e, d in frames if e == "done"], frames
