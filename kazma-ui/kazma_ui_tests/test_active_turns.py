"""Unit tests for the shared turn-lifecycle registry (active_turns).

Covers the Phase 2 additions: ``cancel_turn`` (Stop), ``get_orphan_stamp``
(watchdog accessor), and the atomic reap/cancel semantics both transports
rely on to never interleave two graph runs on one thread.
"""

import asyncio

import pytest

from kazma_ui import active_turns as at


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts from an empty shared registry."""
    with at._lock:
        at._turns.clear()
        at._orphaned_at.clear()
        at._live_sockets.clear()
    yield
    with at._lock:
        at._turns.clear()
        at._orphaned_at.clear()
        at._live_sockets.clear()


class TestRegisterAndOrphan:
    """Registration + orphan stamping semantics."""

    @pytest.mark.asyncio
    async def test_register_unregister_round_trip(self):
        task = asyncio.create_task(asyncio.sleep(0.1))
        at.register_turn("t1", task)
        assert at.get_active_turn("t1") is task
        at.unregister_turn("t1", task)
        assert at.get_active_turn("t1") is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_unregister_superseded_turn_keeps_replacement(self):
        """A stale done-callback must not unregister its replacement."""
        old = asyncio.create_task(asyncio.sleep(0.2))
        new = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", old)
        at.register_turn("t1", new)
        at.unregister_turn("t1", old)
        assert at.get_active_turn("t1") is new
        for t in (old, new):
            t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await new

    @pytest.mark.asyncio
    async def test_mark_orphan_is_idempotent(self):
        task = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", task)
        at.mark_turn_orphaned("t1")
        first = at.get_orphan_stamp("t1")
        assert first is not None
        at.mark_turn_orphaned("t1")
        assert at.get_orphan_stamp("t1") == first
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_orphan_stamp_cleared_by_reregister(self):
        """A reconnect (register_turn) clears the disconnect stamp."""
        task = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", task)
        at.mark_turn_orphaned("t1")
        assert at.get_orphan_stamp("t1") is not None
        fresh = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", fresh)
        assert at.get_orphan_stamp("t1") is None
        for t in (task, fresh):
            t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fresh

    @pytest.mark.asyncio
    async def test_orphan_stamp_none_when_turn_finished(self):
        task = asyncio.create_task(asyncio.sleep(0.01))
        at.register_turn("t1", task)
        await task
        assert at.get_orphan_stamp("t1") is None
        assert not at.is_turn_running("t1")


class TestReapStaleTurn:
    """Atomic reap of abandoned turns."""

    @pytest.mark.asyncio
    async def test_reap_returns_none_until_ttl(self):
        task = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", task)
        at.mark_turn_orphaned("t1")
        assert at.reap_stale_turn("t1", ttl_s=3600) is None
        assert at.get_active_turn("t1") is task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_reap_returns_task_after_ttl(self):
        task = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", task)
        at.mark_turn_orphaned("t1")
        reaped = at.reap_stale_turn("t1", ttl_s=0)
        assert reaped is task
        assert at.get_active_turn("t1") is None
        assert at.get_orphan_stamp("t1") is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_reap_returns_none_when_not_orphaned(self):
        task = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", task)
        assert at.reap_stale_turn("t1", ttl_s=0) is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestCancelTurn:
    """Stop-button semantics."""

    @pytest.mark.asyncio
    async def test_cancel_unregisters_and_returns_task(self):
        task = asyncio.create_task(asyncio.sleep(60))
        at.register_turn("t1", task)
        result = at.cancel_turn("t1")
        assert result is task
        assert at.get_active_turn("t1") is None
        assert at.get_orphan_stamp("t1") is None
        # cancel_turn is fire-and-forget — the caller awaits to completion.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    def test_cancel_noop_without_turn(self):
        assert at.cancel_turn("t1") is None
        assert at.cancel_turn("") is None

    @pytest.mark.asyncio
    async def test_cancel_then_unregister_callback_noop(self):
        """The done_callback of a cancelled turn must not unregister a new one."""
        task = asyncio.create_task(asyncio.sleep(60))
        at.register_turn("t1", task)
        at.cancel_turn("t1")
        replacement = asyncio.create_task(asyncio.sleep(0.2))
        at.register_turn("t1", replacement)
        at.unregister_turn("t1", task)  # late callback from the cancelled turn
        assert at.get_active_turn("t1") is replacement
        replacement.cancel()
        with pytest.raises(asyncio.CancelledError):
            await replacement


class TestLiveSocketRebind:
    """WS tab-switch rebind: delivery follows the newest socket."""

    def test_bind_clears_orphan_and_unbind_is_conditional(self):
        sock_a = object()
        sock_b = object()
        task = object()
        # Fake done() for mark_turn_orphaned
        class T:
            def done(self):
                return False
        at.register_turn("t1", T())
        at.mark_turn_orphaned("t1")
        assert at.get_orphan_stamp("t1") is not None

        at.bind_live_socket("t1", sock_a)
        assert at.get_live_socket("t1") is sock_a
        assert at.get_orphan_stamp("t1") is None

        at.bind_live_socket("t1", sock_b)
        at.unbind_live_socket("t1", sock_a)  # stale close
        assert at.get_live_socket("t1") is sock_b
        at.unbind_live_socket("t1", sock_b)
        assert at.get_live_socket("t1") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
