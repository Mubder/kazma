"""The replay API reads its recorder and graph on every request (2026-10-01).

The app mounted ``/api/replay/*`` in ``_on_startup`` with the recorder and the
graph of that moment, so no gate that reads the built app's route table saw
it (``tests/test_routes_exist_when_built.py``), and a model switch -- which
rebuilds the graph -- left restore and fork writing through the old one. It
is mounted when the app is built now, with getters.

Re-pointed 2026-10-14 (Time Travel TT-12): the recorder is the real
``SnapshotRecorder`` and each graph the real graph of ``tests/_tt_graph.py``
on its own saver; a restore is read back from the graph it went through
(the fake graphs recorded ``aupdate_state`` arguments, which says nothing of
what a restore writes). A step is addressed by its snapshot id (TT-01).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from tests import _tt_graph as tt
from tests.test_thread_ownership_routes import MINE, store  # noqa: F401  (fixture)


def _recorder(tmp_path) -> tuple[Any, str]:
    from kazma_core.time_travel import SnapshotRecorder

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    record = rec.capture({"thread_id": MINE, "iteration": 1, "created_at": "t1",
                          "messages": [{"role": "user", "content": f"private text of {MINE}"}]})
    return rec, record.id


def _messages(graph: Any) -> list[str]:
    snap = tt.run(graph.aget_state(tt.config(MINE)))
    return [m.get("content") for m in (snap.values or {}).get("messages") or []]


def _client(held: dict[str, Any]) -> TestClient:
    from kazma_ui.replay_routes import create_replay_router

    app = FastAPI()
    app.include_router(create_replay_router(
        recorder_getter=lambda: held.get("recorder"),
        graph_getter=lambda: held.get("graph"),
    ))
    return TestClient(app)


def test_before_startup_the_api_says_it_is_unavailable(store, tmp_path) -> None:  # noqa: F811
    held: dict[str, Any] = {}
    client = _client(held)
    got = client.get("/api/replay/threads")
    assert got.status_code == 503 and got.json()["error"] == "time_travel_unavailable"

    held["recorder"], sid = _recorder(tmp_path)
    assert client.get("/api/replay/threads").json()["threads"] == [MINE]
    snap = client.get(f"/api/replay/snapshots/{MINE}/{sid}").json()
    assert f"private text of {MINE}" in str(snap["messages"])


def test_restore_writes_through_the_graph_of_the_moment(store, tmp_path) -> None:  # noqa: F811
    rec, sid = _recorder(tmp_path)
    held: dict[str, Any] = {"recorder": rec}
    client = _client(held)
    body = {"thread_id": MINE, "snapshot_id": sid}
    assert client.post("/api/replay/restore", json=body).status_code == 503  # no graph yet

    first = tt.build_graph(rec, [], InMemorySaver())
    switched = tt.build_graph(rec, [], InMemorySaver())
    held["graph"] = first
    assert client.post("/api/replay/restore", json=body).status_code == 200
    assert _messages(first) == [f"private text of {MINE}"] and _messages(switched) == []
    held["graph"] = switched
    assert client.post("/api/replay/restore", json=body).status_code == 200
    assert _messages(switched) == [f"private text of {MINE}"]


def test_the_old_wiring_kept_the_graph_it_was_mounted_with(store, tmp_path) -> None:  # noqa: F811
    """Negative control: given values, as startup mounted it, the router
    writes through its first graph after the app holds another."""
    from kazma_ui.replay_routes import create_replay_router

    rec, sid = _recorder(tmp_path)
    first = tt.build_graph(rec, [], InMemorySaver())
    held: dict[str, Any] = {"graph": first}
    app = FastAPI()
    app.include_router(create_replay_router(recorder=rec, graph=held["graph"]))
    held["graph"] = switched = tt.build_graph(rec, [], InMemorySaver())
    body = {"thread_id": MINE, "snapshot_id": sid}
    assert TestClient(app).post("/api/replay/restore", json=body).status_code == 200
    assert _messages(first) == [f"private text of {MINE}"] and _messages(switched) == []
