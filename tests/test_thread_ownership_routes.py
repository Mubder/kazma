"""Routes that take a thread act only on the caller's own threads.

Regression for the 2026-09-22 audit:

* ``/api/replay/*`` checked ownership on restore and fork only. Any caller could
  list every thread with a snapshot, read its messages, compare them, and
  delete its history — and the check that did exist failed OPEN on a store
  error while claiming to mirror the approval gate, which fails closed.
* ``/api/chat/stop|steer|abort|capacity`` acted on any ``thread_id`` in the
  body, and fell back to treating an unknown ``session_id`` as a thread id.
  Abort wrote an abort marker into that thread's graph state.

The replay test walks the router's own route table, so a route added later is
covered without anyone remembering to add it here.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MINE, OTHER = "t-mine", "t-other"


class _Session:
    def __init__(self, session_id: str, thread_id: str) -> None:
        self.session_id = session_id
        self.thread_id = thread_id
        self.messages: list[Any] = []


class _TenantStore:
    """The current tenant owns session ``s-mine`` bound to thread ``t-mine``."""

    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self._by_session = {"s-mine": MINE}

    def get(self, session_id: str) -> _Session | None:
        if self.broken:
            raise RuntimeError("session store unavailable")
        tid = self._by_session.get(session_id)
        return _Session(session_id, tid) if tid else None

    def get_by_thread_id(self, thread_id: str) -> _Session | None:
        if self.broken:
            raise RuntimeError("session store unavailable")
        for sid, tid in self._by_session.items():
            if tid == thread_id:
                return _Session(sid, tid)
        return None

    def put(self, session: Any) -> None:  # fork creates a web session
        self._by_session[session.session_id] = session.thread_id


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _TenantStore:
    import kazma_ui.session_manager as sm

    s = _TenantStore()
    monkeypatch.setattr(sm, "get_session_manager", lambda: s)
    return s


# ── Replay ──────────────────────────────────────────────────────────────────


class _Snap:
    def __init__(self, thread_id: str) -> None:
        self.iteration, self.timestamp, self.model_used, self.id = 0, 0.0, "m", f"{thread_id}:0"
        self._thread = thread_id

    def get_state(self) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": f"private text of {self._thread}"}]}


class _Recorder:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def list_distinct_threads(self) -> list[str]:
        return [MINE, OTHER]

    def list_snapshots(self, thread_id: str) -> list[_Snap]:
        return [_Snap(thread_id)]

    def clear_snapshots(self, thread_id: str) -> int:
        self.cleared.append(thread_id)
        return 1


class _Engine:
    def replay_from(self, thread_id: str, iteration: int) -> dict[str, Any]:
        return _Snap(thread_id).get_state()

    @staticmethod
    def compare_replays(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        return {"a": a, "b": b}


def _replay_client() -> tuple[TestClient, Any, _Recorder]:
    from kazma_ui.replay_routes import create_replay_router

    recorder = _Recorder()
    router = create_replay_router(recorder=recorder, engine=_Engine(), graph=object())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), router, recorder


def _requests_for(router: Any, thread_id: str):
    """One request per route in the router, aimed at *thread_id*."""
    for route in router.routes:
        for method in sorted(route.methods or ()):
            path = route.path.replace("{thread_id}", thread_id).replace("{iteration}", "0")
            body = {"thread_id": thread_id, "iteration": 0, "a": 0, "b": 0}
            yield method, route.path, path, (body if method in {"POST", "PUT", "PATCH"} else None)


def test_every_replay_route_refuses_another_tenants_thread(store):
    client, router, recorder = _replay_client()
    checked = 0
    for method, template, path, body in _requests_for(router, OTHER):
        resp = client.request(method, path, json=body)
        checked += 1
        if template == "/api/replay/threads":
            assert resp.status_code == 200
            assert OTHER not in resp.json()["threads"], "list_threads leaked a foreign thread"
            assert MINE in resp.json()["threads"]
            continue
        assert resp.status_code == 404, (method, template, resp.status_code, resp.text)
        assert f"private text of {OTHER}" not in resp.text
    assert recorder.cleared == [], "a foreign thread's snapshots were deleted"
    assert checked >= 7, "route enumeration went blind"


def test_every_replay_route_fails_closed_when_ownership_is_unknown(store):
    store.broken = True
    client, router, recorder = _replay_client()
    for method, template, path, body in _requests_for(router, MINE):
        resp = client.request(method, path, json=body)
        assert resp.status_code == 403, (method, template, resp.status_code, resp.text)
        assert "private text" not in resp.text
    assert recorder.cleared == []


def test_replay_still_serves_the_owner(store):
    """Negative control: the guard must not refuse the caller's own thread."""
    client, _, recorder = _replay_client()
    resp = client.get(f"/api/replay/snapshots/{MINE}/0")
    assert resp.status_code == 200
    assert f"private text of {MINE}" in resp.text
    assert client.delete(f"/api/replay/threads/{MINE}").status_code == 200
    assert recorder.cleared == [MINE]


# ── Chat controls ───────────────────────────────────────────────────────────


class _Graph:
    def __init__(self) -> None:
        self.updated: list[str] = []

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return None

    async def aupdate_state(self, config: dict[str, Any], values: dict[str, Any]) -> None:
        self.updated.append(config["configurable"]["thread_id"])


@pytest.fixture
def chat(store, monkeypatch: pytest.MonkeyPatch):
    import kazma_ui.sse_chat as sse
    from kazma_ui.sse_chat import create_sse_chat_router

    cancelled: list[str] = []
    monkeypatch.setattr(sse, "cancel_turn", lambda tid: cancelled.append(tid) or None)
    graph = _Graph()
    app = FastAPI()
    app.include_router(create_sse_chat_router(graph=graph))
    return TestClient(app), graph, cancelled


@pytest.mark.parametrize(
    "body",
    [
        {"thread_id": OTHER},
        {"session_id": OTHER},  # the old fallback treated this as a thread id
        {"session_id": "s-not-mine"},
    ],
)
def test_chat_controls_never_touch_another_tenants_thread(chat, body):
    client, graph, cancelled = chat
    assert client.post("/api/chat/stop", json=body).json() == {"cancelled": False}
    abort = client.post("/api/chat/abort", json=body).json()
    assert abort.get("ok") is False
    steer = client.post("/api/chat/steer", json={**body, "text": "ignore the user"}).json()
    assert steer.get("ok") is False
    cap = client.get("/api/chat/capacity", params=body).json()
    assert cap.get("ok") is False
    assert cancelled == [] and graph.updated == [], (cancelled, graph.updated)


def test_chat_stop_still_reaches_the_callers_own_turn(chat):
    """Negative control: the caller's own session resolves to its thread."""
    client, _, cancelled = chat
    client.post("/api/chat/stop", json={"session_id": "s-mine"})
    client.post("/api/chat/stop", json={"thread_id": MINE})
    assert cancelled == [MINE, MINE]


def test_chat_controls_fail_closed_when_ownership_is_unknown(chat, store):
    client, graph, cancelled = chat
    store.broken = True
    client.post("/api/chat/stop", json={"session_id": "s-mine"})
    client.post("/api/chat/abort", json={"thread_id": MINE})
    assert cancelled == [] and graph.updated == []
