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

While that write is in flight the task is still in the table and has already
ended. Callers that ask what is active (``list_active_tasks``,
``get_active_task``, cancel, the watchdog) leave it out. The cap still counts
the slot. An exception while the ending is recorded releases the slot.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, is_terminal
from kazma_core.swarm.task_lifecycle import record_task
from kazma_core.swarm.task_store import TaskStore

from tests._as_request import as_owner

REPO = Path(__file__).resolve().parents[1]
_SWARM = REPO / "kazma-core" / "kazma_core" / "swarm"
_LIMIT = 6

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


class _Boom(Exception):
    """A raise from one step of recording an ending."""


async def _finished_in_window(tmp_path: Path):
    """One task whose work has succeeded and whose ending write is held."""
    store = TaskStore(db_path=str(tmp_path / "tasks.db"))
    engine = _engine(store, 2)
    engine.get_worker("w").dispatch = lambda task, context=None: asyncio.sleep(0, _ok())
    delay = _EndingDelay(store)
    delay.__enter__()
    task = SwarmTask(prompt="p", workers=["w"], timeout=0)
    job = asyncio.create_task(engine.dispatch(task))
    if not await asyncio.to_thread(delay.wait_for, 1):
        delay.release.set()
        delay.__exit__(None, None, None)
        store.close()
        raise AssertionError(f"the ending write did not start: {delay.ids}")
    return store, engine, delay, task, job


def _close_window(store: TaskStore, delay: _EndingDelay) -> None:
    delay.release.set()
    delay.__exit__(None, None, None)
    store.close()


async def test_cancel_in_the_window_says_cancelled_for_a_completed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel during the ending write answers false and writes no cancelled orphan.

    The task is not active to a caller, and it still holds the admission slot.
    """
    store, engine, delay, task, job = await _finished_in_window(tmp_path)
    try:
        assert task.id in engine._active_tasks
        assert engine._active_tasks[task.id].status == TaskStatus.COMPLETED
        assert engine.list_active_tasks() == []
        assert engine.get_active_task(task.id) is None
        engine.config.max_concurrent_tasks = 1
        assert engine.at_capacity() == 1
        import kazma_core.runs.adapters.swarm as swarm_adapter

        monkeypatch.setattr(swarm_adapter, "_engine", lambda: engine)
        listed = await swarm_adapter.ADAPTER.list_runs(time.time(), window_s=86_400)
        own = [run for run in listed if run.native_id == task.id]
        assert own and own[0].owner_alive is False
        orphans: list = []
        orig = store.record_orphan

        def _keep(task_id: str, evidence: dict) -> object:
            orphans.append(evidence)
            return orig(task_id, evidence)

        store.record_orphan = _keep  # type: ignore[method-assign]
        canceller = asyncio.create_task(engine.cancel_task(task.id))
        finished = await _until(canceller.done, timeout=1.0)
        delay.release.set()
        answered = await asyncio.wait_for(canceller, 10)
        result = await asyncio.wait_for(job, 10)
    finally:
        _close_window(store, delay)
    assert finished, "cancel waited on the ending write of a task that had already ended"
    assert answered is False, (answered, result.status, orphans)
    assert not any(item.get("status") == "cancelled" for item in orphans), orphans
    assert result.status == "success"


async def test_reaper_in_the_window_reaps_a_completed_task(tmp_path: Path) -> None:
    """The watchdog does not record a timeout over a task that already succeeded."""
    store, engine, delay, task, job = await _finished_in_window(tmp_path)
    try:
        live = engine._active_tasks[task.id]
        live.timeout = 1
        live.started_at = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
        assert engine.list_active_tasks() == []
        assert engine.get_active_task(task.id) is None
        reaped = engine.reap_stale_tasks()
        delay.release.set()
        await asyncio.wait_for(job, 10)
    finally:
        _close_window(store, delay)
    assert reaped == 0, f"the watchdog reaped {reaped} task that had already completed"


def test_a_durable_miss_does_not_end_a_task_that_already_ended() -> None:
    """A durable dispatch that finds the task already ended does not finalize it."""
    from types import SimpleNamespace

    from kazma_core.swarm.durable import _not_run

    task = SwarmTask(prompt="p", id="ended", status=TaskStatus.COMPLETED)
    task.result = TaskResult(task_id="ended", status="success")

    def _no(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("finalized an ended task")

    engine = SimpleNamespace(
        _active_tasks={"ended": task},
        _endings_recorded=set(),
        _finalize_task=_no,
    )
    assert _not_run(engine, task, 0.0, None, "lost") is task.result


def _patch_ending(site: str, engine: SwarmEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise from one step between the status stamp and the ending write."""
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise _Boom(site)

    if site == "checkpoint":
        monkeypatch.setattr(engine, "_close_open_checkpoint", _raise)
    elif site == "history":
        monkeypatch.setattr("kazma_core.swarm.engine._hist_record_task", _raise)
    elif site == "deliver":
        monkeypatch.setattr("kazma_core.swarm.chat_delivery.deliver_finished_task", _raise)
    elif site == "snapshot":
        original = SwarmTask.from_dict.__func__  # type: ignore[attr-defined]

        def _from_dict(cls: type, data: object) -> SwarmTask:
            made = original(cls, data)
            if is_terminal(getattr(made, "status", None)):
                raise _Boom(site)
            return made

        monkeypatch.setattr(SwarmTask, "from_dict", classmethod(_from_dict))
    else:
        raise AssertionError(site)


