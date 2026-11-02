"""Ten readers of the chat session list do not queue behind each other.

2026-11 functional audit, FA-278. Every ``GET /api/chat/sessions`` re-read
every session from the database (messages and all), pruned empty and twin
sessions, and filtered the cache under the store lock: on the live install
0.27 s alone, 1.3 s p50 at ten pages polling, 8.3 s at sixty. Now a tenant's
list re-reads and prunes at most every ``LIST_REFRESH_SECONDS``, one caller
at a time while the others list the cache, and the lock is held only to copy
it. Measured on 194 sessions in a private store: one list 3.2 ms and ten at
once up to 155 ms before, 0.8 ms and 0.9 ms after.

Counted (database reads) and timed; a reader that must wait for the first
refresh is the negative control of the "never held up" test.
"""

from __future__ import annotations

import threading
import time

import pytest

SESSIONS = 60


@pytest.fixture
def store(tmp_path, monkeypatch):
    from kazma_ui import session_manager as sm

    manager = sm.SessionManager(db_path=str(tmp_path / "sessions.db"))
    for i in range(SESSIONS):
        s = manager.get_or_create(f"s-{i:03d}", durable=True, thread_id=f"t-{i:03d}")
        s.add_message("user", "hello " * 20)
        s.add_message("assistant", "hi " * 40)
        manager.put(s)
    reads: list[float] = []
    real = manager._load_all_from_primary

    def counted(**kwargs):
        reads.append(time.monotonic())
        return real(**kwargs)

    monkeypatch.setattr(manager, "_load_all_from_primary", counted)
    manager.reads = reads
    return manager


def _concurrently(fn, n: int = 10) -> list[float]:
    times: list[float] = []
    start = threading.Barrier(n)

    def one():
        start.wait()
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)

    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return times


def test_lists_inside_the_window_read_nothing(store):
    assert len(store.list_all()) == SESSIONS
    store.reads.clear()
    _concurrently(store.list_all)
    assert store.reads == []


def test_a_due_refresh_reads_once_for_ten_readers(store, monkeypatch):
    from kazma_ui import session_manager as sm

    store.list_all()
    store.reads.clear()
    store._list_refreshed_at["default"] = time.monotonic() - sm.LIST_REFRESH_SECONDS - 1
    _concurrently(store.list_all)
    assert len(store.reads) == 1


def test_a_slow_refresh_does_not_hold_up_another_reader(store, monkeypatch):
    from kazma_ui import session_manager as sm

    store.list_all()
    release, entered = threading.Event(), threading.Event()
    real = store._load_all_from_primary

    def slow(**kwargs):
        entered.set()
        release.wait(5)
        return real(**kwargs)

    monkeypatch.setattr(store, "_load_all_from_primary", slow)
    store._list_refreshed_at["default"] = time.monotonic() - sm.LIST_REFRESH_SECONDS - 1
    refresher = threading.Thread(target=store.list_all)
    refresher.start()
    assert entered.wait(5)  # the refresher holds the refresh
    t0 = time.perf_counter()
    rows = store.list_all()
    waited = time.perf_counter() - t0
    release.set()
    refresher.join(5)
    assert len(rows) == SESSIONS and waited < 1.0, waited


def test_negative_control_the_first_list_waits_for_the_first_refresh(store, monkeypatch):
    """Before any refresh a list waits (it must hold the database's
    sessions): the measure above sees a reader held up."""
    release, entered = threading.Event(), threading.Event()
    real = store._load_all_from_primary

    def slow(**kwargs):
        entered.set()
        release.wait(5)
        return real(**kwargs)

    monkeypatch.setattr(store, "_load_all_from_primary", slow)
    refresher = threading.Thread(target=store.list_all)
    refresher.start()
    assert entered.wait(5)
    timer = threading.Timer(0.5, release.set)
    timer.start()
    t0 = time.perf_counter()
    store.list_all()
    waited = time.perf_counter() - t0
    refresher.join(5)
    assert waited >= 0.3, waited


def test_ten_readers_take_about_as_long_as_one(store):
    store.list_all()
    t0 = time.perf_counter()
    store.list_all()
    single = time.perf_counter() - t0
    worst = max(_concurrently(store.list_all))
    assert worst <= 2 * single + 0.05, (single, worst)


def test_a_session_written_here_is_listed_at_once(store):
    store.list_all()
    s = store.get_or_create("s-new", durable=True, thread_id="t-new")
    s.add_message("user", "new chat")
    store.put(s)
    assert "s-new" in {x.session_id for x in store.list_all()}
