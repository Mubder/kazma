"""Settings -> Backup -> "A copy on another drive", in a browser, reached by
the sidebar, in English and in Arabic (the owner's item, 20 Nov 2026).

The folder is chosen with the page's own picker (Browse, then a folder, then
"Use this folder"), switched on, given a count and saved; the card then says
the next backup makes Kazma's copy there, and "Test the folder" lists what it
checked. A save that switches the copy on with no folder is refused in the
page's words. Arabic: the same flow, read right to left, in Arabic.

Set ``KAZMA_TEST_SHOTS`` to a folder to keep screenshots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _shot(page, name: str) -> None:
    folder = os.environ.get("KAZMA_TEST_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(700)
        page.locator("#backup-folder").screenshot(path=str(Path(folder) / f"{name}.png"))


def _open_backup(page, base: str, tab_label: str) -> None:
    """Home, then Settings by the sidebar, then the Backup tab."""
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=20000)
    page.locator("nav.settings-nav button.settings-tab", has_text=tab_label).click()
    page.locator("#backup-folder").wait_for(state="visible", timeout=20000)
    page.locator("#backup-folder-state").wait_for(state="visible", timeout=20000)


def _switch_on(page) -> None:
    """Click the switch's slider, as a person does (the box itself is hidden)."""
    if not page.locator("#backup-folder-enabled").is_checked():
        page.locator("#backup-folder-enabled + .toggle-slider").click()
    page.wait_for_function("document.querySelector('#backup-folder-enabled').checked", timeout=5000)


def _choose_with_the_picker(page, parent: Path, name: str) -> None:
    page.locator("#backup-folder-path").fill(str(parent))
    page.locator("#backup-folder-browse").click()
    picker = page.locator("#backup-folder-picker")
    picker.wait_for(state="visible", timeout=10000)
    picker.locator("button", has_text=name).first.click()
    page.wait_for_function(
        f"(document.querySelector('#backup-folder-picker bdi') || {{}}).textContent === {json.dumps(str(parent / name))}",
        timeout=10000)
    page.locator("#backup-folder-use").click()
    picker.wait_for(state="hidden", timeout=10000)
    assert page.locator("#backup-folder-path").input_value() == str(parent / name)


def _save_and_test(page, *, ready_words: str, test_words: str) -> None:
    _switch_on(page)
    page.locator("#backup-folder-keep").fill("3")
    page.locator("#backup-folder-save").click()
    page.wait_for_function(
        f"(document.querySelector('#backup-folder-state') || {{}}).innerText.includes({json.dumps(ready_words)})",
        timeout=15000)
    page.locator("#backup-folder-test").click()
    page.locator("#backup-folder-test-result").wait_for(state="visible", timeout=30000)
    page.wait_for_function(
        f"(document.querySelector('#backup-folder-test-result') || {{}}).innerText.includes({json.dumps(test_words)})",
        timeout=30000)


def test_the_backup_folder_in_a_browser(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    drive = tmp_path / "usb-disk"
    (drive / "KazmaBackups").mkdir(parents=True)
    (drive / "Photos").mkdir()
    errors: list[str] = []
    with unified_turn_server() as h:
        h: Harness
        host = h.base.split("//", 1)[1].split(":", 1)[0]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                pg = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US").new_page()
                pg.on("pageerror", lambda exc: errors.append(str(exc)))
                _open_backup(pg, h.base, "Backup")
                assert "Off" in pg.locator("#backup-folder-state").inner_text()

                # Switched on with no folder: refused in the page's words.
                _switch_on(pg)
                pg.locator("#backup-folder-save").click()
                pg.locator(".toast", has_text="Choose a folder before switching the copy on").first.wait_for(
                    state="visible", timeout=10000)

                _choose_with_the_picker(pg, drive, "KazmaBackups")
                _save_and_test(pg, ready_words="the next backup makes Kazma's copy",
                               test_words="Kazma can write there")
                saved = pg.evaluate("fetch('/api/settings/backup/folder').then(r => r.json())")
                assert saved["enabled"] is True
                assert saved["path"] == str(drive / "KazmaBackups") and saved["keep"] == 3
                _shot(pg, "en-ready")

                # Arabic, right to left, by the sidebar too.
                pg.evaluate("fetch('/api/settings/backup/folder', {method: 'PUT', headers: "
                            "{'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}, "
                            "body: JSON.stringify({enabled: false, path: ''})})")
                ctx = browser.new_context(viewport={"width": 1366, "height": 900})
                ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
                ar = ctx.new_page()
                ar.on("pageerror", lambda exc: errors.append(str(exc)))
                _open_backup(ar, h.base, "النسخ الاحتياطي")
                assert ar.evaluate("document.documentElement.dir") == "rtl"
                assert ar.locator("#backup-folder h3").inner_text() == "نسخة على قرص آخر"
                _choose_with_the_picker(ar, drive, "KazmaBackups")
                _save_and_test(ar, ready_words="تنشئ النسخة الاحتياطية التالية", test_words="يستطيع Kazma الكتابة فيه")
                _shot(ar, "ar-ready")
            finally:
                browser.close()
    assert errors == [], errors
