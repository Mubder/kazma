"""The matrix cases of Spaces S1-14: Scheduled (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
the scheduled jobs of person B's ``matrix-b``, through the product's own
Scheduled router behind the real tenant middleware, on the real scheduler
and store, or through the product's own scheduling tools.

Jobs are planted straight into the store: a booking by anyone but the owner
is refused while every address Kazma can deliver to is the owner's
(``scheduler.NO_OWN_ADDRESS``, until people link their own chat apps,
S3-05), and the cases check that refusal too. Nothing is fired here
(``tests/test_cron_spaces.py`` fires).
"""

from __future__ import annotations

import importlib
import secrets
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._chat_space_cases import _boundary
from tests._space_matrix import Matrix, case

ITEM = "S1-14"
#: The only address Kazma has before people link their own chat apps.
OWNER_CHAT = "telegram:owner-chat"
_TEMPLATES = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"
_SAME_ORIGIN = {"X-Requested-With": "XMLHttpRequest"}
_UNKNOWN = "cron-000000000000"
#: A space the owner and person A are both members of (the S1-14 review: a
#: member must not put their words into a job that fires as the owner).
SHARED = "matrix-cron-shared"


@dataclass
class Job:
    """A planted job: its id and the marker its prompt holds."""

    id: str
    mark: str


