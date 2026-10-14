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

    def thread_is_exclusive(self, thread_id: str) -> bool:
        return not self.broken and thread_id in self._by_session.values()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _TenantStore:
    import kazma_ui.session_manager as sm

    s = _TenantStore()
    monkeypatch.setattr(sm, "get_session_manager", lambda: s)
    return s


# ── Replay ──────────────────────────────────────────────────────────────────
#
# Re-pointed 2026-10-14 (Time Travel TT-12): the recorder is the real
# ``SnapshotRecorder`` (its reads checked off the loop) and the graph the real
# graph of ``tests/_tt_graph.py``; a step is addressed by its snapshot id
# (TT-01). The fakes this used could not see what a restore wrote.


def _off_the_loop() -> None:
    """The recorder and engine read SQLite and decode full states: never on the loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise AssertionError("snapshot I/O ran on the event loop")


def _recorder_class():
    from kazma_core.time_travel import SnapshotRecorder

    class _Recorder(SnapshotRecorder):
        """The real recorder, its every read and write checked off the loop."""

        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            self.cleared: list[str] = []

        def list_distinct_threads(self, **k: Any) -> list[str]:
            _off_the_loop()
            return super().list_distinct_threads(**k)

        def list_snapshots(self, thread_id: str, **k: Any) -> list[Any]:
            _off_the_loop()
            return super().list_snapshots(thread_id, **k)

        def get(self, snapshot_id: str, **k: Any) -> Any:
            _off_the_loop()
            return super().get(snapshot_id, **k)

        def forget_threads(self, thread_ids: list[str], **k: Any) -> int:
            _off_the_loop()
            self.cleared += list(thread_ids)
            return super().forget_threads(thread_ids, **k)

    return _Recorder


def _replay_client(tmp_path) -> tuple[TestClient, Any, Any, dict[str, str]]:
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_ui.replay_routes import create_replay_router
    from tests import _tt_graph as tt

    recorder = _recorder_class()(db_path=str(tmp_path / "snapshots.db"))
    ids = {}
    for thread in (MINE, OTHER):
        record = recorder.capture({"thread_id": thread, "iteration": 1, "created_at": thread,
                                   "messages": [{"role": "user", "content": f"private text of {thread}"}]})
        ids[thread] = record.id
    router = create_replay_router(recorder=recorder, graph=tt.build_graph(recorder, [], InMemorySaver()))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), router, recorder, ids


def _requests_for(router: Any, thread_id: str, ids: dict[str, str]):
    """One request per route in the router, aimed at *thread_id*."""
    sid = ids[thread_id]
    for route in router.routes:
        for method in sorted(route.methods or ()):
            path = route.path.replace("{thread_id}", thread_id).replace("{snapshot_id}", sid)
            body = {"thread_id": thread_id, "snapshot_id": sid, "a": sid, "b": sid}
            yield method, route.path, path, (body if method in {"POST", "PUT", "PATCH"} else None)


def test_every_replay_route_refuses_another_tenants_thread(store, tmp_path):
    client, router, recorder, ids = _replay_client(tmp_path)
    checked = 0
    for method, template, path, body in _requests_for(router, OTHER, ids):
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


def test_every_replay_route_fails_closed_when_ownership_is_unknown(store, tmp_path):
    store.broken = True
    client, router, recorder, ids = _replay_client(tmp_path)
    for method, template, path, body in _requests_for(router, MINE, ids):
        resp = client.request(method, path, json=body)
        assert resp.status_code == 403, (method, template, resp.status_code, resp.text)
        assert "private text" not in resp.text
    assert recorder.cleared == []


def test_every_replay_route_serves_the_owner_without_blocking_the_loop(store, tmp_path):
    """Walks the route table on the caller's own thread.

    The recorder raises if touched on the event loop, so a route that reads
    snapshots synchronously answers 500 here (audit 2026-09-22: every replay
    route did, sharing a lock with the capture thread). The DELETE runs last:
    it removes the step the others read.
    """
    client, router, _, ids = _replay_client(tmp_path)
    requests = sorted(_requests_for(router, MINE, ids), key=lambda r: r[0] == "DELETE")
    for method, template, path, body in requests:
        resp = client.request(method, path, json=body)
        assert resp.status_code == 200, (method, template, resp.status_code, resp.text)


def test_replay_still_serves_the_owner(store, tmp_path):
    """Negative control: the guard must not refuse the caller's own thread."""
    client, _, recorder, ids = _replay_client(tmp_path)
    resp = client.get(f"/api/replay/snapshots/{MINE}/{ids[MINE]}")
    assert resp.status_code == 200
    assert f"private text of {MINE}" in resp.text
    assert client.delete(f"/api/replay/threads/{MINE}").status_code == 200
    assert MINE in recorder.cleared and recorder.list_snapshots(MINE) == []


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
