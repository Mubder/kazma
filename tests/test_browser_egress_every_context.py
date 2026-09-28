"""Every browser context Kazma drives gets the private-network guard.

``install_*_browser_egress`` de-duplicated by a set of ``id(context)``.
``read_url``'s hard-page fallback and the Knowledge crawler open a context
per fetch and close it afterwards; CPython hands a freed object's id to the
next allocation, so the next context was taken for already guarded and got
no route -- its redirects and subresources reached private addresses
unchecked (found 2026-09-28). The mark now lives on the context itself.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from kazma_core.security import browser_egress as be


class _Context:
    """The part of a Playwright context the guard uses."""

    def __init__(self) -> None:
        self.routes: list = []

    def route(self, pattern, handler) -> None:
        self.routes.append((pattern, handler))


class _AsyncContext(_Context):
    async def route(self, pattern, handler) -> None:  # type: ignore[override]
        self.routes.append((pattern, handler))


class _Unmarkable:
    __slots__ = ("routes",)

    def __init__(self) -> None:
        self.routes: list = []

    def route(self, pattern, handler) -> None:
        self.routes.append((pattern, handler))


def test_each_new_context_is_guarded_even_when_it_reuses_an_id():
    seen_ids: set[int] = set()
    reused = False
    for _ in range(200):
        ctx = _Context()
        reused = reused or id(ctx) in seen_ids
        seen_ids.add(id(ctx))
        be.install_sync_browser_egress(ctx)
        assert len(ctx.routes) == 1, "a fresh context went unguarded"
        del ctx
        gc.collect()
    # The situation the old code failed in did occur in this run.
    assert reused


def test_each_new_async_context_is_guarded():
    async def run() -> None:
        for _ in range(50):
            ctx = _AsyncContext()
            await be.install_async_browser_egress(ctx)
            assert len(ctx.routes) == 1
            del ctx
            gc.collect()

    asyncio.run(run())


def test_one_context_is_guarded_once():
    ctx = _Context()
    be.install_sync_browser_egress(ctx)
    be.install_sync_browser_egress(ctx)
    assert len(ctx.routes) == 1


def test_a_context_that_cannot_be_marked_is_guarded_every_time():
    ctx = _Unmarkable()
    be.install_sync_browser_egress(ctx)
    be.install_sync_browser_egress(ctx)
    assert len(ctx.routes) == 2


def test_a_route_that_fails_leaves_the_context_unmarked():
    class Broken(_Context):
        def route(self, pattern, handler) -> None:
            raise RuntimeError("context closed")

    ctx = Broken()
    with pytest.raises(RuntimeError):
        be.install_sync_browser_egress(ctx)
    assert not be._already_guarded(ctx)


def test_the_old_id_dedupe_skipped_a_context_that_reused_an_id():
    """Negative control: the dedupe as it was, given two contexts that share
    an id -- a closed one and the next, as CPython allocates them."""
    installed: set[int] = set()

    def old_install(context, _id) -> None:
        key = _id(context)
        if key in installed:
            return
        context.route("**/*", be._guard_sync)
        installed.add(key)

    first, second = _Context(), _Context()
    same_id = lambda _ctx: 1234  # noqa: E731
    old_install(first, same_id)
    old_install(second, same_id)
    assert len(first.routes) == 1
    assert second.routes == []  # the hole


def test_real_chromium_contexts_each_block_loopback():
    """Behaviour: three contexts in a row, each closed after use, as read_url
    opens them; every one refuses a loopback address."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001 -- no driver on this machine
        pytest.skip(f"playwright cannot start: {exc}")
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 -- browsers not installed
            pytest.skip(f"chromium is not installed: {exc}")
        try:
            for _ in range(3):
                context = browser.new_context()
                be.install_sync_browser_egress(context)
                page = context.new_page()
                with pytest.raises(sync_api.Error, match="ERR_BLOCKED_BY_CLIENT"):
                    page.goto("http://127.0.0.1:9/", timeout=10000)
                context.close()
                del context, page
                gc.collect()
        finally:
            browser.close()
    finally:
        pw.stop()
