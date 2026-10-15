"""The swarm soak harness works, and sees a violation (Swarm tracker SQ01).

``scripts/swarm_soak.py`` is the harness for the concurrency soak the Swarm
hardening tracker asks for (admission, breakers and the autoscaler under
hundreds of mixed tasks); a session cannot hold that run, so this keeps the
harness honest. A short run, as an operator starts it (its own process), ends
healthy having done the work it reports: every kind of task ran, a breaker
tripped and recovered, the autoscaler spawned and reaped, a restart
interrupted running work, admission refused at its limit. Its report is on
disk while it runs (``running``) and ``passed`` at the end. And its checks
report what they exist for (negative controls): a task row moved out of its
terminal status, more tasks running at once than the limit (in the store and
in a sample), a dispatch stopped with no ending recorded, a breaker outcome
from an older generation applied, an older breaker state put back by a
refresh (the race this test caught in 2 runs of 10 on 2026-10-15), an
autoscaler bound passed and an instance reaped mid-dispatch.

The short run's ``--duration`` is seconds of load: a restart that waits out
the engine's stop grace (a task ignoring its stop) no longer eats the run in
which the breaker must trip and recover.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "swarm_soak.py"
sys.path.insert(0, str(REPO / "scripts"))

import swarm_soak  # noqa: E402

pytestmark = pytest.mark.timeout(300)

_LIMIT = 6
#: Every kind often enough to run despite the bursts' refusals (seed 7: the
#: rarest ran 8 times or more in trial runs).
_MIX = "ok=36,writer=6,flaky=15,slow=6,stale=5,cancel=6,stubborn=5,auto=14,fanout=4,pipeline=3"
_ARGS = [
    "--duration", "24", "--rate", "14", "--max-active", str(_LIMIT), "--burst-every", "7",
    "--burst-size", "12", "--restart-every", "9", "--cycle-seconds", "2", "--idle-ttl", "2",
    "--breaker-cooldown", "1", "--outage-every", "8", "--outage-seconds", "4", "--mix", _MIX,
    "--baseline-seconds", "1", "--drain", "90", "--seed", "7",
]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One short soak in its own process; every report state seen meanwhile."""
    work = tmp_path_factory.mktemp("swarm-soak")
    data, report = work / "data", work / "report.json"
    seen: list[tuple[str, str]] = []
    with (work / "out.txt").open("wb") as out:
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), *_ARGS, "--data-dir", str(data), "--report", str(report)],
            stdout=out, stderr=subprocess.STDOUT, cwd=str(REPO))
        try:
            until = time.monotonic() + 240
            while proc.poll() is None and time.monotonic() < until:
                try:
                    now = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    now = None  # not written yet, or being replaced this instant
                if now is not None and (not seen or seen[-1] != (now["status"], now["updated_at"])):
                    seen.append((now["status"], now["updated_at"]))
                time.sleep(0.2)
            code = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
    final = json.loads(report.read_text(encoding="utf-8"))
    if not seen or seen[-1] != (final["status"], final["updated_at"]):
        seen.append((final["status"], final["updated_at"]))
    return code, final, data, seen, (work / "out.txt").read_text(encoding="utf-8", errors="replace")


def test_a_short_soak_is_healthy_and_did_the_work_it_reports(run):
    code, report, _, _, out = run
    assert code == 0 and report["healthy"] and report["status"] == "passed", (report["violations"], out[-3000:])
    tasks = report["tasks"]
    assert tasks["submitted"] >= 150 and tasks["open"] == 0, tasks
    kinds = tasks["by_kind"]
    assert all(kinds[k]["worker_runs"] >= 1 for k in swarm_soak._KINDS), kinds
    assert kinds["slow"]["settled"].get("timeout") and kinds["stale"]["settled"].get("timeout"), kinds
    assert kinds["cancel"]["settled"].get("cancelled") and kinds["flaky"]["settled"].get("failed"), kinds
    breakers = report["breakers"]
    assert breakers["trips"] >= 1 and breakers["half_open_probes"] >= 1 and breakers["recoveries"] >= 1, breakers
    scaler = report["autoscaler"]
    assert scaler["spawns"] >= 1 and scaler["reaps"] >= 1, scaler
    restarts = report["restarts"]
    assert restarts["count"] >= 1 and restarts["interrupted"] >= 1, restarts
    admission = report["admission"]
    assert admission["refusals"] >= 1, admission
    assert max(admission["peak_in_flight"].values()) <= _LIMIT, admission
    assert report["loop"]["lag_ms"]["n"] > 0 and report["resources"]["max_rss_mib"], report["loop"]


