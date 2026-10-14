"""The Scheduled page and the scheduling clarify card read in Arabic (Scheduled audit SCH-S04, S08).

The page tour (``test_pages_read_in_arabic.py``) sees the Scheduled page
empty. Here it holds a job in every status the scheduler gives -- pending,
running, delivering, undeliverable, missed, delivery unknown, cancelled,
done, failed -- and X posts booked and held for approval, and both tabs are
read with the Arabic cookie: no English, and no raw status id
("undeliverable", "delivery_unknown", "awaiting_approval") where a word
belongs. Until 2026-10-14 the page had no word for six of them and counted
every X row as done.

The clarify card a scheduling request raises is driven through a real turn:
the scripted model asks to cancel a job id that is not a live job, the
commitment gate pauses the turn with the live jobs as options, and the
Arabic chat page paints the card. Its question and options must be Arabic,
and each job must show its next time. The server sent English diagnostics
("job_id 'x' is not a pending job ...") and the options named no time.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, Script, Step, unified_turn_server  # noqa: E402
from tests.e2e.test_pages_load_clean import _settle  # noqa: E402
from tests.e2e.test_pages_read_in_arabic import english_on  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: Status ids that must never be shown as they are.
RAW_IDS = ("undeliverable", "delivery_unknown", "delivering", "missed", "cancelled", "pending",
           "running", "awaiting_approval", "review", "scheduled", "outcome_unknown",
           "failed_permanent", "managed")

ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


async def _seed_cron(db_path: str, rows: list[tuple]) -> None:
    from kazma_core.cron.store import ScheduledJob, SQLiteCronStore

    store = SQLiteCronStore(db_path=db_path)
    await store.init()
    now = datetime.now(UTC)
    try:
        for job_id, status, timing, kind, caps, next_run in rows:
            await store.insert(ScheduledJob(
                job_id=job_id, timing=timing, prompt="اتصل بطبيب الأسنان", platform="telegram",
                thread_id="t-ar", status=status, next_run=next_run, kind=kind, capabilities=caps,
                created_at=now.isoformat(), delivery_target="telegram:42",
                failure_count=3 if status == "failed" else 0))
    finally:
        await store.close()


def _every_status() -> list[tuple]:
    from kazma_core.cron.store import JobStatus

    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    return [
        ("cron-ar-pending", JobStatus.PENDING, "daily at 9am", "reminder", "read", later),
        ("cron-ar-task", JobStatus.PENDING, "2h", "task", "read_write", later),
        ("cron-ar-running", JobStatus.RUNNING, "1h", "reminder", "read", later),
        ("cron-ar-delivering", JobStatus.DELIVERING, "1h", "reminder", "read", later),
        ("cron-ar-undeliv", JobStatus.UNDELIVERABLE, "1h", "reminder", "read", ""),
        ("cron-ar-missed", JobStatus.MISSED, "1h", "reminder", "read", ""),
        ("cron-ar-unknown", JobStatus.DELIVERY_UNKNOWN, "1h", "reminder", "read", ""),
        ("cron-ar-cancelled", JobStatus.CANCELLED, "1h", "reminder", "read", ""),
        ("cron-ar-done", JobStatus.DONE, "1h", "reminder", "read", ""),
        ("cron-ar-failed", JobStatus.FAILED, "daily at 9am", "task", "read", ""),
    ]


def _seed_x(tmp: Path, monkeypatch) -> None:
    import time

    import kazma_core.x_api.config as config_mod
    import kazma_core.x_api.ledger as ledger_mod
    import kazma_core.x_api.policy as policy_mod
    import kazma_core.x_api.publication_service as service
    import kazma_core.x_api.publication_store as pub
    import kazma_core.x_api.schedule as schedule_mod
    from kazma_core.x_api.config import XConfig, XCredentials
    from kazma_core.x_api.ledger import XPostLedger
    from kazma_core.x_api.schedule import XScheduledStore

    ledger = XPostLedger(tmp / "x_posts_ar.db")
    cfg = XConfig(enabled=True, handle="@kazma", account_id="123",
                  credentials=XCredentials("k", "ks", "t", "ts"), max_posts_per_day=8,
                  max_posts_per_month=80, max_mentions=2, max_cashtags=1, max_hashtags=4,
                  max_chars=280, duplicate_window_days=30, kill_switch=False)
    sched = XScheduledStore(tmp / "x_sched_ar.db")

    def no_x(_cfg):
        raise AssertionError("this test must never reach X")

    monkeypatch.setattr(config_mod, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(policy_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(schedule_mod, "get_x_scheduled_store", lambda: sched)
    monkeypatch.setattr(service, "_client", no_x)
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    monkeypatch.delenv("KAZMA_X_POST", raising=False)
    pub._set_publication_store_for_tests(pub._PublicationStore(tmp / "x_pub_ar.db"))
    booked, first = service.schedule(text="منشور مجدول للغد", fire_at=time.time() + 7200)
    assert booked, first
    held_ok, second = service.schedule(text="منشور ثانٍ للمراجعة", fire_at=time.time() + 10800)
    assert held_ok, second
    assert pub.get_publication_store().hold(second["operation_id"], tenant_id="default", reason="review")


def _visible_raw_ids(pg) -> list[str]:
    """Raw ids in the page's own words. Content marked ``translate="no"``
    -- a job's prompt, a hold's stated reason -- is the job's, not a label."""
    text = pg.evaluate(
        "() => { const root = (document.querySelector('.sched-wrap') || document.body);"
        " const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n, out = '';"
        " while ((n = walk.nextNode())) { const el = n.parentElement;"
        "   if (!el || el.closest('[translate=\"no\"]') || !el.getClientRects().length) continue;"
        "   out += ' ' + n.textContent; }"
        " return out; }")
    return [raw for raw in RAW_IDS if re.search(rf"(?<![\w-]){re.escape(raw)}(?![\w-])", text)]


