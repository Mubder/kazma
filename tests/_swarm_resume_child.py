"""A real process for tests/test_swarm_interrupted_effects.py's kill matrix.

Usage: python -I _swarm_resume_child.py <phase> <task_id> <effects_file> [kill_at] [control]

``pause``: dispatch a two-step pipeline (draft, publish) that pauses after
step 1. ``approve``: boot as a fresh process (restore paused tasks), approve
the checkpoint and die (``os._exit(9)``) at ``kill_at``:

* ``after_admit``   -- the resume recorded RUNNING, nothing ran yet
* ``mid_step``      -- publish wrote its effect, the step did not report
* ``after_commit``  -- publish's step was journaled committed
* ``before_finalize`` -- every step ran, the ending was not recorded

``control`` replays the old order (the checkpoint taken and the steps run
without recording RUNNING first): the negative control.

The store is a SQLite file beside *effects_file* (the parent opens the same
one), or the CI job's Postgres when the environment selects it -- never the
ambient default path, which another test in the same run can leave elsewhere. Each published effect is a line in
*effects_file*, fsync'd before anything else happens.
"""

from __future__ import annotations

import asyncio
import os
import sys


def _ok(worker: str) -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": f"{worker} done", "error": None}


async def main(argv: list[str]) -> int:
    phase, task_id, effects = argv[0], argv[1], argv[2]
    kill_at = argv[3] if len(argv) > 3 else ""
    control = len(argv) > 4 and argv[4] == "control"

    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd
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

    from pathlib import Path

    store = TaskStore(db_path=str(Path(effects).parent / "swarm_tasks.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("draft", "publish"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def draft(task, context=None):
        return _ok("draft")

    async def publish(task, context=None):
        with open(effects, "a", encoding="utf-8") as fh:
            fh.write("published\n")
            fh.flush()
            os.fsync(fh.fileno())
        if kill_at == "mid_step":
            os._exit(9)
        return _ok("publish")

    engine.get_worker("draft").dispatch = draft
    engine.get_worker("publish").dispatch = publish

    if phase == "pause":
        task = SwarmTask(prompt="draft then publish", id=task_id, workers=["draft", "publish"],
                         type=TaskType.PIPELINE, timeout=0, metadata={"hitl_checkpoints": [1]})
        result = await engine.dispatch(task)
        print(result.status, flush=True)
        return 0

    engine.restore_paused_tasks()
    if control:
        async def _skip_admission(task, **_kwargs):
            return None  # the old order: nothing recorded before resuming

        async def _no_journal(task, step, state):
            return None

        engine._admit_to_store = _skip_admission
        engine._journal_step = _no_journal
    if kill_at == "after_admit":
        admit = engine._admit_to_store

        async def _admit_then_die(task, **kwargs):
            held = await admit(task, **kwargs)
            os._exit(9)
            return held

        engine._admit_to_store = _admit_then_die
    if kill_at == "after_commit":
        journal = engine._journal_step

        async def _journal_then_die(task, step, state):
            await journal(task, step, state)
            if state == "committed":
                os._exit(9)

        engine._journal_step = _journal_then_die
    if kill_at == "before_finalize":
        finalize = engine._finalize_task

        def _die_before_success(task, *a, **kw):
            if kw.get("status") == "success":
                os._exit(9)
            return finalize(task, *a, **kw)

        engine._finalize_task = _die_before_success
    result = await engine.approve_checkpoint(task_id)
    print(getattr(result, "status", None), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
