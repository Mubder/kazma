"""Work a chat turn starts asks for its own approvals (Swarm audit SW-S01, W1.1).

The graph's tool worker set a boolean ContextVar for its whole step, telling
the registry "the graph is the HITL authority, do not ask the bus". A call it
covered -- ``dispatch_swarm``, which the graph never gates -- started the
swarm task with ``asyncio.create_task``, which copies the context; the swarm
worker's danger tools then skipped the bus, and no graph interrupt covers a
swarm worker: ``file_write``, ``git_push``, ``vault_retrieve`` and
``send_message`` ran with no card on Web, Telegram, Discord or Slack. A web
page read in chat could steer the model to it.

The authority is now bound to the one call the graph decided about and
consumed by its first execution (``safety/call_authority.py``). Held here:

* the real ``dispatch_swarm`` through the real ``tool_worker_node`` with HITL
  on: the swarm worker's ``file_write`` asks the bus, and is refused;
* every way to start work from a covered call -- ``create_task``,
  ``spawn_background``, ``ensure_future``, a ``TaskGroup``, ``to_thread``, a
  nested call, the same tool again -- asks the bus; the covered call itself
  does not (no second prompt);
* the class: product code binds the authority only through
  ``call_authority`` and reads it only through ``claim_call_authority``.

Each with its negative control: the old step-wide flag lets the worker's
danger tool run unasked, and a planted truthiness read fails the gate.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    """No real embedder or model behind this file's memory and swarm paths
    (tests/_no_models.py; tests/_egress_guard.py refuses the real ones)."""
    keep_models_out(monkeypatch)
    yield
    finish_turn_memory()  # a turn's memory write ends inside its test


REPO = Path(__file__).resolve().parents[1]


def _recording_bus(answer: bool = False):
    asked: list[dict[str, Any]] = []

    class _Adapter:  # not a NullBusAdapter: the real gate path runs
        pass

    class _Bus:
        adapter = _Adapter()

        async def request_approval(self, **kwargs: Any) -> bool:
            asked.append(kwargs)
            return answer

    return _Bus(), asked


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """A real registry with the system tools, a real engine and a probe
    worker whose work is one ``file_write`` through the same registry."""
    import kazma_core.swarm.bus as bus_mod
    import kazma_core.swarm.engine as engine_mod
    from kazma_core.agent.tool_builtins.system import register_system_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore
    from kazma_core.swarm.worker import SwarmWorker

    bus, asked = _recording_bus(answer=False)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: bus)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    reg = LocalToolRegistry(include_builtins=False)
    register_system_tools(reg)
    ran: list[str] = []

    @reg.register(description="stand-in for file_write", category="fs", name="file_write")
    async def _file_write(path: str, content: str) -> str:
        ran.append(path)
        return f"wrote {path}"

    class _Probe(SwarmWorker):
        async def dispatch(self, task: str, context: Any = "") -> dict[str, Any]:
            res = await reg.execute("file_write", {"path": "from-worker.txt", "content": "x"})
            return {"worker": self.name, "task_id": "", "status": "success",
                    "output": str(res.get("content")), "error": None}

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]),
                         task_store=TaskStore(str(tmp_path / "swarm_tasks.db")))
    engine._workers["probe"] = _Probe(name="probe")
    monkeypatch.setattr(engine_mod, "_swarm_engine", engine)

    async def settle() -> None:
        for _ in range(200):
            if not engine.list_active_tasks():
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)

    return reg, asked, ran, settle


def _graph_turn_state(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.agent.state import initial_supervisor_state

    state = initial_supervisor_state(thread_id="t-sw-s01")
    state["messages"] = [{"role": "user", "content": "go"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": tool, "arguments": args}]
    return state


class _Tracer:
    def trace_tool_execution(self, **kw):
        pass


_HITL_ON = {"enabled": True, "require_approval_for": {"file_write", "shell_exec"}}
#: A chat turn's graph, and a fired reminder's child graph (no checkpointer:
#: ``auto_deny``, agent_runner.build_child_graph) -- the Scheduled audit's
#: SCH-S01, the same mechanism: a reminder at 03:00 calling dispatch_swarm.
_GRAPHS = {"chat turn": _HITL_ON, "fired reminder": {**_HITL_ON, "auto_deny": True}}


def _run_turn(reg, settle, monkeypatch, config=_HITL_ON) -> None:
    from unittest.mock import patch

    from kazma_core.agent.graph_builder import tool_worker_node

    async def turn() -> None:
        with patch("kazma_core.safety.hitl.get_hitl_config", return_value=dict(_HITL_ON)):
            await tool_worker_node(
                _graph_turn_state("dispatch_swarm", {"prompt": "write it", "worker": "probe"}),
                tool_executor=reg, tracer=_Tracer(), hitl_config=dict(config),
            )
        await settle()

    asyncio.run(turn())


@pytest.mark.parametrize("graph", sorted(_GRAPHS))
def test_a_swarm_task_started_by_a_graph_asks_the_bus(world, monkeypatch, graph):
    """The audit's witnesses (SW-S01, SCH-S01) inverted, through the real
    tool worker."""
    reg, asked, ran, settle = world
    _run_turn(reg, settle, monkeypatch, _GRAPHS[graph])
    assert [a for a in asked if "file_write" in str(a)], asked  # a card was asked for
    assert "from-worker.txt" not in ran  # and the bus said no


@pytest.mark.parametrize("graph", sorted(_GRAPHS))
def test_the_old_step_wide_flag_lets_it_run_unasked(world, monkeypatch, graph):
    """Negative control: authority read as a flag (any value set) -- what the
    step-wide ContextVar was -- and the worker's danger tool runs."""
    from kazma_core.agent import tool_registry

    reg, asked, ran, settle = world
    monkeypatch.setattr(tool_registry, "claim_call_authority", lambda var, name: var.get() is not None)
    monkeypatch.setattr("kazma_core.safety.call_authority.detached_context",
                        lambda: __import__("contextvars").copy_context())
    monkeypatch.setattr("kazma_core.safety.call_authority.without_call_authority",
                        __import__("contextlib").nullcontext)
    _run_turn(reg, settle, monkeypatch, _GRAPHS[graph])
    assert "from-worker.txt" in ran and not [a for a in asked if "file_write" in str(a)]


