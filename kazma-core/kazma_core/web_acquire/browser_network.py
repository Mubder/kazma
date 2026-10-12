"""Replace Chromium HTTP with bounded, pinned acquisition at the CDP boundary.

Playwright routes skip subsequent HTTP redirects. CDP Fetch Request-stage
interception reports each hop, including redirects, BEFORE network dispatch.
Never continue an HTTP request natively. A job-owned deny proxy is the fallback
for unsupported browser transports/targets. This is not an OS network sandbox.
"""

from __future__ import annotations

import asyncio
import base64
from contextvars import copy_context
from urllib.parse import urlsplit

from kazma_core.errors import redact_secrets
from kazma_core.web_acquire import http as acquisition_http
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_core.web_acquire.scope import AcquisitionScope
from kazma_core.web_acquire.trace import AttemptSpan, classify_failure


class BrowserNetwork:
    """One page's bounded HTTP broker; all state is owned by the browser loop."""

    MAX_REQUESTS = 128
    MAX_BYTES = 32_000_000
    MAX_RESPONSE = 5_000_000
    MAX_REDIRECTS = 5

    def __init__(self, page, *, scope: AcquisitionScope | None = None, allow_mutations: bool = False):
        self.allow_mutations = allow_mutations
        self.page, self.scope = page, scope
        # Playwright's connection reader can retain the launch caller's
        # context across many pages. Event tasks must use THIS acquisition's
        # trace/policy context, never that long-lived reader's first trace.
        self.call_context = copy_context()
        self.cdp = self.client = None
        self.pins: dict[str, str] = {}
        self.tasks: set[asyncio.Task] = set()
        self.sem = asyncio.Semaphore(4)
        self.requests = self.completed = self.decoded = self.wire = 0
        self.wire_known = True
        self.reserved = 0
        self.root_requests = 0
        self.redirect_depth: dict[str, int] = {}
        self.failures: list[dict] = []
        self.failure: BaseException | None = None
        self.reasons: set[str] = set()
        self.closed = False
        self.received = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.root_frame = ""
        self.root_url: str | None = None
        self.root_status: int | None = None
        self.via_proxy = False

    async def __aenter__(self):
        from kazma_core.proxy.client import get_active_proxy_url, get_scraping_client

        proxy = get_active_proxy_url()
        self.via_proxy = bool(proxy)
        self.client = get_scraping_client(
            proxy=proxy, pin_hosts=self.pins if not proxy else None,
            follow_redirects=False, timeout=20.0, trust_env=False,
            headers={"Accept-Encoding": "gzip, deflate"})
        await self.client.__aenter__()
        try:
            self.cdp = await self.page.context.new_cdp_session(self.page)
            tree = await self.cdp.send("Page.getFrameTree")
            self.root_frame = tree["frameTree"]["frame"]["id"]
            self.cdp.on("Fetch.requestPaused", self._received)
            await self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            await self.cdp.send("Network.enable")
            self.cdp.on("Network.webSocketCreated", lambda event: self.reasons.add("browser_websocket_denied"))
            await self.cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        except BaseException:
            await self.aclose()
            raise
        return self

    def _received(self, event):
        if not self.closed:
            self.idle.clear()
            self.received += 1
            if self.received > self.MAX_REQUESTS:
                # Bound queued tasks as well as dispatched requests. Closing
                # the owned page cancels all paused requests; native fallback
                # remains denied even if the CDP session detaches first.
                self.failure = AcquisitionError(FetchErrorCode.LIMIT, "Browser intercepted-request limit exceeded.")
                self.closed = True
                task = asyncio.create_task(self.page.close())
                self.tasks.add(task)
                task.add_done_callback(self._finished)
                return
            task = asyncio.create_task(self._handle(event), context=self.call_context.copy())
            self.tasks.add(task)
            task.add_done_callback(self._finished)

    def _finished(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            # Page-close/protocol failures can occur outside _handle's error
            # boundary. Observe and surface them even if the task completed
            # before aclose took its snapshot of pending work.
            self.failure = self.failure or error
            self.closed = True
        if not self.tasks:
            self.idle.set()

    async def settle(self):
        try:
            async with asyncio.timeout(10):
                while True:
                    await self.idle.wait()
                    await self.page.wait_for_timeout(200)
                    if self.idle.is_set():
                        return
        except TimeoutError:
            self.reasons.add("browser_network_unsettled")

    async def _handle(self, event):
        request_id = event["requestId"]
        root = event.get("resourceType") == "Document" and event.get("frameId") == self.root_frame
        url = event["request"]["url"]
        try:
            if self.closed:
                return
            scheme = urlsplit(url).scheme.lower()
            if scheme in ("about", "data", "blob"):
                # Local schemes have no network target. Never continue HTTP.
                await self.cdp.send("Fetch.continueRequest", {"requestId": request_id})
                return
            if scheme not in ("http", "https"):
                raise AcquisitionError(FetchErrorCode.SECURITY, "Browser transport is not admitted.")
            if root and self.scope is not None:
                self.scope.require(url)
            method = event["request"].get("method", "GET").upper()
            permitted = {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'} if self.allow_mutations else {'GET', 'HEAD'}
            if method not in permitted:
                raise AcquisitionError(FetchErrorCode.UNSUPPORTED_CONTENT,
                                       f"Read-only browser acquisition refused HTTP {method}.")
            self.requests += 1
            if self.requests > self.MAX_REQUESTS:
                raise AcquisitionError(FetchErrorCode.LIMIT, "Browser HTTP request limit exceeded.")
            if root:
                self.root_requests += 1
                if self.root_requests > self.MAX_REDIRECTS + 1:
                    raise AcquisitionError(FetchErrorCode.REDIRECT, "Browser main-navigation limit exceeded.")
            depth = self.redirect_depth.get(event.get("redirectedRequestId", ""), -1) + 1
            self.redirect_depth[request_id] = depth
            if depth > self.MAX_REDIRECTS:
                raise AcquisitionError(FetchErrorCode.REDIRECT, "Browser HTTP redirect limit exceeded.")
            async with self.sem:
                await self._fetch(event, root=root)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code, transient = classify_failure(exc)
            if len(self.failures) < 10:
                self.failures.append({"url": url, "error_code": code,
                                      "error": redact_secrets(str(exc))[:250], "transient": transient})
            if root or code in (FetchErrorCode.SECURITY, FetchErrorCode.SCOPE, FetchErrorCode.LIMIT, FetchErrorCode.REDIRECT):
                self.failure = self.failure or exc
            else:
                self.reasons.add("browser_request_failed")
            await self._fail(request_id)

    async def _fetch(self, event, *, root: bool):
        from kazma_core.security.ssrf import validate_url
        from kazma_core.web_acquire import dns_worker

        request = event["request"]
        url = request["url"]
        ips = await dns_worker.validate_public_url(url, validator=validate_url)
        if not ips:
            raise AcquisitionError(FetchErrorCode.SECURITY, "No public address for browser request.")
        if not self.via_proxy:
            self.pins[(urlsplit(url).hostname or "").lower()] = ips[0]
        # Reserve retained bytes before concurrent streams start. A response
        # may consume a smaller reservation; only its unused part is released.
        cap = min(self.MAX_RESPONSE, self.MAX_BYTES - self.decoded - self.reserved)
        if cap < 1:
            raise AcquisitionError(FetchErrorCode.LIMIT, "Browser decoded-byte limit exceeded.")
        headers = {str(k): str(v) for k, v in request.get("headers", {}).items()
                   if k.lower() not in {"host", "content-length", "connection", "proxy-authorization", "accept-encoding"}}
        headers["Accept-Encoding"] = "gzip, deflate"
        body = None
        if request.get('hasPostData') or 'postData' in request:
            if not self.allow_mutations or not isinstance(request.get('postData'), str) :
                raise AcquisitionError(FetchErrorCode.UNSUPPORTED_CONTENT, 'Opaque browser request bodies/uploads are refused.')
            body = request['postData'].encode('utf-8')
            entries = request.get('postDataEntries')
            if entries and b''.join(base64.b64decode(entry['bytes'], validate=True) for entry in entries) != body:
                raise AcquisitionError(FetchErrorCode.UNSUPPORTED_CONTENT, 'Browser body bytes cannot be preserved; upload refused.')
            if len(body) > 1024**2:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Browser request body exceeds its 1MiB budget.')
        self.reserved += cap
        try:
            with AttemptSpan("httpx", url, remote=self.via_proxy) as attempt:
                response, _, truncated = await acquisition_http.read_capped(
                    self.client, url, cap, validated_ips=ips, via_proxy=self.via_proxy,
                    require_peer=True, method=request.get("method", "GET"), request_headers=headers,
                    **({'request_body': body} if body is not None else {}),
                    observe_response=lambda r: attempt.response(r.status_code, final_url=url))
                size = len(response.content)
                self.decoded += size
                wire = response.extensions.get("kazma_wire_bytes")
                if wire is None:
                    self.wire_known = False
                else:
                    self.wire += wire
                self.completed += 1
                attempt.response(response.status_code, final_url=url, decoded_bytes=size,
                                 wire_bytes=wire, truncated=truncated)
                if truncated:
                    raise AcquisitionError(FetchErrorCode.LIMIT, "Browser HTTP response exceeded its byte reservation.")
                if root:
                    self.root_url, self.root_status = url, response.status_code
                    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if response.status_code not in (301, 302, 303, 307, 308) and mime and not (mime.startswith("text/") or mime in {
                        "application/xhtml+xml", "application/json", "application/xml"}):
                        raise AcquisitionError(FetchErrorCode.UNSUPPORTED_CONTENT,
                                               f"Browser document content type is unsupported: {mime}")
                elif response.status_code >= 400 and event.get("resourceType") in ("Script", "XHR", "Fetch", "Document"):
                    self.reasons.add("browser_resource_http_error")
                # Bytes have already been decoded, so remove wire framing. CDP
                # preserves repeated headers (especially Set-Cookie) and makes
                # redirects go through Request-stage interception again.
                is_head = request.get("method", "GET").upper() == "HEAD"
                excluded = {"transfer-encoding", "connection"}
                if not is_head:
                    excluded.update({"content-length", "content-encoding"})
                clean = [{"name": k, "value": v} for k, v in response.headers.multi_items()
                         if k.lower() not in excluded]
                if not is_head:
                    clean.append({"name": "content-length", "value": str(size)})
                await self.cdp.send("Fetch.fulfillRequest", {
                    "requestId": event["requestId"], "responseCode": response.status_code,
                    "responseHeaders": clean, "body": base64.b64encode(response.content).decode("ascii")})
        finally:
            self.reserved -= cap

    async def _fail(self, request_id):
        from playwright.async_api import Error as BrowserError

        if self.cdp is not None:
            try:
                await self.cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
            except BrowserError:
                # Closing the owned page/session has already ended this request.
                return

    def evidence(self) -> dict:
        return {"requests": self.requests, "completed": self.completed,
                "decoded_bytes": self.decoded, "wire_bytes": self.wire if self.wire_known else None,
                "byte_scope": "all brokered browser HTTP responses", "errors": list(self.failures),
                "limits": {"requests": self.MAX_REQUESTS, "decoded_bytes": self.MAX_BYTES,
                           "response_bytes": self.MAX_RESPONSE, "redirects": self.MAX_REDIRECTS}}

    async def aclose(self):
        self.closed = True
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Detach can wait indefinitely while a navigation is still paused.
        # Some event tasks may have been cancelled before their first step, so
        # failing requests only inside _handle is insufficient. Close the owned
        # page first to end EVERY pause; the job's native deny endpoint remains
        # active throughout. Page ownership never escapes this broker/renderer.
        from playwright.async_api import Error as BrowserError

        if not self.page.is_closed():
            try:
                await self.page.close()
            except BrowserError:
                if not self.page.is_closed():
                    raise
        if self.cdp is not None:
            try:
                await self.cdp.detach()
            except BrowserError:
                pass  # The owned page/session may already have closed.
            self.cdp = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def __aexit__(self, *exc):
        await self.aclose()
