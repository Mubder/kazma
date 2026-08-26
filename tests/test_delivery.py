"""Turn Delivery V2 — journal + broker unit tests.

Covers the P0 foundation of docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md:
seq monotonicity, bounded retention + gap semantics, multi-socket fan-out,
per-recipient failure isolation, and per-thread emit ordering.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_ui.delivery import (
    REPLAY_SKIP_TYPES,
    TurnBroker,
    TurnJournal,
    get_turn_broker,
    is_replayable,
    reset_turn_broker,
)


class _FakeSocket:
    """Duck-typed Starlette WebSocket stand-in collecting sent frames."""

    def __init__(self, *, fail: bool = False) -> None:
        self.frames: list[dict[str, Any]] = []
        self._fail = fail

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self._fail:
            raise RuntimeError("socket dead")
        self.frames.append(payload)


# ── TurnJournal: seq + stamping ──────────────────────────────────────────


class TestTurnJournalSeq:
    def test_append_assigns_monotonic_seq(self) -> None:
        journal = TurnJournal()
        seqs = [journal.append("t1", {"type": "token"})["seq"] for _ in range(5)]
        assert seqs == [1, 2, 3, 4, 5]
        assert journal.head_seq("t1") == 5

    def test_seq_is_per_thread(self) -> None:
        journal = TurnJournal()
        assert journal.append("a", {"type": "x"})["seq"] == 1
        assert journal.append("b", {"type": "x"})["seq"] == 1
        assert journal.append("a", {"type": "x"})["seq"] == 2

    def test_append_returns_stamped_copy_identical_to_stored(self) -> None:
        journal = TurnJournal()
        stamped = journal.append("t1", {"type": "token", "data": {"i": 1}})
        assert stamped["seq"] == 1
        frames, gap = journal.replay("t1", 0)
        assert gap is False
        # The stored frame IS the stamped frame — what was journaled is
        # byte-identical to what gets broadcast live.
        assert frames[0] is stamped

    def test_input_frame_not_mutated(self) -> None:
        journal = TurnJournal()
        frame = {"type": "token", "data": {"content": "hi"}}
        journal.append("t1", frame)
        assert "seq" not in frame

    def test_empty_thread_id_raises(self) -> None:
        journal = TurnJournal()
        with pytest.raises(ValueError):
            journal.append("", {"type": "x"})


# ── TurnJournal: replay + gap semantics ─────────────────────────────────


class TestReplay:
    def test_replay_contiguous_range(self) -> None:
        journal = TurnJournal()
        for i in range(5):
            journal.append("t1", {"type": "token", "data": {"i": i}})
        frames, gap = journal.replay("t1", 2)
        assert gap is False
        assert [f["seq"] for f in frames] == [3, 4, 5]

    def test_replay_from_zero_returns_all(self) -> None:
        journal = TurnJournal()
        for _ in range(3):
            journal.append("t1", {"type": "status_update"})
        frames, gap = journal.replay("t1", 0)
        assert gap is False
        assert [f["seq"] for f in frames] == [1, 2, 3]

    def test_replay_caught_up_returns_empty_no_gap(self) -> None:
        journal = TurnJournal()
        for _ in range(3):
            journal.append("t1", {"type": "status_update"})
        frames, gap = journal.replay("t1", 3)
        assert frames == []
        assert gap is False

    def test_unknown_thread_no_gap(self) -> None:
        journal = TurnJournal()
        frames, gap = journal.replay("never-seen", 0)
        assert frames == []
        assert gap is False

    def test_cursor_predates_retention_reports_gap(self) -> None:
        journal = TurnJournal(max_events_per_thread=3)
        for i in range(10):  # retains seqs 8,9,10
            journal.append("t1", {"type": "token", "data": {"i": i}})
        frames, gap = journal.replay("t1", 4)
        assert frames == []
        assert gap is True

    def test_fresh_client_on_pruned_journal_gets_gap(self) -> None:
        # A brand-new client (cursor 0) on a journal that already pruned its
        # first entries MUST get gap=True — silent partial replay would be
        # exactly the bug class this plan removes.
        journal = TurnJournal(max_events_per_thread=2)
        for _ in range(5):  # retains seqs 4,5
            journal.append("t1", {"type": "token"})
        frames, gap = journal.replay("t1", 0)
        assert frames == []
        assert gap is True

    def test_unpruned_full_history_no_gap(self) -> None:
        journal = TurnJournal(max_events_per_thread=100)
        for _ in range(5):
            journal.append("t1", {"type": "token"})
        frames, gap = journal.replay("t1", 0)
        assert gap is False
        assert len(frames) == 5


# ── TurnJournal: retention bounds ────────────────────────────────────────


class TestRetention:
    def test_per_thread_cap_evicts_oldest(self) -> None:
        journal = TurnJournal(max_events_per_thread=4)
        for i in range(10):
            journal.append("t1", {"type": "token", "data": {"i": i}})
        frames, gap = journal.replay("t1", 0)
        assert gap is True  # 1..6 evicted
        # But direct head inspection shows only the retained tail remains.
        assert journal.head_seq("t1") == 10

    def test_ttl_prunes_idle_threads(self) -> None:
        journal = TurnJournal(ttl_seconds=0.05)
        journal.append("old", {"type": "token"})
        import time as _time

        _time.sleep(0.08)
        # Appending to ANOTHER thread triggers lazy prune of 'old'.
        journal.append("new", {"type": "token"})
        assert journal.head_seq("old") == 0
        assert journal.head_seq("new") == 1

    def test_max_threads_lru_eviction(self) -> None:
        journal = TurnJournal(max_threads=2, ttl_seconds=0)
        journal.append("a", {"type": "token"})
        import time as _time

        _time.sleep(0.01)
        journal.append("b", {"type": "token"})
        _time.sleep(0.01)
        # 'a' is least recently active — adding 'c' must evict it.
        journal.append("c", {"type": "token"})
        assert journal.head_seq("a") == 0
        assert journal.head_seq("b") == 1
        assert journal.head_seq("c") == 1

    def test_stats_shape(self) -> None:
        journal = TurnJournal()
        journal.append("t1", {"type": "token"})
        stats = journal.stats()
        assert stats["threads"] == 1
        assert stats["total_events"] == 1


# ── TurnBroker: fan-out ──────────────────────────────────────────────────


class TestFanOut:
    @pytest.mark.asyncio
    async def test_emit_delivers_to_bound_socket_with_seq(self) -> None:
        broker = TurnBroker()
        sock = _FakeSocket()
        conn_id = broker.register_socket("t1", sock)
        try:
            seq = await broker.emit("t1", {"type": "token", "data": {"content": "hi"}})
            assert seq["seq"] == 1
            assert len(sock.frames) == 1
            assert sock.frames[0]["seq"] == 1
            assert sock.frames[0]["data"]["content"] == "hi"
        finally:
            broker.unregister_socket("t1", conn_id)

    @pytest.mark.asyncio
    async def test_multi_tab_both_sockets_receive(self) -> None:
        # The single-slot bind_live_socket defect: N tabs on one session.
        broker = TurnBroker()
        s1, s2 = _FakeSocket(), _FakeSocket()
        c1 = broker.register_socket("t1", s1)
        c2 = broker.register_socket("t1", s2)
        try:
            await broker.emit("t1", {"type": "status_update"})
            await broker.emit("t1", {"type": "token"})
            assert len(s1.frames) == 2
            assert len(s2.frames) == 2
            assert [f["seq"] for f in s2.frames] == [1, 2]
        finally:
            broker.unregister_socket("t1", c1)
            broker.unregister_socket("t1", c2)

    @pytest.mark.asyncio
    async def test_dead_socket_isolated_others_still_receive(self) -> None:
        broker = TurnBroker()
        dead, live = _FakeSocket(fail=True), _FakeSocket()
        cd = broker.register_socket("t1", dead)
        cl = broker.register_socket("t1", live)
        try:
            seq = await broker.emit("t1", {"type": "token"})
            assert seq["seq"] == 1  # emit never raises on recipient failure
            assert live.frames and live.frames[0]["seq"] == 1
        finally:
            broker.unregister_socket("t1", cd)
            broker.unregister_socket("t1", cl)

    @pytest.mark.asyncio
    async def test_emit_without_sockets_still_journals(self) -> None:
        broker = TurnBroker()
        seq = await broker.emit("t1", {"type": "token"})
        assert seq["seq"] == 1
        frames, gap, _head = broker.resume("t1", 0)
        assert gap is False
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_emit_accepts_telemetry_event(self) -> None:
        from kazma_core.tracing.events import TelemetryEvent

        broker = TurnBroker()
        seq = await broker.emit(
            "t1", TelemetryEvent(type="status_update", data={"status": "thinking"}, thread_id="t1")
        )
        assert seq["seq"] == 1
        frames, _gap, _head = broker.resume("t1", 0)
        assert frames[0]["type"] == "status_update"
        assert frames[0]["data"]["status"] == "thinking"

    @pytest.mark.asyncio
    async def test_concurrent_emits_arrive_in_seq_order(self) -> None:
        # Per-thread serialization: even when coroutines race, sockets must
        # observe frames in canonical journal order.
        broker = TurnBroker()
        sock = _FakeSocket()
        conn_id = broker.register_socket("t1", sock)

        async def burst() -> None:
            emits = [broker.emit("t1", {"type": "token", "data": {"i": i}}) for i in range(50)]
            await asyncio.gather(*emits)

        try:
            await asyncio.gather(*(burst() for _ in range(4)))
            seqs = [f["seq"] for f in sock.frames]
            assert seqs == sorted(seqs)
            assert len(seqs) == 200
        finally:
            broker.unregister_socket("t1", conn_id)


# ── TurnBroker: resume handshake data ────────────────────────────────────


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_replays_then_live_continues(self) -> None:
        broker = TurnBroker()
        for i in range(3):
            await broker.emit("t1", {"type": "token", "data": {"i": i}})

        # Client disconnects, misses nothing yet, reconnects with cursor 2.
        frames, gap, head = broker.resume("t1", 2)
        assert gap is False
        assert [f["seq"] for f in frames] == [3]
        assert head == 3

        # Live continues from head on the rebound socket.
        sock = _FakeSocket()
        conn_id = broker.register_socket("t1", sock)
        try:
            await broker.emit("t1", {"type": "token"})
            assert sock.frames[0]["seq"] == 4
        finally:
            broker.unregister_socket("t1", conn_id)

    @pytest.mark.asyncio
    async def test_resume_gap_signals_snapshot_fallback(self) -> None:
        broker = TurnBroker(journal=TurnJournal(max_events_per_thread=2))
        for _ in range(8):
            await broker.emit("t1", {"type": "token"})
        frames, gap, head = broker.resume("t1", 1)
        assert frames == []
        assert gap is True
        assert head == 8


# ── Subscriber queues (SSE attach groundwork) ────────────────────────────


class TestSubscribers:
    @pytest.mark.asyncio
    async def test_subscriber_receives_live_frames(self) -> None:
        broker = TurnBroker()
        queue = broker.subscribe("t1")
        await broker.emit("t1", {"type": "token"})
        frame = queue.get_nowait()
        assert frame["seq"] == 1
        broker.unsubscribe("t1", queue)
        # Sanity: resume API agrees with the live seq the subscriber saw.
        _frames, gap, head = broker.resume("t1", 0)
        assert gap is False and head == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self) -> None:
        broker = TurnBroker()
        queue = broker.subscribe("t1")
        broker.unsubscribe("t1", queue)
        await broker.emit("t1", {"type": "token"})
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_full_queue_drops_never_raises(self) -> None:
        broker = TurnBroker()
        small: asyncio.Queue = asyncio.Queue(maxsize=1)
        # Bypass subscribe() to force a tiny bound directly.
        with broker._lock:
            broker._subscribers.setdefault("t1", []).append(small)
        small.put_nowait({"pre": True})
        seq = await broker.emit("t1", {"type": "token"})  # must not raise
        assert seq["seq"] == 1


# ── Replay filter (command confirmations are not resumable) ─────────────


class TestReplayFilter:
    def test_capacity_and_steer_never_replayable(self):
        assert not is_replayable({"type": "capacity", "data": {"reply": "x"}})
        assert not is_replayable({"type": "steer", "data": {}})
        # capacity flag in data (fast-path stream_end) also excluded.
        assert not is_replayable({"type": "stream_end", "data": {"capacity": True}})

    def test_error_frames_never_replayable(self):
        """2026-08-26: replaying a journaled error re-triggered the client's
        onError → attach → replayed-error retry storm. The done frame that
        follows an error carries the durable outcome."""
        assert not is_replayable({"type": "error", "data": {"message": "boom"}})

    def test_turn_content_always_replayable(self):
        assert is_replayable({"type": "llm_delta", "data": {"content": "hi"}})
        assert is_replayable({"type": "turn_complete", "data": {"content": "done"}})
        assert is_replayable({"type": "approval_required", "data": {"tool": "shell_exec"}})

    @pytest.mark.asyncio
    async def test_resume_skips_command_confirmations(self):
        broker = TurnBroker()
        await broker.emit("tR", {"type": "token", "data": {"i": 1}})
        await broker.emit("tR", {"type": "capacity", "data": {"action": "yolo", "reply": "ON"}})
        await broker.emit("tR", {"type": "stream_end", "data": {"capacity": True}})
        await broker.emit("tR", {"type": "turn_complete", "data": {"content": "ok"}})
        frames, gap, head = broker.resume("tR", 0)
        assert gap is False and head == 4
        served = [f["seq"] for f in frames if is_replayable(f)]
        assert served == [1, 4]  # 2,3 skipped — transcript-persisted already


# ── Singleton ─────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_turn_broker_is_process_wide(self) -> None:
        reset_turn_broker()
        try:
            b1 = get_turn_broker()
            b2 = get_turn_broker()
            assert b1 is b2
        finally:
            reset_turn_broker()

    def test_reset_creates_fresh_instance(self) -> None:
        reset_turn_broker()
        try:
            b1 = get_turn_broker()
            reset_turn_broker()
            b2 = get_turn_broker()
            assert b1 is not b2
        finally:
            reset_turn_broker()


# ── Broker introspection ─────────────────────────────────────────────────


class TestStats:
    @pytest.mark.asyncio
    async def test_broker_stats_counts(self) -> None:
        broker = TurnBroker()
        c1 = broker.register_socket("t1", _FakeSocket())
        c2 = broker.register_socket("t1", _FakeSocket())
        q = broker.subscribe("t1")
        try:
            await broker.emit("t1", {"type": "token"})
            stats = broker.stats()
            assert stats["threads_with_sockets"] == 1
            assert stats["open_sockets"] == 2
            assert stats["subscriber_queues"] == 1
            assert stats["journal"]["total_events"] == 1
        finally:
            broker.unregister_socket("t1", c1)
            broker.unregister_socket("t1", c2)
            broker.unsubscribe("t1", q)