def _arabic_context(p, harness: Harness):
    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
    context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
    return browser, context


def test_the_scheduled_page_reads_in_arabic_in_every_status(harness: Harness, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_core.x_api import publication_store as pub

    asyncio.run(_seed_cron(str(Path(harness.data_dir) / "cron.db"), _every_status()))
    _seed_x(Path(harness.data_dir), monkeypatch)
    problems: list[str] = []
    try:
        with sync_playwright() as p:
            browser, context = _arabic_context(p, harness)
            try:
                pg = context.new_page()
                pg.goto(f"{harness.base}/scheduled", wait_until="domcontentloaded", timeout=30000)
                _settle(pg)
                pg.wait_for_function(
                    "() => { const d = Alpine.$data(document.querySelector('.sched-wrap'));"
                    " return d && d.tasks && d.tasks.length >= 12; }", timeout=30000)
                seen = pg.evaluate(
                    "() => Alpine.$data(document.querySelector('.sched-wrap')).tasks.map(t => t.status)")
                want = {"undeliverable", "missed", "delivery_unknown", "scheduled", "awaiting_approval"}
                assert want <= set(seen), seen
                for tab in ("upcoming", "history"):
                    pg.evaluate("(tab) => { Alpine.$data(document.querySelector('.sched-wrap')).filter = tab; }",
                                tab)
                    pg.wait_for_timeout(400)
                    problems += [f"{tab}: {t}" for t in english_on(pg)]
                    problems += [f"{tab}: raw status id {r!r}" for r in _visible_raw_ids(pg)]
            finally:
                context.close()
                browser.close()
    finally:
        pub._set_publication_store_for_tests(None)
    assert not problems, f"{len(problems)} problems on the Arabic Scheduled page:\n  " + "\n  ".join(problems)


@pytest.fixture
def cancel_harness() -> Iterator[Harness]:
    script = Script(steps=[Step(tool="cancel_scheduled", args={"job_id": "cron-not-a-job"})],
                    final="لم ألغِ شيئًا.")
    with unified_turn_server(script=script) as h:
        yield h


def test_the_cancel_clarify_card_reads_in_arabic_with_a_time(cancel_harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_core.cron.store import JobStatus
    from tests.e2e.test_unified_turn_browser import _send

    later = (datetime.now(UTC) + timedelta(days=1)).replace(hour=6, minute=30, second=0, microsecond=0)
    asyncio.run(_seed_cron(str(Path(cancel_harness.data_dir) / "cron.db"), [
        ("cron-ar-live", JobStatus.PENDING, "daily at 9:30am", "reminder", "read", later.isoformat()),
    ]))
    with sync_playwright() as p:
        browser, context = _arabic_context(p, cancel_harness)
        try:
            pg = context.new_page()
            pg.goto(f"{cancel_harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.evaluate("() => window.KazmaChat.newSession()")
            pg.wait_for_timeout(600)
            _send(pg, "ألغِ تذكير طبيب الأسنان")
            pg.wait_for_function("() => document.querySelectorAll('.hitl-sem-opt').length >= 2", timeout=120000)
            card = pg.locator(".hitl-approval-card").filter(has=pg.locator(".hitl-sem-opt")).last
            question = card.locator(".hitl-message").inner_text()
            labels = [b.inner_text() for b in card.locator(".hitl-sem-opt").all()]
            header = card.locator(".hitl-approval-header").inner_text()
            english = english_on(pg)
        finally:
            context.close()
            browser.close()
    assert ARABIC.search(question) and "job_id" not in question, question
    assert ARABIC.search(header), header
    job, keep = labels[0], labels[-1]
    assert ARABIC.search(job) and "cron-ar-live" in job, job
    assert re.search(r"\d{1,2}:\d{2}", job), job  # the job's next time is shown
    assert ARABIC.search(keep) and "cancel" not in keep.lower(), keep
    on_card = [t for t in english if any(t in s for s in (question, header, *labels))]
    assert on_card == [], on_card
