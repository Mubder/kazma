"""A paused pipeline can end five ways, and each one has to stick.

Approve, reject, the checkpoint timeout, cancel and the stale-task reaper all
end a pipeline paused at a checkpoint. Live 2026-09-25: four pipelines
restored after a restart were rejected with 200s and came back paused at the
next boot -- the reject was never saved. Cancel on the same tasks answered
"not active", and a fifth, paused with no checkpoint at all, answered "not
found" to Approve and Reject alike.

Most tests here restart the engine over a real task store, because the
restart is where each of these went wrong. They run on SQLite locally and on
Postgres in the CI Postgres job -- the live incident was on Postgres. That
database is shared by the whole job, so every task id is unique, every
assertion is about the test's own rows, and anything left paused is closed
at teardown.
"""

from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import TaskResult
from kazma_core.swarm.task_store import TaskStore

# Verified against a real Postgres (throwaway postgres:16); the CI Postgres
# job runs every test carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres

_REPO = Path(__file__).resolve().parents[1]


def _paused(task_id: str, *, checkpoint: bool = True, timeout: float | None = None) -> SwarmTask:
    metadata: dict = {}
    if checkpoint:
        metadata["hitl_checkpoint"] = {
            "step": 1, "worker": "alpha", "output_preview": "draft", "task_id": task_id,
        }
    if timeout is not None:
        metadata["checkpoint_timeout"] = timeout
    return SwarmTask(
        prompt="Paused pipeline",
        id=task_id,
        type=TaskType.PIPELINE,
        status=TaskStatus.PAUSED,
        workers=["alpha", "beta"],
        result=TaskResult(task_id=task_id, status="paused"),
        metadata=metadata,
    )


def _finished(task_id: str, status: TaskStatus, result: TaskResult) -> SwarmTask:
    task = _paused(task_id)
    task.status = status
    task.result = result
    return task


@pytest.fixture
def db(tmp_path) -> str:
    """The SQLite path; ignored when the store runs on Postgres."""
    return str(tmp_path / "swarm_tasks.db")


@pytest.fixture
def new_id(db):
    """Unique task ids. Whatever a test leaves paused is cancelled at teardown,
    so no later test in a shared database restores it."""
    made: list[str] = []

    def _new(label: str) -> str:
        made.append(f"task-{label}-{uuid.uuid4().hex[:10]}")
        return made[-1]

    yield _new
    store = TaskStore(db_path=db)
    try:
        for task_id in made:
            task = store.get_task(task_id)
            if task is not None and task.status == TaskStatus.PAUSED:
                task.status = TaskStatus.CANCELLED
                store.persist_task(task)
    finally:
        store.close()


def _seed(db: str, *tasks: SwarmTask) -> None:
    store = TaskStore(db_path=db)
    try:
        for task in tasks:
            store.persist_task(task)
    finally:
        store.close()


@pytest.fixture
def boot(db):
    """Start an engine over *db* the way the server's boot does."""
    stores: list[TaskStore] = []

    def _boot() -> SwarmEngine:
        store = TaskStore(db_path=db)
        stores.append(store)
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
        engine.restore_paused_tasks()
        return engine

    yield _boot
    for store in stores:
        store.close()


def _status_on_disk(db: str, task_id: str) -> str | None:
    store = TaskStore(db_path=db)
    try:
        task = store.get_task(task_id)
        return None if task is None else str(task.status)
    finally:
        store.close()


def _paused_on_disk(db: str) -> set[str]:
    """Ids of every paused task in the store -- the set the next boot restores."""
    store = TaskStore(db_path=db)
    try:
        return {t.id for t in store.get_paused_tasks()}
    finally:
        store.close()


async def _background_gate_settles(task_id: str) -> None:
    from kazma_core.background import background_tasks

    name = f"swarm-gate-settle:{task_id}"
    await asyncio.gather(*(t for t in background_tasks() if t.get_name() == name))


