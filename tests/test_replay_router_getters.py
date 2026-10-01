"""The replay API reads its recorder and graph on every request (2026-10-01).

The app mounted ``/api/replay/*`` in ``_on_startup`` with the recorder and the
graph of that moment, so no gate that reads the built app's route table saw
it (``tests/test_routes_exist_when_built.py``), and a model switch -- which
rebuilds the graph -- left restore and fork writing through the old one. It
is mounted when the app is built now, with getters.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_thread_ownership_routes import MINE, _Snap, store  # noqa: F401  (fixture)


class _Recorder:
    """What ReplayEngine and the routes read from a SnapshotRecorder."""

    def list_distinct_threads(self) -> list[str]:
        return [MINE]

    def list_snapshots(self, thread_id: str) -> list[_Snap]:
        return [_Snap(thread_id)]

    def get_snapshot(self, thread_id: str, iteration: int, db_path: Any = None) -> _Snap:
        return _Snap(thread_id)

    def clear_snapshots(self, thread_id: str) -> int:
        return 1


class _Graph:
    def __init__(self, name: str) -> None:
        self.name = name
        self.writes: list[str] = []

    async def aupdate_state(self, config: dict[str, Any], values: dict[str, Any], **_: Any) -> None:
        self.writes.append(config["configurable"]["thread_id"])


def _client(held: dict[str, Any]) -> TestClient:
    from kazma_ui.replay_routes import create_replay_router

    app = FastAPI()
    app.include_router(create_replay_router(
        recorder_getter=lambda: held.get("recorder"),
        graph_getter=lambda: held.get("graph"),
    ))
    return TestClient(app)


def test_before_startup_the_api_says_it_is_unavailable(store) -> None:  # noqa: F811
    held: dict[str, Any] = {}
    client = _client(held)
    got = client.get("/api/replay/threads")
    assert got.status_code == 503 and got.json()["error"] == "time_travel_unavailable"

    held["recorder"] = _Recorder()
    assert client.get("/api/replay/threads").json()["threads"] == [MINE]
    snap = client.get(f"/api/replay/snapshots/{MINE}/0").json()
    assert f"private text of {MINE}" in str(snap["messages"])


def test_restore_writes_through_the_graph_of_the_moment(store) -> None:  # noqa: F811
    held: dict[str, Any] = {"recorder": _Recorder()}
    client = _client(held)
    body = {"thread_id": MINE, "iteration": 0}
    assert client.post("/api/replay/restore", json=body).status_code == 503  # no graph yet

    first, switched = _Graph("first"), _Graph("after a model switch")
    held["graph"] = first
    assert client.post("/api/replay/restore", json=body).status_code == 200
    held["graph"] = switched
    assert client.post("/api/replay/restore", json=body).status_code == 200
    assert first.writes == [MINE] and switched.writes == [MINE]


def test_the_old_wiring_kept_the_graph_it_was_mounted_with(store) -> None:  # noqa: F811
    """Negative control: given values, as startup mounted it, the router
    writes through its first graph after the app holds another."""
    from kazma_ui.replay_routes import create_replay_router

    held: dict[str, Any] = {"graph": _Graph("first")}
    first = held["graph"]
    app = FastAPI()
    app.include_router(create_replay_router(recorder=_Recorder(), graph=held["graph"]))
    held["graph"] = switched = _Graph("after a model switch")
    body = {"thread_id": MINE, "iteration": 0}
    assert TestClient(app).post("/api/replay/restore", json=body).status_code == 200
    assert first.writes == [MINE] and switched.writes == []
