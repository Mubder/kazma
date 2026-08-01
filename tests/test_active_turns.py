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
    yield
    with at._lock:
        at._turns.clear()
        at._orphaned_at.clear()


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