class World:
    """The Scheduled router in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, sched: Any, store: Any, db: Path) -> None:
        self.matrix = matrix
        self.client = client
        self.sched = sched
        self.store = store
        self.db = db

    def space_of(self, who: str) -> str:
        return {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]

    def account_of(self, who: str) -> str:
        return {"a": self.matrix.account_a, "b": self.matrix.account_b}[who]

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: self.space_of(who), **_SAME_ORIGIN}
        return self.client.request(method, path, headers=headers, **kwargs)

    def plant(self, who: str, mark: str, *, status: str = "pending") -> Job:
        """A reminder in *who*'s space, booked by them, its prompt carrying
        *mark* (straight into the store: see the module's note)."""
        from kazma_core.cron.store import JobStatus, ScheduledJob

        finished = status in ("done", "failed", "cancelled")
        job = ScheduledJob(
            job_id=f"cron-{secrets.token_hex(6)}", timing="2h", prompt=f"remind {mark}",
            platform="telegram", thread_id=f"thread-{mark}", kind="reminder",
            next_run=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            tenant_id=self.space_of(who), delivery_target=OWNER_CHAT, status=JobStatus(status),
            finished_at=datetime.now(UTC).isoformat() if finished else "",
            creator=self.account_of(who),
        )
        self.client.portal.call(self.store.insert, job)
        return Job(id=job.job_id, mark=mark)

    def owners_in_shared(self, mark: str, *, status: str = "pending") -> Job:
        """A reminder the owner booked in :data:`SHARED`, a space person A is
        a member of beside the owner: it fires as the owner."""
        from kazma_core.cron.store import JobStatus, ScheduledJob
        from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

        finished = status in ("done", "failed", "cancelled")
        job = ScheduledJob(
            job_id=f"cron-{secrets.token_hex(6)}", timing="2h", prompt=f"remind {mark}",
            platform="telegram", thread_id=f"thread-{mark}", kind="reminder",
            next_run=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            tenant_id=SHARED, delivery_target=OWNER_CHAT, status=JobStatus(status),
            finished_at=datetime.now(UTC).isoformat() if finished else "",
            creator=OWNER_ACCOUNT_ID,
        )
        self.client.portal.call(self.store.insert, job)
        return Job(id=job.job_id, mark=mark)

    def call_in_shared(self, method: str, path: str, **kwargs: Any) -> Any:
        """Person A, asking in :data:`SHARED`."""
        headers = {"x-matrix-person": "a", space_binding.SPACE_HEADER: SHARED, **_SAME_ORIGIN}
        return self.client.request(method, path, headers=headers, **kwargs)

    def tool_in_shared(self, name: str, *args: Any, **kwargs: Any) -> str:
        """The product's scheduling tool *name*, run as person A in :data:`SHARED`."""
        tools = importlib.import_module("kazma_skills.native.task_scheduler_cron.tools")
        fn = getattr(tools, name)
        account = self.matrix.account_a

        async def as_member() -> str:
            with scope.bind_scope(SHARED, account):
                return str(await fn(*args, **kwargs))

        return self.client.portal.call(as_member)

    def pair(self, word: str, **kwargs: Any) -> tuple[Job, Job]:
        marks = self.matrix.plant(word)
        return self.plant("a", marks.a, **kwargs), self.plant("b", marks.b, **kwargs)

    def held(self, job: Job) -> dict[str, Any]:
        """A job as the store holds it, whoever asks ({} when gone)."""
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(
                "SELECT prompt, timing, status, tenant_id, version FROM cron_jobs WHERE job_id = ?",
                (job.id,)).fetchone()
        finally:
            conn.close()
        return {} if row is None else dict(zip(("prompt", "timing", "status", "tenant", "version"), row,
                                               strict=True))

    def count(self) -> int:
        conn = sqlite3.connect(str(self.db))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM cron_jobs").fetchone()[0])
        finally:
            conn.close()

    def tool(self, who: str, name: str, *args: Any, **kwargs: Any) -> str:
        """The product's scheduling tool *name*, run as *who* in their space
        (on the app's loop, where the store lives)."""
        tools = importlib.import_module("kazma_skills.native.task_scheduler_cron.tools")
        fn = getattr(tools, name)
        space, account = self.space_of(who), self.account_of(who)

        async def as_them() -> str:
            with scope.bind_scope(space, account):
                return str(await fn(*args, **kwargs))

        return self.client.portal.call(as_them)


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the product's Scheduled router behind the real tenant
    middleware, on the real scheduler and a private store. The person a
    request is signed in as is named by a test header: sign-in is S1-03's."""
    import kazma_core.cron.scheduler as sm
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from kazma_ui.scheduled_api import create_scheduled_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    sent = importlib.import_module("kazma_core.tools.send_message")
    if SHARED not in (identity.read_registry().get("spaces") or {}):
        identity.create_space("Matrix shared", created_by=OWNER_ACCOUNT_ID, space_id=SHARED)
        identity.add_member(SHARED, matrix.account_a)
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        db = Path(tmp) / "cron.db"
        store = sm.SQLiteCronStore(str(db))
        sched = sm.CronScheduler(store=store, graph_builder=None)  # never started: no poll loop
        patch.setattr(sm, "get_cron_scheduler", lambda: sched)
        patch.setattr(sent, "operator_telegram_target", lambda: OWNER_CHAT)
        patch.setattr(sent, "resolve_delivery_target", lambda: OWNER_CHAT)

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> Any:
            await store.init()
            yield
            await store.close()

        class _Agent:
            config: dict[str, Any] = {}

        app = FastAPI(lifespan=lifespan)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_scheduled_router(_Agent(), Jinja2Templates(directory=str(_TEMPLATES))))
        with TestClient(app) as client:
            yield World(matrix, client, sched, store, db)


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def scheduled_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        return case(target, ITEM)(_in_world(run))

    return register


def _never_shown(response: Any, theirs: Job) -> None:
    assert theirs.mark not in response.text and theirs.id not in response.text, response.text


def _same_as_unknown(w: World, method: str, path: str, theirs: Job, **kwargs: Any) -> None:
    """A request for B's job answers exactly what a request for no job does."""
    refused = w.call("a", method, path.format(theirs.id), **kwargs)
    nobodys = w.call("a", method, path.format(_UNKNOWN), **kwargs)
    assert refused.status_code == nobodys.status_code == 404, refused.text
    # The answer echoes the id asked for, and says nothing else.
    assert refused.text.replace(theirs.id, _UNKNOWN) == nobodys.text
    assert theirs.mark not in refused.text


