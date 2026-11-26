"""A configured memory mirror down since start is pinged once per window.

``state_backend.get_state_backend`` caches a mirror that answers; one that
did not answer was built and pinged again on every call, and recall calls it
on every thin result: 5.1 s each while the mirror was down (the recall-strict
delta review's ticket, 2026-11-26). It now answers the null backend within
the mirror's own ping window (``_READY_PROBE_TTL``) and pings again after it.
A changed setting is tried at once (the control), and a mirror that answers
again is cached as before.

No database: the backend is a stand-in that counts its pings.
"""

from __future__ import annotations

import time
import types
from typing import Any

import pytest
from kazma_core.memory import state_backend

URL = "postgresql://kazma:x@127.0.0.1:1/kazma"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Mirror:
    """Counts its pings; answers as ``up`` says."""

    name = "postgres"
    up = False
    pings = 0
    built = 0

    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        type(self).built += 1
        self.url = url

    @property
    def available(self) -> bool:
        type(self).pings += 1
        return type(self).up

    def close(self) -> None:
        pass


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    tick = _Clock()
    fake_time = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})
    fake_time.monotonic = tick
    monkeypatch.setattr(state_backend, "time", fake_time)
    monkeypatch.setattr(state_backend, "PostgresStateBackend", _Mirror)
    monkeypatch.setattr(state_backend, "_state_backend_cache", {})
    # raising=False: the code before the cache had none, and the measure says so.
    monkeypatch.setattr(state_backend, "_state_backend_down", {}, raising=False)
    monkeypatch.setattr(_Mirror, "up", False)
    monkeypatch.setattr(_Mirror, "pings", 0)
    monkeypatch.setattr(_Mirror, "built", 0)
    _configure(monkeypatch, URL)
    return tick


def _configure(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    cfg = {"state": {"provider": "postgres", "url": url}}
    monkeypatch.setattr(state_backend, "_cfg", lambda: cfg)


def _ask(times: int) -> list[Any]:
    return [state_backend.get_state_backend() for _ in range(times)]


def test_a_mirror_down_since_start_is_pinged_once_per_window(clock: _Clock) -> None:
    answers = _ask(20)
    assert {a.name for a in answers} == {"null"}
    assert _Mirror.pings == 1 and _Mirror.built == 1, "one ping for twenty calls within the window"
    clock.now += state_backend._READY_PROBE_TTL - 1
    _ask(5)
    assert _Mirror.pings == 1, "still within the window"
    clock.now += 2
    _ask(5)
    assert _Mirror.pings == 2, "the window ended: pinged again, once"


def test_a_changed_setting_is_tried_at_once(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: the cache is the down mirror's, not every mirror's.
    _ask(3)
    assert _Mirror.pings == 1
    _configure(monkeypatch, URL.replace("127.0.0.1:1", "127.0.0.1:2"))
    _ask(3)
    assert _Mirror.pings == 2


def test_a_mirror_that_answers_again_is_cached_as_before(clock: _Clock) -> None:
    _ask(2)
    clock.now += state_backend._READY_PROBE_TTL + 1
    _Mirror.up = True
    first, second = _ask(2)
    assert first.name != "null" and first is second, "cached once it answers"
    assert _Mirror.pings == 2 and not state_backend._state_backend_down
