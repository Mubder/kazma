"""The bookmark store closes only between statements, and is built once
(audit 2026-10-26 AUD-118).

``BookmarkStore.close()`` freed the connection without the store's lock, the
ConfigStore use-after-free AGENTS.md §8 records (a bare access violation on
Windows, no traceback). As in ``tests/test_config_store_close_race.py`` the
crash itself is not reproduced -- it kills the process -- the two properties
whose conjunction prevents it are pinned instead: a statement holds the lock
while live, and ``close()`` waits for whoever holds it. The singleton had no
init lock: two first callers each built a store with its own connection and
lock.
"""

from __future__ import annotations

import threading
import time

import pytest
from kazma_core.stores import bookmarks
from kazma_core.stores.bookmarks import BookmarkStore

_UNBLOCKED_GRACE_SECONDS = 2.0
_JOIN_TIMEOUT_SECONDS = 30.0


@pytest.fixture()
def store(tmp_path):
    s = BookmarkStore(str(tmp_path / "settings.db"))
    s.create_bookmark("docs", "url", "https://example.com/docs")
    yield s
    s.close()


def _close_while_held(store, close) -> bool:
    """Hold the store lock (a statement in flight) and call *close* from
    another thread. True when *close* finished while the lock was held."""
    done = threading.Event()
    with store._lock:
        t = threading.Thread(target=lambda: (close(store), done.set()), daemon=True)
        t.start()
        finished_early = done.wait(_UNBLOCKED_GRACE_SECONDS)
    t.join(_JOIN_TIMEOUT_SECONDS)
    assert done.is_set()
    return finished_early


def test_a_read_holds_the_lock_for_its_statement(store):
    held: list[bool] = []
    conn = store._get_conn()

    def handler() -> int:
        if not held:
            held.append(store._lock.locked())
        return 0

    conn.set_progress_handler(handler, 1)
    try:
        assert [b["name"] for b in store.list_bookmarks()] == ["docs"]
    finally:
        conn.set_progress_handler(None, 0)
    assert held == [True]


def test_close_waits_for_whoever_holds_the_store_lock(store):
    assert _close_while_held(store, BookmarkStore.close) is False
    assert store._conn is None
    assert [b["name"] for b in store.list_bookmarks()] == ["docs"]  # reopens lazily


def test_negative_control_the_old_close_ran_under_a_live_statement(store):
    def old_close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    assert _close_while_held(store, old_close) is True


def _first_callers(monkeypatch, get, callers: int = 4) -> int:
    """How many stores *callers* simultaneous first calls of *get* built."""
    built: list[BookmarkStore] = []
    real_init = BookmarkStore._init_db

    def slow_init(self):
        built.append(self)
        time.sleep(0.2)  # the window two first callers both used to enter
        real_init(self)

    monkeypatch.setattr(BookmarkStore, "_init_db", slow_init)
    monkeypatch.setattr(bookmarks, "_bookmark_store", None)
    start = threading.Barrier(callers)

    def call():
        start.wait()
        get()

    threads = [threading.Thread(target=call) for _ in range(callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(_JOIN_TIMEOUT_SECONDS)
    for s in built:
        s.close()
    monkeypatch.setattr(bookmarks, "_bookmark_store", None)
    return len(built)


def test_first_callers_share_one_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    assert _first_callers(monkeypatch, bookmarks.get_bookmark_store) == 1


def test_negative_control_the_unlocked_singleton_built_several(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

    singleton: dict[str, BookmarkStore] = {}  # the old module global, unlocked

    def old_get():
        if singleton.get("store") is None:
            singleton["store"] = BookmarkStore()
        return singleton["store"]

    assert _first_callers(monkeypatch, old_get) > 1
