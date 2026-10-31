"""Job-owned Chromium rendering through the pinned acquisition network broker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from kazma_core.errors import redact_secrets
from kazma_core.web_acquire.browser_network import BrowserNetwork
from kazma_core.web_acquire.document import PAGE_TEXT_MAX
from kazma_core.web_acquire.parser import parse_document
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode, FetchResult
from kazma_core.web_acquire.scope import active_scope, require_scope
from kazma_core.web_acquire.trace import classify_failure

logger = logging.getLogger(__name__)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_DOM_CAP = 5_000_000  # the HTTP path's default body budget, so both read the same pages
_NATIVE_CONNECTION_CAP = 16


@dataclass
class RenderedDocument:
    result: FetchResult
    html: str = ""


class BrowserJob:
    """One lazy browser/context per job; pages and HTTP clients are per acquire.

    Native HTTP/HTTPS/WebSocket connections hit a job-owned deny endpoint.
    Only CDP Fetch fulfillment by BrowserNetwork obtains remote HTTP bytes.
    DNS/UDP suppression is defense in depth, not OS sandbox attestation.
    """

    def __init__(self):
        self.pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self._driver = None
        self._token: Token | None = None
        self._start_lock = asyncio.Lock()
        self._render_lock = asyncio.Lock()
        self._closed = False
        self._capacity_lease = None
        self._deny_server = None
        self._deny_writers: set = set()
        self._deny_tasks: set[asyncio.Task] = set()
        self._deny_closing = False
        self.native_denials = 0

    async def __aenter__(self):
        self._token = current_browser_job.set(self)
        return self

    def _native_connected(self, reader, writer):
        # A synchronous accept callback bounds task creation itself. An async
        # start_server callback would create a task for every incoming socket
        # before the coroutine could check a concurrency limit.
        self.native_denials += 1
        if self._deny_closing or len(self._deny_tasks) >= _NATIVE_CONNECTION_CAP:
            writer.close()
            return
        task = asyncio.create_task(self._deny_native(reader, writer))
        self._deny_tasks.add(task)
        task.add_done_callback(self._native_finished)

    def _native_finished(self, task):
        self._deny_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error('[browser] native deny handler failed', exc_info=(type(error), error, error.__traceback__))

    async def _deny_native(self, reader, writer):
        self._deny_writers.add(writer)
        try:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await asyncio.wait_for(writer.drain(), 2)
        except (OSError, TimeoutError):
            logger.debug("[browser] native fallback connection closed")
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                logger.debug('[browser] native fallback socket close ended')
            finally:
                self._deny_writers.discard(writer)

    async def _start_impl(self):
        async with self._start_lock:
            if self._closed:
                raise RuntimeError("Browser job scope has already closed.")
            if self.context is not None:
                return
            from kazma_core.web_acquire import resources

            if self._capacity_lease is None:
                self._capacity_lease = await resources.browser_capacity().acquire()
            try:
                await self._launch_impl()
            except BaseException:
                # Never let a later page overwrite retained handles after a
                # partial launch, especially when cleanup itself fails.
                self._closed = True
                await self._close_owned_impl()
                raise

    async def _launch_impl(self):
        from kazma_core.web_acquire.browser_driver import BrowserDriver

        self._deny_closing = False
        self._deny_server = await asyncio.start_server(self._native_connected, "127.0.0.1", 0)
        port = self._deny_server.sockets[0].getsockname()[1]
        self._driver = BrowserDriver()
        self.pw = await self._driver.start()
        self.browser = await self.pw.chromium.launch(
            headless=True, chromium_sandbox=True,
            proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"},
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage",
                  "--disable-quic", "--disable-background-networking",
                  "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"])
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080}, user_agent=_UA,
            locale="en-US", service_workers="block", accept_downloads=False)
        await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

    async def _close_owned_impl(self):
        try:
            await self._cleanup_owned_impl()
        except AcquisitionError:
            raise
        except Exception as exc:
            logger.error('[browser] owned endpoint/resource teardown failed', exc_info=True)
            raise AcquisitionError(FetchErrorCode.LIMIT,
                'Local browser resource cleanup failed; capacity remains quarantined. '
                'Check the browser cleanup error log before retrying.') from exc
        finally:
            # Only completed cleanup releases the lease, including partial
            # launch and native endpoint failures, not just Chromium errors.
            if self._capacity_lease is not None:
                self._capacity_lease.quarantine()

    async def _cleanup_owned_impl(self):
        forced = False
        if self._driver is not None:
            self._driver.cleanup_window()
            try:
                if not self._driver.ending:
                    async with asyncio.timeout(5):
                        await self._close_playwright_objects()
            except TimeoutError:
                forced = True
                logger.warning('[browser] graceful close exceeded five seconds; terminating owned process tree')
            finally:
                await self._driver.stop()
            self.context = self.browser = self.pw = None
            self._driver = None
        else:
            await self._close_playwright_objects()
        await self._close_endpoint()
        if forced:
            raise AcquisitionError(FetchErrorCode.LIMIT,
                'Local browser graceful cleanup exceeded five seconds; its contained process tree was terminated and capacity released.')

    async def _close_playwright_objects(self):
        objects = (("context", "close"), ("browser", "close"), ("pw", "stop"))
        for attr, method in objects:
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    await getattr(obj, method)()
                except Exception:
                    # Teardown is the exception boundary: still try the owning
                    # browser and driver when a child close fails unexpectedly.
                    logger.error("[browser] owned resource close failed; retain ownership until cleanup succeeds", exc_info=True)
                else:
                    setattr(self, attr, None)
                    if attr == "browser":
                        self.context = None
                    elif attr == "pw":
                        self.context = self.browser = None
        if self._driver is None and any(obj is not None for obj in (self.context, self.browser, self.pw)):
            if self._capacity_lease is not None:
                self._capacity_lease.quarantine()
            raise AcquisitionError(FetchErrorCode.LIMIT,
                "Local browser cleanup did not complete; its capacity remains quarantined. "
                "The native deny endpoint remains active. Check the browser cleanup error log before retrying.")

    async def _close_endpoint(self):
        self._deny_closing = True
        if self._deny_server is not None:
            self._deny_server.close()
        for writer in tuple(self._deny_writers):
            writer.close()
        if self._deny_tasks:
            await asyncio.gather(*tuple(self._deny_tasks))
        if self._deny_server is not None:
            await self._deny_server.wait_closed()
            self._deny_server = None
        if self.context is self.browser is self.pw is None and self._capacity_lease is not None:
            self._capacity_lease.release()
            self._capacity_lease = None

    async def _close_impl(self):
        async with self._start_lock:
            self._closed = True
            await self._close_owned_impl()

    async def __aexit__(self, *exc):
        from kazma_core.playwright_loop import finish_owned, run_in_browser_loop

        cleanup = asyncio.create_task(run_in_browser_loop(self._close_impl()))
        try:
            await finish_owned(cleanup)
        finally:
            if self._token is not None:
                try:
                    current_browser_job.reset(self._token)
                except ValueError:
                    # Async-generator finalization can happen in another task.
                    if current_browser_job.get() is self:
                        current_browser_job.set(None)
                self._token = None


current_browser_job: ContextVar[BrowserJob | None] = ContextVar("acquisition_browser_job", default=None)


async def render_document(
    url: str, *, challenge: Callable[[str, int], bool],
    profile: str = "generic",
) -> RenderedDocument:
    """Run on the dedicated browser loop; return a typed local render outcome."""
    owner = current_browser_job.get()
    if owner is None or owner._closed:
        async with BrowserJob() as owner:
            return await _render_in_job(owner, url, challenge=challenge, profile=profile)
    return await _render_in_job(owner, url, challenge=challenge, profile=profile)


async def _render_in_job(owner, url, *, challenge, profile):
    page = None
    broker = None
    final_url = None
    try:
        require_scope(url)
        async with owner._render_lock:
            await owner._start_impl()
            if owner._driver is not None:
                owner._driver.watch(asyncio.current_task())
            page = await owner.context.new_page()
            broker = BrowserNetwork(page, scope=active_scope.get())
            await broker.__aenter__()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # A bounded settling window lets ordinary SPA scripts run;
                # the outer acquisition deadline also covers this wait.
                await page.wait_for_timeout(1000)
                await broker.settle()
                if broker.failure is not None:
                    raise broker.failure
                final_url = page.url
                require_scope(final_url)
                snapshot = await page.evaluate("""cap => {
                    const html = document.documentElement?.outerHTML || '';
                    return {html: html.slice(0, cap), truncated: html.length > cap};
                }""", _DOM_CAP)
                html = snapshot["html"]
                parsed = await parse_document(html, final_url, profile=profile)
                text, text_capped = parsed['text'], parsed['truncated']
                cap = PAGE_TEXT_MAX
                if len(text) > cap:
                    text, text_capped = text[:cap], True
                if not text or len(text) < 80:
                    body = await page.evaluate("cap => (document.body?.innerText || '').slice(0, cap + 1)", cap)
                    if body and body.strip():
                        text_capped = len(body) > cap
                        text = body[:cap].strip()
                status = broker.root_status
                reasons = set(broker.reasons)
                if snapshot["truncated"]:
                    reasons.add("dom_snapshot_limit")
                if text_capped:
                    reasons.add("text_limit")
                links, complete = parsed['links'], parsed['links_complete']
                if profile == "knowledge":
                    text, extra_links, tab_reasons = await _expand_tabs(page, broker, text)
                    combined = tuple(dict.fromkeys((*links, *extra_links)))
                    if len(combined) > 2000:
                        reasons.add("link_limit")
                    links = combined[:2000]
                    reasons.update(tab_reasons)
                    reasons.update(broker.reasons)
                    require_scope(page.url)
                    if broker.failure is not None:
                        raise broker.failure
                    final_url = page.url
                login = parsed['login']
                code = (FetchErrorCode.LOGIN if login else FetchErrorCode.CHALLENGE if challenge(html, status or 200)
                        else FetchErrorCode.HTTP if status and status >= 400 else FetchErrorCode.EMPTY if not text else None)
                error = ("The page requires login; credentials were not supplied." if login else
                         "Chromium returned an access challenge." if code == FetchErrorCode.CHALLENGE else
                         f"Chromium returned HTTP {status}." if code == FetchErrorCode.HTTP else
                         "Chromium produced no readable content." if not text else None)
                result = FetchResult(
                    ok=code is None, url=url, text=text if code is None else "", backend="chromium",
                    final_url=final_url, http_status=status, error_code=code, error=error,
                    char_count=len(text) if code is None else 0,
                    truncated=bool(reasons), truncation_reasons=tuple(sorted(reasons)),
                    quality="failed" if code else "partial" if reasons else "usable",
                    links=links, links_complete=complete and not reasons,
                    wire_bytes=broker.wire if broker.requests and broker.wire_known else None,
                    decoded_bytes=broker.decoded if broker.requests else None,
                    meta={"proxy_used": broker.via_proxy, "browser_network": broker.evidence(),
                          "browser_process": owner._driver.evidence() if owner._driver is not None else None})
                return RenderedDocument(result, html)
            finally:
                owned_page = page
                page = None
                await _close_render_resources(owner, broker, owned_page)
    except asyncio.CancelledError:
        raise
    except Exception as raised:
        # The failure to report: the broker's or the driver's when they hold
        # the real cause (the handler's own name is not rebound: ruff F841).
        exc = raised
        if broker is not None and broker.failure is not None:
            exc = broker.failure
        exc = _driver_failure(owner) or exc
        code, transient = classify_failure(exc)
        # Playwright's timeout has no relationship to Python/HTTPX timeout.
        if type(exc).__module__.startswith("playwright") and type(exc).__name__ == "TimeoutError":
            code, transient = FetchErrorCode.TIMEOUT, True
        if isinstance(exc, ImportError) or "Executable doesn't exist" in str(exc):
            code = FetchErrorCode.UNAVAILABLE
        return RenderedDocument(FetchResult(
            ok=False, url=url, backend="chromium", final_url=final_url,
            http_status=broker.root_status if broker is not None else None,
            error_code=code, error=redact_secrets(str(exc))[:350], transient=transient,
            quality="failed", meta={"proxy_used": bool(broker and broker.via_proxy),
                                    "browser_network": broker.evidence() if broker is not None else None,
                                    "browser_process": owner._driver.evidence() if owner._driver is not None else None}))
    finally:
        if page is not None:
            await _close_render_resources(owner, broker, page)


def _driver_failure(owner):
    return owner._driver.transport.failure if owner._driver is not None else None


async def _close_render_resources(owner, broker, page):
    driver = owner._driver
    if driver is not None:
        driver.cleanup_window()
    if broker is not None:
        await broker.aclose()
    await page.close()
    # A failed finalizer must leave the native watchdog armed until the owner
    # joins its process tree. Retrying the same page outside this guard could
    # wait forever after a CDP/navigation failure.
    if driver is not None:
        driver.cleanup_done()


async def _expand_tabs(page, broker, initial):
    """Capture each lazy panel while it exists, instead of only the last tab.

    ARIA panel targets avoid repeating page chrome. Other supported tab
    controls retain whole snapshots rather than risk discarding changed code.
    No returned tool calls/scripts are executed; only bounded DOM controls.
    """
    from playwright.async_api import Error as BrowserError

    tabs = page.locator('[role="tab"], button[aria-selected], .tab-button, [data-tab]')
    count = await tabs.count()
    reasons = {"tab_limit"} if count > 12 else set()
    texts, links = [initial], []
    length = len(initial)
    seen = {initial}
    for i in range(min(count, 12)):
        try:
            tab = tabs.nth(i)
            panel = await tab.get_attribute("aria-controls", timeout=1500)
            navigations = broker.root_requests
            await tab.click(timeout=1500)
            await broker.settle()
            if broker.failure is not None:
                raise broker.failure
            require_scope(page.url)
            if broker.root_requests != navigations:
                from kazma_core.web_acquire.result import AcquisitionError

                raise AcquisitionError(FetchErrorCode.REDIRECT, "A documentation tab navigated to another document; panel extraction was refused.")
            snapshot = await page.evaluate("""arg => {
                const root = (arg.panel && document.getElementById(arg.panel)) || document.documentElement;
                const html = root?.outerHTML || '';
                return {html: html.slice(0, arg.cap), truncated: html.length > arg.cap};
            }""", {"panel": panel, "cap": _DOM_CAP})
            if snapshot["truncated"]:
                reasons.add("dom_snapshot_limit")
            parsed = await parse_document(snapshot['html'], page.url, profile='knowledge')
            text = parsed['text']
            if parsed['truncated']:
                reasons.add("text_limit")
            found, complete = parsed['links'], parsed['links_complete']
            links.extend(found)
            if not complete or len(links) > 2000:
                reasons.add("link_limit")
                links = links[:2000]
            if text and text not in seen:
                seen.add(text)
                remaining = PAGE_TEXT_MAX - length - 2
                texts.append(text[:max(0, remaining)])
                length += len(text) + 2
                if length > PAGE_TEXT_MAX:
                    reasons.add("text_limit")
                    break
        except BrowserError:
            reasons.add("tab_interaction_failed")
    return "\n\n".join(texts), tuple(links), reasons
