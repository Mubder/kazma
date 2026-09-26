"""/api/github/status asks GitHub once per repository and token, not once per poll.

Found on the live install 2026-09-26: the workspace page polls the status
every 10 s, its OAuth check fetched it a second time on every tick, and each
status is three GitHub API calls -- about 2,000 calls an hour per open tab
against GitHub's 5,000 (60 without a token). The route also read the
workspace store and the vault on the event loop, and made its three calls one
after the other (3-4 s per status).

Now: one cached status per (owner, repo, token) for 30 s, fetched at most once
at a time; the store reads run in a thread; the two follow-up calls run
concurrently.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import kazma_gateway.routers.github as gh


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(gh, "_status_cache", {})
    monkeypatch.setattr(gh, "_status_inflight", {})


def _counting_fetch(calls: list[str], payload: dict | None = None, delay: float = 0.05):
    async def fetch():
        calls.append("fetch")
        await asyncio.sleep(delay)
        return dict(payload or {"is_github": True, "stars": 7})

    return fetch


async def test_tabs_asking_at_once_share_one_fetch() -> None:
    calls: list[str] = []
    key = gh._status_key("Mubder", "kazma", "tok")
    results = await asyncio.gather(*[gh._status_once(key, _counting_fetch(calls)) for _ in range(5)])
    assert calls == ["fetch"], calls
    assert all(r["stars"] == 7 for r in results)


async def test_a_status_is_reused_for_its_ttl_then_refetched(monkeypatch) -> None:
    calls: list[str] = []
    key = gh._status_key("Mubder", "kazma", "tok")
    await gh._status_once(key, _counting_fetch(calls))
    await gh._status_once(key, _counting_fetch(calls))
    assert calls == ["fetch"], "a poll inside the TTL asked GitHub again"
    monkeypatch.setattr(gh, "_STATUS_TTL_S", 0.0)
    await gh._status_once(key, _counting_fetch(calls))
    assert calls == ["fetch", "fetch"], "an expired status was served"


async def test_another_token_is_another_status() -> None:
    """Connecting (or changing the token) must not serve the old answer."""
    calls: list[str] = []
    await gh._status_once(gh._status_key("o", "r", ""), _counting_fetch(calls))
    await gh._status_once(gh._status_key("o", "r", "new-token"), _counting_fetch(calls))
    assert calls == ["fetch", "fetch"]
    assert gh._status_key("O", "R", "t") == gh._status_key("o", "r", "t")
    # The key holds a fingerprint of the token, never the token itself.
    fingerprint = gh._status_key("o", "r", "ghp_secret-token")[2]
    assert len(fingerprint) == 16 and "secret" not in fingerprint


async def test_a_failed_fetch_reaches_every_waiter_and_is_not_cached() -> None:
    key = gh._status_key("o", "r", "t")
    started = asyncio.Event()

    async def boom():
        started.set()
        await asyncio.sleep(0.05)
        raise RuntimeError("github down")

    leader = asyncio.create_task(gh._status_once(key, boom))
    await started.wait()
    waiter = asyncio.create_task(gh._status_once(key, boom))
    for task in (leader, waiter):
        with pytest.raises(RuntimeError, match="github down"):
            await task
    calls: list[str] = []
    await gh._status_once(key, _counting_fetch(calls))
    assert calls == ["fetch"], "a failure was cached"


# ── the fetch itself ─────────────────────────────────────────────────────


def _github(handler):
    """Route the module's httpx.AsyncClient through *handler*."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    return factory


async def test_the_follow_up_calls_run_concurrently(monkeypatch) -> None:
    """The PR count waits for the workflow call to START: sequential calls
    would never finish (the wait_for below fails the test instead)."""
    workflows_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls"):
            await asyncio.wait_for(workflows_started.wait(), timeout=5)
            return httpx.Response(
                200, json=[{}],
                headers={"Link": '<https://api.github.com/x?page=4>; rel="last"'},
            )
        if path.endswith("/actions/runs"):
            workflows_started.set()
            return httpx.Response(200, json={"workflow_runs": [
                {"name": "CI", "status": "completed", "conclusion": "success",
                 "html_url": "u", "event": "push", "head_branch": "main", "id": 9}]})
        return httpx.Response(200, json={
            "private": False, "stargazers_count": 3, "forks_count": 1,
            "open_issues_count": 10, "description": "d", "html_url": "h"})

    monkeypatch.setattr(gh.httpx, "AsyncClient", _github(handler))
    payload = await asyncio.wait_for(gh._fetch_status("o", "r", "tok", True), timeout=10)
    assert payload["open_prs"] == 4
    assert payload["open_issues"] == 6, "PRs are subtracted from GitHub's issue count"
    assert payload["latest_workflow"]["conclusion"] == "success"
    assert payload["stars"] == 3 and payload["has_token"] is True and payload["token_valid"] is True


async def test_an_error_status_is_a_payload_not_an_exception(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})

    monkeypatch.setattr(gh.httpx, "AsyncClient", _github(handler))
    payload = await gh._fetch_status("o", "r", "", False)
    assert payload["rate_limited"] is True and "60/hr" in payload["error"]


def test_the_workspace_page_refreshes_on_a_new_connection_only() -> None:
    """The page side: its OAuth check no longer re-fetches on every poll."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"
           / "workspace.html").read_text(encoding="utf-8")
    body = src[src.index("async loadOAuthStatus()"):src.index("async connectGitHub()")]
    assert "const wasAuthed = this.ghOAuthConnected || this.ghHasToken;" in body
    assert "&& !wasAuthed" in body
