"""Turn Delivery V2 — SSE transport integration tests (plan P2).

Covers the sse_chat.py wiring of docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md:
journaled turn frames carrying SSE ``id:`` lines, cursor attach
(``last_event_id``) with subscribe-first/no-gap ordering, replay-window
dedupe by seq, retention-gap signalling, and legacy behavior preservation.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

import kazma_ui.sse_chat as sse_chat_mod
from kazma_ui.delivery import TurnBroker, TurnJournal, get_turn_broker, reset_turn_broker
from kazma_ui.sse_chat import (
    _frame_from_journaled,
    _sse_attach_stream,
    create_sse_chat_router,
)
from kazma_ui.session_manager import get_session_manager, reset_session_manager
from kazma_ui.sse_utils import sse_frame


@pytest.fixture(autouse=True)
def _fresh_broker_and_sessions():
    reset_turn_broker()
    reset_session_manager()
    yield
    reset_turn_broker()
    reset_session_manager()


_ID_LINE = re.compile(r"^id: (\d+)$", re.MULTILINE)


def _ids(frame_text: str) -> list[int]:
    return [int(m) for m in _ID_LINE.findall(frame_text)]


async def _collect(agen, timeout: float = 8.0) -> list[str]:
    """Drain an async generator to completion under a watchdog."""
    frames: list[str] = []

    async def _run() -> None:
        async for item in agen:
            frames.append(item)

    await asyncio.wait_for(_run(), timeout=timeout)
    return frames


# ── Frame formatting ──────────────────────────────────────────────────────


class TestFrameFormatting:
    def test_plain_frames_unchanged(self):
        out = sse_frame("token", {"content": "hi"})
        assert out == 'event: token\ndata: {"content": "hi"}\n\n'

    def test_id_line_prepended_when_given(self):
        out = sse_frame("token", {"content": "hi"}, id=7)
        assert out.startswith("id: 7\nevent: token\ndata: ")
        assert out.endswith("\n\n")
        assert _ids(out) == [7]

    def test_frame_from_journaled_stamps_seq(self):
        out = _frame_from_journaled(
            {"type": "llm_delta", "data": {"content": "x"}, "seq": 12}
        )
        assert _ids(out) == [12]
        assert '"seq": 12' in out
        assert out.startswith("id: 12\nevent: llm_delta\ndata: ")


# ── Attach stream behaviour ───────────────────────────────────────────────


class TestAttachStream:
    @pytest.mark.asyncio
    async def test_replay_then_live_until_terminal_no_duplicates(self):
        broker = get_turn_broker()
        # Two events land while the client is "disconnected"; the turn is
        # still RUNNING server-side (detached pump) — register it so attach
        # sees a live turn to reattach to.
        await broker.emit("tA", {"type": "llm_delta", "data": {"content": "one"}})
        await broker.emit("tA", {"type": "llm_delta", "data": {"content": "two"}})
        fake_turn = asyncio.create_task(asyncio.sleep(3600))
        from kazma_ui.active_turns import register_turn, unregister_turn

        register_turn("tA", fake_turn)
        try:
            gen = _sse_attach_stream("tA", "sess-A", 0)
            collector = asyncio.create_task(_collect(gen))
            # Let the generator subscribe + start replaying.
            await asyncio.sleep(0.08)
            # Live continuation — possibly DURING the replay window.
            await broker.emit("tA", {"type": "llm_delta", "data": {"content": "live"}})
            await broker.emit("tA", {"type": "turn_complete", "data": {"content": "live"}})

            frames = await collector
        finally:
            fake_turn.cancel()
            try:
                await fake_turn
            except asyncio.CancelledError:
                pass
            unregister_turn("tA", fake_turn)

        joined = "\n".join(frames)

        # Handshake first, correct shape — and it must report the live turn.
        assert frames[0].startswith("event: resumed\ndata: ")
        assert '"running": true' in frames[0]

        # Every journaled event appears EXACTLY once, in seq order.
        all_ids: list[int] = []
        for f in frames[1:]:
            all_ids.extend(_ids(f))
        assert all_ids == sorted(all_ids)
        assert len(all_ids) == len(set(all_ids)) == 4

        # Contents survived the disconnect-reconnect window losslessly.
        assert '"one"' in joined and '"two"' in joined and '"live"' in joined
        # Terminal closed the stream.
        assert frames[-1].startswith("id: 4\nevent: turn_complete")

    @pytest.mark.asyncio
    async def test_cursor_midstream_replays_only_missed_window(self):
        broker = get_turn_broker()
        for i in range(5):
            await broker.emit("tB", {"type": "llm_delta", "data": {"i": i}})
        frames = await _collect(_sse_attach_stream("tB", "sess-B", 3))
        assert frames[0].startswith("event: resumed")
        body_ids = []
        for f in frames[1:]:
            body_ids.extend(_ids(f))
        assert body_ids == [4, 5]

    @pytest.mark.asyncio
    async def test_caught_up_and_idle_closes_immediately(self):
        broker = get_turn_broker()
        await broker.emit("tC", {"type": "llm_delta"})
        frames = await _collect(
            _sse_attach_stream("tC", "sess-C", 1), timeout=5.0
        )
        resumed = frames[0]
        assert '"count": 0' in resumed and '"running": false' in resumed
        # Nothing else — no live turn to attach to.
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_gap_signals_resync_never_partial_history(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        small = TurnBroker(journal=TurnJournal(max_events_per_thread=2))
        for _ in range(8):
            await small.emit("tGap", {"type": "llm_delta"})
        monkeypatch.setattr(sse_chat_mod, "get_turn_broker", lambda: small)

        frames = await _collect(_sse_attach_stream("tGap", "sess-G", 1), timeout=5.0)
        joined = "\n".join(frames)
        assert '"gap": true' in joined
        assert '"status": "resync"' in joined
        # No journaled event frames may follow a gap signal.
        assert not any(f.startswith("event: llm_delta") for f in frames)


# ── Endpoint integration (POST /api/chat/stream with cursor) ─────────────


def _mk_client() -> TestClient:
    app = __import__("fastapi").FastAPI()
    app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
    return TestClient(app)


def test_endpoint_attach_without_message_replays_journal():
    mgr = get_session_manager()
    sess = mgr.get_or_create("v2-sse-session")
    sess.thread_id = "v2-sse-thread"
    mgr.put(sess)
    broker = get_turn_broker()
    for i in range(3):
        asyncio.run(broker.emit("v2-sse-thread", {"type": "llm_delta", "data": {"c": i}}))

    client = _mk_client()
    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "v2-sse-session", "last_event_id": 1},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: resumed" in body
    ids = _ids(body.split("event: resumed", 1)[1])
    assert ids == [2, 3]


def test_endpoint_empty_message_still_rejected_for_fresh_sends():
    client = _mk_client()
    resp = client.post("/api/chat/stream", json={"session_id": "whatever"})
    assert resp.status_code == 200
    assert "Empty message" in resp.text


def test_endpoint_bad_cursor_treated_as_absent():
    """An unparseable cursor must NOT silently attach — fresh-send rules apply."""
    client = _mk_client()
    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "bad-cursor", "last_event_id": "not-a-number"},
    )
    assert "Empty message" in resp.text
