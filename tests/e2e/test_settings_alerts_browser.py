"""Settings -> Alerts in a browser, reached by the sidebar (the unified alerts
plan, Stage 3), in English and in Arabic.

- The tab opens from the Settings tabs after a soft navigation, every
  section on its own card, each card the colour of the wall.
- The guard's pager: "Send a test page" is answered by a guard (the real
  ``PagerTest`` of ``kazma_guard.py``, ticked here as the guard's loop ticks
  it) and the page says it was sent, then the last page as delivered.
- Kazma Watch against a stand-in observer: pair with the pairing token,
  send a test page (the channels that took it), unpair (with "also delete"
  ticked in the confirm dialog).
- Arabic: the tab reads right to left, in Arabic, and pairs and unpairs.

Set ``KAZMA_TEST_SHOTS`` to a folder to keep screenshots (light, dark, Arabic).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, unified_turn_server  # noqa: E402
from tests.test_settings_alerts import TOKEN, _Watch  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]


def _load_guard():
    spec = importlib.util.spec_from_file_location("_alerts_tab_guard", REPO / "scripts" / "service" / "kazma_guard.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A Kazma (the harness), a stand-in Kazma Watch, and a guard's loop."""
    from kazma_core.observability import outside_watch

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.setattr(outside_watch, "_restrict_to_owner", lambda path: True)
    state = tmp_path / "guard.state.json"
    for name in ("KAZMA_GUARD_STATE", "KAZMA_GUARD_STATE_FILE"):
        monkeypatch.setenv(name, str(state))
    monkeypatch.setenv("KAZMA_GUARD_RELOAD_FILE", str(tmp_path / "guard.reload"))
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "123:fake")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "4242")

    handler = type("W", (_Watch,), {"keys": {}, "settings": {}, "seen": [],
                                    "channels": {"telegram": "sent", "email": "off"}})
    watch = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=watch.serve_forever, daemon=True).start()
    handler.url = f"http://127.0.0.1:{watch.server_address[1]}"

    # The guard: its state file beats, and its PagerTest answers. Telegram is
    # a recorder (the notifier's own send, the card it would send).
    guard = _load_guard()
    sent: list[str] = []

    def notifier():
        n = guard.Notifier(lambda *a, **k: None)

        def post(text, *, delayed=False):
            # Telegram took it; the notifier hears the outcome itself
            # (Notifier._heard), as a real post's.
            sent.append(text)
            return True

        n._post = post
        return n

    fake = SimpleNamespace(log=lambda *a, **k: None, notify=None, cwd=REPO, server_python=None)
    pager = guard.PagerTest(fake, notifier_factory=notifier)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            guard._update_state(heartbeat=time.time(), guard_features=list(guard.GUARD_FEATURES))
            pager.tick()
            stop.wait(0.2)

    ticker = threading.Thread(target=loop, daemon=True)
    ticker.start()
    try:
        with unified_turn_server() as h:
            yield SimpleNamespace(h=h, watch=handler, sent=sent, home=home)
    finally:
        stop.set()
        ticker.join(timeout=5)
        watch.shutdown()


def _shot(page, name: str) -> None:
    folder = os.environ.get("KAZMA_TEST_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(700)  # the tab's x-transition
        size = page.viewport_size
        page.set_viewport_size({"width": size["width"], "height": 2300})
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(folder) / f"{name}.png"))
        page.set_viewport_size(size)


def _open_alerts(page, base: str, tab_label: str) -> None:
    """Home, then Settings by the sidebar, then the Alerts tab."""
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=20000)
    page.locator("nav.settings-nav button.settings-tab", has_text=tab_label).click()
    page.locator("#alerts-routes").wait_for(state="visible", timeout=20000)
    page.wait_for_function("document.querySelector('#alerts-guard-last') && "
                           "getComputedStyle(document.querySelector('#alerts-guard-last')).display !== 'none'",
                           timeout=20000)


def _cards_are_the_wall(page) -> list[str]:
    """Each section card whose fill is not the wall's (none, per the design)."""
    return page.evaluate("""() => {
        const probe = document.createElement('div');
        probe.style.background = 'var(--surface-wall)';
        document.body.appendChild(probe);
        const wall = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return [...document.querySelectorAll('#alerts-tab .card-nested')]
            .filter(c => getComputedStyle(c).backgroundColor !== wall)
            .map(c => c.id + ': ' + getComputedStyle(c).backgroundColor + ' vs ' + wall);
    }""")


