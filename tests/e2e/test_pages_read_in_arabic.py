"""Every page reads in Arabic when the reader chose Arabic (2026-09-28).

``docs/FEATURES.md`` promises an English and Arabic interface on every page.
Touring the live install in Arabic found the Memory page almost entirely in
English, and English on nearly every other page: the IDE's toolbar, the
Documents page, the chat's composer bar, the header's tooltips, "msgs" and
"ago" under every chat. The catalog tests (``tests/test_i18n.py``) check
that each KEY has an Arabic value; nothing checked that a page USES a key.

This loads every page the navigation links to, and every Settings tab, with
the Arabic cookie, and lists each visible text, placeholder, title and
aria-label that is English: no Arabic letter, and a word that is not a name
or an acronym. What sits inside ``translate="no"`` -- the HTML attribute for
what must not be translated: the user's own words, file names, a tool's own
documentation -- is content, not interface, and is skipped, as are ``code``,
``pre``, ``kbd``, form values and the conversation's own words (a message's
text is the speakers', in whatever language they wrote).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)
from tests.e2e.test_pages_load_clean import (  # noqa: E402
    _NAV_JS,
    _SETTINGS_TABS_JS,
    _settle,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: Names are not translated: products, companies, platforms and protocols,
#: and unit symbols (one list with the static template gate).
from tests._ui_names import UI_NAMES as NAMES  # noqa: E402

#: Every English piece of interface on the page, as "text" or "attr: value".
_ENGLISH_JS = r"""(names) => {
  const NAMES = new Set(names);
  const ARABIC = /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/;
  const english = (s) => {
    if (!s || ARABIC.test(s)) return false;
    for (const raw of s.split(/\s+/)) {
      const tok = raw.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, '');
      if (!/^[A-Za-z]{2,}$/.test(tok)) continue;      // identifiers, numbers
      if (tok === tok.toUpperCase()) continue;         // acronyms: JSON, MCP
      if (NAMES.has(tok)) continue;
      return true;
    }
    return false;
  };
  // The conversation's words are the speakers', not the interface's.
  const content = (el) => el.closest(
    '[translate="no"], code, pre, kbd, samp, script, style, noscript, textarea, option, .message-text');
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const found = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const el = n.parentElement;
    if (!el || content(el) || !shown(el)) continue;
    // bidi.js wraps each Latin run of an Arabic sentence in its own span
    // ("... عبر <span class="bidi-isolate">Playwright</span>."): the run is
    // judged with the sentence it belongs to.
    let sentence = el;
    while (sentence.classList.contains('bidi-isolate') && sentence.parentElement) sentence = sentence.parentElement;
    if (sentence !== el && ARABIC.test(sentence.textContent)) continue;
    const text = n.textContent.replace(/\s+/g, ' ').trim();
    if (english(text)) found.add(text.slice(0, 90));
  }
  document.querySelectorAll('[placeholder], [title], [aria-label]').forEach((el) => {
    if (el.closest('[translate="no"], .message-text') || !shown(el)) return;
    for (const a of ['placeholder', 'title', 'aria-label']) {
      const v = (el.getAttribute(a) || '').replace(/\s+/g, ' ').trim();
      if (english(v)) found.add(a + ': ' + v.slice(0, 90));
    }
  });
  // A dropdown's choices are read when it opens: every option of a shown
  // <select> counts, not only the selected one.
  document.querySelectorAll('select').forEach((sel) => {
    if (sel.closest('[translate="no"]') || !shown(sel)) return;
    for (const opt of sel.options) {
      if (opt.closest('[translate="no"]')) continue;
      const v = (opt.textContent || '').replace(/\s+/g, ' ').trim();
      if (english(v)) found.add('option: ' + v.slice(0, 90));
    }
  });
  return [...found];
}"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def english_on(pg) -> list[str]:
    return pg.evaluate(_ENGLISH_JS, sorted(NAMES))


