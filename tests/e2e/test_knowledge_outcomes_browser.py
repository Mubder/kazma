"""What the Knowledge page says about a refused crawl and a partial one, in a
browser, through the real routes (Knowledge audit KB-S05, KB-S15).

* KB-S05: a refused crawl reaches the operator as its stable code, worded by
  the page from ``knowledge.err.<code>`` in the page's language -- never a
  generic "internal error", never an ERROR in the log, no library left behind.
* KB-S15: a crawl that finished with failed pages is never titled "Done";
  one that finished clean is (the control: the title follows the outcome).

Every visit arrives by the sidebar, as a person does (a soft navigation runs
the page's script with the previous page's address still in the bar).
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _arrive_at_knowledge(page, base: str) -> None:
    page.goto(base + "/memory", wait_until="domcontentloaded")
    page.wait_for_function("() => !!window.Alpine")
    page.evaluate("() => { window.__notReloaded = true; }")
    page.evaluate("() => document.querySelector('.sidebar .nav-links a[href=\"/knowledge\"]').click()")
    page.wait_for_function("() => location.pathname === '/knowledge'", timeout=20000)
    page.wait_for_function("() => !!document.getElementById('knowledge-root')", timeout=20000)
    assert page.evaluate("() => window.__notReloaded === true"), "arrived by a soft navigation"
    page.evaluate("""() => {
        window.__toasts = [];
        const show = window.showToast;
        window.showToast = (message, type, ms) => {
            window.__toasts.push([type, message]);
            return show ? show(message, type, ms) : undefined;
        };
    }""")


#: Seeds refused at admission, and the code each is refused with: only an
#: http(s) address is crawled (KB-S09), and never one on a private network
#: (2026-10-15: a loopback seed was admitted, its library created and left
#: empty, and the page said nothing).
_REFUSED_SEEDS = [
    ("file:///C:/Windows/win.ini", "invalid_url"),
    ("http://127.0.0.1:9/docs", "security_blocked"),
]


@pytest.mark.parametrize(("seed", "expected_code"), _REFUSED_SEEDS)
@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_refused_crawl_is_said_in_the_pages_language(language: str, seed: str, expected_code: str,
                                                       caplog) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_ui.i18n import TRANSLATIONS

    caplog.set_level(logging.INFO)
    with unified_turn_server() as h, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _arrive_at_knowledge(page, h.base)
            page.fill('input[x-model="form.id"]', "private-docs")
            page.fill('input[x-model="form.seed_url"]', seed)
            crawl = page.locator("button", has_text=TRANSLATIONS["knowledge.crawl_tree"][language])
            with page.expect_response(lambda r: r.url.endswith("/api/kb/ingest")) as answered:
                crawl.click()
            data = answered.value.json()
            assert data["ok"] is False and answered.value.status < 500, data
            code = data["code"]
            assert code == expected_code, data
            entry = TRANSLATIONS[f"knowledge.err.{code}"][language]
            expected = entry.replace("{reason}", data.get("error") or "")
            page.wait_for_function("() => window.__toasts.length > 0", timeout=10000)
            assert page.evaluate("() => window.__toasts") == [["error", expected]]
            libraries = page.evaluate("async () => (await (await fetch('/api/kb/libraries')).json()).libraries")
            assert not [lib for lib in libraries if lib.get("id") == "private-docs"], "no empty library"
            jobs = page.evaluate("async () => (await (await fetch('/api/kb/jobs')).json()).jobs")
            assert not [job for job in jobs if job.get("library_id") == "private-docs"], "no journal row"
            assert not errors, errors
        finally:
            browser.close()
    refusals = [r for r in caplog.records if r.levelno >= logging.ERROR and "kb" in r.name]
    assert not refusals, [r.getMessage() for r in refusals]


def _finished_crawl(outcome: str) -> str:
    """A finished crawl in the real journal: two pages stored, and with
    *outcome* ``partial`` a third that failed."""
    from types import SimpleNamespace

    from kazma_core.web_acquire import crawl_jobs

    seed = "https://docs.example.test/"
    pages = [seed + "a", seed + "b"] + ([seed + "c"] if outcome == "partial" else [])
    policy = dict(mode="local_only", providers=[], robots=True, scope="host", max_pages=10,
                  max_depth=2, delay_ms=300)
    job = crawl_jobs.start("docs", seed, policy, job_id=f"docs:{outcome}", kind="site")
    job.frontier(pages)
    for url in pages:
        job.begin_page(url)
        failed = url.endswith("c")
        receipt = SimpleNamespace(
            ok=not failed, url=url, error_code="http_error" if failed else None, backend="httpx",
            quality="ok", truncated=False,
            evidence=lambda: {"attempts": [], "status": 503 if failed else 200})
        job.receipt(receipt, url=url)
        job.end_page(url, error="http_error: 503" if failed else "", new=0 if failed else 3)
    job.finish(outcome)
    return job.job_id


@pytest.mark.parametrize("outcome", ["partial", "completed"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_partial_crawl_is_never_titled_done(language: str, outcome: str) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_ui.i18n import TRANSLATIONS

    with unified_turn_server() as h, sync_playwright() as p:
        _finished_crawl(outcome)
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _arrive_at_knowledge(page, h.base)
            done = TRANSLATIONS["knowledge.phase_done"][language]
            partial = TRANSLATIONS["knowledge.phase_partial"][language]
            want = partial if outcome == "partial" else done
            page.wait_for_function(
                "(want) => [...document.querySelectorAll('[x-text=\"jobTitle()\"]')]"
                ".some(el => el.textContent.trim() === want)",
                arg=want, timeout=20000,
            )
            titles = page.evaluate(
                "() => [...document.querySelectorAll('[x-text=\"jobTitle()\"]')].map(el => el.textContent.trim())")
            assert want in titles and (done not in titles if outcome == "partial" else True), titles
            assert not errors, errors
        finally:
            browser.close()
