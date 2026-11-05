"""An origin's leases stay true under contention (live 2026-11-05).

A deep research ran several searches of the local SearXNG at once. Each
waiter polled its origin every 50 ms with a compare-and-set write of the
whole origins table, so the writers raced each other's revision until a
write gave up as "Durable acquisition state is busy". A release that gave
up raised from the request's ``finally`` and left its lease held by the
live server: nothing reaps a live process's lease, so the origin's two
slots stayed taken and every later search waited out its 30 seconds until
a restart.

Held here, each with its negative control:
- this process's writers of a table take turns (no conflict among them);
- a conflict from another process waits before the next try;
- a release that cannot be written never raises, and the origin's next
  admission takes the lease back -- a lease still held is never taken;
- a poll that only waits writes nothing;
- a request's own error is never replaced by its release.
"""
from __future__ import annotations

import threading

import pytest

from kazma_core import config_store
from kazma_core.config_store import ConfigRevisionConflict
from kazma_core.web_acquire import durable_state, origin_state, origins
from kazma_core.web_acquire.result import AcquisitionError

_ORIGIN = origins._origin('http://127.0.0.1:8888/')


class _Store(config_store.ConfigStore):
    """The real SQLite store, counting writes and conflicts."""

    def __init__(self, path):
        super().__init__(path)
        self.writes = 0
        self.conflicts = 0
        self.refuse = 0
        self.barrier = None
        self._seen: set[int] = set()

    def get_category(self, category):
        data = super().get_category(category)
        if self.barrier is not None and category == 'acquisition_origins':
            me = threading.get_ident()
            if me not in self._seen:
                self._seen.add(me)
                try:
                    self.barrier.wait()
                except threading.BrokenBarrierError:
                    pass
        return data

    def batch_set(self, items, *, expected=None):
        if self.refuse:
            self.refuse -= 1
            self.conflicts += 1
            raise ConfigRevisionConflict('moved by another process')
        try:
            result = super().batch_set(items, expected=expected)
        except ConfigRevisionConflict:
            self.conflicts += 1
            raise
        self.writes += 1
        return result


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = _Store(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: s)
    yield s
    s.close()


def _row(store):
    table = store.get_category('acquisition_origins')['acquisition.origins']
    return table[origin_state._key(_ORIGIN)]


def _two_writers_at_once(store):
    """Two threads of this process change the table, both having read the
    same revision first (the barrier holds each first read)."""
    store.barrier = threading.Barrier(2, timeout=0.5)
    errors = []

    def queue():
        try:
            origin_state.queue(_ORIGIN)
        except Exception as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=queue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    store.barrier = None
    assert not errors
    return len(_row(store)['waiting'])


def test_writers_of_this_process_take_turns(store):
    assert _two_writers_at_once(store) == 2
    assert store.conflicts == 0


def test_without_the_table_lock_the_writers_collide(store, monkeypatch):
    monkeypatch.setattr(durable_state, '_table_lock', lambda name: threading.RLock())
    assert _two_writers_at_once(store) == 2
    assert store.conflicts >= 1


def test_a_conflict_from_another_process_waits_before_the_next_try(store, monkeypatch):
    pauses = []
    monkeypatch.setattr(durable_state, '_retry_pause', pauses.append)
    store.refuse = 3
    token = origin_state.queue(_ORIGIN)
    assert token in _row(store)['waiting']
    assert pauses == [1, 2, 3]
    origin_state.release(_ORIGIN, token)


def _leak_two_leases(store, monkeypatch, caplog):
    """Admit two requests, then release both while every write conflicts."""
    one, two = origin_state.queue(_ORIGIN), origin_state.queue(_ORIGIN)
    assert origin_state.start(_ORIGIN, one, 0) == 0
    assert origin_state.start(_ORIGIN, two, 0) == 0
    monkeypatch.setattr(durable_state, '_retry_pause', lambda attempt: None)
    store.refuse = 10_000
    with caplog.at_level('WARNING', logger='kazma_core.web_acquire.origin_state'):
        origin_state.release(_ORIGIN, one)  # never raises
        origin_state.release(_ORIGIN, two)
    store.refuse = 0
    assert sorted(_row(store)['active']) == sorted([one, two])
    assert sum('not released' in r.getMessage() for r in caplog.records) == 2
    return one, two