# ── every way to start work from a covered call ────────────────────────


def _starters():
    from kazma_core.background import spawn_background

    async def via_create_task(work):
        await asyncio.create_task(work())

    async def via_spawn_background(work):
        await spawn_background(work(), name="sw-s01-probe")

    async def via_ensure_future(work):
        await asyncio.ensure_future(work())

    async def via_task_group(work):
        async with asyncio.TaskGroup() as tg:
            tg.create_task(work())

    async def via_to_thread(work):
        await asyncio.to_thread(lambda: asyncio.run(work()))

    async def via_nested_call(work):
        await work()

    return {
        "create_task": via_create_task, "spawn_background": via_spawn_background,
        "ensure_future": via_ensure_future, "task_group": via_task_group,
        "to_thread": via_to_thread, "nested_call": via_nested_call,
    }


@pytest.mark.parametrize("authority", ["graph", "approved"])
@pytest.mark.parametrize("starter", sorted(_starters()))
def test_work_a_covered_call_starts_asks_the_bus(monkeypatch, starter, authority):
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.agent.tool_registry import (
        LocalToolRegistry,
        _graph_hitl_gate_ctx,
        _hitl_approved_ctx,
        call_authority,
    )

    bus, asked = _recording_bus(answer=False)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: bus)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    reg = LocalToolRegistry(include_builtins=False)
    ran: list[str] = []
    start = _starters()[starter]

    @reg.register(description="stand-in for file_write", category="fs", name="file_write")
    async def _file_write(path: str, content: str) -> str:
        ran.append(path)
        return "ok"

    @reg.register(description="a covered call that starts work", category="test", name="covered_tool")
    async def _covered() -> str:
        async def work() -> None:
            await reg.execute("file_write", {"path": f"{starter}.txt", "content": "x"})

        await start(work)
        return "started"

    var = _graph_hitl_gate_ctx if authority == "graph" else _hitl_approved_ctx

    async def turn() -> None:
        with call_authority(var, "covered_tool"):
            await reg.execute("covered_tool", {})

    asyncio.run(turn())
    assert f"{starter}.txt" not in ran
    assert len(asked) == 1 and "file_write" in str(asked[0]), asked


@pytest.mark.parametrize("authority", ["graph", "approved"])
def test_the_covered_call_itself_is_not_asked_twice(monkeypatch, authority):
    """The graph's own decision stands: the call it covers runs without a
    second prompt -- once. The same tool again asks."""
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.agent.tool_registry import (
        LocalToolRegistry,
        _graph_hitl_gate_ctx,
        _hitl_approved_ctx,
        call_authority,
    )

    bus, asked = _recording_bus(answer=False)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: bus)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    reg = LocalToolRegistry(include_builtins=False)
    ran: list[str] = []

    @reg.register(description="stand-in for file_write", category="fs", name="file_write")
    async def _file_write(path: str, content: str) -> str:
        ran.append(path)
        return "ok"

    var = _graph_hitl_gate_ctx if authority == "graph" else _hitl_approved_ctx

    async def turn() -> None:
        with call_authority(var, "file_write"):
            await reg.execute("file_write", {"path": "covered.txt", "content": "x"})
            await reg.execute("file_write", {"path": "again.txt", "content": "x"})

    asyncio.run(turn())
    assert ran == ["covered.txt"]
    assert len(asked) == 1  # the second call asked, and was refused


# ── the class: one way to bind it, one way to read it ──────────────────

_VARS = {"_graph_hitl_gate_ctx", "_hitl_approved_ctx"}
_HOME = "kazma-core/kazma_core/safety/call_authority.py"


def _product_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    # A file deleted in the working tree is still listed until it is staged.
    return [REPO / f for f in listed
            if f.startswith("kazma-") and "/tests/" not in f and "_tests/" not in f
            and (REPO / f).exists()]


def _misuse(source: str, label: str) -> list[str]:
    """A ``.get(`` / ``.set(`` / ``.reset(`` on one of the authority vars
    anywhere but their home: the bug was a flag read by truthiness."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "set", "reset")
                and isinstance(node.func.value, ast.Name) and node.func.value.id in _VARS):
            found.append(f"{label}:{node.lineno} {node.func.value.id}.{node.func.attr}()")
    return found


def test_the_authority_is_bound_and_read_in_one_place():
    found = []
    for path in _product_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == _HOME:
            continue
        found += _misuse(path.read_text(encoding="utf-8"), rel)
    assert not found, (
        "Bind the HITL call authority with call_authority(...) and read it with "
        "claim_call_authority(...) (kazma_core/safety/call_authority.py): a flag "
        "read by truthiness reaches the work a call starts (SW-S01).\n  " + "\n  ".join(found))


def test_the_gate_sees_a_flag_read():
    """Negative control: the shape of the document handler's old check."""
    planted = "from x import _graph_hitl_gate_ctx\nif _graph_hitl_gate_ctx.get(None):\n    pass\n"
    assert _misuse(planted, "planted.py") == ["planted.py:2 _graph_hitl_gate_ctx.get()"]
