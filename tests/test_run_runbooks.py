"""Automatic first steps and "Ask Kazma to diagnose".

Phase 4 of ``docs/plans/RUN_SUPERVISION_PLAN.md``:

* every runbook is off until the owner switches it on;
* switched on, it takes ONE step per lineage a day: a retried task that fails
  again is not retried again by itself;
* never on unknown effects, never through ``retry_checked`` (planted entries
  as the negative controls);
* the diagnosis runs on a read-only graph, sees the incident fenced, runs one
  at a time, and its answer reaches the incident's chat.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.runs import registry, runbooks, supervisor
from kazma_core.runs.contract import ActionResult, Run, RunState
from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
from kazma_core.runs.kinds import KINDS

REPO = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0


class Recorder:
    STATE_MAP = {"failed": RunState.FAILED}

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self.acted: list[str] = []

    async def list_runs(self, now, *, window_s):
        return list(self.runs)

    async def act(self, native_id, action, *, actor):
        self.acted.append(action)
        return ActionResult(True, "retried", "Retrying it as a new task.")


def _failed(ident="t1", *, effects="none", actions=("retry",), lineage="") -> Run:
    return Run(run_id=f"swarm_task:{ident}", kind="swarm_task", native_id=ident, title="research", state=RunState.FAILED,
               native_state="failed", ended_at=T0, effects=effects, actions=actions, version="1",
               origin={"platform": "telegram", "target": "telegram:42", "thread_id": "gw"},
               lineage=lineage or f"swarm_task:{ident}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    adapter = Recorder()
    for kind in list(KINDS):
        if kind != "swarm_task":
            monkeypatch.delitem(KINDS, kind)
    monkeypatch.setitem(registry._ADAPTERS, "swarm_task", adapter)
    reset_incident_store(IncidentStore(tmp_path / "inc.db"))
    flags: dict[str, bool] = {}
    monkeypatch.setattr(runbooks, "enabled", lambda: {name: flags.get(name, False) for name in runbooks.RUNBOOK})
    sent: list[dict] = []

    async def fake_send(target, text, *, backend="telegram", **kw):
        sent.append({"text": text, **kw})
        return f"sent:{target}"

    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr(supervisor, "_watch_started", T0 - 60)
    yield SimpleNamespace(adapter=adapter, flags=flags, sent=sent)
    reset_incident_store(None)


def tick(at=T0 + 5):
    return asyncio.run(supervisor.tick(now=at))


def test_every_runbook_is_off_by_default(world):
    world.adapter.runs = [_failed()]
    tick()
    assert world.adapter.acted == []
    assert world.sent[0]["run_card"]["actions"] == ["retry", "dismiss", "diagnose"]


def test_a_runbook_switched_on_takes_its_step_and_says_so(world):
    world.flags["swarm_task.failed"] = True
    world.adapter.runs = [_failed()]
    assert tick()["automatic"] == 1
    assert world.adapter.acted == ["retry"]
    message = world.sent[0]
    assert "automatic first step: Retry" in message["text"]
    assert message["run_card"]["actions"] == [], "nothing left to press"
    row = get_incident_store().recent()[0]
    assert row["state"] == "closed" and row["decided_by"] == "kazma" and row["decided_platform"] == "runbook"


def test_a_retry_that_fails_again_is_not_retried_by_itself(world):
    world.flags["swarm_task.failed"] = True
    world.adapter.runs = [_failed("t1")]
    tick()
    # The retry ran as a new task, and it failed too: same lineage.
    world.adapter.runs.append(_failed("t2", lineage="swarm_task:t1"))
    tick(T0 + 35)
    assert world.adapter.acted == ["retry"]
    assert world.sent[-1]["run_card"]["actions"] == ["retry", "dismiss", "diagnose"]


def test_never_on_unknown_effects(world):
    world.flags["swarm_task.failed"] = True
    world.adapter.runs = [_failed(effects="unknown", actions=("retry_checked",))]
    tick()
    assert world.adapter.acted == []
    # Negative control: the same run with known effects is retried.
    assert runbooks.refusal(_failed(effects="unknown", actions=("retry",)), "retry") == "its effects are unknown"
    assert runbooks.refusal(_failed(), "retry") == ""


def test_never_through_the_checked_retry(world, monkeypatch):
    """A planted runbook entry that would retry an unknown run is refused."""
    monkeypatch.setitem(runbooks.RUNBOOK, "swarm_task.failed", "retry_checked")
    world.flags["swarm_task.failed"] = True
    world.adapter.runs = [_failed(actions=("retry_checked",))]
    tick()
    assert world.adapter.acted == []


def test_the_setting_accepts_only_known_runbooks():
    from kazma_core.settings_validation import validate_setting

    value, _category = validate_setting("runs.runbooks", {"swarm_task.failed": True})
    assert value == {"swarm_task.failed": True}
    with pytest.raises(ValueError):
        validate_setting("runs.runbooks", {"swarm_task.stalled": True})
    with pytest.raises(ValueError):
        validate_setting("runs.runbooks", {"swarm_task.failed": "yes"})


# ── diagnosis ────────────────────────────────────────────────────────────

@pytest.fixture
def incident(world):
    world.adapter.runs = [_failed()]
    tick()
    return get_incident_store().live()[0]


def test_a_diagnosis_runs_read_only_with_the_incident_fenced(world, incident, monkeypatch):
    from kazma_core.runs import diagnose
    from kazma_core.runs.decide import decide

    seen: dict = {}
    done = threading.Event()
    release = threading.Event()

    async def fake_turn(*, graph, thread_id, state, config, persist):
        seen.update(graph=graph, state=state, persist=persist)
        while not release.is_set():  # still diagnosing while the second press lands
            await asyncio.sleep(0.01)
        return SimpleNamespace(text="It hit a provider outage; retry it.", turn_failed=False, error=None)

    async def fake_send(target, text, *, backend="telegram", **kw):
        world.sent.append({"text": text})
        done.set()
        return "sent:x"

    monkeypatch.setattr("kazma_core.agent.turn.run_agent_turn", fake_turn)
    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr(diagnose, "_graph_builder", lambda: "READ-ONLY GRAPH")

    async def scenario():
        first = await decide(incident["id"], "diagnose", actor="telegram:1", platform="telegram")
        second = await decide(incident["id"], "diagnose", actor="telegram:1", platform="telegram")
        release.set()
        for _ in range(100):
            if done.is_set():
                break
            await asyncio.sleep(0.02)
        return first, second

    first, second = asyncio.run(scenario())
    assert first.ok and first.code == "diagnosing"
    assert not second.ok and second.code == "diagnosis_running"
    assert seen["graph"] == "READ-ONLY GRAPH" and seen["persist"] is False
    system = [m["content"] for m in seen["state"]["messages"] if m["role"] == "system"]
    assert "run_status('swarm_task:t1')" in system[0]
    assert "kazma:data" in system[1] and "research" in system[1], "the run's own text is fenced"
    assert "research" not in system[0], "nothing of the run's text outside the fence"
    assert "Diagnosis of swarm_task:t1" in world.sent[-1]["text"]
    row = get_incident_store().get(incident["id"])
    assert row["diagnosis_state"] == "done" and row["state"] == "open", "a diagnosis decides nothing"


def test_the_diagnosis_graph_is_the_read_capability_graph():
    """The app builds it with ``cron_fire_graph(agent, capabilities="read")``;
    read offers read-tier tools only, read_write offers writes too."""
    app = (REPO / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    start = app.index("def _diagnosis_graph()")
    assert 'cron_fire_graph(self.agent, capabilities="read")' in app[start:start + 600]
    from kazma_core.cron.capabilities import _declared_tool_names

    defs = [{"name": "list_runs"}, {"name": "run_status"}, {"name": "save_proposal"}, {"name": "shell_exec"}]
    assert _declared_tool_names(defs, "read") == ["list_runs", "run_status"]
    assert "save_proposal" in _declared_tool_names(defs, "read_write")  # negative control: a write tool