def test_a_lease_whose_release_failed_is_taken_back(store, monkeypatch, caplog):
    one, two = _leak_two_leases(store, monkeypatch, caplog)
    three = origin_state.queue(_ORIGIN)
    assert origin_state.start(_ORIGIN, three, 0) == 0
    assert set(_row(store)['active']) == {three}
    origin_state.release(_ORIGIN, three)


def test_a_lease_still_held_is_never_taken(store, monkeypatch, caplog):
    one, two = _leak_two_leases(store, monkeypatch, caplog)
    with origin_state._HELD_LOCK:
        origin_state._HELD.update({one, two})
    try:
        three = origin_state.queue(_ORIGIN)
        assert origin_state.start(_ORIGIN, three, 0) > 0
        assert set(_row(store)['active']) == {one, two}
    finally:
        for token in (one, two, three):
            origin_state.release(_ORIGIN, token)


def test_without_taking_back_the_leaked_leases_block_the_origin(store, monkeypatch, caplog):
    one, two = _leak_two_leases(store, monkeypatch, caplog)
    monkeypatch.setattr(origin_state, '_left_behind', lambda identity, token: False)
    three = origin_state.queue(_ORIGIN)
    assert origin_state.start(_ORIGIN, three, 0) > 0
    origin_state.release(_ORIGIN, three)


def test_a_poll_that_only_waits_writes_nothing(store):
    one, two, three = (origin_state.queue(_ORIGIN) for _ in range(3))
    assert origin_state.start(_ORIGIN, one, 0) == 0
    assert origin_state.start(_ORIGIN, two, 0) == 0
    before = store.writes
    for _ in range(10):
        assert origin_state.start(_ORIGIN, three, 0) > 0
    assert store.writes == before
    for token in (one, two, three):
        origin_state.release(_ORIGIN, token)


def test_a_spacing_wait_writes_nothing(store):
    one = origin_state.queue(_ORIGIN)
    assert origin_state.start(_ORIGIN, one, 5) == 0
    origin_state.release(_ORIGIN, one)
    two = origin_state.queue(_ORIGIN)
    assert origin_state.start(_ORIGIN, two, 5) > 0  # opens nothing new
    before = store.writes
    for _ in range(10):
        assert origin_state.start(_ORIGIN, two, 5) > 0
    assert store.writes == before
    origin_state.release(_ORIGIN, two)


def test_the_old_poll_wrote_on_every_wait(store, monkeypatch):
    monkeypatch.setattr(origin_state, '_waited', lambda before, row, now, wait: wait)
    one, two, three = (origin_state.queue(_ORIGIN) for _ in range(3))
    origin_state.start(_ORIGIN, one, 0)
    origin_state.start(_ORIGIN, two, 0)
    before = store.writes
    for _ in range(10):
        origin_state.start(_ORIGIN, three, 0)
    assert store.writes == before + 10
    for token in (one, two, three):
        origin_state.release(_ORIGIN, token)


def test_a_request_keeps_its_own_error_when_its_release_fails(store, monkeypatch):
    scheduler = origins._OriginScheduler(durable=True)
    monkeypatch.setattr(durable_state, '_retry_pause', lambda attempt: None)
    with pytest.raises(ValueError, match='the request itself'):
        with scheduler.request_sync('http://127.0.0.1:8888/search'):
            store.refuse = 10_000
            raise ValueError('the request itself failed')
    store.refuse = 0


def test_the_old_release_replaced_the_request_error(store, monkeypatch):
    def old_release(origin, token):
        def change(table):
            row = table.get(origin_state._key(origin))
            if row is not None:
                row['active'].pop(token, None)
                row['waiting'].pop(token, None)
        durable_state.mutate('origins', change)

    monkeypatch.setattr(origin_state, 'release', old_release)
    monkeypatch.setattr(durable_state, '_retry_pause', lambda attempt: None)
    scheduler = origins._OriginScheduler(durable=True)
    with pytest.raises(AcquisitionError, match='busy'):
        with scheduler.request_sync('http://127.0.0.1:8888/search'):
            store.refuse = 10_000
            raise ValueError('the request itself failed')
    store.refuse = 0
