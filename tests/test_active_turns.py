"""Unit tests for the shared active-turn registry (kazma_ui.active_turns).

The registry is the single source of truth for "is a turn still running on
thread X?" shared by the SSE and WebSocket chat transports, plus the orphan
stamp used to reap abandoned turns (credit savings) without ever punishing a
quick refresh / tab switch.
"""

from __future__ import annotations

import pytest
from kazma_ui import active_turns as at


class _FakeTask:
    """Minimal stand-in for an asyncio.Task: only ``done()`` matters here."""

    def __init__(self, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest.fixture(autouse=True)
def _clean_registry():
    """Registry is process-global — always start and finish clean."""
    at.unregister_turn("", None)  # no-op safety
    with at._lock:
        at._turns.clear()
        at._orphaned_at.clear()
        at._live_sockets.clear()
    yield
    with at._lock:
        at._turns.clear()
        at._orphaned_at.clear()
        at._live_sockets.clear()


def test_register_and_conditional_unregister():
    a = _FakeTask()
    b = _FakeTask()
    at.register_turn("t1", a)
    assert at.get_active_turn("t1") is a
    assert at.is_turn_running("t1")
    # A superseded task's callback must NOT unregister its replacement.
    at.register_turn("t1", b)
    at.unregister_turn("t1", a)
    assert at.get_active_turn("t1") is b
    # The registered task can unregister itself.
    at.unregister_turn("t1", b)
    assert at.get_active_turn("t1") is None
    assert not at.is_turn_running("t1")


def test_is_turn_running_false_for_finished_task():
    at.register_turn("t1", _FakeTask(done=True))
    assert not at.is_turn_running("t1")


def test_empty_thread_id_is_ignored():
    at.register_turn("", _FakeTask())
    assert at.get_active_turn("") is None
    assert at.is_turn_running("") is False
    assert at.mark_turn_orphaned("") is None
    assert at.reap_stale_turn("") is None


def test_mark_turn_orphaned_is_idempotent_and_first_wins():
    task = _FakeTask()
    at.register_turn("t1", task)
    at.mark_turn_orphaned("t1")
    stamp = at._orphaned_at["t1"]
    at.mark_turn_orphaned("t1")
    assert at._orphaned_at["t1"] == stamp


def test_mark_turn_orphaned_skips_finished_turn():
    at.register_turn("t1", _FakeTask(done=True))
    at.mark_turn_orphaned("t1")
    assert "t1" not in at._orphaned_at


def test_reap_returns_none_without_orphan_stamp():
    at.register_turn("t1", _FakeTask())
    assert at.reap_stale_turn("t1", ttl_s=0) is None
    assert at.get_active_turn("t1") is not None


def test_reap_returns_none_for_fresh_orphan():
    at.register_turn("t1", _FakeTask())
    at.mark_turn_orphaned("t1")
    assert at.reap_stale_turn("t1", ttl_s=3600) is None
    assert at.get_active_turn("t1") is not None


def test_reap_returns_task_after_ttl_and_is_atomic():
    task = _FakeTask()
    at.register_turn("t1", task)
    at.mark_turn_orphaned("t1")
    # Force the stamp into the past.
    at._orphaned_at["t1"] -= 3600.0

    assert at.reap_stale_turn("t1", ttl_s=60) is task
    # Atomic pop: the second caller sees nothing left to reap.
    assert at.reap_stale_turn("t1", ttl_s=0) is None
    assert at.get_active_turn("t1") is None
    assert "t1" not in at._orphaned_at


def test_unregister_clears_orphan_stamp():
    at.register_turn("t1", _FakeTask())
    at.mark_turn_orphaned("t1")
    at.unregister_turn("t1")
    assert "t1" not in at._orphaned_at


def test_register_clears_previous_orphan_stamp():
    old = _FakeTask()
    at.register_turn("t1", old)
    at.mark_turn_orphaned("t1")
    at.register_turn("t1", _FakeTask())  # replacement resets the clock
    assert "t1" not in at._orphaned_at


def test_default_ttl_is_sane():
    assert at.DETACHED_TTL_S == 300


def test_live_socket_rebind_and_conditional_unbind():
    """Tab-switch reconnect rebinds delivery; old disconnect must not wipe it."""
    sock_a = object()
    sock_b = object()
    at.register_turn("t1", _FakeTask())
    at.mark_turn_orphaned("t1")
    assert at.get_orphan_stamp("t1") is not None

    at.bind_live_socket("t1", sock_a)
    assert at.get_live_socket("t1") is sock_a
    # Re-attach clears orphan TTL so the turn is not reaped while watched.
    assert at.get_orphan_stamp("t1") is None

    at.bind_live_socket("t1", sock_b)
    assert at.get_live_socket("t1") is sock_b

    # Stale disconnect of sock_a must not clear sock_b.
    at.unbind_live_socket("t1", sock_a)
    assert at.get_live_socket("t1") is sock_b

    at.unbind_live_socket("t1", sock_b)
    assert at.get_live_socket("t1") is None


def test_live_socket_empty_thread_ignored():
    at.bind_live_socket("", object())
    assert at.get_live_socket("") is None
    at.unbind_live_socket("", object())


# ── the detached-pump watchdog decision ───────────────────────────────
#
# 367 disconnects, zero engagements. That zero only reassures if the
# decision can be shown to work -- the repetition breaker had a comparable
# zero and turned out to have been incapable of firing for its whole life.


def test_a_connected_client_is_never_reaped():
    """No orphan stamp means the client is still there. A turn someone is
    watching must never be cancelled, however long it runs."""
    from kazma_ui.active_turns import pump_is_stalled, register_turn

    task = _FakeTask()
    register_turn("t-connected", task)
    assert pump_is_stalled("t-connected", 0.0) is False


def test_a_gone_client_with_a_progressing_stream_is_left_alone():
    """The reason this is progress-based and not a plain disconnect timer:
    a background turn still emitting events is doing exactly what the
    detached pump exists to allow. Reaping it would break the feature."""
    import time as _t

    from kazma_ui.active_turns import mark_turn_orphaned, pump_is_stalled, register_turn

    task = _FakeTask()
    register_turn("t-progress", task)
    mark_turn_orphaned("t-progress")
    now = _t.monotonic()
    assert pump_is_stalled("t-progress", now, ttl_s=300.0, now=now + 299) is False


def test_a_gone_client_with_a_stalled_stream_is_reaped():
    """The astream_events hang: client gone, nothing streamed, thread held
    hostage. This is the case the watchdog exists for."""
    import time as _t

    from kazma_ui.active_turns import mark_turn_orphaned, pump_is_stalled, register_turn

    task = _FakeTask()
    register_turn("t-stalled", task)
    mark_turn_orphaned("t-stalled")
    now = _t.monotonic()
    assert pump_is_stalled("t-stalled", now, ttl_s=300.0, now=now + 301) is True


def test_the_deadline_is_measured_from_the_later_of_disconnect_and_progress():
    """A turn that streamed AFTER the disconnect resets the clock; using
    the disconnect alone would reap turns that are plainly still working."""
    import time as _t

    from kazma_ui.active_turns import mark_turn_orphaned, pump_is_stalled, register_turn

    task = _FakeTask()
    register_turn("t-later", task)
    mark_turn_orphaned("t-later")
    now = _t.monotonic()
    # Disconnected long ago, but an event arrived one second ago.
    assert pump_is_stalled("t-later", now + 400, ttl_s=300.0, now=now + 401) is False


def test_the_sse_watchdog_uses_this_predicate():
    """Extracting the decision only helps if the live path calls it."""
    import inspect

    from kazma_ui import sse_chat

    src = inspect.getsource(sse_chat)
    assert "pump_is_stalled(thread_id" in src
