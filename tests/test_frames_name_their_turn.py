"""Every journaled chat frame names the turn it belongs to.

KNOWN_GAPS, "Most chat frames do not say which turn they belong to": token,
tool, status and heartbeat frames were journaled without a turn id -- three
frames of sixty in a measured replay had one -- so every client filed them
under "the current turn", and correctness hung on knowing where one turn ends.
That guess went wrong twice on 2026-09-26 (a watching tab; a catch-up attach
across a turn boundary). The broker's choke point now stamps ``data.turn_id``
(delivery._with_turn_id), and the client adopts the id mid-turn
(chat.js applyTurnEvent; the browser suites under tests/e2e prove it).
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.observability.correlation import bind_turn_id, reset_turn_id


@pytest.fixture
def broker():
    from kazma_ui.delivery import get_turn_broker, reset_turn_broker

    reset_turn_broker()
    yield get_turn_broker()
    reset_turn_broker()


@pytest.fixture
def open_turn():
    from kazma_ui import reply_sink

    opened: list[str] = []

    def _open(thread_id: str, turn_id: str) -> str:
        opened.append(thread_id)
        return reply_sink.open_reply_turn(thread_id, turn_id)

    yield _open
    for thread_id in opened:
        reply_sink.close_reply_turn(thread_id)


_TURN_FRAMES = [
    {"type": "token", "data": {"content": "Hel"}},
    {"type": "tool_call", "data": {"tool_name": "file_read", "tool_call_id": "c1"}},
    {"type": "tool_result", "data": {"tool_name": "file_read", "tool_call_id": "c1"}},
    {"type": "turn_heartbeat", "data": {"elapsed_s": 3}},
    {"type": "status_update", "data": {"status": "thinking"}},
    {"type": "token", "data": {"content": "lo"}},
]


def _replayed(broker, thread_id: str) -> list[dict]:
    frames, gap, _head = broker.resume(thread_id, 0)
    assert not gap
    return frames


def test_every_frame_of_a_turn_names_it(broker, open_turn) -> None:
    open_turn("th-1", "turn-A")

    async def run() -> None:
        for frame in _TURN_FRAMES:
            await broker.emit("th-1", frame)

    asyncio.run(run())
    frames = _replayed(broker, "th-1")
    assert [f["data"].get("turn_id") for f in frames] == ["turn-A"] * len(_TURN_FRAMES), frames


def test_a_superseded_turn_keeps_its_own_id(broker, open_turn) -> None:
    """A turn that was replaced while its task still runs: its late frames are
    named by the task's own bound turn, not the thread's new one."""
    open_turn("th-2", "turn-new")

    async def late_frame_of_the_old_turn() -> dict:
        token = bind_turn_id("turn-old")
        try:
            return await broker.emit("th-2", {"type": "token", "data": {"content": "late"}})
        finally:
            reset_turn_id(token)

    stamped = asyncio.run(late_frame_of_the_old_turn())
    assert stamped["data"]["turn_id"] == "turn-old"


def test_a_frame_that_names_its_turn_keeps_it(broker, open_turn) -> None:
    open_turn("th-3", "turn-A")

    async def run() -> tuple[dict, dict]:
        a = await broker.emit("th-3", {"type": "hitl", "data": {"turn_id": "turn-Z", "state": "pending"}})
        b = await broker.emit("th-3", {"type": "done", "turn_id": "turn-Y", "data": {"content": "x"}})
        return a, b

    a, b = asyncio.run(run())
    assert a["data"]["turn_id"] == "turn-Z"
    assert "turn_id" not in b["data"] and b["turn_id"] == "turn-Y"


def test_between_turns_a_frame_stays_unnamed(broker) -> None:
    stamped = asyncio.run(broker.emit("th-4", {"type": "turn_heartbeat", "data": {}}))
    assert "turn_id" not in stamped["data"]


def test_negative_control_without_the_stamp_frames_are_unnamed(broker, open_turn, monkeypatch) -> None:
    import kazma_ui.delivery as delivery

    monkeypatch.setattr(delivery, "_with_turn_id", lambda thread_id, frame: frame)
    open_turn("th-5", "turn-A")
    stamped = asyncio.run(broker.emit("th-5", {"type": "token", "data": {"content": "x"}}))
    assert "turn_id" not in stamped["data"]
