"""Settings -> Restore settings backup, in a browser (2026-10-01).

The owner downloads a backup, a setting changes, and the backup file is
picked: the page shows what will change (no value, nothing written yet),
writes the confirmed plan, offers a restart (declined here: it would stop the
test's server), and offers to undo the restore -- which puts the setting back
as it was. The same file read in Arabic shows the preview in Arabic.
Screenshots of the preview and the undo offer go to pytest's tmp_path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

KEY = "memory.v2.summaries_min_turns"

_SAVE = """([k, v, c]) => window.kazmaSave('/api/settings/single',
    {method: 'PUT', body: {key: k, value: v, category: c}})"""
_READ = """(k) => fetch('/api/settings').then((r) => r.json()).then((all) => {
    for (const cat of Object.values(all)) { if (k in cat) return cat[k]; }
    return null;
})"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _answer(pg, button: str) -> str:
    """The open dialog's text, then press *button* in it. (The Settings page
    holds other, hidden modals: the dialog is the one with a message.)"""
    dialog = pg.locator(".modal", has=pg.locator(".confirm-message"))
    dialog.wait_for(timeout=15000)
    text = dialog.locator(".confirm-message").inner_text()
    dialog.locator(".modal-footer button", has_text=button).click()
    pg.wait_for_function(
        "(t) => { const el = document.querySelector('.modal .confirm-message');"
        " return !el || el.innerText !== t; }",
        arg=text, timeout=10000,
    )
    return text


def test_a_backup_restores_and_undoes_through_the_page(harness: Harness, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/settings?tab=system", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.kazmaSave && !!window.Alpine", timeout=30000)
            pg.evaluate(_SAVE, [KEY, 4, "memory"])
            backup = pg.evaluate("() => fetch('/api/settings/system/backup').then((r) => r.text())")
            assert "kazma_settings_backup: 1" in backup and f"{KEY}: 4" in backup
            pg.evaluate(_SAVE, [KEY, 9, "memory"])
            file = tmp_path / "kazma-settings-2026-10-01.yaml"
            file.write_text(backup, encoding="utf-8")

            pg.set_input_files("#settings-restore-file", str(file))
            pg.locator(".modal .confirm-message").wait_for(timeout=15000)
            pg.screenshot(path=str(tmp_path / "restore-preview.png"))
            preview = _answer(pg, "Restore")
            assert KEY in preview and "kazma-settings-2026-10-01.yaml" in preview, preview
            assert "Nothing is deleted" in preview, preview

            restart = _answer(pg, "Cancel")  # the restart offer: declined
            assert "read when Kazma starts" in restart, restart
            assert pg.evaluate(_READ, KEY) == 4

            undo = pg.locator("button", has_text="Undo last restore")
            undo.wait_for(state="visible", timeout=10000)
            pg.screenshot(path=str(tmp_path / "restore-undo-offer.png"))
            undo.click()
            text = _answer(pg, "Undo")
            assert KEY in text, text
            _answer(pg, "Cancel")  # the restart offer again
            assert pg.evaluate(_READ, KEY) == 9
            pg.locator("button", has_text="Undo last restore").wait_for(state="hidden", timeout=10000)
        finally:
            context.close()
            browser.close()


def test_the_preview_reads_in_arabic(harness: Harness, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/settings?tab=system", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.kazmaSave && !!window.Alpine", timeout=30000)
            assert pg.evaluate("() => document.documentElement.lang") == "ar"
            pg.evaluate(_SAVE, [KEY, 4, "memory"])
            backup = pg.evaluate("() => fetch('/api/settings/system/backup').then((r) => r.text())")
            pg.evaluate(_SAVE, [KEY, 9, "memory"])
            file = tmp_path / "backup.yaml"
            file.write_text(backup, encoding="utf-8")
            pg.set_input_files("#settings-restore-file", str(file))
            pg.locator(".modal .confirm-message").wait_for(timeout=15000)
            pg.screenshot(path=str(tmp_path / "restore-preview-ar.png"))
            text = _answer(pg, "إلغاء")
            assert "الإعدادات التي ستتغير" in text and "لا يُحذف شيء" in text, text
            assert pg.evaluate(_READ, KEY) == 9, "a cancelled preview writes nothing"
        finally:
            context.close()
            browser.close()
