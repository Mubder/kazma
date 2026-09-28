"""``/replay`` and ``/fork`` in the web chat answer at once, without the model.

Live 2026-09-28: typed in the web chat, ``/replay list`` went to the model,
which spent 40 s searching the codebase for what "replay" means -- the
commands exist only on Telegram, Discord and Slack. The web now answers with
the chat's saved steps and a link that opens the Time Travel page on this
chat (``/replay?thread=...``, honoured by ``replay.js``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.sse_chat import create_sse_chat_router
from kazma_ui.sse_chat._time_travel import is_time_travel_command, time_travel_reply


@pytest.fixture(autouse=True)
def _fresh_sessions():
    from kazma_ui.session_manager import reset_session_manager

    reset_session_manager()


class _Recorder:
    def __init__(self, snapshots: list) -> None:
        self.snapshots = snapshots
        self.asked: list[str] = []

    def list_snapshots(self, thread_id: str) -> list:
        self.asked.append(thread_id)
        return self.snapshots


def _snap(iteration: int, ts: str = "2026-09-28T06:05:11+00:00"):
    return SimpleNamespace(iteration=iteration, timestamp=ts, model_used="deepseek-flash")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/replay", True), ("/replay list", True), ("/fork 3", True), ("/REPLAY", True),
        ("  /fork", True), ("/replayed", False), ("replay list", False), ("/reset", False),
    ],
)
def test_the_commands_are_recognised(text, expected):
    assert is_time_travel_command(text) is expected


def test_the_answer_names_the_steps_and_links_the_page():
    rec = _Recorder([_snap(1), _snap(2), _snap(7)])
    out = time_travel_reply("/replay list", "web thread/1", rec)
    assert rec.asked == ["web thread/1"]
    assert "3 saved step(s), iterations 1 to 7" in out
    assert "2026-09-28 06:05 UTC" in out
    assert "(/replay?thread=web%20thread%2F1)" in out  # the id is one query value
    assert "`/replay <n>`" in out


def test_fork_names_its_own_command():
    assert "`/fork <n>`" in time_travel_reply("/fork 2", "t", _Recorder([_snap(2)]))


def test_a_chat_without_steps_says_so():
    out = time_travel_reply("/replay", "t", _Recorder([]))
    assert "No steps of this chat have been saved yet" in out and "/replay?thread" not in out


def test_no_recorder_is_reported_not_hidden():
    assert "unavailable" in time_travel_reply("/replay", "t", None)


def _client(recorder) -> tuple[TestClient, MagicMock]:
    graph = MagicMock()
    agent = SimpleNamespace(_snapshot_recorder=recorder)
    router = create_sse_chat_router(graph=graph, checkpointer=None, agent_getter=lambda: agent)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), graph


def test_the_web_chat_answers_without_the_model():
    rec = _Recorder([_snap(1), _snap(4)])
    client, graph = _client(rec)
    resp = client.post("/api/chat/stream", json={"message": "/replay list"})
    assert resp.status_code == 200
    assert "2 saved step(s), iterations 1 to 4" in resp.text
    assert "event: done" in resp.text
    assert rec.asked, "the chat's own snapshots were not read"
    graph.astream_events.assert_not_called()
    graph.ainvoke.assert_not_called()


def test_an_ordinary_message_still_reaches_the_model():
    """Control: only the two commands take the fast path."""
    rec = _Recorder([])
    assert not is_time_travel_command("please replay the last step for me")
    client, _ = _client(rec)
    client.post("/api/chat/stream", json={"message": "hello"})
    assert rec.asked == []