def _pair_test_unpair(page, watch, *, paired_word: str, channel_word: str, unpaired_word: str,
                      shot: str) -> None:
    page.locator("[data-watch-not-paired]").wait_for(state="visible", timeout=20000)
    page.locator("#alerts-watch-token").fill(TOKEN)
    page.locator("#alerts-watch-url").fill(watch.url)
    page.locator("#alerts-watch-pair").click()
    page.locator("[data-watch-paired]").wait_for(state="visible", timeout=30000)
    assert paired_word in page.locator("#alerts-watch-result").inner_text()
    assert page.locator("#alerts-watch-token").input_value() == "", "the token is not kept in the page"
    assert len(watch.keys) == 1
    page.locator("#alerts-watch-test").click()
    page.wait_for_function(
        f"(document.querySelector('#alerts-watch-result') || {{}}).innerText.includes({json.dumps(channel_word)})",
        timeout=30000)
    _shot(page, shot)
    page.locator("#alerts-watch-unpair").click()
    dialog = page.locator(".modal[aria-modal='true']")
    dialog.wait_for(state="visible", timeout=10000)
    dialog.locator("input[type=checkbox]").check()
    dialog.locator(".modal-footer button").last.click()
    page.locator("[data-watch-not-paired]").wait_for(state="visible", timeout=30000)
    assert unpaired_word in page.locator("#alerts-watch-result").inner_text()
    assert watch.keys == {}
    forgets = [json.loads(s["raw"]) for s in watch.seen if s["path"] == "/v1/leave"]
    assert forgets and forgets[-1]["forget"] is True


def test_the_alerts_tab_in_a_browser(setup) -> None:
    from playwright.sync_api import sync_playwright

    h: Harness = setup.h
    errors: list[str] = []
    host = h.base.split("//", 1)[1].split(":", 1)[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_alerts(pg, h.base, "Alerts")
            assert "tab=alerts" in pg.url
            for section in ("#alerts-routes", "#alerts-status", "#alerts-guard", "#alerts-watch",
                            "#alerts-notices", "#alerts-recent"):
                assert pg.locator(section).is_visible(), section
            assert _cards_are_the_wall(pg) == []
            _shot(pg, "en-light-not-paired")

            # The guard's pager: the test page, answered by the guard.
            pg.locator("#alerts-guard-test").click()
            pg.wait_for_function(
                "(document.querySelector('#alerts-guard-test-result') || {}).innerText"
                ".includes('sent the test page to chat 4242')", timeout=30000)
            assert len(setup.sent) == 1 and "[Guard] Test page" in setup.sent[0]
            pg.wait_for_function(
                "(document.querySelector('#alerts-guard-last') || {}).innerText.includes('Last page delivered')",
                timeout=15000)

            # Kazma Watch: pair, test page, unpair.
            _pair_test_unpair(pg, setup.watch, paired_word="Paired", channel_word="Telegram: took it",
                              unpaired_word="Unpaired", shot="en-light-paired")

            # A save: the notices to the bell only, then back.
            pg.locator("#alerts-notices-choice").select_option("bell")
            pg.locator("#alerts-save").click()
            pg.wait_for_function(
                "fetch('/api/settings/alerts').then(r => r.json()).then(d => d.notices_to_chats === false)",
                timeout=15000)

            # Dark: the cards are still the wall.
            pg.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            assert _cards_are_the_wall(pg) == []
            _shot(pg, "en-dark")

            # Arabic, right to left, by the sidebar too.
            ctx = browser.new_context(viewport={"width": 1366, "height": 900})
            ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_alerts(ar, h.base, "التنبيهات")
            assert ar.evaluate("document.documentElement.dir") == "rtl"
            assert ar.locator("#alerts-routes-title").inner_text() == "وجهة تنبيهات كاظمه"
            assert ar.locator("#alerts-guard-title").inner_text() == "منبّه الحارس"
            _shot(ar, "ar-light-not-paired")
            _pair_test_unpair(ar, setup.watch, paired_word="تم الربط", channel_word="استلمها",
                              unpaired_word="فُكّ الربط", shot="ar-light-paired")
            _shot(ar, "ar-light-after")
        finally:
            browser.close()
    assert errors == [], errors
