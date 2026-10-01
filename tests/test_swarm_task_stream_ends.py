"""A swarm task's stream ends with its terminal event, and "finished" is one list (2026-10-02).

The Swarm page opens ``/api/swarm/tasks/{id}/stream`` for a task it shows as
running, and settles the card on ``task_completed``. Its client reconnects
whenever a stream closes, so a stream must not close without that event:

- the stream replayed the in-memory history of a finished task and closed.
  That history is gone after a restart (a deploy) and after the bus cleans up
  a finished task, so the stream sent nothing, the client reconnected ten
  times over a few minutes and the card's timer kept counting;
- its list of finished statuses left out ``cancelled``, so the stream of a
  cancelled task polled the store once a second and never ended.

Now the terminal event is rebuilt from the stored task whenever the stream
ends without one, and every module asks ``kazma_core.swarm.task`` what
"finished" means (the engine held three copies of the set, the task store a
fourth).
"""

from __future__ import annotations

import ast
import json
import textwrap
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.swarm.task import (
    TERMINAL_STATUSES,
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    is_terminal,
)

ROOT = Path(__file__).resolve().parents[1]


# ── the stream ───────────────────────────────────────────────────────────


class _Store:
    """A task store whose answer can change between reads (the poll)."""

    def __init__(self, *tasks: SwarmTask) -> None:
        self.answers = list(tasks)
        self.reads = 0

    def get_task(self, task_id: str) -> SwarmTask | None:
        self.reads += 1
        index = min(self.reads - 1, len(self.answers) - 1)
        return self.answers[index]


class _Engine:
    def __init__(self, store: _Store) -> None:
        self.task_store = store

    def get_task(self, task_id: str) -> None:
        return None


def _task(status: TaskStatus, result_status: str = "", output: str = "") -> SwarmTask:
    task = SwarmTask(prompt="p", type=TaskType.DISPATCH, workers=["auto"], id="task-1")
    task.status = status
    if result_status:
        task.result = TaskResult(
            task_id="task-1", status=result_status, synthesized_output=output or None
        )
    return task


def _frames(store: _Store, emit: list[tuple[str, dict]] | None = None, *, wait: float = 8.0) -> list[dict]:
    """Every frame the stream sends, read until it closes (or *wait* runs out)."""
    from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

    bus = SSEEventBus()
    for event, data in emit or []:
        bus.emit("task-1", event, data)
    app = FastAPI()
    app.include_router(create_sse_router(event_bus=bus))
    lines: list[str] = []
    closed = threading.Event()

    def read() -> None:
        with patch("kazma_ui.swarm_sse.get_swarm_engine", return_value=_Engine(store)):
            with TestClient(app).stream("GET", "/api/swarm/tasks/task-1/stream") as resp:
                for line in resp.iter_lines():
                    lines.append(line)
        closed.set()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    closed.wait(wait)
    frames: list[dict] = []
    event = None
    for line in lines:
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            frames.append({"event": event, "data": json.loads(line.split(":", 1)[1])})
    frames.append({"closed": closed.is_set()})
    return frames


def test_a_finished_task_with_no_history_ends_with_its_result():
    """After a restart the bus remembers nothing: the store holds the result."""
    frames = _frames(_Store(_task(TaskStatus.COMPLETED, "success", "the answer")))
    assert frames[-1] == {"closed": True}
    assert [f["event"] for f in frames[:-1]] == ["task_completed"]
    result = frames[0]["data"]["result"]
    assert frames[0]["data"]["task_id"] == "task-1"
    assert result["status"] == "success"
    assert result["synthesized_output"] == "the answer"


def test_a_cancelled_task_stream_ends():
    """The stream's own list of finished statuses left ``cancelled`` out."""
    frames = _frames(_Store(_task(TaskStatus.CANCELLED, "cancelled")))
    assert frames[-1] == {"closed": True}, "the stream of a cancelled task never ended"
    assert [f["event"] for f in frames[:-1]] == ["task_completed"]
    assert frames[0]["data"]["result"]["status"] == "cancelled"


def test_a_finished_task_without_a_stored_result_still_ends():
    frames = _frames(_Store(_task(TaskStatus.FAILED)))
    assert frames[-1] == {"closed": True}
    assert frames[0]["event"] == "task_completed"
    assert frames[0]["data"]["result"] == {"task_id": "task-1", "status": "failed"}


def test_history_that_ends_with_its_terminal_event_is_replayed_once():
    emitted = [
        ("task_started", {"task_id": "task-1"}),
        ("task_completed", {"task_id": "task-1", "result": {"status": "success"}}),
    ]
    frames = _frames(_Store(_task(TaskStatus.COMPLETED, "success")), emitted)
    assert [f.get("event") for f in frames[:-1]] == ["task_started", "task_completed"]
    assert frames[-1] == {"closed": True}


