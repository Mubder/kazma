"""Truthful, live, non-blocking (Swarm audit 2026-10-13, stage 4).

Each test inverts a witness in ``session-artifacts/audits-2026-10-13/``:

* SW-S09 -- a reconnect replayed the whole history with no ids, and nothing
  kept a quiet stream alive. Now every event has an ``id:``, a reconnect gets
  only what it missed, a quiet stream sends ``: keepalive``, a stream has a
  lifetime, and open streams are capped.
* SW-S08 -- approve held the request for the whole remaining pipeline, the
  page reported any answer (a 403 included) as approved, and no page
  dispatch asked for the background. Now approve answers 202 once the
  resumed run is recorded RUNNING, every answer has ``status`` ok|error plus
  ``task_status``, and the page reads it.
* SW-E07 / SW-S12 -- the task store, the shared breakers, the routing
  embedder and the approval path's settings ran on the event loop. Now they
  run in threads; a tracer over every task type and the approval path finds
  none on the loop.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore

REPO = Path(__file__).resolve().parents[1]
SWARM_JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "swarm.js"


@pytest.fixture(autouse=True)
def _inert(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _none)
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


def _ok(worker: str, output: str = "ok") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


# ── SW-S09: the task stream ───────────────────────────────────────────────


async def _frames(gen, n: int) -> list[str]:
    return [await gen.__anext__() for _ in range(n)]


async def test_every_event_has_an_id_and_a_reconnect_gets_only_what_it_missed(monkeypatch) -> None:
    from kazma_ui import swarm_sse

    monkeypatch.setattr(swarm_sse, "get_swarm_engine", lambda: None)
    bus = swarm_sse.SSEEventBus()
    task = SwarmTask(prompt="p", workers=["w"], status=TaskStatus.RUNNING)
    for i in range(3):
        bus.emit(task.id, "worker_progress", {"worker": "w", "tokens": i})

    first_gen = swarm_sse._stream_events(task.id, bus, task)
    first = await _frames(first_gen, 3)
    await first_gen.aclose()
    assert [f.split("\n", 1)[0] for f in first] == ["id: 1", "id: 2", "id: 3"]

    bus.emit(task.id, "worker_progress", {"worker": "w", "tokens": 3})
    again = swarm_sse._stream_events(task.id, bus, task, last_event_id=2)
    resumed = await _frames(again, 2)
    await again.aclose()
    assert [f.split("\n", 1)[0] for f in resumed] == ["id: 3", "id: 4"], "only what it missed"


async def test_a_quiet_stream_keeps_alive_and_ends_at_its_lifetime(monkeypatch) -> None:
    from kazma_ui import swarm_sse

    monkeypatch.setattr(swarm_sse, "get_swarm_engine", lambda: None)
    bus = swarm_sse.SSEEventBus()
    task = SwarmTask(prompt="p", workers=["w"], status=TaskStatus.RUNNING)
    gen = swarm_sse._stream_events(task.id, bus, task, heartbeat_s=0.2, max_stream_s=3.0)
    started = time.monotonic()
    first = await asyncio.wait_for(gen.__anext__(), 5)
    assert first == ": keepalive\n\n"
    rest = []
    async for frame in gen:
        rest.append(frame)
    assert time.monotonic() - started < 6, "the stream ended at its lifetime"
    assert all(f == ": keepalive\n\n" for f in rest)


def test_open_streams_are_capped(monkeypatch, tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import swarm_sse

    store = TaskStore(db_path=str(tmp_path / "t.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    task = SwarmTask(prompt="p", status=TaskStatus.COMPLETED)
    store.persist_task(task)
    monkeypatch.setattr(swarm_sse, "get_swarm_engine", lambda: engine)
    app = FastAPI()
    app.include_router(swarm_sse.create_sse_router())
    client = TestClient(app)
    monkeypatch.setattr(swarm_sse, "_open_streams", {"testclient": swarm_sse.MAX_STREAMS_PER_CLIENT})
    r = client.get(f"/api/swarm/tasks/{task.id}/stream")
    assert r.status_code == 429 and r.json()["code"] == "too_many_streams"
    monkeypatch.setattr(swarm_sse, "_open_streams", {})
    r = client.get(f"/api/swarm/tasks/{task.id}/stream")
    assert r.status_code == 200 and "task_completed" in r.text
    assert swarm_sse._open_streams == {}, "a closed stream gives its slot back"
    store.close()


def test_the_page_resumes_from_its_last_id_and_drops_repeats() -> None:
    js = SWARM_JS.read_text(encoding="utf-8")
    body = js[js.index("function ResilientEventSource"): js.index("function approveCheckpoint")]
    assert "last_event_id=" in body and "lastEventId" in body
    assert body.count("if (fresh(e)) callback(e)") == 2, "both listener paths drop repeats"


# ── SW-S08: approve answers 202; every answer is read ─────────────────────


def _panel(monkeypatch, engine: SwarmEngine):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.swarm_panel import routes_tasks

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return engine

        def has_swarm_core(self) -> bool:
            return True

        def register_task_handle(self, *a: Any) -> None: ...

        def unregister_task_handle(self, *a: Any) -> None: ...

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_approve_answers_202_and_the_run_finishes_in_the_background(monkeypatch, tmp_path: Path) -> None:
    store = TaskStore(db_path=str(tmp_path / "t.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("a", "b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    release = threading.Event()

    async def a(task, context=None):
        return _ok("a")

    async def b(task, context=None):
        while not release.is_set():
            await asyncio.sleep(0.02)
        return _ok("b")

    engine.get_worker("a").dispatch = a
    engine.get_worker("b").dispatch = b
    client = _panel(monkeypatch, engine)
    with client:
        r = client.post("/api/swarm/dispatch", json={
            "workers": ["a", "b"], "task": "p", "pattern": "pipeline",
            "metadata": {"hitl_checkpoints": [1]}})
        task_id = r.json()["task_id"]
        approve = client.post(f"/api/swarm/tasks/{task_id}/approve")
        assert approve.status_code == 202, approve.text
        assert approve.json() == {"status": "ok", "task_status": "running", "task_id": task_id,
                                  "message": "Checkpoint approved, pipeline resumed"}
        assert store.get_task(task_id).status == TaskStatus.RUNNING, "recorded before it resumes"
        again = client.post(f"/api/swarm/tasks/{task_id}/approve")
        assert again.status_code == 409 and again.json()["status"] == "error"
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and store.get_task(task_id).status != TaskStatus.COMPLETED:
            time.sleep(0.05)
        assert store.get_task(task_id).status == TaskStatus.COMPLETED
    store.close()


def test_every_page_decision_reads_the_answer() -> None:
    """Negative control in the witness: the page used to call any answer a
    success. Every approve/reject fetch reads the answer through
    readDecision, which is ok only for a 2xx with status ok."""
    js = SWARM_JS.read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r"fetch\('/api/swarm/tasks/' \+ encodeURIComponent\([^)]*\) \+ '/(approve|reject)'", js)]
    assert len(calls) >= 6, calls
    for start in calls:
        chain = js[start: start + 300]
        assert ".then(readDecision)" in chain, chain
    helper = js[js.index("function readDecision"): js.index("function decisionErrorText")]
    assert "r.ok && data.status === 'ok'" in helper


def test_every_page_dispatch_asks_for_the_background() -> None:
    js = SWARM_JS.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"fetch\('/api/swarm/dispatch'", js)]
    assert len(starts) == 3
    for start in starts:
        region = js[max(0, start - 3000): start]
        literal = region[region.rfind("var payload = {"):]
        literal = literal[: literal.index("};") + 2]
        assert "background: true" in literal, literal[:200]


# ── SW-E07: nothing on the loop, every task type ──────────────────────────


class _RecordingSettings:
    """A settings store that records the thread of each call."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.calls: list[tuple[str, bool]] = []
        self._lock = threading.Lock()

    def _note(self, what: str) -> None:
        self.calls.append((what, _on_loop()))

    def get(self, key, default=None, *a, **k):
        self._note("get:" + str(key))
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value, *a, **k):
        self._note("set:" + str(key))
        with self._lock:
            self.data[key] = value

    def set_if_absent(self, key, value, *a, **k):
        self._note("cas:" + str(key))
        with self._lock:
            if key in self.data:
                return False
            self.data[key] = value
            return True

    def delete(self, key, *a, **k):
        self._note("delete:" + str(key))
        with self._lock:
            return self.data.pop(key, None) is not None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _recording_store(path: Path, notes: list[tuple[str, bool]]) -> TaskStore:
    class _Store(TaskStore):
        def persist_task(self, task):
            notes.append(("persist", _on_loop()))
            return super().persist_task(task)

        def admit(self, task):
            notes.append(("admit", _on_loop()))
            return super().admit(task)

        def get_task(self, task_id):
            notes.append(("get", _on_loop()))
            return super().get_task(task_id)

        def record_worker_metric(self, **kw):
            notes.append(("metric", _on_loop()))
            return super().record_worker_metric(**kw)

    return _Store(db_path=str(path))