def test_the_report_is_on_disk_while_the_soak_runs(run):
    """A long run a reboot ends early still leaves its evidence: the report is
    rewritten, whole, as the run goes, then marked finished."""
    _, _, _, seen, _ = run
    statuses = [status for status, _ in seen]
    assert statuses.count("running") >= 3, seen
    assert statuses[-1] == "passed" and "passed" not in statuses[:-1], seen


def _copy(data: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    return copy


def test_the_check_sees_a_task_moved_out_of_its_terminal_status(run, tmp_path):
    """Negative control: the run's own stores, then one finished row put back
    to running."""
    _, _, data, _, _ = run
    assert swarm_soak.check_store(data) == []
    copy = _copy(data, tmp_path)
    conn = sqlite3.connect(str(copy / swarm_soak.TASK_DB))
    try:
        tid, other = [r[0] for r in conn.execute(
            "SELECT id FROM swarm_tasks WHERE status = 'completed' LIMIT 2")]
        conn.execute("UPDATE swarm_tasks SET status = 'running' WHERE id = ?", (tid,))
        conn.execute("UPDATE swarm_tasks SET generation = generation - 1 WHERE id = ?", (other,))
        conn.commit()
    finally:
        conn.close()
    problems = swarm_soak.check_store(copy)
    # The store's own status history (the trigger), and the settled status.
    assert any(f"{tid} left its terminal status completed for running" in p for p in problems), problems
    assert any(tid in p and "left its recorded status completed for running" in p for p in problems), problems
    assert any(f"{other}: a write of generation 0 applied over generation 1" in p for p in problems), problems


def test_the_check_sees_more_tasks_running_at_once_than_the_limit(run, tmp_path):
    """Negative control: limit + 1 finished rows made to overlap one second."""
    _, _, data, _, _ = run
    copy = _copy(data, tmp_path)
    conn = sqlite3.connect(str(copy / swarm_soak.TASK_DB))
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM swarm_tasks WHERE status = 'completed' LIMIT ?", (_LIMIT + 1,))]
        conn.executemany(
            "UPDATE swarm_tasks SET started_at = '2026-01-01T00:00:00+00:00', "
            "completed_at = '2026-01-01T00:00:01+00:00' WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    finally:
        conn.close()
    problems = swarm_soak.check_store(copy)
    assert any(f"{_LIMIT + 1} tasks running at once" in p for p in problems), problems
    assert swarm_soak.in_flight_problems([_LIMIT, _LIMIT + 1, 2], _LIMIT)
    assert swarm_soak.in_flight_problems([_LIMIT, 2], _LIMIT) == []


def _soak(tmp_path: Path):
    soak = swarm_soak._Soak(swarm_soak._parse([]), tmp_path, swarm_soak._CountingHandler())
    return soak, swarm_soak._instrumented_classes(soak)


async def test_the_soak_sees_a_dispatch_stopped_with_no_ending(tmp_path):
    """The engine's defect the 24-hour run found: a run stopped while its
    RUNNING row was written raised a cancellation at its caller instead of an
    ending. The harness names it; a recorded ending (the control) is not
    one, and the runner's own cancellation at the end is let through."""
    from kazma_core.swarm.task import TaskResult

    class _Stopped:
        async def dispatch(self, task):
            raise asyncio.CancelledError

    class _Ended:
        async def dispatch(self, task):
            return TaskResult(task_id=task.id, status="cancelled",
                              metadata={"interrupted_by": "shutdown", "execution_outcome": "none_invoked"})

    soak = swarm_soak._Soak(swarm_soak._parse([]), tmp_path, swarm_soak._CountingHandler())

    async def _one(engine, tid):
        spec = swarm_soak._Spec(id=tid, kind="ok", task_type="dispatch", workers=["steady-1"],
                                prompt=f"soak:ok:{tid} x", timeout=30.0, fallback=[], work_s=0.01)
        rec = soak.recs[tid] = swarm_soak._Rec(spec)
        rec.submissions = rec.pending = 1
        soak.engine = engine
        await soak._dispatch(spec, rec, engine, False)
        return rec

    assert (await _one(_Ended(), "ended")).outcomes == ["cancelled"] and soak.violations == []
    assert (await _one(_Stopped(), "stopped")).outcomes == ["stopped-without-ending"]
    assert any("stopped with no ending recorded" in v for v in soak.violations), soak.violations
    soak.closing = True
    with pytest.raises(asyncio.CancelledError):
        await _one(_Stopped(), "at-the-end")


def test_the_breaker_check_sees_an_older_generation_outcome_applied(monkeypatch, tmp_path):
    """Negative control: the product's staleness guard switched off."""
    from kazma_core.swarm.reliability import CircuitBreaker

    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    soak, classes = _soak(tmp_path)
    breaker = classes["breaker"](failure_threshold=2, cooldown_seconds=3600)
    early = breaker.admit("w")  # admitted before the trip
    breaker.record_failure(breaker.admit("w"))
    breaker.record_failure(breaker.admit("w"))  # tripped: a new generation
    breaker.record_success(early)
    assert soak.breakers["trips"] == 1 and soak.breakers["older_outcomes"] == 1, soak.breakers
    assert soak.violations == []  # the product counted it, applied nothing
    monkeypatch.setattr(CircuitBreaker, "_stale", lambda self, token: False)
    breaker.record_failure(early)
    assert any("an outcome admitted in generation 0" in v for v in soak.violations), soak.violations


def test_the_breaker_check_sees_an_older_state_put_back_by_a_refresh(monkeypatch, tmp_path):
    """The violation the harness test reported in 2 runs of 10 (2026-10-15):
    a refresh laid the shared copy of the generation before over a newer
    state. With the product's refresh (a newer generation only) nothing is
    reported; with the old refresh (the negative control) the check names it."""
    from kazma_core.swarm.reliability import CircuitBreaker

    store: dict = {}

    class _Settings:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value, category="general"):
            store[key] = value

        def atomic_update(self, key, fn, category="general"):
            store[key] = fn(store.get(key))
            return store[key]

        def delete(self, key):
            return store.pop(key, None) is not None

    def _old_refresh(self, worker_name):
        shared = type(self).load_shared(worker_name)
        if shared is None:
            return
        self.consecutive_failures = shared.consecutive_failures
        self._state, self._opened_at = shared._state, shared._opened_at
        self.generation = max(self.generation, shared.generation)

    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Settings())
    soak, classes = _soak(tmp_path)
    breaker = classes["breaker"](failure_threshold=1, cooldown_seconds=3600)
    breaker.persist_shared("w")  # closed, generation 0
    breaker.record_failure(breaker.admit("w"))  # open, generation 1, not shared yet
    breaker.refresh_from_shared("w")
    assert soak.violations == [] and breaker.generation == 1
    monkeypatch.setattr(CircuitBreaker, "refresh_from_shared", _old_refresh)
    breaker.refresh_from_shared("w")
    assert any("moved it from open to closed at generation 1" in v for v in soak.violations), soak.violations


