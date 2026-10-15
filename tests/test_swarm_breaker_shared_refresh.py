"""A refresh from the shared copy never puts an older breaker state back.

Found by the SQ01 soak harness test (``tests/test_swarm_soak_harness.py``),
which failed 2 runs in 10 on the code of 2026-10-15 with "a refresh from the
shared store moved it from open to closed at generation 7 (no newer
generation)". A dispatch's outcome changes its breaker under the breaker's
lock (``record_success`` / ``record_failure``) and shares it afterwards, in a
second step (``persist_shared``, in the same worker thread). Another
dispatch's ``check_or_raise`` running between the two read the shared copy of
the generation before and laid its state over the newer one: a trip undone (a
worker that just failed past its threshold admitted again), a recovery
reopened. ``refresh_from_shared`` now takes only a newer generation's state.

Negative control: the refresh as it was puts the older state back.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from kazma_core.swarm.reliability import CircuitBreaker, CircuitState


class _Store:
    """The part of the settings store the shared breakers use."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.data.get(key, default)

    def set(self, key: str, value: Any, category: str = "general") -> None:
        with self.lock:
            self.data[key] = value

    def atomic_update(self, key: str, fn: Any, category: str = "general") -> Any:
        with self.lock:
            self.data[key] = fn(self.data.get(key))
            return self.data[key]

    def delete(self, key: str) -> bool:
        with self.lock:
            return self.data.pop(key, None) is not None


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    shared = _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: shared)
    return shared


def _old_refresh(self: CircuitBreaker, worker_name: str) -> None:
    """``refresh_from_shared`` before 2026-10-15: the shared copy overlaid
    whatever its generation."""
    shared = type(self).load_shared(worker_name)
    if shared is None:
        return
    self.consecutive_failures = shared.consecutive_failures
    self.failure_threshold = shared.failure_threshold
    self.cooldown_seconds = shared.cooldown_seconds
    self._state = shared._state
    self._opened_at = shared._opened_at
    self._opened_at_wall = shared._opened_at_wall
    self.generation = max(self.generation, shared.generation)


def _trip_before_it_is_shared(store: _Store) -> CircuitBreaker:
    """A breaker that tripped (generation 1) whose shared copy still holds
    generation 0 -- the window between ``record_failure`` and
    ``persist_shared``."""
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
    breaker.persist_shared("w")
    breaker.record_failure(breaker.admit("w"))
    assert breaker.state is CircuitState.OPEN and breaker.generation == 1
    assert store.data["swarm.breaker.w"]["generation"] == 0
    return breaker


def test_a_trip_is_not_undone_by_the_copy_before_it(store: _Store) -> None:
    breaker = _trip_before_it_is_shared(store)
    breaker.refresh_from_shared("w")
    assert breaker.state is CircuitState.OPEN and breaker.generation == 1
    with pytest.raises(Exception, match="(?i)circuit"):
        breaker.check_or_raise("w")  # the worker that just failed is not admitted


def test_a_recovery_is_not_reopened_by_the_copy_before_it(store: _Store) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure(breaker.admit("w"))
    breaker.persist_shared("w")  # open, generation 1
    breaker._opened_at -= 61  # its cooldown has passed: half-open
    probe = breaker.admit("w")
    assert probe is not None and probe.probe
    breaker.record_success(probe)  # closed, generation 2, not yet shared
    breaker.refresh_from_shared("w")
    assert breaker.state is CircuitState.CLOSED and breaker.generation == 2


def test_negative_control_the_old_refresh_puts_the_older_state_back(
        store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = _trip_before_it_is_shared(store)
    monkeypatch.setattr(CircuitBreaker, "refresh_from_shared", _old_refresh)
    breaker.refresh_from_shared("w")
    assert breaker.state is CircuitState.CLOSED and breaker.generation == 1
    assert breaker.check_or_raise("w") is not None  # the tripped worker admitted again


def test_another_replicas_newer_trip_is_still_taken(store: _Store) -> None:
    mine = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
    other = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
    other.record_failure(other.admit("w"))
    other.persist_shared("w")  # generation 1, open
    mine.refresh_from_shared("w")
    assert mine.state is CircuitState.OPEN and mine.generation == 1


def test_the_same_generation_brings_counters_never_state_or_timing(store: _Store) -> None:
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=3600)
    breaker.record_failure(breaker.admit("w"))
    breaker.persist_shared("w")
    store.data["swarm.breaker.w"] = {**store.data["swarm.breaker.w"], "consecutive_failures": 2,
                                     "state": "open", "opened_at_wall": time.time() - 7200}
    breaker.refresh_from_shared("w")
    assert breaker.consecutive_failures == 2  # another replica's failures count
    assert breaker.state is CircuitState.CLOSED  # but not a state without a generation
    assert breaker._opened_at is None


@pytest.mark.parametrize("old", [False, True], ids=["fixed", "negative-control-old-refresh"])
def test_outcomes_racing_refreshes_never_go_back(store: _Store, monkeypatch: pytest.MonkeyPatch,
                                                 old: bool) -> None:
    """Threads recording outcomes (change, then share) while others refresh,
    as dispatches do: no refresh ever moves the state without a newer
    generation (the soak's check, at a higher rate). With the old refresh
    the same run sees older states put back."""
    if old:
        monkeypatch.setattr(CircuitBreaker, "refresh_from_shared", _old_refresh)
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.002)
    breaker.persist_shared("w")
    stop = threading.Event()
    regressions: list[str] = []

    def outcomes() -> None:
        flip = 0
        while not stop.is_set():
            admission = breaker.admit("w")
            if admission is None:
                time.sleep(0.001)
                continue
            if flip % 3:
                breaker.record_failure(admission)
            else:
                breaker.record_success(admission)
            flip += 1
            time.sleep(0)  # let a refresh in between, as a worker thread does
            breaker.persist_shared("w")

    def refreshes() -> None:
        while not stop.is_set():
            with breaker._lock:
                before, generation = breaker.state, breaker.generation
                breaker.refresh_from_shared("w")
                if breaker._state != before and breaker.generation <= generation:
                    regressions.append(f"{before.value} -> {breaker._state.value} at {generation}")

    threads = [threading.Thread(target=outcomes) for _ in range(2)]
    threads += [threading.Thread(target=refreshes) for _ in range(2)]
    for t in threads:
        t.start()
    ceiling = time.monotonic() + 20.0
    while time.monotonic() < ceiling and not (
            breaker.generation >= 40 or (old and len(regressions) >= 3 and breaker.generation > 4)):
        stop.wait(0.05)
    stop.set()
    for t in threads:
        t.join(5)
    assert breaker.generation > 4  # it did trip and recover, many times
    if old:
        assert regressions, "the old refresh never put an older state back"
    else:
        assert regressions == [], regressions[:5]