# ── reject ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reject_after_a_restart_is_saved(db, boot, new_id):
    """The live failure. The checkpoint handler marks the shared task failed
    before the engine asked "was this already finished?", so the engine took
    its own reject for an earlier ending and skipped the save."""
    task_id = new_id("restored")
    _seed(db, _paused(task_id))
    engine = boot()

    result = await engine.reject_checkpoint(task_id)

    assert result is not None and result.status == "failed"
    assert _status_on_disk(db, task_id) == "failed"
    assert task_id not in _paused_on_disk(db), "the rejected pipeline comes back paused at the next boot"


@pytest.mark.asyncio
async def test_a_paused_task_with_no_checkpoint_is_closed_by_reject(db, boot, new_id):
    """Nothing can approve it, so Reject is the only way to close it."""
    task_id = new_id("orphan")
    _seed(db, _paused(task_id, checkpoint=False))
    engine = boot()

    result = await engine.reject_checkpoint(task_id, reason="Test pipeline")

    assert result is not None and result.status == "failed"
    assert "no checkpoint was pending" in (result.error or "")
    assert _status_on_disk(db, task_id) == "failed"
    assert task_id not in _paused_on_disk(db)


@pytest.mark.asyncio
async def test_reject_leaves_an_unknown_or_finished_task_alone(db, boot, new_id):
    done = new_id("done")
    _seed(db, _finished(done, TaskStatus.COMPLETED,
                        TaskResult(task_id=done, status="success", aggregated_output="kept")))
    engine = boot()

    assert await engine.reject_checkpoint(new_id("unknown")) is None
    assert await engine.reject_checkpoint(done) is None
    assert _status_on_disk(db, done) == "completed"


def test_boot_names_paused_tasks_that_have_no_checkpoint(db, boot, new_id, caplog):
    fine, orphan = new_id("fine"), new_id("orphan")
    _seed(db, _paused(fine), _paused(orphan, checkpoint=False))
    with caplog.at_level("WARNING", logger="kazma_core.swarm.checkpoint_manager"):
        boot()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(orphan in w and "Reject or Cancel" in w for w in warnings), warnings
    assert not any(fine in w for w in warnings)


# ── cancel ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_paused_pipeline_restored_after_a_restart_can_be_cancelled(db, boot, new_id):
    """It is in history only, never in the in-flight map cancel looked at."""
    from kazma_core.safety.hitl_gates import live_gates

    task_id = new_id("restored")
    _seed(db, _paused(task_id))
    engine = boot()
    assert [g.gate_id for g in live_gates(task_id)] == [f"pipeline-{task_id}-step1"]

    assert await engine.cancel_task(task_id) is True
    await _background_gate_settles(task_id)

    assert _status_on_disk(db, task_id) == "cancelled"
    assert task_id not in _paused_on_disk(db)
    assert engine.get_checkpoint_info(task_id) is None, (
        "Approve is still offered on a cancelled task"
    )
    assert live_gates(task_id) == [], "its gate row is still pending on the approvals list"


@pytest.mark.asyncio
async def test_cancel_stops_the_checkpoint_timer(db, boot, new_id):
    """A cancelled pipeline must not be auto-rejected later."""
    task_id = new_id("timed")
    _seed(db, _paused(task_id, timeout=3600))
    engine = boot()  # inside a running loop, so the timer is armed at once
    await engine.arm_pending_checkpoint_timeouts()
    timer = engine._checkpoint_handler._paused[task_id].timeout_task
    assert timer is not None and not timer.done()

    assert await engine.cancel_task(task_id) is True
    await _background_gate_settles(task_id)
    await asyncio.gather(timer, return_exceptions=True)

    assert timer.cancelled()


@pytest.mark.asyncio
async def test_cancel_of_an_unknown_or_finished_task_still_answers_false(db, boot, new_id):
    done = new_id("done")
    _seed(db, _finished(done, TaskStatus.FAILED,
                        TaskResult(task_id=done, status="failed", error="earlier")))
    engine = boot()

    assert await engine.cancel_task(new_id("unknown")) is False
    assert await engine.cancel_task(done) is False
    assert _status_on_disk(db, done) == "failed"


