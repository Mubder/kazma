"""The matrix cases of Spaces S1-16: research (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own Research router behind
the real tenant middleware, on a private data folder (its own
``research_sessions.db``) and a real swarm engine with a private task store
(research tasks are swarm tasks with ``kind: research``), or through the
product's own tools.

Sessions are made by the product's own ``create_session`` bound to each
person's space, as a run or a chat search makes them. A run's pipeline is
stood in for (no search, no fetch, no model): what S1-16 holds is the
session's space, not the run's work. A research report and the other
research files are the workspace folder's, which S1-19 makes the space's.
"""

from __future__ import annotations

import asyncio
import importlib
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._as_request import own
from tests._chat_space_cases import _boundary
from tests._no_models import keep_models_out
from tests._space_matrix import Matrix, case

ITEM = "S1-16"
_UNKNOWN_SESSION = "rs_0000000000000000"
_UNKNOWN_TASK = "task-unknown-000000"


@dataclass
class Planted:
    """A planted session or task: its id and the marker its topic holds."""

    id: str
    mark: str


async def _no_pipeline(topic: str, **_kwargs: Any) -> str:
    """The run's work, stood in for: no search, fetch or model."""
    return f"# {topic}\n\n**Sources acquired:** 0"


class World:
    """The Research router in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, engine: Any) -> None:
        self.matrix = matrix
        self.client = client
        self.engine = engine

    def space_of(self, who: str) -> str:
        return {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]

    def account_of(self, who: str) -> str:
        return {"a": self.matrix.account_a, "b": self.matrix.account_b}[who]

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: self.space_of(who)}
        return self.client.request(method, path, headers=headers, **kwargs)

    def session(self, who: str, mark: str, *, status: str = "done") -> Planted:
        """A session in *who*'s space, made as a run makes one, its topic
        carrying *mark*."""
        from kazma_core.tools.research_session import create_session, update_session

        with scope.bind_scope(self.space_of(who), self.account_of(who)):
            made = create_session(f"topic {mark}", depth="deep")
        update_session(made.id, status=status, stage="complete", message="Done", summary=f"summary {mark}")
        return Planted(id=made.id, mark=mark)

    def sessions(self, word: str, **kwargs: Any) -> tuple[Planted, Planted]:
        marks = self.matrix.plant(word)
        return self.session("a", marks.a, **kwargs), self.session("b", marks.b, **kwargs)

    def held_session(self, planted: Planted) -> dict[str, Any]:
        """A session as the store holds it, whoever asks ({} when gone)."""
        from kazma_core.tools.research_session import get_session

        got = get_session(planted.id)
        return {} if got is None else {"status": got.status, "archived": got.archived,
                                       "tenant": got.tenant_id, "topic": got.topic}

    def task(self, who: str, mark: str) -> Planted:
        """A finished research task of *who*'s space (a swarm task, kind research)."""
        from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus

        task = SwarmTask(prompt=f"research {mark}", status=TaskStatus.COMPLETED,
                         metadata={"kind": "research", "tenant_id": self.space_of(who),
                                   "asker": self.account_of(who)},
                         completed_at="2026-11-18T08:00:00+00:00")
        task.result = TaskResult(task_id=task.id, status="success", aggregated_output=f"report {mark}")
        assert own(self.engine._task_store).persist_task(task), task.id
        return Planted(id=task.id, mark=mark)

    def tasks(self, word: str) -> tuple[Planted, Planted]:
        marks = self.matrix.plant(word)
        return self.task("a", marks.a), self.task("b", marks.b)

    def held_task(self, planted: Planted) -> dict[str, Any]:
        row = own(self.engine._task_store).get_task(planted.id)
        return {} if row is None else {"status": str(row.status), "meta": dict(row.metadata or {})}

    def tool(self, who: str, name: str, **kwargs: Any) -> str:
        """The product's tool *name*, run as *who* in their space, on the
        app's loop."""
        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(name)
        assert tool is not None, name
        space, account = self.space_of(who), self.account_of(who)

        async def as_them() -> str:
            with scope.bind_scope(space, account):
                return str(await tool.func(**kwargs))

        return self.client.portal.call(as_them)

    def settle(self) -> None:
        """Every background run this world started has ended."""
        from kazma_core.tools import research_session

        async def wait() -> None:
            for _ in range(250):
                with research_session._RUNNING_LOCK:
                    running = [t for t in research_session._RUNNING.values() if not t.done()]
                if not running:
                    return
                await asyncio.sleep(0.02)

        self.client.portal.call(wait)


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the product's Research router behind the real tenant
    middleware, on a private data folder and task store. The person a
    request is signed in as is named by a test header: sign-in is S1-03's."""
    from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore, get_swarm_engine, set_swarm_engine
    from kazma_ui.research_panel import create_research_router
    from kazma_ui.services import reset_swarm_service

    pipeline = importlib.import_module("kazma_core.tools.research_pipeline")
    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    before = get_swarm_engine()
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setenv("KAZMA_DATA_DIR", tmp)
        patch.setenv("KAZMA_WORKSPACE", tmp)
        keep_models_out(patch)
        patch.setattr(pipeline, "run_research_pipeline", _no_pipeline)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]),
                             task_store=TaskStore(str(Path(tmp) / "swarm_tasks.db")))
        set_swarm_engine(engine)
        reset_swarm_service()
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_research_router())
        try:
            with TestClient(app) as client:
                made = World(matrix, client, engine)
                try:
                    yield made
                finally:
                    made.settle()
        finally:
            set_swarm_engine(before)
            reset_swarm_service()
            engine._task_store.close()


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def research_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        return case(target, ITEM)(_in_world(run))

    return register