def test_the_autoscaler_check_sees_a_bound_passed_and_a_busy_reap(monkeypatch, tmp_path):
    """Negative controls: the scaler's capacity count broken, and a reap of an
    instance with a dispatch in flight."""
    from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate

    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")  # a reap forgets no settings key here
    soak, classes = _soak(tmp_path)
    engine = MagicMock()
    scaler = classes["scaler"](engine, templates_path=tmp_path / "templates.json")
    scaler.register_template(WorkerTemplate(name="gen", catch_all=True, max_instances=1))
    assert scaler.maybe_scale("anything") is not None and scaler.maybe_scale("anything") is None
    assert soak.violations == []
    monkeypatch.setattr(AutoScaler, "_count_active_instances", lambda self, name: 0)
    assert scaler.maybe_scale("anything") is not None
    assert any("2 instances of gen, above its bound of 1" in v for v in soak.violations), soak.violations

    soak.violations.clear()
    name = next(iter(scaler._instances))
    engine.get_worker.return_value = SimpleNamespace(in_flight=0, busy=False)
    scaler._reap_instance(name)
    assert soak.violations == []
    other = next(iter(scaler._instances))
    engine.get_worker.return_value = SimpleNamespace(in_flight=1, busy=True)
    scaler._reap_instance(other)
    assert any(f"reaped {other} with 1 dispatch(es) in flight" in v for v in soak.violations), soak.violations
