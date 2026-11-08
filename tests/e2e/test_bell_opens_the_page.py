"""A notification in the bell opens the page it is about, in a browser
(2026-11-08, the owner's request), reached by the sidebar like a person.

- A memory alert opens the Memory page.
- A model failover opens Settings on the Agent tab, where the backup model
  is set: a deep link through soft navigation lands on its tab (it opened
  the first tab: the page read the address before the push), and Back
  returns to the page before, its address untouched.
- Every item the bell reads carries a page of this site.
- Opening one marks it read: the badge counts the unread, a read problem
  stays (dimmed), read news leaves, "Mark all as read" clears the badge, and
  what was read stays read after a reload (the server keeps it).
- Arabic: the rows are links too, and "Mark all as read" is in Arabic.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
httpx = pytest.importorskip("httpx")

from tests.e2e.test_provider_settings_browser import kazma  # noqa: E402,F401 -- the server fixture

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _seed() -> None:
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.reset_state()
    AlertDispatcher.post_banner(subsystem="Memory", title="Memory recall degraded",
                                reason="3 failures in the last hour", severity="WARNING")
    AlertDispatcher.post_banner(subsystem="llm-failover:deepseek", title="Model failover: deepseek failed",
                                reason="The failover chain answered.", severity="WARNING")
    AlertDispatcher.post_banner(subsystem="Acquisition", title="Research completed",
                                reason="A report is saved.", severity="SUCCESS", link="/research")


def _badge(pg) -> str:
    """The bell's count, or "" when it is not shown."""
    dot = pg.locator("#notifications-bell .notification-count")
    return dot.inner_text().strip() if dot.is_visible() else ""


def _wait_badge(pg, expected: str) -> None:
    pg.wait_for_function(
        "(e) => { const d = document.querySelector('#notifications-bell .notification-count');"
        " const shown = d && d.offsetParent !== null ? d.textContent.trim() : ''; return shown === e; }",
        arg=expected, timeout=15000)


def _open_bell(pg, title: str):
    pg.locator("#notifications-bell").click()
    row = pg.locator(".notif-dropdown a.notif-item", has_text=title).first
    row.wait_for(state="visible", timeout=15000)
    return row


def test_a_notification_opens_its_page(kazma) -> None:  # noqa: F811
    from kazma_core.observability.alerts import AlertDispatcher
    from playwright.sync_api import sync_playwright

    base, _fake_url = kazma
    _seed()
    host = base.split("//", 1)[1].split(":", 1)[0]
    errors: list[str] = []
    try:
        items = httpx.get(f"{base}/api/alerts/recent", timeout=10).json()
        assert items and all(str(i.get("link", "")).startswith("/") for i in items), items
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                pg = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US").new_page()
                pg.on("pageerror", lambda exc: errors.append(str(exc)))
                pg.goto(base + "/", wait_until="domcontentloaded")
                pg.locator("a.nav-link[href='/dashboard']").first.click()
                pg.wait_for_function("location.pathname === '/dashboard'", timeout=15000)
                _wait_badge(pg, "3")

                # Memory health: the Memory page.
                row = _open_bell(pg, "Memory recall degraded")
                assert row.get_attribute("href") == "/memory"
                row.click()
                pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
                assert not pg.locator(".notif-dropdown").is_visible(), "the bell stayed open"
                _wait_badge(pg, "2")  # opened: read

                # A model failover: Settings, on the Agent tab. The memory
                # alert is still listed, read: a problem stays until fixed.
                failover = _open_bell(pg, "Model failover")
                memory_row = pg.locator(".notif-dropdown a.notif-item", has_text="Memory recall degraded").first
                assert "is-read" in (memory_row.get_attribute("class") or "")
                failover.click()
                pg.wait_for_function(
                    "location.pathname === '/settings' && location.search.includes('tab=agent')", timeout=15000)
                pg.wait_for_function(
                    "(document.querySelector('.settings-tab.active') || {getAttribute: () => ''})"
                    ".getAttribute('@click') === \"onTabChange('agent')\"", timeout=15000)
                # Back is the page it came from, its address as it was: the tab
                # switch ran before the soft navigation's push and rewrote it.
                pg.go_back()
                pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
                assert pg.evaluate("location.search") == ""

                # Replay's own deep link (the web chat's /replay answer), soft-
                # navigated the same way: it selects that chat's timeline.
                pg.route("**/api/replay/threads", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body='{"threads": ["t-linked"], "items": [{"thread_id": "t-linked", "title": "Linked chat"}]}'))
                pg.route("**/api/replay/snapshots/t-linked", lambda route: route.fulfill(
                    status=200, content_type="application/json", body='{"snapshots": [], "items": []}'))
                pg.evaluate("""() => { const a = document.createElement('a');
                    a.href = '/replay?thread=t-linked'; a.textContent = 'replay';
                    document.body.appendChild(a); a.click(); }""")
                pg.wait_for_function("location.pathname === '/replay'", timeout=15000)
                pg.wait_for_function(
                    "(document.getElementById('replay-thread-select') || {}).value === 't-linked'", timeout=15000)

                # "Mark all as read": the badge goes, read news leaves, the
                # problems stay dimmed; a reload keeps it (the server's).
                _wait_badge(pg, "1")
                _open_bell(pg, "Research completed")
                pg.locator("#notifications-mark-all").click()
                _wait_badge(pg, "")
                assert pg.locator(".notif-dropdown a.notif-item", has_text="Research completed").count() == 0
                assert pg.locator(".notif-dropdown a.notif-item.is-read").count() == 2
                pg.reload(wait_until="domcontentloaded")
                pg.wait_for_function("window.Alpine && Alpine.store('notifications').loaded", timeout=15000)
                assert _badge(pg) == ""
                read = {a["title"]: a["read"] for a in httpx.get(f"{base}/api/alerts/recent", timeout=10).json()}
                assert all(read.values()), read

                # Arabic: the rows are links, and a new alert is unread.
                AlertDispatcher.post_banner(subsystem="System", title="RAM high", reason="r", severity="WARNING")
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
                ar = ctx.new_page()
                ar.on("pageerror", lambda exc: errors.append(str(exc)))
                ar.goto(base + "/dashboard", wait_until="domcontentloaded")
                _wait_badge(ar, "1")
                assert _open_bell(ar, "Memory recall degraded").get_attribute("href") == "/memory"
                assert ar.locator("#notifications-mark-all").inner_text().strip() == "تعليم الكل كمقروء"
                assert ar.evaluate("document.documentElement.dir") == "rtl"
            finally:
                browser.close()
    finally:
        AlertDispatcher.reset_state()
    assert errors == [], errors