def test_a_live_task_that_finishes_without_an_event_still_ends_with_one():
    """The once-a-second poll finds the task finished; the event is rebuilt."""
    store = _Store(_task(TaskStatus.RUNNING), _task(TaskStatus.COMPLETED, "partial", "half"))
    frames = _frames(store)
    assert frames[-1] == {"closed": True}
    assert [f["event"] for f in frames[:-1]] == ["task_completed"]
    assert frames[0]["data"]["result"]["status"] == "partial"


def test_without_the_rebuilt_event_a_finished_stream_says_nothing(monkeypatch):
    """Negative control: the old stream closed a finished task's stream empty."""
    from kazma_ui import swarm_sse

    monkeypatch.setattr(swarm_sse, "_final_event", lambda task_id, task: None)
    frames = _frames(_Store(_task(TaskStatus.COMPLETED, "success")))
    assert frames == [{"closed": True}]


# ── one list of finished statuses ────────────────────────────────────────


def test_every_task_status_is_classified():
    """A new status must be declared finished or not (the stream depends on it)."""
    not_finished = {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED}
    assert TERMINAL_STATUSES | not_finished == set(TaskStatus)
    assert not TERMINAL_STATUSES & not_finished
    assert is_terminal("cancelled") and is_terminal(TaskStatus.TIMEOUT)
    assert not is_terminal("paused") and not is_terminal("") and not is_terminal(None)


_STATUS_WORDS = {s.value for s in TERMINAL_STATUSES}
#: Where swarm task statuses are judged: the engine and its modules, the
#: Swarm page's routes and stream, the gateway's routers.
_SWARM_SOURCES = (
    "kazma-core/kazma_core/swarm",
    "kazma-ui/kazma_ui/swarm_panel",
    "kazma-ui/kazma_ui/swarm_sse.py",
    "kazma-gateway/kazma_gateway/routers",
)


def _swarm_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in _SWARM_SOURCES:
        path = ROOT / rel
        files = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.is_dir() else []
        for p in files:
            if "__pycache__" not in p.parts:
                out[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
    return out


_TASK_STATUS_WORDS = {s.value for s in TaskStatus}


def _status_word(elt: ast.expr) -> str | None:
    """The task status an element names (``"failed"`` or ``TaskStatus.FAILED``)."""
    if isinstance(elt, ast.Constant) and elt.value in _TASK_STATUS_WORDS:
        return str(elt.value)
    if (
        isinstance(elt, ast.Attribute)
        and isinstance(elt.value, ast.Name)
        and elt.value.id == "TaskStatus"
        and elt.attr.lower() in _TASK_STATUS_WORDS
    ):
        return elt.attr.lower()
    return None


def _hand_written_finished_sets(sources: dict[str, str]) -> list[str]:
    """Literals made of task statuses, two or more of them finished, outside task.py.

    Only lists of task statuses: a table that maps a worker result's words
    (``"error"``, ``"failure"``, ``"timeout"``) onto something else answers
    another question.
    """
    problems: list[str] = []
    for rel, text in sources.items():
        if rel.endswith("kazma_core/swarm/task.py"):
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.Set, ast.Tuple, ast.List)) or not node.elts:
                continue
            words = [_status_word(elt) for elt in node.elts]
            if None in words:
                continue
            finished = {w for w in words if w in _STATUS_WORDS}
            if len(finished) >= 2:
                problems.append(f"{rel}:{node.lineno} {sorted(finished)}")
    return problems


def test_finished_statuses_are_listed_once():
    problems = _hand_written_finished_sets(_swarm_sources())
    assert not problems, (
        "A hand-written list of finished task statuses: use "
        "kazma_core.swarm.task.TERMINAL_STATUSES / is_terminal (the stream's "
        "own copy left out 'cancelled'):\n  " + "\n  ".join(problems)
    )


def test_hand_written_finished_sets_are_caught():
    """Negative control: the stream's old set and the engine's old one."""
    planted = {
        "a.py": '_TERMINAL_STATUSES = frozenset({"completed", "failed", "timeout"})\n',
        "b.py": textwrap.dedent(
            """
            _TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED}
            ONE = ("completed",)
            WORKER_FAILURE = ("error", "failed", "failure", "timeout", "cancelled")
            """
        ),
    }
    assert _hand_written_finished_sets(planted) == [
        "a.py:1 ['completed', 'failed', 'timeout']",
        "b.py:2 ['completed', 'failed']",
    ]
