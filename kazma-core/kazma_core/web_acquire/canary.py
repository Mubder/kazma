"""The daily crawl canary: a real Knowledge crawl, told the day it fails.

Every crawl bug of 2026-10-31 reached the owner as a failed crawl of their
own library -- the robots claim the vault swallowed, a sitemap past the
parser's bound, a page cut at 100,000 characters -- days after it shipped.
Once a day (counted from its last run, ``observability.cadence``) Kazma
crawls one known page the product's own way: admission, robots with its
durable claim in the live settings store, the fetch ladder, the parser
child, chunking, publication and the journal -- into a private Knowledge
store under the data folder (``<data dir>/canary``: never a library the owner
sees, kept in place of the install's by ``knowledge_ingest._PRIVATE_KNOWLEDGE``).
It passes when the page was stored (unchanged, after the first day) and its
stored text holds the marker words.

A second leg renders the same page in Kazma's own browser (2026-11-13): the
crawl reads a plain page over HTTP and reaches Chromium only when that fails,
so a broken browser -- the driver, its contained process tree, the request
broker -- went unseen until a page that needs JavaScript. The leg is
``render`` in the outcome: ``passed``, ``failed`` (the canary fails), or
``skipped`` with why (the browser part not installed, or a host that refuses
to start it: ``browser_isolation.browser_unavailable``).

A failure is an ops alert at once (``acquisition.canary_failed``, error),
naming what failed; the first pass after one says so
(``acquisition.canary_recovered``). Its journal row (kind ``canary``) is
removed when it ends, so it never takes a place in the crawl history, and
the monitor and the Knowledge page do not show it.

Settings (``acquisition.canary_settings``): ``enabled`` (default on), ``url``
(default RFC 2606 at rfc-editor.org, a text that has not changed since 1999)
and ``marker`` (words the page holds). The outcome is ``acquisition.canary``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["metric_lines", "run_canary", "settings", "save_settings", "validate_settings", "start", "stop",
           "state"]

LIBRARY = "kazma-canary"
_INTERVAL_S = 24 * 3600.0
#: A first run waits this long after start (the server answers first).
_SETTLE_S = 600.0
_CADENCE_KEY = "system.acquisition.canary_ran"
_STATE_KEY = "acquisition.canary"
_SETTINGS_KEY = "acquisition.canary_settings"
_CATEGORY = "acquisition_canary"
#: The longest one canary crawl may take.
_DEADLINE_S = 600.0
#: The longest the browser leg may take (a cold Chromium start included).
_RENDER_DEADLINE_S = 180.0
_DEFAULTS = dict(enabled=True, url="https://www.rfc-editor.org/rfc/rfc2606.html",
                 marker="Reserved Top Level DNS Names")
_TASK: asyncio.Task | None = None


def validate_settings(value: Any) -> dict[str, Any]:
    from kazma_core.stores.knowledge import KnowledgeUrlRefused, validate_web_url

    if not isinstance(value, dict) or set(value) != set(_DEFAULTS):
        raise ValueError("Expected enabled, url and marker.")
    if type(value["enabled"]) is not bool:
        raise ValueError("enabled must be a boolean.")
    try:
        url = validate_web_url(value["url"])
    except (KnowledgeUrlRefused, TypeError) as exc:
        raise ValueError(f"url: {exc}") from None
    marker = value["marker"]
    if not isinstance(marker, str) or not 1 <= len(marker.strip()) <= 200:
        raise ValueError("marker must be 1 to 200 characters.")
    return dict(enabled=value["enabled"], url=url, marker=marker.strip())


def settings() -> dict[str, Any]:
    """The canary's settings (a blocking settings read: call it in a thread)."""
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get(_SETTINGS_KEY, {})
    return validate_settings({**_DEFAULTS, **raw} if isinstance(raw, dict) else raw)


def save_settings(value: Any) -> dict[str, Any]:
    from kazma_core.config_store import get_config_store

    value = validate_settings(value)
    get_config_store().set(_SETTINGS_KEY, value, category=_CATEGORY)
    return value


def state() -> dict[str, Any]:
    """The last outcome: ``ok``, ``ran_at``, ``reason``, ``failing_since``."""
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get(_STATE_KEY, {})
    return dict(raw) if isinstance(raw, dict) else {}


def _private_knowledge():
    """The canary's own Knowledge store and index (``<data dir>/canary``)."""
    from kazma_core.paths import data_dir
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import knowledge_index_for

    folder = data_dir() / "canary"
    folder.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(db_path=str(folder / "knowledge.db"))
    return store, knowledge_index_for(store)


