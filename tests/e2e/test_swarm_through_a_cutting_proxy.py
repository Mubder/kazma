"""The Swarm page through a proxy that cuts long requests (Swarm audit SW-S08, SW-S09).

The live install sits behind Cloudflare Tunnel, which ends a request that
runs too long and re-chunks a stream into arbitrary pieces. The Swarm page
held its dispatch POST until the task ended, held its Approve POST until the
whole remaining pipeline ended, reported any approve answer as success, and
on every stream reconnect got the whole history again with no ids.

Here the real app runs behind :class:`CuttingProxy`: a ``/api/swarm`` reply
not started within ``CUT_S`` is answered 524 (the request is abandoned, as a
proxy does), a stream is ended ``CUT_S`` after it starts, and every stream
body is cut into small pieces. A worker takes several times ``CUT_S``. In
English and Arabic the page must: dispatch in the background and show the
result, reconnect the stream from its last id without a repeated line, and
approve a checkpoint (202) and see the resumed run finish.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

CUT_S = 2.0
SLOW_S = 5.0


class CuttingProxy:
    """ASGI wrapper: what a tunnel does to long ``/api/swarm`` requests."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.rng = random.Random(20261014)
        self.cut_streams = 0
        self.timed_out = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not path.startswith("/api/swarm"):
            await self.app(scope, receive, send)
            return
        started = asyncio.Event()
        ended = asyncio.Event()
        state = {"start": 0.0, "dead": False}

        async def _send(message: dict[str, Any]) -> None:
            if state["dead"]:
                return
            if message.get("type") == "http.response.start":
                started.set()
                state["start"] = time.monotonic()
                await send(message)
                return
            body = message.get("body") or b""
            more = bool(message.get("more_body", False))
            if path.endswith("/stream"):
                at = 0
                while at < len(body):
                    step = self.rng.randint(1, 30)
                    await send({"type": "http.response.body", "body": body[at:at + step],
                                "more_body": True})
                    at += step
                    await asyncio.sleep(0.002)
                if not more:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    ended.set()
                return
            await send(message)
            if not more:
                ended.set()

        run = asyncio.ensure_future(self.app(scope, receive, _send))
        try:
            await asyncio.wait_for(started.wait(), CUT_S)
        except TimeoutError:
            state["dead"] = True
            self.timed_out += 1
            run.cancel()
            await send({"type": "http.response.start", "status": 524,
                        "headers": [(b"content-type", b"text/html")]})
            await send({"type": "http.response.body", "body": b"<html>A timeout occurred</html>"})
            return
        if path.endswith("/stream"):
            done, _ = await asyncio.wait({run, asyncio.ensure_future(ended.wait())},
                                         timeout=max(0.0, CUT_S - (time.monotonic() - state["start"])),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not ended.is_set() and not run.done():
                state["dead"] = True
                self.cut_streams += 1
                run.cancel()
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
        await run


def _workers(engine: Any) -> None:
    from kazma_core.swarm.config import WorkerConfig

    for name in ("fast", "slow"):
        if engine.get_worker(name) is None:
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def fast(task, context=None):
        return {"worker": "fast", "task_id": "", "status": "success", "output": "fast done", "error": None}

    async def slow(task, context=None):
        await asyncio.sleep(SLOW_S)
        return {"worker": "slow", "task_id": "", "status": "success", "output": "slow done", "error": None}

    engine.get_worker("fast").dispatch = fast
    engine.get_worker("slow").dispatch = slow


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_swarm_page_survives_a_cutting_proxy(language: str) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_core.swarm.engine import get_swarm_engine

    with unified_turn_server(asgi_wrapper=CuttingProxy) as h:
        engine = get_swarm_engine()
        assert engine is not None
        _workers(engine)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.goto(h.base + "/swarm")
                page.wait_for_function("() => !!window.Alpine && !!window.KazmaSwarm")
                page.evaluate("""() => {
                    window.__toasts = [];
                    const store = Alpine.store('toast');
                    const add = store.add.bind(store);
                    store.add = (m, t, d) => { window.__toasts.push([t, m]); return add(m, t, d); };
                }""")

                # ── a dispatch that outlives the proxy's cut ──
                page.select_option("#pattern-select", "dispatch")
                page.wait_for_selector('#worker-checkboxes input[value="slow"]', state="attached", timeout=15000)
                page.check('#worker-checkboxes input[value="slow"]')
                page.fill("#dispatch-task", "outlive the proxy")
                page.click("#btn-dispatch")
                page.wait_for_function(
                    "() => [...document.querySelectorAll('[id^=status-]')].some("
                    "el => el.className.includes('badge-success'))",
                    timeout=(SLOW_S + 15) * 1000,
                )
                card_id = page.evaluate(
                    "() => [...document.querySelectorAll('[id^=status-]')].find("
                    "el => el.className.includes('badge-success')).id.replace('status-', '')")
                lines = page.evaluate(
                    "(id) => [...document.getElementById('events-' + id).children].map(n => n.textContent)",
                    card_id,
                )
                assert len(lines) == len(set(lines)), f"a reconnect repeated a line: {lines}"
                assert h.app.cut_streams >= 1, "the proxy cut the stream (the instrument ran)"
                assert not [m for t, m in page.evaluate("() => window.__toasts") if t == "error"]

                # ── an approve whose resumed run outlives the cut ──
                # A pipeline that pauses at once (its first step is fast).
                import httpx

                paused = httpx.post(h.base + "/api/swarm/dispatch", json={
                    "workers": ["fast", "slow"], "task": "approve through the proxy",
                    "pattern": "pipeline", "timeout": 60, "metadata": {"hitl_checkpoints": [1]}},
                    timeout=30).json()
                assert paused["result_status"] == "paused", paused
                task_id = paused["task_id"]
                page.evaluate("""(id) => {
                    const b = document.createElement('button');
                    b.dataset.action = 'approve'; b.dataset.taskId = id; b.id = 'probe-approve';
                    document.body.appendChild(b);
                }""", task_id)
                page.locator("#probe-approve").dispatch_event("click")
                page.wait_for_function("() => window.__toasts.length > 0", timeout=10000)
                kinds = [t for t, _m in page.evaluate("() => window.__toasts")]
                assert "error" not in kinds and "success" in kinds, page.evaluate("() => window.__toasts")
                deadline = time.monotonic() + SLOW_S + 15
                while time.monotonic() < deadline:
                    done = engine.get_task(task_id)
                    if done is not None and done.result is not None and done.result.status == "success":
                        break
                    time.sleep(0.1)
                done = engine.get_task(task_id)
                assert done is not None and done.result.status == "success", "the resumed run finished"
                # The old page held its dispatch and approve requests until
                # the work ended: the proxy would have answered them 524.
                assert h.app.timed_out == 0, "a page request outlived the proxy's cut"
                assert not errors, errors
            finally:
                context.close()
                browser.close()