_KINDS = {
    "dispatch": (TaskType.DISPATCH, ["w"], {}),
    "broadcast": (TaskType.BROADCAST, ["w"], {}),
    "pipeline": (TaskType.PIPELINE, ["w", "w2"], {"hitl_checkpoints": [1]}),
    "fan_out": (TaskType.FAN_OUT, ["w", "w2"], {}),
    "consult": (TaskType.CONSULT, ["w"], {}),
    "conditional": (TaskType.CONDITIONAL, ["router", "w"], {"routes": {"go": "w"}}),
}


@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_no_store_settings_or_breaker_io_on_the_loop(kind: str, monkeypatch, tmp_path: Path) -> None:
    """The SW-E11 witness (engine numbering) inverted, for every task type,
    with the shared breakers on."""
    import kazma_core.config_store as cs

    settings = _RecordingSettings()
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    monkeypatch.setattr(cs, "get_config_store", lambda *a, **k: settings)
    notes: list[tuple[str, bool]] = []
    store = _recording_store(tmp_path / "t.db", notes)
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("router", "w", "w2"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def router(task, context=None):
        return _ok("router", "go")

    async def worker(task, context=None):
        return _ok("w")

    engine.get_worker("router").dispatch = router
    engine.get_worker("w").dispatch = worker
    engine.get_worker("w2").dispatch = worker
    task_type, workers, meta = _KINDS[kind]
    task = SwarmTask(prompt="p", id=f"loop-{uuid.uuid4().hex[:8]}", type=task_type, workers=workers,
                     timeout=0, metadata=dict(meta))
    result = await engine.dispatch(task)
    if kind == "pipeline":
        assert result.status == "paused"
        result = await engine.approve_checkpoint(task.id)
    assert result.status in ("success", "partial"), (kind, result.error)
    await asyncio.sleep(0)
    await engine._flush_writes(task.id)

    on_loop = [what for what, loop in notes if loop]
    breaker_on_loop = [what for what, loop in settings.calls if loop and "breaker" in what]
    assert notes and not on_loop, (kind, on_loop)
    assert any("breaker" in what for what, _ in settings.calls), "the shared breakers were used"
    assert not breaker_on_loop, (kind, breaker_on_loop)
    store.close()


async def test_routing_embeds_off_the_loop() -> None:
    """The SW-E11b witness inverted: every embedding a route asks for (the
    task and each profile, through Kazma's embedder since SW-E10) runs in a
    thread."""
    from kazma_core.swarm.config import WorkerCapabilities
    from kazma_core.swarm.semantic_router import SemanticRouter

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    for name in ("w", "other-a", "other-b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process",
                                       capabilities=WorkerCapabilities(expertise=[f"{name} things"])))
    seen: list[bool] = []

    class _Embedder:
        dim = 2

        def encode(self, text):
            seen.append(_on_loop())
            return [1.0, 0.0] if ("anything" in text or "Worker: w\n" in text) else [0.0, 1.0]

    engine._routing_engine._semantic_router = SemanticRouter(embedder_getter=lambda: _Embedder())

    async def w(task, context=None):
        return _ok("w")

    engine.get_worker("w").dispatch = w
    result = await engine.dispatch(SwarmTask(prompt="anything", workers=["auto"], timeout=0))
    assert result.status == "success" and engine.get_task(result.task_id).workers == ["w"]
    assert len(seen) == 4 and not any(seen), seen  # the task and three profiles, none on the loop


