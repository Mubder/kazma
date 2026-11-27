"""The admission slot stays until the task's ending is in the store.

The swarm soak counts tasks in flight from stored ``started_at`` /
``completed_at``, and a row with no ``completed_at`` counts as running until
the check (``scripts/swarm_soak.py`` ``peak_in_flight``). The limit counts
``_active_tasks``. On 2026-11-27 a whole-suite run saw 7 in the store against
a limit of 6; alone, the same tests passed.

``_finalize_task`` stamped ``completed_at`` and dropped the slot, then wrote
the ending in a thread. Under load that write was still in flight when the
next task was admitted and its start stored, so the store showed one more
running task than the slots ever held. A rejected paused task did the same:
``reject_checkpoint`` dropped the slot before the ending write landed.

The slot is what the limit governs and the store is what the soak measures.
A task that was admitted keeps its slot until that write has landed, so a
reader cannot see a seventh running row while six slots are the cap. A
refusal before any RUNNING row still drops the slot at once.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskStatus, is_terminal
from kazma_core.swarm.task_lifecycle import record_task
from kazma_core.swarm.task_store import TaskStore

from tests._as_request import as_owner

REPO = Path(__file__).resolve().parents[1]
_SWARM = REPO / "kazma-core" / "kazma_core" / "swarm"
_LIMIT = 6

#: A pop of ``_active_tasks`` that is not an admitted task's slot. Each is a
#: task that never got a RUNNING row of this run (refused, or another run
#: holds the id). ``_release_admission_slot`` is the one pop after the ending
#: write.
_SLOT_POPS = frozenset({
    "_store_refused",
    "_stopped_at_admission",
    "_approval_stopped_at_admission",
    "_dispatch",
    "approve_checkpoint",
    "_release_admission_slot",
})

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(autouse=True)
def _the_owners_request():
    with as_owner():
        yield


@pytest.fixture(autouse=True)
def _inert(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _no_self_improvement(task, worker_results, status):
        return None

    async def _no_memory_index(**_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _no_self_improvement)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _no_memory_index)
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


class _EndingDelay:
    """Hold every terminal ``persist_task`` until :meth:`release` is set.

    The admission write is not delayed: the RUNNING row lands, and the ending
    does not, which is the window the soak's check reads."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.release = threading.Event()
        self.ids: list[str] = []
        self._lock = threading.Lock()
        self._orig = store.persist_task

    def persist_task(self, task: SwarmTask) -> bool:
        if is_terminal(getattr(task, "status", None)):
            with self._lock:
                self.ids.append(str(task.id))
            if not self.release.wait(20):
                raise TimeoutError("the ending write was still held")
        return self._orig(task)

    def count(self) -> int:
        with self._lock:
            return len(self.ids)

    def wait_for(self, n: int, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count() >= n:
                return True
            time.sleep(0.02)
        return False

    def __enter__(self) -> _EndingDelay:
        self.store.persist_task = self.persist_task  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self.store.persist_task = self._orig  # type: ignore[method-assign]


def _engine(store: TaskStore, limit: int) -> SwarmEngine:
    engine = SwarmEngine(
        SwarmConfig(enabled=True, max_concurrent_tasks=limit, workers=[]),
        task_store=store,
    )
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    return engine


def _ok(worker: str = "w") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": "ok", "error": None}


def _held(engine: SwarmEngine) -> int:
    paused = TaskStatus.PAUSED
    return sum(1 for task in engine.list_active_tasks() if task.status != paused)


def _store_intervals(db: Path) -> list[tuple[str, str | None]]:
    conn = sqlite3.connect(str(db))
    try:
        return [(row[0], row[1]) for row in conn.execute(
            "SELECT started_at, completed_at FROM swarm_tasks")]
    finally:
        conn.close()


def _peak(db: Path) -> int:
    import sys

    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import swarm_soak

    return swarm_soak.peak_in_flight(_store_intervals(db))


async def _until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_a_delayed_ending_write_cannot_show_one_more_task_than_the_limit(tmp_path: Path) -> None:
    """Six tasks finish while their ending writes are held. A seventh dispatch
    must be refused, and the store — read the way the soak reads it, counting
    a missing ``completed_at`` as still running — must not show seven."""
    store = TaskStore(db_path=str(tmp_path / "tasks.db"))
    engine = _engine(store, _LIMIT)
    slot_peak = 0

    async def _body(task: str, context: object = None) -> dict:
        nonlocal slot_peak
        slot_peak = max(slot_peak, _held(engine))
        return _ok()

    engine.get_worker("w").dispatch = _body
    delay = _EndingDelay(store)
    jobs = [
        asyncio.create_task(engine.dispatch(SwarmTask(prompt=f"t{i}", workers=["w"], timeout=0)))
        for i in range(_LIMIT)
    ]
    try:
        with delay:
            assert await asyncio.to_thread(delay.wait_for, _LIMIT), delay.ids
            extra = asyncio.create_task(
                engine.dispatch(SwarmTask(prompt="seventh", workers=["w"], timeout=0)))
            await _until(lambda: extra.done() or delay.count() >= _LIMIT + 1)
            held = _held(engine)
            during = _peak(Path(store._db_path))
            delay.release.set()
            await asyncio.wait_for(asyncio.gather(*jobs, extra), 10)
    finally:
        delay.release.set()
        store.close()
    final = _peak(Path(store._db_path))
    assert during <= _LIMIT, (
        f"store showed {during} tasks in flight while endings were unwritten "
        f"(limit {_LIMIT}); slots peaked at {slot_peak} and {held} were held then; "
        f"after the writes landed the store showed {final}"
    )
    assert slot_peak <= _LIMIT, slot_peak
    assert final <= _LIMIT, final
    assert extra.result().metadata.get("admission_denied") is True


async def test_a_rejected_task_keeps_its_slot_until_the_ending_is_stored(tmp_path: Path) -> None:
    """The reject path is the other place an admitted task was dropped from
    ``_active_tasks`` before its ending write. While that write is held, the
    limit is still full."""
    store = TaskStore(db_path=str(tmp_path / "tasks.db"))
    engine = _engine(store, 1)
    engine.get_worker("w").dispatch = lambda task, context=None: asyncio.sleep(0, _ok())
    paused = SwarmTask(prompt="paused", id="paused-1", status=TaskStatus.PAUSED, timeout=0)
    engine._active_tasks[paused.id] = paused
    record_task(engine._task_history, engine._task_lock, paused, max_history=engine._max_history)
    delay = _EndingDelay(store)
    try:
        with delay:
            rejected = asyncio.create_task(engine.reject_checkpoint(paused.id, reason="no"))
            assert await asyncio.to_thread(delay.wait_for, 1), "reject did not write an ending"
            extra = asyncio.create_task(
                engine.dispatch(SwarmTask(prompt="next", workers=["w"], timeout=0)))
            await _until(lambda: extra.done() or delay.count() >= 2)
            still_held = paused.id in engine._active_tasks
            delay.release.set()
            await asyncio.wait_for(asyncio.gather(rejected, extra), 10)
    finally:
        delay.release.set()
        store.close()
    assert still_held, "the slot was freed before the ending write landed"
    assert extra.result().metadata.get("admission_denied") is True
    assert rejected.result() is not None and rejected.result().status == "failed"


def _is_slot_pop(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pop"
        and "_active_tasks" in ast.unparse(node.func.value)
    )


def slot_pop_functions(tree: ast.AST) -> set[str]:
    """The functions that pop ``_active_tasks``, nested functions as themselves."""
    found: set[str] = set()

    class _Visit(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            for stmt in node.body:
                self.visit(stmt)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if self.stack and _is_slot_pop(node):
                found.add(self.stack[-1])
            self.generic_visit(node)

    _Visit().visit(tree)
    return found


def _swarm_sources() -> list[Path]:
    return sorted(path for path in _SWARM.rglob("*.py") if path.is_file())


def test_the_gate_flags_a_slot_freed_beside_the_end_stamp() -> None:
    """Negative control: the old ending, which popped the slot at the stamp
    and wrote afterwards. A pop in a function the allowlist names is not it."""
    old = ast.parse(
        "class E:\n"
        "    def _finalize_task(self, task):\n"
        "        task.completed_at = _utc_now_iso()\n"
        "        self._active_tasks.pop(task.id, None)\n"
        "        self._persist(task)\n"
        "    def _store_refused(self, task):\n"
        "        self._active_tasks.pop(task.id, None)\n"
    )
    assert slot_pop_functions(old) - _SLOT_POPS == {"_finalize_task"}
    nested = ast.parse(
        "class E:\n"
        "    def reject_checkpoint(self, task_id):\n"
        "        def _mark_failed(task):\n"
        "            task.completed_at = _utc_now_iso()\n"
        "        self._active_tasks.pop(task_id, None)\n"
    )
    assert slot_pop_functions(nested) - _SLOT_POPS == {"reject_checkpoint"}


def test_every_slot_pop_is_before_a_running_row_or_after_the_ending_write() -> None:
    """Every ``_active_tasks.pop`` in the swarm, from the source. A new one
    that is not in ``_SLOT_POPS`` frees an admitted slot at the wrong time."""
    found: set[str] = set()
    for path in _swarm_sources():
        found |= slot_pop_functions(ast.parse(path.read_text(encoding="utf-8")))
    assert found == _SLOT_POPS, sorted(found - _SLOT_POPS) or sorted(_SLOT_POPS - found)
