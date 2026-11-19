"""Settings -> Packages over parts that did not install, in a browser, reached
by the sidebar, in English and in Arabic (2026-11-19).

The owner's fresh Full install had no Docker, SearXNG, Neo4j or document
worker, and every row of Packages was green: the page listed Python extras
only. Here the install's record says the same four parts failed, and so did
the part of one extra whose Python packages this checkout has (read from the
page's own data). The page names the parts in a warning, and that extra's row
is a warning, never "Installed".

Set ``KAZMA_TEST_SHOTS`` to a folder to keep screenshots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

FAILED = ("docker", "document_container", "private_search", "graph_neo4j")


def _installed_extra(base: str) -> tuple[str, str]:
    """An extra whose packages this checkout has, and the part that brings it."""
    extras = httpx.get(f"{base}/api/system/packages", timeout=60).json()["extras"]
    for extra in extras:
        if extra.get("installed") and extra.get("part") and extra["part"] not in FAILED:
            return extra["name"], extra["part"]
    pytest.skip("no optional extra installed here")


def _record(also_failed: str) -> dict:
    from kazma_core.install_components import full_ids

    chosen = full_ids()
    failed = set(FAILED) | {also_failed}
    return {"install": {"mode": "full", "at": 1763550000, "without": [], "components": chosen,
                        "results": {cid: {"ok": cid not in failed} for cid in chosen}}}


def _open_packages(page, base: str, tab_label: str) -> None:
    """Home, then Settings by the sidebar, then the Packages tab."""
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=20000)
    page.locator("nav.settings-nav button.settings-tab", has_text=tab_label).click()
    page.locator("#pkg-not-in-place").wait_for(state="visible", timeout=30000)


def _shot(page, name: str) -> None:
    folder = os.environ.get("KAZMA_TEST_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(700)
        page.locator("#pkg-not-in-place").scroll_into_view_if_needed()
        page.screenshot(path=str(Path(folder) / f"{name}.png"))


def _badge(page, extra: str) -> str:
    row = page.locator(".pkg-extra-row", has=page.locator(".pkg-extra-slug", has_text=f"[{extra}]"))
    return row.locator(".pkg-badge").inner_text().strip()


def test_packages_names_the_parts_not_in_place() -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with unified_turn_server() as h:
        extra, part = _installed_extra(h.base)
        Path(h.data_dir, "setup_record.json").write_text(json.dumps(_record(part)), encoding="utf-8")
        from kazma_core.install_components import by_id
        host = h.base.split("//", 1)[1].split(":", 1)[0]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                pg = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US").new_page()
                pg.on("pageerror", lambda exc: errors.append(str(exc)))
                _open_packages(pg, h.base, "Packages")
                notice = pg.locator("#pkg-not-in-place").inner_text()
                for title in ("Docker", "Locked document worker", "Private web search (SearXNG)",
                              "Neo4j memory graph", by_id(part).title):
                    assert title in notice, notice
                assert "kazma setup install" in notice
                assert _badge(pg, extra) == "Part not in place"
                _shot(pg, "packages-en")

                ctx = browser.new_context(viewport={"width": 1366, "height": 900})
                ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
                ar = ctx.new_page()
                ar.on("pageerror", lambda exc: errors.append(str(exc)))
                _open_packages(ar, h.base, "الحزم")
                assert ar.evaluate("document.documentElement.dir") == "rtl"
                assert "لم يكتمل تثبيت" in ar.locator("#pkg-not-in-place").inner_text()
                assert _badge(ar, extra) == "الجزء غير مكتمل"
                _shot(ar, "packages-ar")
            finally:
                browser.close()
    assert errors == [], errors
