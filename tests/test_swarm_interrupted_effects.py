"""Effect-safe interruption and resume (Swarm audit 2026-10-13, stage 3).

Each test inverts a witness in
``session-artifacts/audits-2026-10-13/test_swarm_engine_witnesses.py``:

* SW-E04 -- an interruption dropped the evidence of what had run: a caller's
  own deadline came back as "Cancelled by user", the engine deadline kept no
  worker evidence, a pattern step that timed out after a write was "safe to
  retry". Now one tracker per task, step and dispatch; every interruption
  records its cause and the tools that change things it had invoked
  (``execution_outcome: unknown``), and the caller's cancellation is raised.
* SW-E01 -- an approved pipeline that died mid-resume was offered again and
  its post-checkpoint step ran twice. Now the resume is recorded RUNNING (at
  the next generation) before any step, each step's begin and commit are
  journaled, and a boot holds the interrupted run as unknown -- proven with a
  real process killed at each boundary, on SQLite here and on Postgres in the
  CI Postgres job.
* SW-E08 -- "partial" was stored COMPLETED with an unfinished write inside, a
  retry dropped the workspace, schema and aggregation and ignored the unknown
  outcome, and a 401 was retried.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from kazma_core.agent.dispatch_effects import note_effect_invocation
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.reliability import RetryPolicy
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore

pytestmark = pytest.mark.postgres

REPO = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).resolve().parent / "_swarm_resume_child.py"


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
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


@pytest.fixture
def store(tmp_path: Path):
    s = TaskStore(db_path=str(tmp_path / "tasks.db"))
    yield s
    s.close()


def _ok(worker: str, output: str = "ok") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ── SW-E04: every interruption keeps what had run ────────────────────────

_TYPES = {
    "dispatch": (TaskType.DISPATCH, ["w"], {}),
    "broadcast": (TaskType.BROADCAST, ["w"], {}),
    "pipeline": (TaskType.PIPELINE, ["w"], {}),
    "fan_out": (TaskType.FAN_OUT, ["w"], {}),
    "consult": (TaskType.CONSULT, ["w"], {}),
    "conditional": (TaskType.CONDITIONAL, ["router", "w"], {"routes": {"go": "w"}}),
}
_STEP_TYPES = {"pipeline", "fan_out", "consult", "conditional"}
_CAUSES = ("deadline", "caller", "cancel", "reap", "shutdown")


def _engine_with_writer(store: TaskStore) -> tuple[SwarmEngine, asyncio.Event]:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("router", "w"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    engine.set_retry_policy("w", RetryPolicy(max_retries=0))
    wrote = asyncio.Event()

    async def router(task, context=None):
        return _ok("router", "go")

    async def writer(task, context=None):
        note_effect_invocation("file_write")  # a tool that changes things ran
        wrote.set()
        await asyncio.sleep(30)
        return _ok("w")

    engine.get_worker("router").dispatch = router
    engine.get_worker("w").dispatch = writer
    return engine, wrote


@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("kind", sorted(_TYPES))
async def test_every_interruption_records_the_tools_that_ran(store: TaskStore, kind: str, cause: str) -> None:
    task_type, workers, meta = _TYPES[kind]
    engine, wrote = _engine_with_writer(store)
    deadline = 0.3 if cause == "deadline" else 60.0
    task = SwarmTask(prompt="p", id=_id(kind), type=task_type, workers=workers, timeout=deadline,
                     metadata=dict(meta))
    caller = asyncio.create_task(engine.dispatch(task))
    if cause == "caller":
        await asyncio.wait_for(wrote.wait(), 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller  # the caller's own stop is raised, never a user's cancel
    elif cause == "deadline":
        await asyncio.wait_for(caller, 10)
    else:
        await asyncio.wait_for(wrote.wait(), 5)
        if cause == "cancel":
            assert await engine.cancel_task(task.id) is True
        elif cause == "reap":
            task.started_at = (datetime.now(UTC) - timedelta(seconds=100_000)).isoformat()
            assert engine.reap_stale_tasks() == 1
        else:
            assert await engine.stop_all_runs() == 1
        await asyncio.wait_for(caller, 10)

    stored = store.get_task(task.id)
    meta_out = {**(stored.result.metadata or {})}
    assert meta_out.get("execution_outcome") == "unknown", (kind, cause, meta_out, stored.result.error)
    if cause == "deadline" and kind in _STEP_TYPES | {"broadcast"}:
        # The step / worker that ran out of time carries it: not retryable.
        step = [wr for wr in stored.result.worker_results if wr.worker == "w"][-1]
        assert step.retry_safe is False and "file_write" in (step.error or ""), step
        assert meta_out.get("effects_uncertain") is True
    else:
        expected_cause = {"deadline": "deadline", "caller": "caller_stopped", "cancel": "cancelled_by_user",
                          "reap": "deadline", "shutdown": "shutdown"}[cause]
        assert meta_out["interrupted_by"] == expected_cause
        assert meta_out["invoked_tools"] == ["file_write"]
        assert "file_write" in (stored.result.error or "")
    assert stored.status in (TaskStatus.TIMEOUT, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.COMPLETED)


async def test_a_caller_deadline_raises_timeout_not_a_user_cancel(store: TaskStore) -> None:
    """The SW-E04 witness inverted."""
    engine, wrote = _engine_with_writer(store)
    task = SwarmTask(prompt="p", id=_id("callerdeadline"), workers=["w"])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(engine.dispatch(task), timeout=0.3)
    stored = store.get_task(task.id)
    assert stored.result.error != "Cancelled by user"
    assert stored.result.metadata["interrupted_by"] == "caller_stopped"


async def test_an_interruption_before_any_write_says_so(store: TaskStore) -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="r", type="in_process"))
    started = asyncio.Event()

    async def reader(task, context=None):
        started.set()
        await asyncio.sleep(30)

    engine.get_worker("r").dispatch = reader
    task = SwarmTask(prompt="p", id=_id("noeffect"), workers=["r"], timeout=0)
    run = asyncio.create_task(engine.dispatch(task))
    await asyncio.wait_for(started.wait(), 5)
    await engine.cancel_task(task.id)
    await asyncio.wait_for(run, 5)
    assert store.get_task(task.id).result.metadata["execution_outcome"] == "none_invoked"


def test_the_old_step_timeout_was_labelled_safe() -> None:
    """Negative control: the step timeout result as it was built before."""
    from kazma_core.swarm.task import WorkerResult

    old = WorkerResult(worker="w", task_id="t", status="timeout", output="", error="timed out")
    assert old.retry_safe is True


# ── SW-E01: an approved pipeline killed mid-resume, in a real process ────


def _run_child(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env["KAZMA_TEMPORAL"] = "0"
    env["KAZMA_SHARED_BREAKERS"] = "0"
    env["KAZMA_GATE_REGISTRY"] = "0"
    env["KAZMA_SELF_IMPROVEMENT"] = "0"
    return subprocess.run(
        [sys.executable, "-I", str(CHILD), *args],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180,
    )


def _boot(effects: Path) -> tuple[SwarmEngine, TaskStore]:
    """What the server's startup does: restore paused tasks, then hold
    interrupted running ones (kazma_ui/app.py). Its publish step writes the
    same effects file as the child's, and opens the same task store (a file
    beside it; the CI job's Postgres when the environment selects it)."""
    store = TaskStore(db_path=str(effects.parent / "swarm_tasks.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("draft", "publish"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def draft(task, context=None):
        return _ok("draft")

    async def publish(task, context=None):
        with open(effects, "a", encoding="utf-8") as fh:
            fh.write("published\n")
        return _ok("publish")

    engine.get_worker("draft").dispatch = draft
    engine.get_worker("publish").dispatch = publish
    engine.restore_paused_tasks()
    store.requeue_orphaned_running()
    return engine, store


@pytest.mark.parametrize("kill_at,published,step_note", [
    ("after_admit", 0, None),
    ("mid_step", 1, "step 2 (begun)"),
    ("after_commit", 1, "step 2 (committed)"),
    ("before_finalize", 1, "step 2 (committed)"),
])
async def test_an_approved_pipeline_killed_mid_resume_never_runs_again(
    tmp_path: Path, kill_at: str, published: int, step_note: str | None,
) -> None:
    effects = tmp_path / "effects.txt"
    task_id = _id(f"kill-{kill_at}")
    paused = _run_child("pause", task_id, str(effects))
    assert paused.returncode == 0 and "paused" in paused.stdout, paused.stderr[-2000:]

    died = _run_child("approve", task_id, str(effects), kill_at)
    assert died.returncode == 9, (died.returncode, died.stdout, died.stderr[-2000:])

    engine, store = _boot(effects)
    try:
        assert engine.get_checkpoint_info(task_id) is None, "the approval is not offered again"
        row = store.get_task(task_id)
        assert row.status == TaskStatus.FAILED
        assert row.metadata["execution_outcome"] == "unknown"
        if step_note:
            assert step_note in row.metadata["recovery_error"]
        again = await engine.approve_checkpoint(task_id)
        assert again is None or again.status == "failed"
        lines = effects.read_text(encoding="utf-8").splitlines() if effects.exists() else []
        assert len(lines) == published, "each post-checkpoint effect ran at most once"
    finally:
        store.close()


async def test_resuming_before_recording_it_offers_the_approval_again(tmp_path: Path) -> None:
    """Negative control: the old order (take the checkpoint and run, with
    nothing recorded first) -- the same kill leaves the row PAUSED, the boot
    offers Approve again, and the published step runs twice."""
    effects = tmp_path / "effects.txt"
    task_id = _id("kill-control")
    assert _run_child("pause", task_id, str(effects)).returncode == 0
    assert _run_child("approve", task_id, str(effects), "mid_step", "control").returncode == 9
    engine, store = _boot(effects)
    try:
        assert engine.get_checkpoint_info(task_id) is not None, "re-offered"
        final = await engine.approve_checkpoint(task_id)
        assert final is not None and final.status == "success"
        assert effects.read_text(encoding="utf-8").splitlines() == ["published", "published"]
    finally:
        store.close()


# ── SW-E08: status, retry, error taxonomy ────────────────────────────────


def test_every_run_status_has_a_task_status() -> None:
    from kazma_core.swarm.engine import _RESULT_STATUS_TO_TASK, _task_status_for

    for status in ("success", "partial", "failed", "error", "timeout", "cancelled", "paused"):
        assert status in _RESULT_STATUS_TO_TASK, status
    assert _task_status_for("something-new") == TaskStatus.FAILED, "an unknown status is never COMPLETED"

    def old(status: str) -> TaskStatus:  # negative control: the old chain
        return {"timeout": TaskStatus.TIMEOUT, "failed": TaskStatus.FAILED, "paused": TaskStatus.PAUSED,
                "cancelled": TaskStatus.CANCELLED}.get(status, TaskStatus.COMPLETED)

    assert old("something-new") == TaskStatus.COMPLETED and old("error") == TaskStatus.COMPLETED


async def test_a_failed_writing_step_is_not_hidden_as_completed(store: TaskStore) -> None:
    """The SW-E09 witness (engine numbering) inverted."""
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    for name in ("a", "b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))

    async def a(task, context=None):
        return _ok("a", "first")

    async def b(task, context=None):
        note_effect_invocation("file_write")
        raise RuntimeError("write half-applied")

    engine.get_worker("a").dispatch = a
    engine.get_worker("b").dispatch = b
    task = SwarmTask(prompt="p", id=_id("partial"), workers=["a", "b"], type=TaskType.PIPELINE, timeout=0)
    result = await engine.dispatch(task)
    stored = store.get_task(task.id)
    assert result.status == "partial"
    assert stored.status == TaskStatus.FAILED, "a writing step that did not finish: not completed"
    assert stored.result.metadata["result_status"] == "partial"
    assert stored.result.metadata["effects_uncertain"] is True
    assert stored.result.metadata["execution_outcome"] == "unknown"


async def test_a_retry_copies_the_task_and_asks_when_effects_are_unknown(store: TaskStore) -> None:
    """The SW-E10 witness (engine numbering) inverted."""
    from kazma_core.swarm.task_control import RETRY_EXCLUDED_FIELDS, RetryNeedsAcknowledgement

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="w", type="in_process"))

    async def w(task, context=None):
        note_effect_invocation("file_write")
        raise RuntimeError("partial write")

    engine.get_worker("w").dispatch = w
    original = SwarmTask(prompt="p", id=_id("retry"), workers=["w"], timeout=0, workspace_id="ws-repo-a",
                         validation_schema={"type": "object"}, aggregation="vote", priority=3,
                         fallback_chain=["w"], dependencies=["x"])
    await engine.dispatch(original)
    with pytest.raises(RetryNeedsAcknowledgement) as refused:
        await engine.retry_task(original.id)
    assert "file_write" in refused.value.tools

    retry = await engine.retry_task(original.id, acknowledge_unknown=True)
    for field in dataclasses.fields(SwarmTask):
        if field.name in RETRY_EXCLUDED_FIELDS or field.name == "metadata":
            continue
        assert getattr(retry, field.name) == getattr(original, field.name), field.name
    assert retry.metadata["retry_of"] == original.id
    assert "execution_outcome" not in retry.metadata


def test_every_task_field_is_copied_or_excluded_with_a_reason() -> None:
    from kazma_core.swarm.task_control import RETRY_EXCLUDED_FIELDS

    names = {f.name for f in dataclasses.fields(SwarmTask)}
    assert set(RETRY_EXCLUDED_FIELDS) <= names, set(RETRY_EXCLUDED_FIELDS) - names
    assert all(RETRY_EXCLUDED_FIELDS.values())


@pytest.mark.parametrize("text,retryable", [
    ("401 Unauthorized: invalid api key", False),
    ("403 Forbidden", False),
    ("insufficient_quota: billing", False),
    ("429 Too Many Requests: rate limit", True),
    ("Connection reset by peer", True),
])
def test_one_error_taxonomy(text: str, retryable: bool) -> None:
    from kazma_core.swarm.reliability import _is_retryable_exception

    assert _is_retryable_exception(RuntimeError(text)) is retryable


def test_an_llm_error_says_itself() -> None:
    from kazma_core.swarm.reliability import _is_retryable_exception

    class _LLMError(Exception):
        transient = True

    assert _is_retryable_exception(_LLMError("401 at a proxy, but the provider said transient")) is True


async def test_a_permanent_error_is_not_retried() -> None:
    """The SW-E14 witness (engine numbering) inverted."""
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    engine.set_retry_policy("w", RetryPolicy(max_retries=2, base_delay=0.0, jitter=False))
    attempts: list[int] = []

    async def w(task, context=None):
        attempts.append(1)
        raise RuntimeError("401 Unauthorized: invalid api key")

    engine.get_worker("w").dispatch = w
    await engine.dispatch(SwarmTask(prompt="p", workers=["w"], timeout=0))
    assert attempts == [1]