# ── the reaper ────────────────────────────────────────────────────────


def test_the_reaper_settles_the_gate_of_a_paused_pipeline(new_id):
    """It dropped the checkpoint entry but never settled the gate row."""
    from kazma_core.safety.hitl_gates import live_gates
    from kazma_core.swarm.checkpoint import HITLCheckpoint
    from kazma_core.swarm.checkpoint_manager import _gate_register_pipeline

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    task = _paused(new_id("stale"))
    task.timeout = 1.0
    task.started_at = "2020-01-01T00:00:00+00:00"
    engine._active_tasks[task.id] = task
    engine._checkpoint_handler.store_paused_pipeline(
        task=task,
        checkpoint=HITLCheckpoint(task_id=task.id, step=1, worker="alpha", output_preview="x"),
        worker_results=[],
        blackboard_data={},
    )
    _gate_register_pipeline(task.id, 1, "alpha", "x")

    assert engine.reap_stale_tasks() == 1

    assert engine.get_checkpoint_info(task.id) is None
    assert live_gates(task.id) == []


# ── the routes ────────────────────────────────────────────────────────


@pytest.fixture
def client(boot, monkeypatch):
    """The swarm routes over a booted engine, as an admin."""
    monkeypatch.setattr("kazma_ui.auth.get_kazma_secret", lambda: "")
    monkeypatch.setattr(
        "kazma_ui.auth.get_request_principal", lambda *a, **k: {"source": "secret"}
    )
    from kazma_ui.services import reset_swarm_service
    from kazma_ui.swarm_panel import create_swarm_router

    def _client() -> TestClient:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(_REPO / "kazma-ui/kazma_ui/templates"))
        app.include_router(create_swarm_router(templates, swarm_manager=boot()))
        return TestClient(app)

    reset_swarm_service()
    yield _client
    reset_swarm_service()


def test_the_reject_route_closes_a_paused_task_with_no_checkpoint(db, client, new_id):
    task_id = new_id("orphan")
    _seed(db, _paused(task_id, checkpoint=False))

    response = client().post(f"/api/swarm/tasks/{task_id}/reject")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert task_id not in _paused_on_disk(db)


def test_the_approve_route_says_there_is_nothing_to_approve(db, client, new_id):
    task_id = new_id("orphan")
    _seed(db, _paused(task_id, checkpoint=False))

    response = client().post(f"/api/swarm/tasks/{task_id}/approve")

    assert response.status_code == 409, "the task exists; 'not found' was wrong"
    assert "Reject it to close it" in response.json()["message"]
    assert task_id in _paused_on_disk(db), "approve must not change anything"


def test_the_cancel_route_cancels_a_pipeline_restored_after_a_restart(db, client, new_id):
    task_id = new_id("restored")
    _seed(db, _paused(task_id))

    response = client().post(f"/api/swarm/tasks/{task_id}/cancel")

    assert response.status_code == 200, response.text
    assert _status_on_disk(db, task_id) == "cancelled"


@pytest.mark.parametrize("action", ["approve", "reject", "cancel"])
def test_an_unknown_task_is_still_not_found(client, new_id, action):
    response = client().post(f"/api/swarm/tasks/{new_id('unknown')}/{action}")
    assert response.status_code == 404


def test_the_cancel_route_refuses_a_finished_task(db, client, new_id):
    done = new_id("done")
    _seed(db, _finished(done, TaskStatus.COMPLETED, TaskResult(task_id=done, status="success")))

    response = client().post(f"/api/swarm/tasks/{done}/cancel")

    assert response.status_code == 404
    assert _status_on_disk(db, done) == "completed"


# ── the class: one writer for a task's ending ─────────────────────────
#
# _finalize_task saves the ending, tells the panel, and closes the task's
# checkpoint. Cancel skipping the last of those is what left Approve on
# cancelled tasks. A new way to end a task that sets the status itself skips
# all three unless it says how it covers them.

