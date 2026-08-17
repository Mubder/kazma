"""Browser boot event-loop policy regression (incident 2026-08-16).

Kazma's server forces a global ``WindowsSelectorEventLoopPolicy`` (psycopg
refuses Proactor). Playwright's sync API creates its own loop via
``asyncio.new_event_loop()`` on start, which inherits the selector policy; the
selector loop cannot spawn the Node driver subprocess on Windows
(``create_subprocess_exec`` → ``NotImplementedError``), so every browser tool
silently returned nothing while logging "Future exception was never retrieved".

``_ensure_page_sync`` must therefore boot Playwright under a Proactor policy
and restore the original policy afterwards. This test mocks Playwright (no real
chromium) and asserts the policy seen during ``start()`` and the restore.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from kazma_skills.native.browser_automation import tools as bt

_IS_WINDOWS = sys.platform.startswith("win")


class _FakePage:
    pass


class _FakeBrowser:
    def new_page(self):
        return _FakePage()


class _FakeChromium:
    def launch(self, headless=True):  # noqa: ARG002
        return _FakeBrowser()


class _FakePw:
    chromium = _FakeChromium()

    def stop(self):
        pass


class _FakeCM:
    """Stands in for sync_playwright(); records the policy active at start()."""

    seen_policy: str = ""

    def start(self):
        _FakeCM.seen_policy = type(asyncio.get_event_loop_policy()).__name__
        return _FakePw()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setitem(bt._state, "playwright", None)
    monkeypatch.setitem(bt._state, "browser", None)
    monkeypatch.setitem(bt._state, "page", None)
    # Restore whatever policy was active before the test, so this test does not
    # leak a Proactor/Selector switch into later tests in the session.
    prev_policy = asyncio.get_event_loop_policy()
    yield
    asyncio.set_event_loop_policy(prev_policy)


def test_boot_uses_proactor_and_restores_policy(monkeypatch):
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeCM())

    # Begin from the selector policy the server installs (Windows).
    if _IS_WINDOWS:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    before = type(asyncio.get_event_loop_policy()).__name__

    page = bt._ensure_page_sync()

    assert isinstance(page, _FakePage)
    if _IS_WINDOWS:
        assert _FakeCM.seen_policy == "WindowsProactorEventLoopPolicy", (
            "Playwright must boot under a Proactor policy on Windows"
        )
    # The original policy is restored after boot (loop already created).
    assert type(asyncio.get_event_loop_policy()).__name__ == before


def test_boot_caches_page_and_reuses(monkeypatch):
    calls = {"n": 0}

    class CountingCM(_FakeCM):
        def start(self):
            calls["n"] += 1
            return super().start()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: CountingCM())
    p1 = bt._ensure_page_sync()
    p2 = bt._ensure_page_sync()
    assert p1 is p2
    assert calls["n"] == 1, "second call must reuse the cached page, not re-boot"