# ── the list and one job ─────────────────────────────────────────────────


@scheduled_case("GET /api/scheduled/tasks")
def scheduled_list(w: World) -> None:
    mine, theirs = w.pair("list")
    listed = w.call("a", "GET", "/api/scheduled/tasks")
    assert listed.status_code == 200, listed.text
    assert mine.id in listed.text and mine.mark in listed.text
    _never_shown(listed, theirs)


@scheduled_case("GET /api/scheduled/cron/{job_id}")
def scheduled_get(w: World) -> None:
    mine, theirs = w.pair("get")
    _same_as_unknown(w, "GET", "/api/scheduled/cron/{}", theirs)
    own = w.call("a", "GET", f"/api/scheduled/cron/{mine.id}")
    assert own.status_code == 200 and mine.mark in own.text


# ── changes ──────────────────────────────────────────────────────────────


@scheduled_case("PUT /api/scheduled/cron/{job_id}")
def scheduled_edit(w: World) -> None:
    mine, theirs = w.pair("edit")
    before = w.held(theirs)
    _same_as_unknown(w, "PUT", "/api/scheduled/cron/{}", theirs, json={"prompt": "taken over"})
    assert w.held(theirs) == before
    done = w.call("a", "PUT", f"/api/scheduled/cron/{mine.id}", json={"prompt": "remind me of mine"})
    assert done.status_code == 200, done.text
    assert w.held(mine)["prompt"] == "remind me of mine" and w.held(theirs) == before
    _member_cannot_reword_the_owners_job(w)


def _member_cannot_reword_the_owners_job(w: World) -> None:
    """Inside a shared space: A is a member and sees the owner's job, but its
    words are the owner's (it fires as the owner, into the owner's chat). A
    new prompt is refused; a new time is not."""
    owners = w.owners_in_shared("shared-edit")
    before = w.held(owners)
    refused = w.call_in_shared("PUT", f"/api/scheduled/cron/{owners.id}", json={"prompt": "my words"})
    assert refused.status_code == 403 and refused.json()["code"] == "not_yours", refused.text
    assert w.held(owners) == before
    daily = w.call_in_shared("PUT", f"/api/scheduled/cron/{owners.id}", json={"timing": "every day at 09:00"})
    assert daily.status_code == 403 and daily.json()["code"] == "not_yours", daily.text
    assert w.held(owners) == before, "a recurrence would fire it as the owner every day"
    timed = w.call_in_shared("PUT", f"/api/scheduled/cron/{owners.id}", json={"timing": "4h"})
    assert timed.status_code == 200, timed.text
    assert w.held(owners)["prompt"] == before["prompt"]


@scheduled_case("DELETE /api/scheduled/cron/{job_id}")
def scheduled_delete(w: World) -> None:
    mine, theirs = w.pair("delete")
    before = w.held(theirs)
    _same_as_unknown(w, "DELETE", "/api/scheduled/cron/{}", theirs)
    assert w.held(theirs) == before
    done = w.call("a", "DELETE", f"/api/scheduled/cron/{mine.id}")
    assert done.status_code == 200, done.text
    assert w.held(mine)["status"] == "cancelled" and w.held(theirs) == before


@scheduled_case("POST /api/scheduled/cron/{job_id}/restore")
def scheduled_restore(w: World) -> None:
    mine, theirs = w.pair("restore", status="done")
    before = w.held(theirs)
    _same_as_unknown(w, "POST", "/api/scheduled/cron/{}/restore", theirs, json={"timing": "3h"})
    assert w.held(theirs) == before
    done = w.call("a", "POST", f"/api/scheduled/cron/{mine.id}/restore", json={"timing": "3h"})
    assert done.status_code == 200, done.text
    assert w.held(mine)["status"] == "pending" and w.held(theirs) == before
    # Inside a shared space: the owner's finished job is not A's to run again.
    owners = w.owners_in_shared("shared-restore", status="done")
    before = w.held(owners)
    refused = w.call_in_shared("POST", f"/api/scheduled/cron/{owners.id}/restore", json={"timing": "3h"})
    assert refused.status_code == 403 and refused.json()["code"] == "not_yours", refused.text
    assert w.held(owners) == before


