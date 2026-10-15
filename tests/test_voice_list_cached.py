"""The edge-tts voice list is fetched once, not on every Settings visit.

``/api/voice/voices?provider=edgetts`` asked Microsoft's servers for the
voice list every time Settings -> Voice loaded, from two copies of the
route (2026-10-15; ``tests/test_no_route_registered_twice.py`` holds the
copies to one).
"""

from __future__ import annotations

import asyncio
import types

import pytest

from kazma_ui import routes_voice
from tests._module_stubs import stub_modules


def _fake_edge_tts(calls: list[str], *, fail: bool = False) -> types.ModuleType:
    class _Manager:
        voices = [{"ShortName": "ar-KW-FahedNeural"}, {"ShortName": "en-US-AriaNeural"}]

        @classmethod
        async def create(cls):
            calls.append("fetch")
            if fail:
                raise OSError("no network")
            return cls()

    module = types.ModuleType("edge_tts")
    module.VoicesManager = _Manager
    return module


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(routes_voice, "_edge_voices_cache", {"voices": None, "until": 0.0})


def _list() -> list[str]:
    return asyncio.run(routes_voice.list_voices("edgetts"))


def test_two_visits_fetch_the_list_once():
    calls: list[str] = []
    with stub_modules({"edge_tts": _fake_edge_tts(calls)}):
        first, second = _list(), _list()
    assert first == second == ["default", "ar-KW-FahedNeural", "en-US-AriaNeural"]
    assert calls == ["fetch"]


def test_the_count_sees_a_second_fetch_once_the_list_expires(monkeypatch):
    """Control: with the kept list expired, the next visit fetches again."""
    calls: list[str] = []
    with stub_modules({"edge_tts": _fake_edge_tts(calls)}):
        _list()
        routes_voice._edge_voices_cache["until"] = 0.0
        _list()
    assert calls == ["fetch", "fetch"]


def test_a_failed_read_answers_the_short_list_and_asks_again_later():
    calls: list[str] = []
    with stub_modules({"edge_tts": _fake_edge_tts(calls, fail=True)}):
        first, second = _list(), _list()
    assert first == second == routes_voice._EDGE_FALLBACK_VOICES
    assert "ar-SA-ZariyahNeural" in first
    assert calls == ["fetch"]  # kept for the retry window, not asked per visit
    import time

    assert routes_voice._edge_voices_cache["until"] <= time.monotonic() + routes_voice._EDGE_VOICES_RETRY_S