def _never_shown(response: Any, theirs: Planted) -> None:
    assert theirs.mark not in response.text and theirs.id not in response.text, response.text


def _same_as_unknown(w: World, method: str, path: str, theirs: Planted, unknown: str, **kwargs: Any) -> Any:
    """A request for B's session or task answers exactly what one for no
    such id does; the answer echoes the id asked for and nothing else."""
    refused = w.call("a", method, path.format(theirs.id), **kwargs)
    nobodys = w.call("a", method, path.format(unknown), **kwargs)
    assert refused.status_code == nobodys.status_code, (refused.text, nobodys.text)
    assert refused.text.replace(theirs.id, unknown) == nobodys.text
    assert theirs.mark not in refused.text
    return nobodys


def _reached(w: World, method: str, path: str, mine: Planted, unknown: str, **kwargs: Any) -> Any:
    """A's own id reaches the handler: not answered as an unknown one."""
    own = w.call("a", method, path.format(mine.id), **kwargs)
    nobodys = w.call("a", method, path.format(unknown), **kwargs)
    assert own.text.replace(mine.id, unknown) != nobodys.text, own.text
    return own


# ── sessions ─────────────────────────────────────────────────────────────


@research_case("GET /api/research/sessions")
def sessions_list(w: World) -> None:
    mine, theirs = w.sessions("list")
    listed = w.call("a", "GET", "/api/research/sessions")
    assert listed.status_code == 200, listed.text
    assert mine.id in listed.text
    _never_shown(listed, theirs)


@research_case("POST /api/research/sessions")
def sessions_create(w: World) -> None:
    from kazma_core.tools.research_session import get_session

    mark = w.matrix.plant("create").a
    made = w.call("a", "POST", "/api/research/sessions", json={"topic": f"topic {mark}", "depth": "brief"})
    assert made.status_code == 200, made.text
    sid = made.json()["session"]["id"]
    w.settle()
    assert get_session(sid).tenant_id == Matrix.SPACE_A
    assert mark not in w.call("b", "GET", "/api/research/sessions").text


@research_case("GET /api/research/sessions/{session_id}")
def sessions_get(w: World) -> None:
    mine, theirs = w.sessions("get")
    _same_as_unknown(w, "GET", "/api/research/sessions/{}", theirs, _UNKNOWN_SESSION)
    own = w.call("a", "GET", f"/api/research/sessions/{mine.id}")
    assert own.status_code == 200 and mine.mark in own.text, own.text


@research_case("GET /api/research/sessions/{session_id}/stream")
def sessions_stream(w: World) -> None:
    mine, theirs = w.sessions("stream")
    _same_as_unknown(w, "GET", "/api/research/sessions/{}/stream", theirs, _UNKNOWN_SESSION)
    own = w.call("a", "GET", f"/api/research/sessions/{mine.id}/stream")
    assert own.status_code == 200 and mine.mark in own.text, own.text