# ── bookings ─────────────────────────────────────────────────────────────


def _refused_booking(w: World, path: str, body: dict[str, Any]) -> None:
    """A booking by A lands nowhere: not in A's space (the only address is
    the owner's), and never in B's."""
    _mine, theirs = w.pair("book")
    before, count = w.held(theirs), w.count()
    refused = w.call("a", "POST", path, json=body)
    assert refused.status_code == 409 and refused.json()["code"] == "no_own_address", refused.text
    assert w.count() == count and w.held(theirs) == before
    _never_shown(refused, theirs)


@scheduled_case("POST /api/scheduled/cron")
def scheduled_book(w: World) -> None:
    _refused_booking(w, "/api/scheduled/cron", {"timing": "2h", "prompt": "water the plants",
                                               "kind": "reminder"})


@scheduled_case("POST /api/scheduled/recipes/{recipe_id}")
def scheduled_recipe(w: World) -> None:
    from kazma_core.cron.recipes import RECIPES

    _refused_booking(w, f"/api/scheduled/recipes/{RECIPES[0].id}", {"at": "08:00"})


# ── routes that keep nothing of a space ──────────────────────────────────

for _method, _path in (("GET", "/scheduled"), ("GET", "/api/scheduled/recipes")):
    case(f"{_method} {_path}", ITEM)(_boundary(_method, _path))


# ── tools ────────────────────────────────────────────────────────────────


@scheduled_case("tool:list_scheduled")
def tool_list(w: World) -> None:
    mine, theirs = w.pair("toollist")
    listed = w.tool("a", "list_scheduled")
    assert mine.id in listed and theirs.id not in listed and theirs.mark not in listed


@scheduled_case("tool:edit_scheduled")
def tool_edit(w: World) -> None:
    mine, theirs = w.pair("tooledit")
    before = w.held(theirs)
    refused = w.tool("a", "edit_scheduled", theirs.id, prompt="taken over")
    assert '"not_found"' in refused and theirs.mark not in refused
    assert w.held(theirs) == before
    w.tool("a", "edit_scheduled", mine.id, prompt="mine again")
    assert w.held(mine)["prompt"] == "mine again" and w.held(theirs) == before
    # Inside a shared space: the owner's job is not A's to reword.
    owners = w.owners_in_shared("shared-tooledit")
    before = w.held(owners)
    refused = w.tool_in_shared("edit_scheduled", owners.id, prompt="my words")
    assert '"not_yours"' in refused, refused
    assert w.held(owners) == before


@scheduled_case("tool:cancel_scheduled")
def tool_cancel(w: World) -> None:
    mine, theirs = w.pair("toolcancel")
    before = w.held(theirs)
    refused = w.tool("a", "cancel_scheduled", theirs.id)
    assert '"not_found"' in refused and theirs.mark not in refused
    assert w.held(theirs) == before
    w.tool("a", "cancel_scheduled", mine.id)
    assert w.held(mine)["status"] == "cancelled" and w.held(theirs) == before


@scheduled_case("tool:schedule_task")
def tool_schedule(w: World) -> None:
    _mine, theirs = w.pair("toolbook")
    before, count = w.held(theirs), w.count()
    answer = w.tool("a", "schedule_task", timing="2h", prompt="water the plants")
    assert '"no_own_address"' in answer and theirs.mark not in answer
    assert w.count() == count and w.held(theirs) == before


# (tests/test_cron_spaces.py books as the owner, who can: there the job is
# the owner's and fires in its space.)
