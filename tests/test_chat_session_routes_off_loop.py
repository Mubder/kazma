"""The chat session routes use the chat store off the event loop (2026-09-28).

The sidebar list (every page load), the history route (every chat opened),
rename, archive, pin, the status poll and delete called the session store
inline in ``async def`` handlers. On Postgres every call is a round trip --
the list measured 150-400 ms on the live install -- and while it runs,
every SSE stream and WebSocket on the server waits. Handlers that await
nothing are plain functions now (FastAPI runs them in its threadpool, the
request's context included); the rest await ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _OnLoopRecorder:
    """The real session store, noting each call made where an event loop runs."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.on_loop: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._real, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self.on_loop.append(name)
            return attr(*args, **kwargs)

        return call


@pytest.fixture
def client_and_store(monkeypatch) -> Iterator[tuple[TestClient, _OnLoopRecorder]]:
    import kazma_ui.session_manager as sm_mod

    sm_mod.reset_session_manager()
    recorder = _OnLoopRecorder(sm_mod.get_session_manager())
    monkeypatch.setattr(sm_mod, "get_session_manager", lambda: recorder)
    from kazma_ui.sse_chat import create_sse_chat_router

    app = FastAPI()
    app.include_router(create_sse_chat_router(graph=None, checkpointer=None))
    session = recorder._real.get_or_create("s-off-loop")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi there")
    recorder._real.put(session)
    try:
        yield TestClient(app), recorder
    finally:
        sm_mod.reset_session_manager()


def test_every_chat_session_route_uses_the_store_off_the_loop(client_and_store) -> None:
    client, store = client_and_store
    calls = [
        ("get", "/api/chat/sessions", None),
        ("get", "/api/chat/sessions/archived", None),
        ("get", "/api/chat/sessions/s-off-loop/messages", None),
        ("get", "/api/chat/sessions/s-off-loop/status", None),
        ("patch", "/api/chat/sessions/s-off-loop", {"title": "Renamed"}),
        ("post", "/api/chat/sessions/s-off-loop/archive", None),
        ("post", "/api/chat/sessions/s-off-loop/unarchive", None),
        ("post", "/api/chat/sessions/s-off-loop/pin", None),
        ("post", "/api/chat/sessions/s-off-loop/unpin", None),
        ("delete", "/api/chat/sessions/s-off-loop", None),
    ]
    for method, path, body in calls:
        resp = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
    assert store._real.get("s-off-loop") is None, "the delete really deleted"
    assert store.on_loop == [], f"store calls made on the event loop: {store.on_loop}"


def test_negative_control_the_recorder_sees_a_call_on_the_loop() -> None:
    class _Store:
        def get(self, session_id: str) -> None:
            return None

    store = _OnLoopRecorder(_Store())

    async def handler() -> None:
        store.get("x")  # inline in an async handler: the old shape
        await asyncio.to_thread(store.get, "y")  # the fixed shape

    asyncio.run(handler())
    assert store.on_loop == ["get"]
