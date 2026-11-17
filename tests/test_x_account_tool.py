"""The X account's numbers through the official X API (x_account, 2026-11-17).

Live 2026-11-17, on WhatsApp: "check if there are new followers on Kazma's X
account". Kazma had no X read for the account, so the agent asked to open
x.com in the browser (an approval card for browser_navigate), and X answered
with a sign-in page. x_account reads GET /2/users/me with public_metrics,
says how each count changed since the last check, and with followers=true
lists the newest followers, marking those not seen last time.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kazma_core.x_api.client import XApiError
from kazma_skills.native.x_publisher import tools as t


class _Creds:
    def __init__(self, complete: bool = True) -> None:
        self._complete = complete

    def complete(self) -> bool:
        return self._complete


class _Cfg:
    def __init__(self, complete: bool = True) -> None:
        self.credentials = _Creds(complete)
        self.handle = "KazmaAI"


class _Store:
    def __init__(self) -> None:
        self.rows: dict = {}

    def get(self, key, default=None):
        return self.rows.get(key, default)

    def set(self, key, value):
        self.rows[key] = value


def _client(counts: dict, followers: list[dict] | Exception, calls: list[str]):
    class FakeClient:
        def __init__(self, creds) -> None:
            pass

        async def get_account(self):
            calls.append("account")
            return {"id": "42", "username": "KazmaAI", "name": "Kazma", "public_metrics": dict(counts)}

        async def get_followers(self, user_id, *, max_results=20):
            calls.append(f"followers:{user_id}")
            if isinstance(followers, Exception):
                raise followers
            return followers

    return FakeClient


@pytest.fixture
def x(monkeypatch):
    store = _Store()
    calls: list[str] = []
    monkeypatch.setattr(t, "get_x_config", lambda: _Cfg())
    monkeypatch.setattr(t, "_account_snapshot", lambda: dict(store.rows.get(t._ACCOUNT_KEY) or {}))
    monkeypatch.setattr(t, "_save_account_snapshot", lambda snap: store.set(t._ACCOUNT_KEY, snap))

    def use(counts, followers=()):
        monkeypatch.setattr("kazma_core.x_api.client.XClient",
                            _client(counts, followers if isinstance(followers, Exception) else list(followers), calls))

    return store, calls, use


def _ask(**kw) -> dict:
    return json.loads(asyncio.run(t.x_account(**kw)))


def _counts(followers, following=10, posts=100, listed=1):
    return {"followers_count": followers, "following_count": following, "tweet_count": posts, "listed_count": listed}


def test_the_counts_and_how_they_changed_since_the_last_check(x):
    store, calls, use = x
    use(_counts(120))
    first = _ask()
    assert first["ok"] and first["followers"] == 120 and first["first_check"] is True
    assert "changes" not in first
    use(_counts(127, following=11, posts=103))
    second = _ask()
    assert second["followers"] == 127
    assert second["changes"] == {"followers": 7, "following": 1, "posts": 3, "listed": 0}
    assert second["since"]
    assert calls == ["account", "account"], "counts alone are one read each, no followers list"


def test_new_followers_are_marked_against_the_list_seen_last_time(x):
    store, calls, use = x
    use(_counts(2), [{"id": "1", "username": "a"}, {"id": "2", "username": "b"}])
    first = _ask(followers=True)
    assert [p["new"] for p in first["newest_followers"]] == [False, False]
    assert "followers_note" in first
    use(_counts(3), [{"id": "3", "username": "c"}, {"id": "1", "username": "a"}, {"id": "2", "username": "b"}])
    second = _ask(followers=True)
    assert [(p["handle"], p["new"]) for p in second["newest_followers"]] == [("c", True), ("a", False), ("b", False)]
    assert calls[-1] == "followers:42"


def test_x_refusing_the_followers_list_keeps_the_counts_and_says_why(x):
    store, calls, use = x
    use(_counts(5), XApiError("forbidden", status=403))
    answer = _ask(followers=True)
    assert answer["ok"] and answer["followers"] == 5
    assert "follows lookup" in answer["followers_error"] and "403" in answer["followers_error"]
    assert "newest_followers" not in answer


def test_not_configured_reads_nothing(monkeypatch, x):
    store, calls, use = x
    use(_counts(1))
    monkeypatch.setattr(t, "get_x_config", lambda: _Cfg(complete=False))
    answer = _ask()
    assert answer["ok"] is False and "Settings" in answer["error"]
    assert calls == []


def test_the_tool_is_registered_read_tier_and_points_away_from_the_browser():
    from pathlib import Path

    import kazma_core.product_knowledge as knowledge
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import TOOL_TIERS

    tool = LocalToolRegistry()._tools.get("x_account")
    assert tool is not None, "x_account is not registered"
    assert TOOL_TIERS.get("x_account") == "read"
    description = str(getattr(tool, "description", ""))
    assert "followers" in description and "browser" in description
    # The product knowledge the model reads sends followers questions here.
    assert "`x_account`" in Path(knowledge.__file__).read_text(encoding="utf-8")


def test_control_before_this_there_was_no_x_read_for_followers():
    """Every X tool but x_account, as it stood: none reads the account's
    followers, so a followers question had nowhere to go but the browser."""
    from kazma_core.agent.tool_registry import LocalToolRegistry

    tools = LocalToolRegistry()._tools
    others = [n for n in tools if n.startswith("x_") and n != "x_account"]
    assert others
    assert not any("followers" in str(getattr(tools[n], "description", "")).lower()
                   and "account" in str(getattr(tools[n], "description", "")).lower()
                   for n in others)