# ── SW-S12: the approval path keeps the loop free ─────────────────────────


async def test_the_approval_path_reads_settings_off_the_loop(monkeypatch) -> None:
    """The SW-S12 witness inverted: the HITL configuration and the shared
    approval row are read and written in threads."""
    import kazma_core.safety.hitl as hitl_mod
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.safety import SafetyMiddleware

    on_loop: list[str] = []
    real_cfg = hitl_mod.get_hitl_config

    def traced_cfg(*a, **k):
        if _on_loop():
            on_loop.append("get_hitl_config")
        return real_cfg(*a, **k)

    monkeypatch.setattr(hitl_mod, "get_hitl_config", traced_cfg)
    real_store = sa._store

    def traced_store():
        if _on_loop():
            on_loop.append("shared_approvals store")
        return real_store()

    monkeypatch.setattr(sa, "_store", traced_store)

    class _Bus:
        class _Adapter:
            pass

        adapter = _Adapter()

        async def request_approval(self, **kw):
            await asyncio.to_thread(sa.resolve, kw["task_id"], False, actor="telegram:1")
            return False

    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus())
    assert await SafetyMiddleware().check("file_write", json.dumps({"path": "a"})) is False
    assert on_loop == [], on_loop


@pytest.mark.postgres
async def test_twenty_parallel_approvals_keep_the_loop_responsive(monkeypatch) -> None:
    """20 approvals at once: the loop never stalls a second (measured; on a
    Postgres settings store in the CI Postgres job)."""
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.safety import SafetyMiddleware

    class _Bus:
        class _Adapter:
            pass

        adapter = _Adapter()

        async def request_approval(self, **kw):
            await asyncio.sleep(0.05)
            await asyncio.to_thread(sa.resolve, kw["task_id"], True, actor="telegram:1")
            return True

    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus())
    worst = 0.0
    stop = asyncio.Event()

    async def probe() -> None:
        nonlocal worst
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - t0 - 0.01)

    watcher = asyncio.create_task(probe())
    mw = SafetyMiddleware()
    results = await asyncio.gather(*(mw.check("file_write", json.dumps({"path": f"f{i}"})) for i in range(20)))
    stop.set()
    await watcher
    assert all(results)
    assert worst < 1.0, f"the loop stalled {worst:.2f}s"