@research_case("DELETE /api/research/sessions/{session_id}")
def sessions_delete(w: World) -> None:
    mine, theirs = w.sessions("delete")
    before = w.held_session(theirs)
    _same_as_unknown(w, "DELETE", "/api/research/sessions/{}", theirs, _UNKNOWN_SESSION)
    assert w.held_session(theirs) == before
    done = w.call("a", "DELETE", f"/api/research/sessions/{mine.id}")
    assert done.status_code == 200 and w.held_session(mine) == {}, done.text


def _session_action(w: World, verb: str, **kwargs: Any) -> None:
    mine, theirs = w.sessions(verb, **kwargs)
    before = w.held_session(theirs)
    path = "/api/research/sessions/{}/" + verb
    _same_as_unknown(w, "POST", path, theirs, _UNKNOWN_SESSION, json={})
    assert w.held_session(theirs) == before
    _reached(w, "POST", path, mine, _UNKNOWN_SESSION, json={})
    w.settle()
    assert w.held_session(theirs) == before


@research_case("POST /api/research/sessions/{session_id}/archive")
def sessions_archive(w: World) -> None:
    _session_action(w, "archive")


@research_case("POST /api/research/sessions/{session_id}/unarchive")
def sessions_unarchive(w: World) -> None:
    _session_action(w, "unarchive")


@research_case("POST /api/research/sessions/{session_id}/cancel")
def sessions_cancel(w: World) -> None:
    _session_action(w, "cancel", status="running")


@research_case("POST /api/research/sessions/{session_id}/export")
def sessions_export(w: World) -> None:
    _session_action(w, "export")


@research_case("POST /api/research/sessions/{session_id}/resume")
def sessions_resume(w: World) -> None:
    _session_action(w, "resume", status="interrupted")


# ── research tasks (swarm tasks of kind research) ────────────────────────


@research_case("GET /api/research/tasks")
def tasks_list(w: World) -> None:
    mine, theirs = w.tasks("tasklist")
    listed = w.call("a", "GET", "/api/research/tasks")
    assert listed.status_code == 200, listed.text
    assert mine.id in listed.text
    _never_shown(listed, theirs)


@research_case("GET /api/research/tasks/{task_id}")
def tasks_get(w: World) -> None:
    mine, theirs = w.tasks("taskget")
    _same_as_unknown(w, "GET", "/api/research/tasks/{}", theirs, _UNKNOWN_TASK)
    own = w.call("a", "GET", f"/api/research/tasks/{mine.id}")
    assert own.status_code == 200 and mine.mark in own.text, own.text


@research_case("POST /api/research/compare")
def tasks_compare(w: World) -> None:
    mine, theirs = w.tasks("compare")
    other = w.task("a", w.matrix.plant("compare2").a)
    refused = w.call("a", "POST", "/api/research/compare", json={"a": mine.id, "b": theirs.id})
    nobodys = w.call("a", "POST", "/api/research/compare", json={"a": mine.id, "b": _UNKNOWN_TASK})
    assert refused.status_code == nobodys.status_code == 404, refused.text
    _never_shown(refused, theirs)
    own = w.call("a", "POST", "/api/research/compare", json={"a": mine.id, "b": other.id})
    assert own.status_code == 200 and mine.mark in own.text, own.text


@research_case("POST /api/research/{task_id}/export")
def tasks_export(w: World) -> None:
    mine, theirs = w.tasks("taskexport")
    _same_as_unknown(w, "POST", "/api/research/{}/export", theirs, _UNKNOWN_TASK, json={"format": "markdown"})
    _reached(w, "POST", "/api/research/{}/export", mine, _UNKNOWN_TASK, json={"format": "markdown"})


def _task_change(w: World, method: str, path: str, word: str) -> None:
    mine, theirs = w.tasks(word)
    before = w.held_task(theirs)
    w.call("a", method, path.format(theirs.id))
    assert w.held_task(theirs) == before, "another space's task is not changed"
    if method != "DELETE":
        _same_as_unknown(w, method, path, theirs, _UNKNOWN_TASK)
    w.call("a", method, path.format(mine.id))
    assert w.held_task(mine) != w.held_task(theirs)


@research_case("POST /api/research/tasks/{task_id}/archive")
def tasks_archive(w: World) -> None:
    _task_change(w, "POST", "/api/research/tasks/{}/archive", "taskarchive")