def _ensure_library(store, url: str) -> None:
    """The canary's one library, made once, after the crawl was admitted. A
    later day finds the page unchanged: fetch, robots, parsing, chunking and
    the comparison with the stored page all run; only an unchanged write is
    skipped, as for any refresh."""
    if store.get_library(LIBRARY) is None:
        store.create_library(LIBRARY, "Kazma canary", seed_url=url)


def _judge(store, projection: dict[str, Any] | None, url: str, marker: str) -> str:
    """What failed, or '' when the canary passed."""
    if projection is None:
        return "the crawl left no record in the journal"
    pages = projection.get("pages") or []
    page = next((p for p in pages if p["url"].rstrip("/") == url.rstrip("/")), pages[0] if pages else None)
    if projection["acquisition"]["status"] != "completed":
        detail = f"{page['outcome']}: {page['reason'] or page.get('error', '')}" if page else projection.get("message", "")
        return f"the crawl ended {projection['acquisition']['status']} ({detail})"
    if page is None or page["outcome"] not in ("ingested", "unchanged"):
        return f"the page was not stored ({page['outcome'] if page else 'never reached'})"
    text = " ".join(" ".join(str(chunk.get(field) or "") for field in ("document_title", "section_header", "content"))
                    for chunk in store.list_chunks(LIBRARY, limit=500))
    if marker.lower() not in text.lower():
        return f"the stored text does not hold {marker!r}"
    return ""


async def _public_check(url: str) -> None:
    """The check the crawl's fetch makes before it connects (the browser leg's
    seam for tests: the crawl keeps its own)."""
    from kazma_core.web_acquire.dns_worker import validate_public_url

    await validate_public_url(url)


async def _render_leg(url: str, marker: str) -> tuple[str, str]:
    """The page rendered in Kazma's browser: ``("passed", "")``,
    ``("skipped", why)`` or ``("failed", what)``."""
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.tools.read_url import _looks_like_bot_block
    from kazma_core.web_acquire.browser import render_document
    from kazma_core.web_acquire.browser_isolation import browser_unavailable
    from kazma_core.web_acquire.result import AcquisitionError

    why_not = await asyncio.to_thread(browser_unavailable)
    if why_not:
        return "skipped", why_not
    try:
        await _public_check(url)
        document = await run_in_browser_loop(render_document(url, challenge=_looks_like_bot_block),
                                             timeout=_RENDER_DEADLINE_S)
    except TimeoutError:
        return "failed", f"Kazma's browser did not render it in {int(_RENDER_DEADLINE_S)} s"
    except AcquisitionError as exc:
        return "failed", f"Kazma's browser was refused ({exc.code.value}): {exc}"
    result = document.result
    if not result.ok:
        code = result.error_code.value if result.error_code else "failed"
        return "failed", f"Kazma's browser did not render it ({code}: {result.error or 'no text'})"
    if marker.lower() not in (result.text or "").lower():
        return "failed", f"the page Kazma's browser rendered does not hold {marker!r}"
    return "passed", ""


async def run_canary(*, alert=None) -> dict[str, Any]:
    """One canary crawl; records and announces the outcome, returns it."""
    from kazma_core.bounded_cancel import wait_for_bounded
    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeLibraryGone, KnowledgeStorageFull
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.result import AcquisitionError

    if alert is None:
        from kazma_core.observability.ops_alerts import alert
    config = await asyncio.to_thread(settings)
    started = time.time()
    store, index = await asyncio.to_thread(_private_knowledge)
    job_id = f"canary:{int(started)}"
    reason = ""
    token = ki._PRIVATE_KNOWLEDGE.set((store, index))
    try:
        async def crawl() -> None:
            prepared = await ki._prepare_ingest_site(LIBRARY, config["url"], max_pages=1, job_id=job_id,
                                                     scope="exact")
            # Its own kind: never in the crawl history, the monitor or a resume.
            await crawl_jobs.call(prepared[0].update, lambda row: row.update(kind="canary"))
            await asyncio.to_thread(_ensure_library, store, config["url"])
            async for _update in ki.ingest_site(LIBRARY, config["url"], max_pages=1, job_id=job_id,
                                                _prepared=prepared):
                pass

        await wait_for_bounded(crawl(), _DEADLINE_S, what="crawl canary", cancel_timeout=30.0)
        projection = await crawl_jobs.call(crawl_jobs.projection, job_id)
        reason = await asyncio.to_thread(_judge, store, projection, config["url"], config["marker"])
    except TimeoutError:
        reason = f"the crawl did not finish in {int(_DEADLINE_S)} s"
    except AcquisitionError as exc:
        reason = f"refused ({exc.code.value}): {exc}"
    except (KnowledgeStorageFull, KnowledgeLibraryGone) + store_errors() as exc:
        # The stores refused: what broke is the canary's news. A programming
        # error ends the daily task loudly instead, and the weekly report
        # counts the days it did not run.
        logger.warning("[acquisition] canary could not run", exc_info=True)
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        ki._PRIVATE_KNOWLEDGE.reset(token)
        try:
            await crawl_jobs.call(crawl_jobs.purge_terminal, kinds={"canary"})
        except AcquisitionError:  # a leftover row is evicted with the history
            logger.warning("[acquisition] the canary's journal row could not be removed", exc_info=True)
        await asyncio.to_thread(store.close)
    render, render_reason = await _render_leg(config["url"], config["marker"])
    if render == "failed":
        reason = f"{reason}; and {render_reason}" if reason else render_reason
    return await asyncio.to_thread(_record, reason, started, config["url"], alert,
                                   {"status": render, "reason": render_reason})