@pytest.mark.parametrize("site", ["checkpoint", "history", "deliver", "snapshot"])
async def test_a_raise_while_recording_the_ending_frees_the_slot(
    site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each step that can raise while an ending is recorded leaves the slot free.

    The RUNNING row stays open: the ending write never started. The slot is
    not held until the process restarts.
    """
    store = TaskStore(db_path=str(tmp_path / "raise.db"))
    engine = _engine(store, 2)
    engine.get_worker("w").dispatch = lambda task, context=None: asyncio.sleep(0, _ok())
    _patch_ending(site, engine, monkeypatch)
    task = SwarmTask(prompt="p", workers=["w"], timeout=0)
    try:
        try:
            await engine.dispatch(task)
        except _Boom:
            pass
        stored = await asyncio.to_thread(store.get_task, task.id)
    finally:
        store.close()
    assert stored is not None and not is_terminal(stored.status), getattr(stored, "status", None)
    assert task.id not in engine._active_tasks


async def test_a_raise_while_rejecting_frees_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reject whose ending raises releases the slot from ``reject_checkpoint``."""
    store = TaskStore(db_path=str(tmp_path / "reject.db"))
    engine = _engine(store, 1)
    paused = SwarmTask(prompt="paused", id="paused-1", status=TaskStatus.PAUSED, timeout=0)
    engine._active_tasks[paused.id] = paused
    record_task(engine._task_history, engine._task_lock, paused, max_history=engine._max_history)
    _patch_ending("deliver", engine, monkeypatch)
    try:
        with pytest.raises(_Boom):
            await engine.reject_checkpoint(paused.id, reason="no")
    finally:
        store.close()
    assert paused.id not in engine._active_tasks


# --- the admission table: who may remove a slot, and who may read it ---
#
# A removal (``.pop``, ``del``, ``.clear()``, including through an alias or a
# parameter the caller passed the table as) is allowed only in named places:
# ``_release_admission_slot`` (after the ending is stored), before this
# function's ``_admit_to_store`` (no RUNNING row of this run yet), in the
# branch that says admission failed (``held is not None``, ``admitted is
# False``, ``not reopen``), or in ``_store_refused`` while that function
# still records no ending. ``.clear()`` is never allowed. Calling none of
# the ending helpers is not itself an allowance: a new ``retire_task`` that
# only pops would free an admitted slot.
#
# A read (subscription, ``.get`` / ``.values`` / ``.items`` / ``.keys``,
# iteration, ``in``, ``len``, a copy) that acts on a task calls
# ``task_has_ended`` or ``_task_has_ended``. A read that counts slots still
# held may call ``_occupies_admission_slot`` (an ended task still counts
# until the write lands), and only the named counters do. ``.pop``,
# ``.clear`` and ``.setdefault`` are not reads. Other packages do not name
# the table: ``list_active_tasks`` and ``get_active_task`` are the view, and
# they already leave out a task that has ended. Reading the table from
# outside the engine would cancel, count or mark live a task whose work has
# finished while its ending is stored.

# A function that calls one of these has an ending or a RUNNING row in reach.
# ``_store_refused`` may pop only while it calls none of them.
_OWNS_AN_ENDING = frozenset({
    "_persist",
    "_finalize_task",
    "_dispatch_inner",
    "_run_admitted",
    "_resume",
    "_resume_run",
    "_run_owned",
    "_admit_to_store",
})
# The one refusal that drops a slot because the store never took a RUNNING row.
_REFUSED_BEFORE_A_ROW = frozenset({"_store_refused"})
# Asks whether the task has ended. An acting reader calls one of these.
_ACTING = frozenset({"task_has_ended", "_task_has_ended"})
# Asks whether the slot still counts. An ended task still does, until the write.
_COUNTING = frozenset({"_occupies_admission_slot"})
# Readers whose question is the count, not "is this task active work".
# ``redispatch_recovered_tasks`` skips a slot this process still holds,
# including one whose ending is not stored yet.
_COUNTS_THE_SLOT = frozenset({
    "_at_capacity",
    "running_in",
    "_running_ahead_of",
    "redispatch_recovered_tasks",
})
_COPIES = frozenset({"len", "list", "dict", "set", "tuple", "iter", "sorted"})
_TABLE_NAME = re.compile(r"(?<![A-Za-z0-9])_active_tasks(?![A-Za-z0-9])")
_PACKAGE_ROOTS = (
    REPO / "kazma-core" / "kazma_core",
    REPO / "kazma-ui" / "kazma_ui",
    REPO / "kazma-gateway" / "kazma_gateway",
    REPO / "kazma-cli" / "kazma_cli",
    REPO / "kazma-tui" / "kazma_tui",
    REPO / "kazma-skills" / "kazma_skills",
)


def _called(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_table(node: ast.AST, aliases: set[str]) -> bool:
    """True when *node* is the admission table, an alias of it, or ``getattr`` of it."""
    if isinstance(node, ast.Attribute) and node.attr == "_active_tasks":
        return True
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "_active_tasks"
    ):
        return True
    if isinstance(node, ast.BoolOp):
        return any(_is_table(value, aliases) for value in node.values)
    return False


def _walk(node: ast.AST):
    """*node* and its children, not the bodies of nested functions or classes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield from _walk(child)


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    found: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            found[child] = node
    return found


def _module_key(module: str, keys: set[str]) -> str | None:
    parts = module.split(".")
    for index in range(len(parts)):
        candidate = "/".join(parts[index:])
        if candidate in keys:
            return candidate
    return None


def _aliases(func: ast.AST, bound: set[str]) -> set[str]:
    """Names in *func* that are the admission table, including parameters."""
    names = set(bound)
    changed = True
    while changed:
        changed = False
        for node in _walk(func):
            targets: list[str] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                value = node.value
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                value = node.value
                targets = [node.target.id]
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                value = node.value
                targets = [node.target.id]
            if value is not None and targets and _is_table(value, names):
                for name in targets:
                    if name not in names:
                        names.add(name)
                        changed = True
    return names


def _admission_failure(test: ast.AST) -> bool:
    """The branch where this run did not get the row."""
    if (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Name)
    ):
        return test.operand.id in {"admitted", "reopen"}
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.left, ast.Name):
        op, comp = test.ops[0], test.comparators[0]
        if isinstance(op, ast.Is) and isinstance(comp, ast.Constant) and comp.value is False:
            return test.left.id in {"admitted", "reopen"}
        if isinstance(op, ast.IsNot) and isinstance(comp, ast.Constant) and comp.value is None:
            return test.left.id == "held"
    return False


def _in_failure_if(node: ast.AST, parents: dict[ast.AST, ast.AST], func: ast.AST) -> bool:
    current: ast.AST | None = node
    while current is not None and current is not func:
        parent = parents.get(current)
        if parent is None or isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            return False
        if isinstance(parent, ast.If) and current in parent.body and _admission_failure(parent.test):
            return True
        current = parent
    return False


def _call_names(func: ast.AST) -> set[str]:
    return {name for node in _walk(func) if isinstance(node, ast.Call) and (name := _called(node))}


class _TableScan:
    """Reads and removals of the admission table across the swarm modules."""

    def __init__(self, modules: list[tuple[str, ast.AST]]) -> None:
        self.modules = modules
        self.funcs: dict[tuple[str, str], list[ast.AST]] = {}
        self.imports: dict[str, dict[str, tuple[str, str]]] = {}
        self.bound: dict[int, set[str]] = {}
        self.parents: dict[str, dict[ast.AST, ast.AST]] = {}
        self._collect()
        self._bind_parameters()

    def _collect(self) -> None:
        keys = {key for key, _tree in self.modules}
        for key, tree in self.modules:
            self.parents[key] = _parents(tree)
            self.imports[key] = {}
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module:
                    target = _module_key(node.module, keys)
                    if target is None:
                        continue
                    for alias in node.names:
                        self.imports[key][alias.asname or alias.name] = (target, alias.name)
            for func in _functions(tree):
                self.funcs.setdefault((key, func.name), []).append(func)

    def _resolve(self, module: str, call: ast.Call) -> tuple[str, str] | None:
        func = call.func
        if isinstance(func, ast.Name):
            mapped = self.imports.get(module, {}).get(func.id)
            if mapped is not None and mapped in self.funcs:
                return mapped
            if (module, func.id) in self.funcs:
                return module, func.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self":
            if (module, func.attr) in self.funcs:
                return module, func.attr
        return None

    def _bind_parameters(self) -> None:
        changed = True
        while changed:
            changed = False
            for module, _tree in self.modules:
                for func in _functions(_tree):
                    aliases = _aliases(func, self.bound.get(id(func), set()))
                    for node in _walk(func):
                        if not isinstance(node, ast.Call):
                            continue
                        target = self._resolve(module, node)
                        if target is None:
                            continue
                        for callee in self.funcs.get(target, []):
                            if self._mark(node, callee, aliases):
                                changed = True

    def _mark(self, call: ast.Call, callee: ast.AST, aliases: set[str]) -> bool:
        params = [arg.arg for arg in callee.args.args]
        if params and params[0] == "self":
            params = params[1:]
        bound = self.bound.setdefault(id(callee), set())
        added = False
        pairs = list(zip(params, call.args))
        pairs += [(kw.arg, kw.value) for kw in call.keywords if kw.arg]
        for name, value in pairs:
            if name in params and name not in bound and _is_table(value, aliases):
                bound.add(name)
                added = True
        return added

    def _uses(self, func: ast.AST) -> tuple[bool, list[tuple[ast.AST, str]]]:
        aliases = _aliases(func, self.bound.get(id(func), set()))
        read = False
        removals: list[tuple[ast.AST, str]] = []
        for node in _walk(func):
            if isinstance(node, ast.Subscript) and _is_table(node.value, aliases):
                if isinstance(node.ctx, ast.Load):
                    read = True
                elif isinstance(node.ctx, ast.Del):
                    removals.append((node, "del"))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if _is_table(node.func.value, aliases):
                    attr = node.func.attr
                    if attr in {"pop", "clear"}:
                        removals.append((node, attr))
                    elif attr != "setdefault":
                        read = True
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _COPIES
                and any(_is_table(arg, aliases) for arg in node.args)
            ):
                read = True
            elif isinstance(node, (ast.For, ast.comprehension)) and _is_table(node.iter, aliases):
                read = True
            elif isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and _is_table(comp, aliases):
                        read = True
        return read, removals

    def illegal_removals(self) -> list[str]:
        found: list[str] = []
        for module, _tree in self.modules:
            parents = self.parents[module]
            for func in _functions(_tree):
                calls = _call_names(func)
                _read, removals = self._uses(func)
                for node, kind in removals:
                    if _removal_allowed(func, calls, node, kind, parents):
                        continue
                    found.append(f"{module}.{func.name}:{node.lineno}:{kind}")
        return sorted(found)

    def reader_gaps(self) -> list[str]:
        found: list[str] = []
        for module, _tree in self.modules:
            for func in _functions(_tree):
                read, _removals = self._uses(func)
                if not read:
                    continue
                calls = _call_names(func)
                if not _ACTING.isdisjoint(calls):
                    continue
                if func.name in _COUNTS_THE_SLOT and not _COUNTING.isdisjoint(calls):
                    continue
                found.append(f"{module}.{func.name}")
        return sorted(found)


def _removal_allowed(
    func: ast.AST,
    calls: set[str],
    node: ast.AST,
    kind: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if kind == "clear":
        return False
    if func.name == "_release_admission_slot":
        return True
    admit = [
        call.lineno for call in _walk(func)
        if isinstance(call, ast.Call) and _called(call) == "_admit_to_store"
    ]
    if admit and node.lineno < min(admit):
        return True
    if _in_failure_if(node, parents, func):
        return True
    # A named refusal before any RUNNING row. Any other function that merely
    # calls none of the ending helpers is not allowed to pop.
    return func.name in _REFUSED_BEFORE_A_ROW and _OWNS_AN_ENDING.isdisjoint(calls)


def _scan(modules: list[tuple[str, ast.AST]]) -> _TableScan:
    return _TableScan(modules)


def _snippet(source: str) -> list[tuple[str, ast.AST]]:
    return [("snippet", ast.parse(source))]


def _swarm_modules() -> list[tuple[str, ast.AST]]:
    modules: list[tuple[str, ast.AST]] = []
    for path in sorted(item for item in _SWARM.rglob("*.py") if item.is_file()):
        key = path.relative_to(_SWARM).with_suffix("").as_posix()
        modules.append((key, ast.parse(path.read_text(encoding="utf-8"))))
    return modules


def _mentions_admission_table(source: str) -> bool:
    return _TABLE_NAME.search(source) is not None


def _outside_swarm_reads() -> list[str]:
    """Product modules outside ``kazma_core/swarm`` that name the table."""
    swarm = _SWARM.resolve()
    found: list[str] = []
    for root in _PACKAGE_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            resolved = path.resolve()
            if resolved == swarm or swarm in resolved.parents:
                continue
            if _mentions_admission_table(path.read_text(encoding="utf-8")):
                found.append(resolved.relative_to(REPO).as_posix())
    return found


def test_the_gate_flags_a_slot_freed_beside_the_end_stamp() -> None:
    """Negative control: a slot dropped at the ending, or through an alias.

    A pop beside the stamp, a pop in ``reject_checkpoint`` after it finalizes,
    ``del`` / ``.pop`` / ``.clear()`` through an alias, a pop on the success
    path after admission, ``.clear()`` inside the release helper, and a
    ``retire_task`` that only pops are all flagged. A refusal before a row,
    a pop before ``_admit_to_store``, a pop in ``if held is not None``, and
    the release helper's own ``.pop`` are not.
    """
    old = _scan(_snippet(
        "class E:\n"
        "    def _finalize_task(self, task):\n"
        "        task.completed_at = _utc_now_iso()\n"
        "        self._active_tasks.pop(task.id, None)\n"
        "        self._persist(task)\n"
        "    def _store_refused(self, task):\n"
        "        self._active_tasks.pop(task.id, None)\n"
    ))
    flagged = old.illegal_removals()
    assert any(item.startswith("snippet._finalize_task:") and item.endswith(":pop") for item in flagged)
    assert not any("_store_refused" in item for item in flagged)
    nested = _scan(_snippet(
        "class E:\n"
        "    def reject_checkpoint(self, task_id):\n"
        "        def _mark_failed(task):\n"
        "            task.completed_at = _utc_now_iso()\n"
        "        self._finalize_task(task_id)\n"
        "        self._active_tasks.pop(task_id, None)\n"
    ))
    assert any(item.startswith("snippet.reject_checkpoint:") for item in nested.illegal_removals())
    assert not any("_mark_failed" in item for item in nested.illegal_removals())
    alias = _scan(_snippet(
        "class E:\n"
        "    def _finalize_task(self, task):\n"
        "        tasks = self._active_tasks\n"
        "        tasks.pop(task.id, None)\n"
        "        del tasks[task.id]\n"
        "        tasks.clear()\n"
        "        self._persist(task)\n"
    ))
    kinds = {item.rsplit(":", 1)[-1] for item in alias.illegal_removals()}
    assert kinds == {"pop", "del", "clear"}
    late = _scan(_snippet(
        "class E:\n"
        "    def _dispatch(self, task):\n"
        "        self._active_tasks.pop(task.id, None)\n"
        "        held = self._admit_to_store(task)\n"
        "        if held is not None:\n"
        "            self._active_tasks.pop(task.id, None)\n"
        "        self._active_tasks.pop(task.id, None)\n"
        "        self._run_admitted(task)\n"
    ))
    late_lines = [item for item in late.illegal_removals() if item.startswith("snippet._dispatch:")]
    assert len(late_lines) == 1, late_lines
    helper = _scan(_snippet(
        "class E:\n"
        "    def _release_admission_slot(self, task_id):\n"
        "        self._active_tasks.pop(task_id, None)\n"
        "        self._active_tasks.clear()\n"
    ))
    helper_hits = helper.illegal_removals()
    assert len(helper_hits) == 1 and helper_hits[0].endswith(":clear")
    retire = _scan(_snippet(
        "class E:\n"
        "    def retire_task(self, task_id):\n"
        "        self._active_tasks.pop(task_id, None)\n"
    ))
    assert retire.illegal_removals() == ["snippet.retire_task:3:pop"]


def test_every_slot_pop_is_before_a_running_row_or_after_the_ending_write() -> None:
    """Every removal of the admission table in the swarm, from the source.

    A new ``.pop``, ``del`` or ``.clear()`` after a RUNNING row, outside
    ``_release_admission_slot`` and outside an admission-failure branch,
    frees an admitted slot at the wrong time.
    """
    found = _scan(_swarm_modules()).illegal_removals()
    assert found == [], found


def test_the_gate_flags_a_reader_that_treats_an_ended_task_as_active() -> None:
    """Negative control: a reader that does not ask whether the task has ended.

    Returning the table's values is flagged. The same read that calls
    ``task_has_ended`` is not. A parameter the caller passed the table as is
    the table: reading it without the check is flagged too. An acting reader
    that only calls ``_occupies_admission_slot`` is flagged. A named counter
    that calls it is not.
    """
    raw = _scan(_snippet(
        "class E:\n"
        "    def list_active_tasks(self):\n"
        "        return list(self._active_tasks.values())\n"
    ))
    assert raw.reader_gaps() == ["snippet.list_active_tasks"]
    checked = _scan(_snippet(
        "class E:\n"
        "    def list_active_tasks(self):\n"
        "        return [t for t in self._active_tasks.values() if not task_has_ended(t)]\n"
    ))
    assert checked.reader_gaps() == []
    passed = _scan(_snippet(
        "def cancel_active_task(task_id, active_tasks):\n"
        "    return active_tasks[task_id]\n"
        "class E:\n"
        "    def cancel_task(self, task_id):\n"
        "        return cancel_active_task(task_id, self._active_tasks)\n"
    ))
    assert "snippet.cancel_active_task" in passed.reader_gaps()
    safe = _scan(_snippet(
        "def cancel_active_task(task_id, active_tasks):\n"
        "    task = active_tasks[task_id]\n"
        "    if task_has_ended(task):\n"
        "        return None\n"
        "    return task\n"
        "class E:\n"
        "    def cancel_task(self, task_id):\n"
        "        return cancel_active_task(task_id, self._active_tasks)\n"
    ))
    assert safe.reader_gaps() == []
    acting = _scan(_snippet(
        "class E:\n"
        "    def cancel_task(self, task_id):\n"
        "        task = self._active_tasks.get(task_id)\n"
        "        if task is not None and self._occupies_admission_slot(task):\n"
        "            return task\n"
        "        return None\n"
    ))
    assert acting.reader_gaps() == ["snippet.cancel_task"]
    counted = _scan(_snippet(
        "class E:\n"
        "    def _at_capacity(self):\n"
        "        return sum(1 for task in self._active_tasks.values()\n"
        "                   if self._occupies_admission_slot(task))\n"
    ))
    assert counted.reader_gaps() == []


def test_every_reader_of_the_admission_table_asks_whether_the_task_has_ended() -> None:
    """Every acting read calls the ended-task check. A named counter may count."""
    found = _scan(_swarm_modules()).reader_gaps()
    assert found == [], found


def test_other_packages_do_not_read_the_admission_table() -> None:
    """The admission table stays inside ``kazma_core.swarm``.

    ``list_active_tasks`` and ``get_active_task`` are the only view, and they
    leave out a task that has ended. kazma-core outside that package, and the
    ui, gateway, cli, tui and skills packages, are scanned so a new reader
    cannot cancel, count or mark live a task whose ending is already recorded.
    Tests and scripts are not the product. A catalog key such as
    ``no_active_tasks`` is not the table.
    """
    assert _mentions_admission_table("engine._active_tasks")
    assert not _mentions_admission_table("engine.list_active_tasks()")
    assert not _mentions_admission_table("swarm.no_active_tasks")
    assert _outside_swarm_reads() == []