_TERMINAL_MEMBERS = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}
_TERMINAL_STRINGS = {"completed", "failed", "cancelled", "timeout"}

# (file under kazma_core/swarm, function) -> why it may set the status itself.
_ENDS_OUTSIDE_FINALIZE = {
    ("checkpoint.py", "HITLCheckpointHandler.reject"):
        "the checkpoint's own close; the engine saves the task after it",
    ("engine.py", "SwarmEngine.reject_checkpoint._mark_failed"):
        "runs after the handler closed the checkpoint (or there was none), "
        "and the caller saves the task",
    ("task_store.py", "TaskStore.requeue_orphaned_running"):
        "only 'running' rows left by a crash; a paused task never gets here",
}


def _terminal_value(node: ast.AST, *, strings: bool) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "TaskStatus"
            and sub.attr in _TERMINAL_MEMBERS
        ):
            return True
        if strings and isinstance(sub, ast.Constant) and sub.value in _TERMINAL_STRINGS:
            return True
    return False


def _status_endings(source: str, *, strings: bool = True) -> set[str]:
    """Functions that set a task's status to a terminal value themselves."""
    found: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _scope(self, node: ast.AST) -> None:
            self.stack.append(node.name)  # type: ignore[attr-defined]
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "status"
                    and not (isinstance(target.value, ast.Name) and target.value.id == "self")
                    and _terminal_value(node.value, strings=strings)
                    and "_finalize_task" not in self.stack
                ):
                    found.add(".".join(self.stack) or "<module>")
            self.generic_visit(node)

    _Visitor().visit(ast.parse(source))
    return found


def test_only_the_declared_places_end_a_task_without_finalize():
    swarm = _REPO / "kazma-core" / "kazma_core" / "swarm"
    found = {
        (path.name, fn)
        for path in sorted(swarm.glob("*.py"))
        for fn in _status_endings(path.read_text(encoding="utf-8"))
    }
    undeclared = found - set(_ENDS_OUTSIDE_FINALIZE)
    assert not undeclared, (
        f"{sorted(undeclared)} end a task by setting its status directly. End it "
        "through SwarmEngine._finalize_task, which saves it, tells the panel and "
        "closes its checkpoint; or add it to _ENDS_OUTSIDE_FINALIZE saying how "
        "it covers those three."
    )
    stale = set(_ENDS_OUTSIDE_FINALIZE) - found
    assert not stale, f"{sorted(stale)} no longer set a status; drop them from the list"


def test_no_other_package_ends_a_swarm_task_itself():
    swarm = _REPO / "kazma-core" / "kazma_core" / "swarm"
    offenders = []
    for package in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills"):
        for path in sorted((_REPO / package).rglob("*.py")):
            if path.parent == swarm:
                continue  # covered above, with its declarations
            if any(part == "tests" or part.endswith("_tests") for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "TaskStatus." not in text:
                continue
            if _status_endings(text, strings=False):
                offenders.append(str(path.relative_to(_REPO)))
    assert not offenders, f"{offenders} set a swarm task's ending outside the engine"


def test_the_scanner_sees_an_ending_that_skips_finalize():
    """Negative control: a scanner that never flags anything proves nothing."""
    source = (
        "class Engine:\n"
        "    def cancel_somehow(self, task):\n"
        "        task.status = TaskStatus.CANCELLED\n"
        "    def time_out(self, task):\n"
        "        task.status = TaskStatus.TIMEOUT if task else 'failed'\n"
        "    def _finalize_task(self, task):\n"
        "        task.status = TaskStatus.FAILED\n"
        "    def pause(self, task):\n"
        "        task.status = TaskStatus.PAUSED\n"
        "class Stage:\n"
        "    def __init__(self):\n"
        "        self.status = 'completed'\n"
    )
    assert _status_endings(source) == {"Engine.cancel_somehow", "Engine.time_out"}