def _record(reason: str, started: float, url: str, alert, render: dict[str, str] | None = None) -> dict[str, Any]:
    from kazma_core.config_store import get_config_store
    from kazma_core.observability.cadence import stamp_run

    before = state()
    now = time.time()
    outcome = dict(ok=not reason, ran_at=now, seconds=round(now - started, 1), url=url, reason=reason,
                   failing_since=None if not reason else before.get("failing_since") or now,
                   render=render or {"status": "skipped", "reason": "not run"})
    get_config_store().set(_STATE_KEY, outcome, category=_CATEGORY)
    stamp_run(_CADENCE_KEY, now)
    if reason:
        logger.warning("[acquisition] canary failed: %s", reason)
        alert("acquisition.canary_failed", "The daily crawl check failed",
              f"Kazma crawled {url} and {reason}. Knowledge crawls may be failing the same way; "
              "check the server log and the Knowledge page.", severity="error", cooldown_s=20 * 3600.0)
    else:
        render_note = outcome["render"]["status"]
        if render_note == "skipped":
            render_note += ": " + outcome["render"]["reason"]
        logger.info("[acquisition] canary passed in %.1f s (browser leg %s)", outcome["seconds"], render_note)
        if before.get("ok") is False:
            alert("acquisition.canary_recovered", "The daily crawl check passes again",
                  f"It had failed since {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(before.get('failing_since') or now))}: "
                  f"{before.get('reason', '')}", severity="info")
    return outcome


def metric_lines() -> list[str]:
    """``/metrics``: whether the last daily check passed, and when it ran."""
    last = state()
    if not last.get("ran_at"):
        return []
    return ["# HELP kazma_acquisition_canary_ok 1 when the last daily crawl check passed",
            "# TYPE kazma_acquisition_canary_ok gauge",
            f"kazma_acquisition_canary_ok {int(bool(last.get('ok')))}",
            "# HELP kazma_acquisition_canary_last_run_seconds When the daily crawl check last ran (epoch)",
            "# TYPE kazma_acquisition_canary_last_run_seconds gauge",
            f"kazma_acquisition_canary_last_run_seconds {float(last['ran_at']):.0f}"]


async def _loop() -> None:
    from kazma_core.db import availability
    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.observability.cadence import seconds_until_due, stamp_run
    from kazma_core.web_acquire.result import AcquisitionError

    while True:
        # Before the stamp is read too: an unreadable stamp means "due", so a
        # read during an outage would run the day's check again once it ends.
        if await availability.wait_while_away():
            return
        wait = await asyncio.to_thread(seconds_until_due, _CADENCE_KEY, _INTERVAL_S, settle_s=_SETTLE_S)
        await asyncio.sleep(wait)
        if await availability.wait_while_away():
            return
        try:
            if (await asyncio.to_thread(settings))["enabled"]:
                await run_canary()
            else:  # switched off: asked again tomorrow
                await asyncio.to_thread(stamp_run, _CADENCE_KEY)
        except (AcquisitionError, ValueError) + store_errors():  # the next day's run is still scheduled
            logger.warning("[acquisition] canary pass failed to run", exc_info=True)
            await asyncio.sleep(3600)


def start() -> None:
    """Start the daily canary (not under pytest: ``background_schedulers_enabled``)."""
    global _TASK
    from kazma_core.background import spawn_background
    from kazma_core.memory.worker_bootstrap import background_schedulers_enabled

    if background_schedulers_enabled() and (_TASK is None or _TASK.done()):
        _TASK = spawn_background(_loop(), name="acquisition-canary", never_completes=True)


async def stop() -> None:
    global _TASK
    from kazma_core.bounded_cancel import cancel_and_wait

    if _TASK is not None:
        task, _TASK = _TASK, None
        await cancel_and_wait(task, what="acquisition canary", timeout=30.0)