def test_every_page_reads_in_arabic(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            first = context.new_page()
            first.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            first.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            assert first.evaluate("() => document.documentElement.dir") == "rtl"
            pages = first.evaluate(_NAV_JS)
            first.close()
            tabs_page = context.new_page()
            tabs_page.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
            _settle(tabs_page)
            tabs = tabs_page.evaluate(_SETTINGS_TABS_JS)
            tabs_page.close()
            assert len(pages) >= 10 and len(tabs) >= 10, (pages, tabs)
            # The login page is not in the navigation; a visitor sees it first.
            for path in pages + ["/login"] + [f"/settings?tab={t}" for t in tabs]:
                pg = context.new_page()
                pg.goto(f"{harness.base}{path}", wait_until="domcontentloaded", timeout=30000)
                if path == "/login":
                    # A page of its own: no app shell, no Alpine.
                    pg.wait_for_load_state("load", timeout=30000)
                else:
                    _settle(pg)
                problems += [f"{path}: {text}" for text in english_on(pg)]
                pg.close()
        finally:
            context.close()
            browser.close()
    assert not problems, f"{len(problems)} English strings on Arabic pages:\n  " + "\n  ".join(problems)


def test_a_chat_turn_reads_in_arabic(harness: Harness) -> None:
    """A turn, paused at its approval card and then finished, in Arabic.

    The page tour above sees what a page shows on load; a turn's own
    interface -- the approval card and its buttons, the turn header's
    phase, a message's action buttons, its time, the chat list's line --
    only appears while one runs. On 2026-09-28 all of those were English
    for an Arabic reader: "Approve once", "Deny", "Completed", "Copy",
    "11:32 am", "web · 2 msgs · just now". The harness's scripted turn asks
    for four approvals; every one is approved here with a real click.
    """
    from playwright.sync_api import sync_playwright

    from tests.e2e.test_unified_turn_browser import PROMPT, _send, _wait_for_pending_row

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.evaluate("() => window.KazmaChat.newSession()")
            pg.wait_for_timeout(800)
            # Typing "/" opens the command menu: twenty descriptions that were
            # English in every language until 2026-09-28.
            pg.fill("#chat-input", "/")
            pg.wait_for_function(
                "() => { const m = document.getElementById('chat-slash-menu');"
                " return !!m && m.style.display === 'block'"
                " && m.querySelectorAll('.chat-slash-item').length >= 10; }",
                timeout=10000,
            )
            problems += [f"slash menu: {t}" for t in english_on(pg)]
            pg.fill("#chat-input", "")
            _send(pg, PROMPT)
            _wait_for_pending_row(pg)
            # One live Approve: the card is on the page, measured as it stands.
            pg.wait_for_function(
                "() => document.querySelectorAll("
                "'.turn-approvals .hitl-approve:not([disabled])').length === 1",
                timeout=30000,
            )
            problems += [f"paused: {t}" for t in english_on(pg)]
            for _ in range(8):
                live = pg.locator(".turn-approvals .hitl-approve:not([disabled])")
                if live.count() == 0:
                    break
                live.first.click()
                pg.wait_for_timeout(1500)
            pg.wait_for_function(
                "() => { const h = document.querySelector('.turn-header');"
                " return !!h && h.className.indexOf('is-completed') >= 0; }", timeout=120000)
            pg.wait_for_timeout(800)
            problems += [f"finished: {t}" for t in english_on(pg)]
        finally:
            context.close()
            browser.close()
    assert not problems, f"{len(problems)} English strings in an Arabic chat turn:\n  " + "\n  ".join(problems)


def test_research_results_read_in_arabic(harness: Harness) -> None:
    """The Research page with runs on it, in Arabic -- and dated right.

    The tour above sees the harness's pages empty. On the live install
    (2026-09-28) an Arabic reader's Research list said "session · done ·
    done · 3 sources · rubric 100 passed · 5h ago" and "[Paper] ...", and a
    run's detail "Session · done · Stage: done · Sources: 3". A run's topic,
    report path and progress text are the run's own words and stay as they
    are. The Archived tab also dated every session 21 January 1970, in every
    language: the sessions API gives epoch seconds and the card read them as
    milliseconds.
    """
    from playwright.sync_api import sync_playwright

    from kazma_core.tools import research_session as rs

    deep = rs.create_session("What does the AgentDojo prompt-injection benchmark measure?", depth="deep")
    rs.update_session(
        deep.id, status="done", stage="done", sources=3, rubric_score=100.0, rubric_ok=True,
        summary="AgentDojo measures how often an agent follows injected instructions.",
        report_path="research/reports/agentdojo.md",
    )
    failed = rs.create_session("Kuwait AI adoption in 2026", depth="brief")
    rs.update_session(failed.id, status="error", stage="acquire", sources=2,
                      message="Search provider unavailable", error="Search provider unavailable")
    chat = rs.create_session("Latest LangGraph release notes", depth="chat")
    rs.update_session(chat.id, status="done", stage="complete", sources=4,
                      rubric_score=62.0, rubric_ok=False, summary="LangGraph adds durable streams.")
    old = rs.create_session("Vector store choices for a single install", depth="deep")
    rs.update_session(old.id, status="done", stage="done", sources=5)
    rs.archive_session(old.id, True)

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            pg = context.new_page()
            pg.goto(f"{harness.base}/research", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            pg.wait_for_function(
                "() => document.querySelectorAll('#research-list .card').length >= 3", timeout=30000)
            problems += [f"list: {t}" for t in english_on(pg)]
            pg.locator(f'#research-list .card[data-task-id="session:{deep.id}"]').click()
            pg.wait_for_function(
                "() => /\\S/.test((document.getElementById('research-detail-meta') || {}).textContent || '')",
                timeout=30000)
            pg.wait_for_timeout(500)
            problems += [f"detail: {t}" for t in english_on(pg)]
            pg.evaluate("() => window.KazmaResearch.switchTab('archived')")
            pg.wait_for_function(
                "() => document.querySelectorAll('#research-archived-list .card').length >= 1", timeout=30000)
            problems += [f"archived: {t}" for t in english_on(pg)]
            context.close()

            english = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            english.add_cookies([{"name": "kazma-lang", "value": "en", "domain": host, "path": "/"}])
            pe = english.new_page()
            pe.goto(f"{harness.base}/research", wait_until="domcontentloaded", timeout=30000)
            _settle(pe)
            pe.evaluate("() => window.KazmaResearch.switchTab('archived')")
            pe.wait_for_function(
                "() => document.querySelectorAll('#research-archived-list .card').length >= 1", timeout=30000)
            archived_text = pe.locator("#research-archived-list").inner_text()
            english.close()
        finally:
            browser.close()
    assert not problems, f"{len(problems)} English strings on the Arabic Research page:\n  " + "\n  ".join(problems)
    assert "1970" not in archived_text, archived_text


_SWARM_TABS = ("task-history", "results-dashboard", "templates", "worker-registry", "workflow-editor")


def test_swarm_and_scheduled_read_in_arabic(harness: Harness) -> None:
    """The Swarm and Scheduled pages with runs on them, in Arabic.

    On the live install (2026-09-28) the Swarm history said "dispatch ·
    auto · • success", the results board "success" / "failed", the task
    detail the same, the templates' Edit / Delete buttons were English, and
    the Scheduled page's X activity said "success" for every call. A task's
    prompt, a worker's output, a template's own words and a post's text are
    content and stay as they are. With the old swarm.js and scheduled.html
    this found 59 English strings (the templates' expertise tags and
    prompts among them: content the old markup never marked).
    """
    from playwright.sync_api import sync_playwright

    from kazma_core.swarm.task import (
        SwarmTask,
        TaskResult,
        TaskStatus,
        TaskType,
        WorkerResult,
    )
    from kazma_core.swarm.task_store import TaskStore
    from kazma_core.x_api.audit import log_x_event, reset_x_audit

    # The X audit log is one object per process, bound to the data dir of
    # its first use: an earlier harness's, deleted with it. Rebind it to
    # this harness's; the app reads through the same singleton.
    reset_x_audit()
    store = TaskStore()
    try:
        done = SwarmTask(prompt="In one sentence: what is idempotency in HTTP APIs?",
                         workers=["auto"], status=TaskStatus.COMPLETED)
        done.result = TaskResult(
            task_id=done.id, status="success", duration_seconds=3.1, total_cost=0.0012,
            worker_results=[WorkerResult(worker="auto", task_id=done.id, status="success",
                                         output="Idempotency means repeating a request changes nothing more.")],
            aggregated_output="Idempotency means repeating a request changes nothing more.",
        )
        store.persist_task(done)
        failed = SwarmTask(prompt="Explain a circuit breaker in two sentences.",
                           workers=["auto"], type=TaskType.PIPELINE, status=TaskStatus.FAILED)
        failed.result = TaskResult(
            task_id=failed.id, status="failed", duration_seconds=1.4, error="worker timed out",
            worker_results=[WorkerResult(worker="auto", task_id=failed.id, status="error",
                                         output="", error="worker timed out")],
        )
        store.persist_task(failed)
    finally:
        store.close()
    log_x_event(action="read_mentions", method="GET", endpoint="/2/users/1/mentions",
                status="success", http_status=200, duration_ms=421)
    log_x_event(action="create_tweet", method="POST", endpoint="/2/tweets", status="error",
                http_status=429, response_body="Too Many Requests", duration_ms=310)

    # A reminder in the app's own cron store (the web route refuses to book
    # one without a Telegram delivery address, which the harness has not).
    import asyncio
    from pathlib import Path

    from kazma_core.cron.scheduler import ScheduledJob, SQLiteCronStore

    async def _book() -> None:
        cron = SQLiteCronStore(str(Path(harness.data_dir) / "cron.db"))
        await cron.init()
        await cron.insert(ScheduledJob(
            job_id="cron-e2e-arabic", timing="2036-01-01T09:00:00", prompt="Renew the kazma.ai domain",
            platform="web", thread_id="", next_run="2036-01-01T09:00:00+00:00"))
        await cron.close()

    asyncio.run(_book())

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/swarm", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            for tab in _SWARM_TABS:
                pg.click(f'.tab[data-tab="{tab}"]')
                if tab == "task-history":
                    pg.wait_for_function(
                        "() => document.querySelectorAll('#history-table-body tr[data-task-id]').length >= 2",
                        timeout=30000)
                elif tab == "results-dashboard":
                    pg.wait_for_function(
                        "() => document.querySelectorAll('#results-dashboard-list .result-card').length >= 2",
                        timeout=30000)
                elif tab == "templates":
                    pg.wait_for_function(
                        "() => document.querySelectorAll('#template-cards-container .card').length >= 1",
                        timeout=30000)
                else:
                    pg.wait_for_timeout(600)
                problems += [f"swarm {tab}: {t}" for t in english_on(pg)]
            pg.click('.tab[data-tab="task-history"]')
            pg.locator("#history-table-body tr[data-task-id]").first.click()
            pg.wait_for_function(
                "() => { const m = document.getElementById('task-detail-modal');"
                " return !!m && m.style.display !== 'none' && /\\S/.test(m.textContent); }",
                timeout=30000)
            pg.wait_for_timeout(500)
            problems += [f"swarm task detail: {t}" for t in english_on(pg)]
            pg.close()

            pg = context.new_page()
            pg.goto(f"{harness.base}/scheduled", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            for tab in ("upcoming", "history", "x"):
                pg.click(f'.tabs [role="tab"]:nth-child({ {"upcoming": 1, "history": 2, "x": 3}[tab] })')
                if tab == "upcoming":
                    pg.wait_for_function(
                        "() => document.querySelectorAll('.sched-table tbody tr').length >= 1", timeout=30000)
                elif tab == "x":
                    pg.wait_for_function(
                        "() => [...document.querySelectorAll('.sched-table tbody tr')]"
                        ".some(r => /read_mentions/.test(r.textContent))",
                        timeout=30000)
                else:
                    pg.wait_for_timeout(600)
                problems += [f"scheduled {tab}: {t}" for t in english_on(pg)]
        finally:
            context.close()
            browser.close()
    assert not problems, (
        f"{len(problems)} English strings on the Arabic Swarm/Scheduled pages:\n  " + "\n  ".join(problems)
    )


def test_negative_control_the_instrument_tells_interface_from_content() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(
                '<button title="Search (Ctrl+K)">بحث</button>'
                '<span>web · 3 msgs · 1h ago</span>'
                '<span>خوادم MCP</span><span>Kazma</span><span>JSON</span>'
                '<span>deepseek-flash</span><span>1.2M</span>'
                '<span translate="no">What is my code word?</span>'
                '<code>file_read</code><kbd>Ctrl</kbd>'
                '<span style="display:none">Hidden</span>'
                '<select><option>Choose a model</option><option translate="no">Default model</option></select>'
                '<p>أتمتة المتصفح عبر <span class="bidi-isolate">Playwright</span>.</p>'
                '<p>توليد <span class="bidi-isolate">PDF و<span class="bidi-isolate">Markdown</span></span>.</p>'
                '<p><span class="bidi-isolate">Stays English</span></p>'
                '<div class="message"><div class="message-text">What the model said <b title="A tip">x</b></div></div>'
            )
            found = english_on(pg)
        finally:
            browser.close()
    assert sorted(found) == [
        "Stays English", "option: Choose a model", "title: Search (Ctrl+K)", "web · 3 msgs · 1h ago",
    ], found