_ASYNC_FORBIDDEN = {"is_danger_tool", "approval_timeout", "_allowed_without_asking"}
_APPROVAL_IO = {"get", "open_request", "settle", "close", "resolve", "purge_stale"}


def _loop_io_in_async(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr in _ASYNC_FORBIDDEN and \
                    isinstance(node.value, ast.Name) and node.value.id == "self":
                found.append(f"{fn.name}: self.{node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr in _APPROVAL_IO and ast.unparse(node.func.value) in ("shared_approvals", "sa"):
                found.append(f"{fn.name}: {ast.unparse(node.func)}()")
    return found


def test_no_async_approval_code_reads_settings_inline() -> None:
    """The loop-stall gate, extended to SafetyMiddleware's methods and
    properties and shared_approvals' durable calls: an async function names
    them only as an argument to asyncio.to_thread."""
    planted = ("class S:\n    async def check(self, t):\n        return self.is_danger_tool(t)\n"
               "async def f(shared_approvals):\n    shared_approvals.get('x')\n")
    assert len(_loop_io_in_async(ast.parse(planted))) == 2, "negative control"
    for rel in ("kazma-core/kazma_core/swarm/safety.py", "kazma-core/kazma_core/swarm/shared_approvals.py",
                "kazma-gateway/kazma_gateway/adapters/telegram_bus.py",
                "kazma-gateway/kazma_gateway/adapters/discord_bus.py",
                "kazma-gateway/kazma_gateway/adapters/slack_bus.py"):
        bad = _loop_io_in_async(ast.parse((REPO / rel).read_text(encoding="utf-8")))
        assert not bad, (rel, bad)
