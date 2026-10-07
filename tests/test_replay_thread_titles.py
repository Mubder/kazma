"""The Replay page's chat picker names each thread by its chat.

``GET /api/replay/threads`` listed bare thread ids; on the live install the
picker was 131 uuids, and nobody could tell which chat a step history
belonged to (2026-09-28). The route now adds ``items``: the chat's title,
platform and last activity from the chat store, newest first, threads no
chat owns last -- and keeps ``threads`` (the ids the ownership test walks).

The same helper (``thread_ownership.chats_by_thread``) feeds the Dashboard's
session table; a store that only answers thread lookups yields no titles
and the picker shows the id, as before.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MINE_NEW = "t-new"
MINE_OLD = "t-old"
ORPHAN = "t-orphan"
OTHER = "t-other"


class _Session:
    def __init__(self, session_id: str, thread_id: str, title: str, updated_at: str, platform: str = "web") -> None:
        self.session_id, self.thread_id, self.title = session_id, thread_id, title
        self.updated_at, self.platform = updated_at, platform

    def to_summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "title": self.title,
            "platform": self.platform,
            "updated_at": self.updated_at,
            "archived": False,
        }


class _Store:
    """The tenant's chats: two bound to threads with snapshots, one to a
    thread with none. ``OTHER`` belongs to another tenant."""

    def __init__(self, listing: bool = True) -> None:
        self.listing = listing
        self.sessions = [
            _Session("s-old", MINE_OLD, "Trip to Muscat", "2026-09-01T10:00:00+00:00"),
            _Session("s-new", MINE_NEW, "Plan the launch", "2026-09-28T09:00:00+00:00", "telegram"),
            _Session("s-none", "t-no-snapshots", "No steps yet", "2026-09-27T09:00:00+00:00"),
        ]

    def get_by_thread_id(self, thread_id: str) -> Any:
        if thread_id in (MINE_NEW, MINE_OLD, ORPHAN):
            return object()
        return None

    def thread_is_exclusive(self, thread_id: str) -> bool:
        return thread_id in (MINE_NEW, MINE_OLD, ORPHAN)

    def list_all(self, include_archived: bool = False, *, include_empty: bool = False, prune_empty: bool = True):
        if not self.listing:
            raise AssertionError("list_all must not be called")
        return list(self.sessions)


class _NoListStore(_Store):
    """A store without a chat list: the ownership test's shape."""

    list_all = None  # type: ignore[assignment]


class _Recorder:
    def list_distinct_threads(self) -> list[str]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return [MINE_OLD, OTHER, MINE_NEW, ORPHAN]
        raise AssertionError("snapshot I/O ran on the event loop")


def _client(monkeypatch: pytest.MonkeyPatch, store: Any) -> TestClient:
    import kazma_ui.session_manager as sm
    from kazma_ui.replay_routes import create_replay_router

    monkeypatch.setattr(sm, "get_session_manager", lambda: store)
    app = FastAPI()
    app.include_router(create_replay_router(recorder=_Recorder(), engine=object(), graph=None))
    return TestClient(app)


def test_each_thread_is_named_by_its_chat_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch, _Store()).get("/api/replay/threads").json()
    assert body["count"] == 3
    assert set(body["threads"]) == {MINE_NEW, MINE_OLD, ORPHAN}, "the id list the picker's value uses"
    assert OTHER not in body["threads"]
    items = body["items"]
    assert [it["thread_id"] for it in items] == [MINE_NEW, MINE_OLD, ORPHAN]
    assert items[0] == {
        "thread_id": MINE_NEW, "title": "Plan the launch", "platform": "telegram",
        "updated_at": "2026-09-28T09:00:00+00:00", "archived": False,
    }
    assert items[2]["title"] == "" and items[2]["updated_at"] == "", "a thread no chat owns keeps its id"


def test_a_store_without_a_chat_list_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch, _NoListStore()).get("/api/replay/threads").json()
    assert set(body["threads"]) == {MINE_NEW, MINE_OLD, ORPHAN}
    assert all(it["title"] == "" for it in body["items"])


def test_the_dashboard_reads_the_same_helper() -> None:
    """One answer to 'which chat is this thread' for both pages."""
    import inspect

    from kazma_ui import dashboard

    assert "chats_by_thread" in inspect.getsource(dashboard._chats_by_thread)
