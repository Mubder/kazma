"""A real Kazma process for tests/test_swarm_temporal_durable.py's kill case.

Usage: python -I _swarm_durable_child.py <host> <queue> <db> <markers> <task_id> [control]

Starts this process's Temporal workers against *host* (the test's
time-skipping server), then dispatches a three-step pipeline through the
durable path: step ``p1`` commits (a line in *markers*), step ``p2`` notes a
tool that changes things (``file_write``), writes ``p2-begun`` and prints
``READY <workflow id> <activity queue> <pid>``, then waits for the kill (of
that pid: on Windows a venv's ``python.exe`` is a launcher, and killing the
launcher leaves the interpreter running). ``p3``
would write ``p3``. The parent kills the process there.

``control`` puts back what the durable path lacked before 2026-10-15: no
heartbeat timeout (Temporal hears nothing of a lost worker until the run's
time limit) and no step journal on a first run (the next start cannot say
which step the run stopped in).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _pkg in ("kazma-cli", "kazma-ui", "kazma-tui", "kazma-skills", "kazma-gateway", "kazma-core"):
    sys.path.insert(0, str(REPO / _pkg))  # this checkout's packages, whatever is installed


def _mark(path: str, what: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(what + "\n")
        fh.flush()
        os.fsync(fh.fileno())


async def main(argv: list[str]) -> int:
    host, queue, db, markers, task_id = argv[:5]
    control = len(argv) > 5 and argv[5] == "control"
    os.environ.update(KAZMA_TEMPORAL_HOST=host, KAZMA_TEMPORAL_QUEUE=queue, KAZMA_TEMPORAL_REQUIRED="1",
                      KAZMA_SHARED_BREAKERS="0", KAZMA_GATE_REGISTRY="0", KAZMA_SELF_IMPROVEMENT="0")
    os.environ.pop("KAZMA_TEMPORAL", None)

    import kazma_core.swarm.durable as durable
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd
    from kazma_core.agent.dispatch_effects import note_effect_invocation
    from kazma_core.swarm.config import SwarmConfig, WorkerConfig
    from kazma_core.swarm.engine import SwarmEngine
    from kazma_core.swarm.task import SwarmTask, TaskType
    from kazma_core.swarm.task_store import TaskStore

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _none(*_a, **_kw):
        return None

    # A process of its own: nothing to restore afterwards.
    setattr(patterns, "_synthesize_refined_output", _no_refiner)
    setattr(patterns, "_run_self_improvement", _none)
    setattr(wd, "_index_worker_l4_memory", _none)
    setattr(durable, "HEARTBEAT_EVERY_S", 0.2)
    setattr(durable, "HEARTBEAT_TIMEOUT_S", 3600.0 if control else 2.0)
    if control:
        first_run = patterns.execute_pipeline

        async def _unjournaled(task, *, dispatch_worker_by_name, on_step=None):
            return await first_run(task, dispatch_worker_by_name=dispatch_worker_by_name)

        import kazma_core.swarm.dispatch_inner as dispatch_inner

        setattr(dispatch_inner, "execute_pipeline", _unjournaled)

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=TaskStore(db_path=db))
    for name in ("p1", "p2", "p3"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    task = SwarmTask(prompt="three steps", id=task_id, type=TaskType.PIPELINE, workers=["p1", "p2", "p3"],
                     timeout=600)

    async def p1(prompt: str, context=None) -> dict:
        _mark(markers, "p1")
        return {"worker": "p1", "task_id": "", "status": "success", "output": "p1 done", "error": None}

    async def p2(prompt: str, context=None) -> dict:
        note_effect_invocation("file_write")
        _mark(markers, "p2-begun")
        print("READY", durable._workflow_id_for(task), durable.activity_queue(), os.getpid(), flush=True)
        await asyncio.sleep(3600)
        return {"worker": "p2", "task_id": "", "status": "success", "output": "p2 done", "error": None}

    async def p3(prompt: str, context=None) -> dict:
        _mark(markers, "p3")
        return {"worker": "p3", "task_id": "", "status": "success", "output": "p3 done", "error": None}

    for name, fn in (("p1", p1), ("p2", p2), ("p3", p3)):
        engine.get_worker(name).dispatch = fn
    await durable.start_temporal_worker()
    if not durable.worker_running():
        print("NO-WORKER", flush=True)
        return 2
    await engine.dispatch(task)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