@research_case("POST /api/research/tasks/{task_id}/unarchive")
def tasks_unarchive(w: World) -> None:
    _task_change(w, "POST", "/api/research/tasks/{}/unarchive", "taskunarchive")


@research_case("DELETE /api/research/tasks/{task_id}")
def tasks_delete(w: World) -> None:
    mine, theirs = w.tasks("taskdelete")
    before = w.held_task(theirs)
    refused = w.call("a", "DELETE", f"/api/research/tasks/{theirs.id}")
    nobodys = w.call("a", "DELETE", f"/api/research/tasks/{_UNKNOWN_TASK}")
    assert refused.text.replace(theirs.id, _UNKNOWN_TASK) == nobodys.text
    assert w.held_task(theirs) == before, "another space's task stays"
    w.call("a", "DELETE", f"/api/research/tasks/{mine.id}")
    assert w.held_task(mine) == {}


# ── the page ─────────────────────────────────────────────────────────────

case("GET /research", ITEM)(_boundary("GET", "/research"))


# ── tools ────────────────────────────────────────────────────────────────


@research_case("tool:research_session_status")
def tool_status(w: World) -> None:
    mine, theirs = w.sessions("toolstatus")
    refused = w.tool("a", "research_session_status", session_id=theirs.id)
    nobodys = w.tool("a", "research_session_status", session_id=_UNKNOWN_SESSION)
    assert refused == nobodys and theirs.mark not in refused
    listed = w.tool("a", "research_session_status")
    assert mine.id in listed and theirs.id not in listed and theirs.mark not in listed


@research_case("tool:cancel_research_session")
def tool_cancel(w: World) -> None:
    mine, theirs = w.sessions("toolcancel", status="running")
    before = w.held_session(theirs)
    refused = w.tool("a", "cancel_research_session", session_id=theirs.id)
    nobodys = w.tool("a", "cancel_research_session", session_id=_UNKNOWN_SESSION)
    assert refused == nobodys and theirs.mark not in refused
    assert w.held_session(theirs) == before
    own = w.tool("a", "cancel_research_session", session_id=mine.id)
    assert mine.id in own, own


@research_case("tool:start_deep_research")
def tool_start(w: World) -> None:
    from kazma_core.tools.research_session import get_session

    mine, theirs = w.sessions("toolstart", status="interrupted")
    before = w.held_session(theirs)
    refused = w.tool("a", "start_deep_research", resume_session_id=theirs.id)
    nobodys = w.tool("a", "start_deep_research", resume_session_id=_UNKNOWN_SESSION)
    assert refused == nobodys and theirs.mark not in refused
    assert w.held_session(theirs) == before
    mark = w.matrix.plant("toolstartnew").a
    import json

    made = json.loads(w.tool("a", "start_deep_research", topic=f"topic {mark}", depth="brief"))
    w.settle()
    assert get_session(made["id"]).tenant_id == Matrix.SPACE_A
    assert mark not in w.tool("b", "research_session_status")


def _searched(w: World, tool: str, query_arg: str) -> None:
    """The same question searched in both spaces keeps one row per space:
    each person's list shows their own search and never the other's."""
    from kazma_core.tools import web_search as ws

    marks = w.matrix.plant(tool)

    def results(query: str, max_results: int) -> tuple[list[dict[str, str]], list[str], str]:
        mark = marks.a if scope.require_space_id() == Matrix.SPACE_A else marks.b
        return ([{"title": f"result {mark}", "url": "https://example.org/x", "snippet": f"about {mark}"}],
                ["searxng"], "")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ws, "_run_search", results)
        w.tool("a", tool, **{query_arg: "the shared question"})
        w.tool("b", tool, **{query_arg: "the shared question"})
    mine = w.call("a", "GET", "/api/research/sessions?kind=search").text
    theirs = w.call("b", "GET", "/api/research/sessions?kind=search").text
    assert marks.a in mine and marks.b not in mine, mine
    assert marks.b in theirs and marks.a not in theirs, theirs


@research_case("tool:web_search")
def tool_web_search(w: World) -> None:
    _searched(w, "web_search", "query")


@research_case("tool:web_search_duckduckgo")
def tool_web_search_ddg(w: World) -> None:
    _searched(w, "web_search_duckduckgo", "query")

